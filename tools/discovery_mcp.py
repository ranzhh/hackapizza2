"""
discovery_mcp.py
~~~~~~~~~~~~
Crawls the Hackapizza MCP server (tools/list, prompts/list, resources/list)
and persists a discovery report with the **same JSON format** used by
``discovery_harness.py``:

    {
      "generated_at": "...",
      "team_id": "...",
      "base_url": "...",
      "results": [
        {
          "endpoint": "tools/list",
          "status":   "ok" | "error",
          "duration_ms": 42.1,
          "args":    {},
          "result":  <raw server response>,
          "error_type":    null,
          "error_message": null
        },
        ...
      ],
      "summary": { "total": N, "ok": N, "error": N }
    }

One result row is emitted per JSON-RPC call made during discovery.

Usage
-----
    python tools/discovery_mcp.py [--out artifacts/mcp_discovery/latest.json]

Environment variables
---------------------
    HACKAPIZZA_API_KEY   - required unless passed via --api-key
    HACKAPIZZA_TEAM_ID   - required unless passed via --team-id
    HACKAPIZZA_BASE_URL  - optional, defaults to https://hackapizza.datapizza.tech
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Awaitable

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import AnyUrl

from hp2.core.settings import get_settings  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "https://hackapizza.datapizza.tech"
MCP_ENDPOINT = "/mcp"
TIMEOUT_SECONDS = 30


# ---------------------------------------------------------------------------
# Shared result dataclass – identical shape to EndpointCallResult in discovery_harness
# ---------------------------------------------------------------------------


@dataclass
class EndpointCallResult:
    endpoint: str
    status: str          # "ok" | "error"
    duration_ms: float
    args: dict[str, Any]
    result: Any | None = None
    error_type: str | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def _schema_type(prop: dict) -> str:
    """Convert a JSON Schema property dict to a compact Python-style type string."""
    t = prop.get("type")
    enum = prop.get("enum")
    if enum:
        return "Literal[" + ", ".join(repr(v) for v in enum) + "]"
    mapping = {
        "string":  "str",
        "integer": "int",
        "number":  "float",
        "boolean": "bool",
        "array":   "list",
        "object":  "dict",
    }
    return mapping.get(t, "Any") if t else "Any"


def _render_tool(tool: dict) -> str:
    """Render a single MCP tool as a Python function stub (smolagents style)."""
    name: str = tool["name"]
    description: str = tool.get("description") or ""
    schema: dict = tool.get("inputSchema") or {}
    props: dict = schema.get("properties") or {}
    required: list = schema.get("required") or []

    # Derive output type
    out_schema = tool.get("outputSchema") or {}
    output_type = _schema_type(out_schema) if out_schema else "Any"

    # Build argument list for the signature
    sig_args = ", ".join(props.keys())
    lines: list[str] = []
    lines.append(f"def {name}({sig_args}) -> {output_type}:")
    lines.append(f'  """{description}')

    if props:
        lines.append("  Args:")
        for arg_name, arg_info in props.items():
            arg_type = _schema_type(arg_info)
            if arg_name not in required:
                arg_type += ", optional"
            arg_desc = arg_info.get("description", "")
            # Inline constraints (min, max, enum already covered by type)
            constraints = []
            if "minLength" in arg_info:
                constraints.append(f"minLength={arg_info['minLength']}")
            if "maxLength" in arg_info:
                constraints.append(f"maxLength={arg_info['maxLength']}")
            if "minimum" in arg_info:
                constraints.append(f">={arg_info['minimum']}")
            if "exclusiveMinimum" in arg_info:
                constraints.append(f">{arg_info['exclusiveMinimum']}")
            if "maximum" in arg_info:
                constraints.append(f"<={arg_info['maximum']}")
            if constraints:
                suffix = " [" + ", ".join(constraints) + "]"
            else:
                suffix = ""
            desc_text = (arg_desc + suffix).strip() or "–"
            lines.append(f"    {arg_name} ({arg_type}): {desc_text}")

    lines.append('  """')
    return "\n".join(lines)


