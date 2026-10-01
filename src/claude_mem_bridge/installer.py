"""Register and remove the bridge's hooks and MCP server in each agent's settings."""
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .hosts import Host

# Hook groups and MCP servers whose command contains this are owned by the bridge,
# including entries written by hand before the installer existed.
MARKER = "claude-mem-bridge"
MCP_SERVER_NAME = "claude-mem"
BACKUP_SUFFIX = ".claude-mem-bridge.bak"


@dataclass
class Change:
    path: Path
    changed: bool


def portable_path(path: Path) -> str:
    """Spell a path under the home directory as $HOME/... so settings carry no user-specific paths."""
    home = str(Path.home())
    text = str(path)
    if text == home or text.startswith(home + os.sep):
        return "$HOME" + text[len(home):]
    return text


def hook_command(executable: str, host: Host) -> str:
    return f'"{executable}" hook --host {host.name}'


def mcp_server(executable: str, host: Host) -> dict:
    # MCP clients usually exec the command without a shell, so let sh expand $HOME.
    return {"command": "sh", "args": ["-c", f'exec "{executable}" mcp'], **(host.mcp_entry or {})}


def _is_ours(group: dict) -> bool:
    return any(MARKER in str(hook.get("command", "")) for hook in group.get("hooks", []))


def _hook_group(executable: str, host: Host, event) -> dict:
    hook = {"type": "command", "command": hook_command(executable, host), "timeout": event.timeout}
    if host.status_message:
        hook["statusMessage"] = host.status_message
    group = {"matcher": event.matcher} if event.matcher else {}
    group["hooks"] = [hook]
    return group


def add_hooks(settings: dict, executable: str, host: Host) -> dict:
    hooks = settings.setdefault("hooks", {})
    for event in host.events:
        groups = hooks.setdefault(event.name, [])
        group = _hook_group(executable, host, event)
        owned = [i for i, g in enumerate(groups) if _is_ours(g)]
        if owned:
            # Replace in place: Codex tracks hook trust by position in hooks.json.
            groups[owned[0]] = group
            for i in reversed(owned[1:]):
                del groups[i]
        else:
            groups.append(group)
    return settings


def remove_hooks(settings: dict) -> dict:
    hooks = settings.get("hooks", {})
    for name in list(hooks):
        hooks[name] = [g for g in hooks[name] if not _is_ours(g)]
        if not hooks[name]:
            del hooks[name]
    return settings


def add_mcp(config: dict, executable: str, host: Host) -> dict:
    config.setdefault("mcpServers", {})[MCP_SERVER_NAME] = mcp_server(executable, host)
    return config


def remove_mcp(config: dict) -> dict:
    servers = config.get("mcpServers", {})
    if MARKER in json.dumps(servers.get(MCP_SERVER_NAME, {})):
        del servers[MCP_SERVER_NAME]
    return config


def is_installed(host: Host) -> bool:
    groups = read_json(host.settings_path).get("hooks", {})
    return all(any(_is_ours(g) for g in groups.get(event.name, [])) for event in host.events)


def is_mcp_installed(host: Host) -> bool:
    if host.mcp_path is None:
        return False
    server = read_json(host.mcp_path).get("mcpServers", {}).get(MCP_SERVER_NAME, {})
    return MARKER in json.dumps(server)


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def update_json(path: Path, edit) -> Change:
    """Apply edit to the JSON file, keeping its formatting style and backing it up before the first write."""
    try:
        original = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        original = None
    data = json.loads(original) if original else {}
    before = json.dumps(data, sort_keys=True)
    data = edit(data)
    if json.dumps(data, sort_keys=True) == before:
        return Change(path, False)

    if original is None or "\n" in original.strip():
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    else:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if original is not None:
        shutil.copy2(path, path.with_name(path.name + BACKUP_SUFFIX))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return Change(path, True)


def install(host: Host, executable: str) -> list[Change]:
    changes = [update_json(host.settings_path, lambda s: add_hooks(s, executable, host))]
    if host.mcp_path is not None:
        changes.append(update_json(host.mcp_path, lambda c: add_mcp(c, executable, host)))
    return changes


def uninstall(host: Host) -> list[Change]:
    changes = []
    if host.settings_path.exists():
        changes.append(update_json(host.settings_path, remove_hooks))
    if host.mcp_path is not None and host.mcp_path.exists():
        changes.append(update_json(host.mcp_path, remove_mcp))
    return changes
