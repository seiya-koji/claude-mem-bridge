import json

from claude_mem_bridge import installer
from claude_mem_bridge.hosts import HOSTS

EXE = "$HOME/.local/bin/claude-mem-bridge"


def write(path, data, compact=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")) if compact else json.dumps(data, indent=2))


def read(path):
    return json.loads(path.read_text())


def test_portable_path_uses_home_variable(home):
    assert installer.portable_path(home / ".local/bin/claude-mem-bridge") == EXE
    assert installer.portable_path(home.parent / "elsewhere") == str(home.parent / "elsewhere")


def test_install_adds_hooks_and_keeps_other_entries():
    host = HOSTS["bob"]
    other = {"hooks": [{"type": "command", "command": "other.sh"}]}
    write(host.settings_path, {"approval": {}, "hooks": {"SessionStart": [other]}})

    installer.install(host, EXE)

    hooks = read(host.settings_path)["hooks"]
    assert set(hooks) == {e.name for e in host.events}
    assert hooks["SessionStart"][0] == other
    assert hooks["SessionStart"][1] == {
        "matcher": "^(startup|resume|compact)$",
        "hooks": [{"type": "command", "command": f'"{EXE}" hook --host bob', "timeout": 40}],
    }
    assert read(host.settings_path)["approval"] == {}


def test_install_is_idempotent():
    host = HOSTS["copilot"]
    write(host.settings_path, {})
    installer.install(host, EXE)
    first = host.settings_path.read_text()

    changes = installer.install(host, EXE)

    assert not any(c.changed for c in changes)
    assert host.settings_path.read_text() == first


def test_install_replaces_legacy_entry_in_place():
    host = HOSTS["codex"]
    legacy = {"hooks": [{"type": "command", "command": "python3 '/Users/x/.claude/hooks/claude-mem-bridge.py' --host codex"}]}
    official = {"hooks": [{"type": "command", "command": "node worker-service.cjs hook codex context"}]}
    write(host.settings_path, {"hooks": {"SessionStart": [official, legacy, official]}})

    installer.install(host, EXE)

    groups = read(host.settings_path)["hooks"]["SessionStart"]
    assert [g == official for g in groups] == [True, False, True]
    assert groups[1]["hooks"][0]["command"] == f'"{EXE}" hook --host codex'


def test_settings_contain_no_absolute_home_path(home):
    for host in HOSTS.values():
        write(host.settings_path, {})
        installer.install(host, EXE)
        assert str(home) not in host.settings_path.read_text()
        if host.mcp_path:
            assert str(home) not in host.mcp_path.read_text()


def test_install_keeps_compact_style_and_backs_up():
    host = HOSTS["copilot"]
    write(host.settings_path, {"model": "auto"}, compact=True)

    installer.install(host, EXE)

    assert "\n" not in host.settings_path.read_text()
    backup = host.settings_path.with_name(host.settings_path.name + installer.BACKUP_SUFFIX)
    assert read(backup) == {"model": "auto"}


def test_mcp_server_replaces_existing_claude_mem_entry():
    host = HOSTS["copilot"]
    write(host.settings_path, {})
    write(host.mcp_path, {"mcpServers": {"claude-mem": {"command": "node", "args": ["-e", "..."]}, "other": {}}})

    installer.install(host, EXE)

    servers = read(host.mcp_path)["mcpServers"]
    assert servers["other"] == {}
    assert servers["claude-mem"] == {
        "command": "sh", "args": ["-c", f'exec "{EXE}" mcp'], "type": "local", "tools": ["*"],
    }
    assert installer.is_mcp_installed(host)


def test_uninstall_removes_only_bridge_entries():
    host = HOSTS["bob"]
    other = {"hooks": [{"type": "command", "command": "other.sh"}]}
    write(host.settings_path, {"hooks": {"Stop": [other]}})
    write(host.mcp_path, {"mcpServers": {"other": {}}})
    installer.install(host, EXE)
    assert installer.is_installed(host)

    installer.uninstall(host)

    assert read(host.settings_path)["hooks"] == {"Stop": [other]}
    assert read(host.mcp_path)["mcpServers"] == {"other": {}}
    assert not installer.is_installed(host)


def test_uninstall_leaves_foreign_claude_mem_mcp_server():
    host = HOSTS["bob"]
    foreign = {"command": "node", "args": ["mcp-server.cjs"]}
    write(host.mcp_path, {"mcpServers": {"claude-mem": foreign}})

    installer.uninstall(host)

    assert read(host.mcp_path)["mcpServers"]["claude-mem"] == foreign


def test_claude_config_dir_is_respected(home, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / "custom"))
    assert HOSTS["claude"].settings_path == home / "custom/settings.json"