def _render_markdown(
    server_info: dict,
    tools: list[dict],
    prompts: list[dict],
    resources: list[dict],
    generated_at: str,
    base_url: str,
) -> str:
    sv = server_info.get("serverInfo", {})
    server_name = sv.get("name", "MCP Server")
    server_ver  = sv.get("version", "?")
    protocol    = server_info.get("protocolVersion", "?")

    lines: list[str] = [
        f"# {server_name} — MCP Reference",
        "",
        "| | |",
        "|---|---|",
        f"| **Server** | `{server_name}` v{server_ver} |",
        f"| **Protocol** | `{protocol}` |",
        f"| **Endpoint** | `{base_url}/mcp` |",
        f"| **Generated** | `{generated_at}` |",
        "",
    ]

    # ── Tools ──────────────────────────────────────────────────────────────
    lines.append("## Tools")
    lines.append("")
    if tools:
        lines.append(f"> {len(tools)} tool(s) available.")
        lines.append("")
        for tool in tools:
            lines.append(f"### `{tool['name']}`")
            lines.append("")
            if tool.get("description"):
                lines.append(tool["description"])
                lines.append("")
            lines.append("```python")
            lines.append(_render_tool(tool))
            lines.append("```")
            lines.append("")
    else:
        lines.append("_No tools available._")
        lines.append("")

    # ── Prompts ────────────────────────────────────────────────────────────
    if prompts:
        lines.append("## Prompts")
        lines.append("")
        for p in prompts:
            lines.append(f"### `{p['name']}`")
            lines.append("")
            if p.get("description"):
                lines.append(p["description"])
                lines.append("")
            args = p.get("arguments") or []
            if args:
                lines.append("| Argument | Required | Description |")
                lines.append("|---|---|---|")
                for a in args:
                    req = "✓" if a.get("required") else ""
                    lines.append(f"| `{a['name']}` | {req} | {a.get('description', '')} |")
                lines.append("")

    # ── Resources ──────────────────────────────────────────────────────────
    if resources:
        lines.append("## Resources")
        lines.append("")
        lines.append("| URI | MIME type | Description |")
        lines.append("|---|---|---|")
        for r in resources:
            lines.append(
                f"| `{r.get('uri', '')}` "
                f"| `{r.get('mimeType', '')}` "
                f"| {r.get('description', '')} |"
            )
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Timed SDK call wrapper – produces an EndpointCallResult
# ---------------------------------------------------------------------------


async def _timed(
    label: str,
    args: dict,
    call: Callable[[], Awaitable[Any]],
) -> EndpointCallResult:
    """Run ``call``, serialise the Pydantic result, and wrap in EndpointCallResult."""
    started = perf_counter()
    try:
        raw = await call()
        duration_ms = (perf_counter() - started) * 1000
        # SDK methods return Pydantic models – dump to plain dict
        result = raw.model_dump() if hasattr(raw, "model_dump") else raw
        print(f"  [OK]    {label}  ({duration_ms:.0f} ms)")
        return EndpointCallResult(
            endpoint=label,
            status="ok",
            duration_ms=duration_ms,
            args=args,
            result=result,
        )
    except Exception as exc:
        duration_ms = (perf_counter() - started) * 1000
        print(f"  [ERROR] {label}  ({duration_ms:.0f} ms): {exc}")
        return EndpointCallResult(
            endpoint=label,
            status="error",
            duration_ms=duration_ms,
            args=args,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )


# ---------------------------------------------------------------------------
# Main crawl
# ---------------------------------------------------------------------------


