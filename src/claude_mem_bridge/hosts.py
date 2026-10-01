"""Where each agent keeps its hook and MCP settings, and which hooks the bridge registers there."""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Event:
    name: str
    timeout: int
    matcher: str | None = None


@dataclass(frozen=True)
class Host:
    name: str
    # The claude-mem platformSource whose SessionStart context the official claude-mem hook already injects.
    official_platform: str | None
    events: tuple[Event, ...]
    status_message: str | None = None
    mcp_entry: dict | None = None

    @property
    def settings_path(self) -> Path:
        return _SETTINGS_PATHS[self.name]()

    @property
    def mcp_path(self) -> Path | None:
        return _MCP_PATHS[self.name]() if self.mcp_entry is not None else None


def claude_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


_SETTINGS_PATHS = {
    "claude": lambda: claude_config_dir() / "settings.json",
    "codex": lambda: codex_home() / "hooks.json",
    "copilot": lambda: Path.home() / ".copilot" / "settings.json",
    "bob": lambda: Path.home() / ".bob" / "settings" / "settings.json",
}

_MCP_PATHS = {
    "copilot": lambda: Path.home() / ".copilot" / "mcp-config.json",
    "bob": lambda: Path.home() / ".bob" / "settings" / "mcp.json",
}

HOSTS = {
    host.name: host
    for host in (
        Host(
            name="claude",
            official_platform="claude-code",
            events=(Event("SessionStart", 30),),
            status_message="claude-mem: loading memory from other agents",
        ),
        Host(
            name="codex",
            official_platform="codex",
            events=(Event("SessionStart", 30),),
            status_message="claude-mem: loading memory from other agents",
        ),
        Host(
            name="copilot",
            official_platform=None,
            events=(
                Event("SessionStart", 40),
                Event("UserPromptSubmit", 20),
                Event("PostToolUse", 120),
                Event("Stop", 60),
                Event("SessionEnd", 10),
            ),
            mcp_entry={"type": "local", "tools": ["*"]},
        ),
        Host(
            name="bob",
            official_platform=None,
            events=(
                Event("SessionStart", 40, matcher="^(startup|resume|compact)$"),
                Event("UserPromptSubmit", 20),
                Event("PostToolUse", 120),
                Event("Stop", 30),
            ),
            mcp_entry={"type": "stdio", "disabled": False},
        ),
    )
}
