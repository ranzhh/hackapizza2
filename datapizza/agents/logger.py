import logging
import os
from datetime import datetime

from rich.console import Console
from rich.panel import Panel

log = logging.getLogger(__name__)


class AgentLogger:
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        # Use a deterministic hash function based on the string
        color_num = sum(ord(c) * i for i, c in enumerate(self.agent_name, 1)) % 255
        self.color = f"color({color_num})"
        self.console = Console()

    def _colored_log(self, log_text: str, *args, **kwargs) -> None:
        if not log_text:
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.console.print(
            f"[white]{timestamp}[/] [{self.color}]<{self.agent_name}> {log_text} [/]",
            *args,
            **kwargs,
        )

    def _log(self, log_text: int, *args, **kwargs) -> None:
        log.log(log_text, *args, **kwargs)

    def log_panel(self, *args, **kwargs) -> None:
        if self._isEnabledFor(logging.DEBUG) and args:
            self.console.print(
                f"<[{self.color}]{self.agent_name}[/]>",
                Panel(*args, **kwargs, border_style=self.color, subtitle_align="left"),
            )

    def _isEnabledFor(self, level: int) -> bool:
        env_level = os.getenv("DATAPIZZA_AGENT_LOG_LEVEL", "DEBUG")
        numeric_level = getattr(logging, env_level.upper(), logging.INFO)
        return level >= numeric_level

    def debug(self, msg, *args, **kwargs):
        if self._isEnabledFor(logging.DEBUG):
            self._colored_log(msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        if self._isEnabledFor(logging.INFO):
            self._log(logging.INFO, msg, args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        if self._isEnabledFor(logging.WARNING):
            self._log(logging.WARNING, msg, args, **kwargs)

    def error(self, msg, *args, **kwargs):
        if self._isEnabledFor(logging.ERROR):
            self._log(logging.ERROR, msg, args, **kwargs)

    def critical(self, msg, *args, **kwargs):
        if self._isEnabledFor(logging.CRITICAL):
            self._log(logging.CRITICAL, msg, args, **kwargs)

    def fatal(self, msg, *args, **kwargs):
        if self._isEnabledFor(logging.FATAL):
            self._log(logging.FATAL, msg, args, **kwargs)