async def crawl(
    base_url: str,
    api_key: str,
    team_id: str,
    out_path: str | Path,
) -> Path:
    mcp_url = f"{base_url}{MCP_ENDPOINT}"
    headers = {"x-api-key": api_key}
    timeout = timedelta(seconds=TIMEOUT_SECONDS)

    results: list[EndpointCallResult] = []
    server_info: dict = {}
    tools: list[dict] = []
    prompts: list[dict] = []
    resources: list[dict] = []

    print(f"[*] Connecting to {mcp_url} …")

    async with (
        streamablehttp_client(mcp_url, headers=headers, timeout=timeout) as (read, write, _),
        ClientSession(read, write, read_timeout_seconds=timeout) as session,
    ):
        # 1 – handshake (initialize + notifications/initialized)
        init_r = await _timed("initialize", {}, session.initialize)
        results.append(init_r)
        if init_r.status == "ok":
            server_info = init_r.result or {}
            sv = server_info.get("serverInfo", {})
            print(f"[+] Server: {sv.get('name', '?')} v{sv.get('version', '?')}")

        # 2 – tools/list
        tools_r = await _timed("tools/list", {}, session.list_tools)
        results.append(tools_r)
        if tools_r.status == "ok":
            tools = (tools_r.result or {}).get("tools", [])
            print(f"[+] {len(tools)} tool(s): {[t['name'] for t in tools]}")

        # 3 – prompts/list
        prompts_r = await _timed("prompts/list", {}, session.list_prompts)
        results.append(prompts_r)
        if prompts_r.status == "ok":
            prompts = (prompts_r.result or {}).get("prompts", [])
            print(f"[+] {len(prompts)} prompt(s).")

        # 4 – resources/list
        resources_r = await _timed("resources/list", {}, session.list_resources)
        results.append(resources_r)
        if resources_r.status == "ok":
            resources = (resources_r.result or {}).get("resources", [])
            print(f"[+] {len(resources)} resource(s).")

        # 5 – prompts/get (per prompt)
        for prompt in prompts:
            name = prompt["name"]
            results.append(await _timed(
                f"prompts/get:{name}",
                {"name": name, "arguments": {}},
                lambda n=name: session.get_prompt(n, {}),
            ))

        # 6 – resources/read (per resource)
        for resource in resources:
            uri = resource.get("uri")
            if not uri:
                continue
            results.append(await _timed(
                f"resources/read:{uri}",
                {"uri": uri},
                lambda u=uri: session.read_resource(AnyUrl(u)),
            ))

    # -----------------------------------------------------------------------
    # Build report  (same envelope as discovery_harness.py)
    # -----------------------------------------------------------------------
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "generated_at": now,
        "team_id": team_id,
        "base_url": base_url,
        "server_info": server_info,
        "results": [asdict(r) for r in results],
        "summary": {
            "total": len(results),
            "ok":    sum(r.status == "ok"    for r in results),
            "error": sum(r.status == "error" for r in results),
            "tool_count":     len(tools),
            "prompt_count":   len(prompts),
            "resource_count": len(resources),
            "tool_names":     [t["name"] for t in tools],
            "prompt_names":   [p["name"] for p in prompts],
            "resource_uris":  [r.get("uri") for r in resources],
        },
    }

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # Also write a timestamped snapshot alongside latest.json
    snapshot_path = out.parent / f"discovery_{now}.json"
    snapshot_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # Human-readable markdown (always overwritten with the latest data)
    md_path = out.parent / "mcp.md"
    md_content = _render_markdown(server_info, tools, prompts, resources, now, base_url)
    md_path.write_text(md_content, encoding="utf-8")

    ok    = report["summary"]["ok"]
    total = report["summary"]["total"]
    print(f"[✓] {ok}/{total} calls succeeded.")
    print(f"    JSON   → {out}")
    print(f"    Snapshot → {snapshot_path}")
    print(f"    Markdown → {md_path}")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crawl the Hackapizza MCP server and emit a discovery report."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("HACKAPIZZA_BASE_URL", DEFAULT_BASE_URL),
    )
    parser.add_argument("--api-key",  default=None, help="Override API key from settings")
    parser.add_argument("--team-id",  default=None, help="Override team ID from settings")
    parser.add_argument(
        "--out",
        default="artifacts/mcp_discovery/latest.json",
        help="Output file path (default: artifacts/mcp_discovery/latest.json)",
    )
    args = parser.parse_args()

    settings = get_settings()
    api_key = args.api_key or settings.hackapizza_team_api_key
    team_id = str(args.team_id or settings.hackapizza_team_id)

    if not api_key:
        print("ERROR: API key not found – set HACKAPIZZA_TEAM_API_KEY or pass --api-key", file=sys.stderr)
        sys.exit(1)
    if not team_id or team_id == "0":
        print("ERROR: Team ID not found – set HACKAPIZZA_TEAM_ID or pass --team-id", file=sys.stderr)
        sys.exit(1)

    asyncio.run(crawl(args.base_url, api_key, team_id, args.out))


if __name__ == "__main__":
    main()
