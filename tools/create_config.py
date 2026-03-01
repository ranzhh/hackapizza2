from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CATEGORIES = {
    "Esploratore Galattico": {
        "multiplier": 5.0,
        "price": 2.0,
        "prestige_order": "asc",
        "time_order": "asc",
    },
    "Famiglie Orbitali": {
        "multiplier": 8.0,
        "price": 3.0,
        "prestige_order": "asc",
        "time_order": "desc",
    },
    "Saggi del Cosmo": {
        "multiplier": 10.0,
        "price": 3.5,
        "prestige_order": "desc",
        "time_order": "desc",
    },
    "Astrobarone": {
        "multiplier": 15.0,
        "price": 5.0,
        "prestige_order": "desc",
        "time_order": "asc",
    },
}


def select_recipes_for_category(
    recipes: list[dict],
    prestige_order: str,
    time_order: str,
    top_n: int | None = None,
) -> list[dict]:
    """Sort recipes by prestige and preparation time, then pick top N (or all if top_n is None)."""
    prestige_reverse = prestige_order == "desc"
    time_reverse = time_order == "desc"

    sorted_recipes = sorted(
        recipes,
        key=lambda r: (
            r["prestige"] * (-1 if prestige_reverse else 1),
            r["preparation_time_ms"] * (-1 if time_reverse else 1),
            len(r["ingredients"]),
        ),
    )
    return sorted_recipes if top_n is None else sorted_recipes[:top_n]


def build_config(recipes: list[dict], top_n: int | None = None) -> dict:
    """Build the full config dictionary from the list of available recipes."""
    config_recipes: dict[str, list[dict]] = {}
    config_ingredients: dict[str, dict[str, list[dict]]] = {}

    for category, settings in CATEGORIES.items():
        selected = select_recipes_for_category(
            recipes,
            prestige_order=settings["prestige_order"],
            time_order=settings["time_order"],
            top_n=top_n,
        )

        multiplier = settings["multiplier"]
        price = settings["price"]

        config_recipes[category] = [
            {"name": r["name"], "multiplier": multiplier}
            for r in selected
        ]

        config_ingredients[category] = {
            r["name"]: [
                {"name": ingredient_name, "price": price}
                for ingredient_name in r["ingredients"]
            ]
            for r in selected
        }

    return {"recipes": config_recipes, "ingredients": config_ingredients}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate a config JSON file from an available recipes JSON."
    )
    parser.add_argument(
        "input_file",
        type=Path,
        help="Path to the available_recipes.json file produced by discovery_recipes.py",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("config.json"),
        help="Path to the output config JSON file (default: config.json)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=None,
        help="Limit the number of recipes per category (default: all recipes)",
    )
    args = parser.parse_args()

    # --- Load recipes ---
    input_path: Path = args.input_file
    if not input_path.exists():
        print(f"Error: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    try:
        raw = json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON in {input_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    if isinstance(raw, dict):
        recipes_input: list[dict] = raw.get("recipes", [])
    elif isinstance(raw, list):
        recipes_input = raw
    else:
        print("Error: JSON must be an object with a 'recipes' key or a plain list.", file=sys.stderr)
        sys.exit(1)

    # --- Build and write config ---
    config = build_config(recipes_input, top_n=args.top_n)

    output_path: Path = args.output
    output_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Config written to {output_path}")
    for category, recipe_list in config["recipes"].items():
        print(f"\n  [{category}] — {len(recipe_list)} recipes")
        for r in recipe_list:
            print(f"    - {r['name']} (multiplier: {r['multiplier']})")
