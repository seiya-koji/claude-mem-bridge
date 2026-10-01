"""Share claude-mem memory across Claude Code, Codex, GitHub Copilot CLI, and IBM Bob Shell."""
import argparse
import json
import os
import sys
from pathlib import Path

from . import bridge, installer
from .hosts import HOSTS


def _selected_hosts(args) -> list:
    if args.all:
        return list(HOSTS.values())
    if not args.hosts:
        sys.exit("error: name at least one host or pass --all")
    unknown = [name for name in args.hosts if name not in HOSTS]
    if unknown:
        sys.exit(f"error: unknown host: {', '.join(unknown)} (choose from {', '.join(HOSTS)})")
    return [HOSTS[name] for name in args.hosts]


def _executable() -> str:
    return installer.portable_path(Path(sys.argv[0]).absolute())


def _report(changes) -> None:
    for change in changes:
        print(f"  {'updated' if change.changed else 'unchanged'}: {installer.portable_path(change.path)}")


def cmd_hook(args) -> None:
    output = bridge.handle(HOSTS[args.host], json.load(sys.stdin))
    if output:
        print(json.dumps(output))


def cmd_install(args) -> None:
    executable = _executable()
    for host in _selected_hosts(args):
        if not host.settings_path.parent.exists():
            print(f"{host.name}: skipped ({installer.portable_path(host.settings_path.parent)} not found)")
            continue
        print(f"{host.name}:")
        _report(installer.install(host, executable))


def cmd_uninstall(args) -> None:
    for host in _selected_hosts(args):
        print(f"{host.name}:")
        _report(installer.uninstall(host))


def cmd_status(args) -> None:
    root = bridge.plugin_root()
    print(f"claude-mem plugin: {installer.portable_path(root) if root else 'not found'}")
    if root:
        supported = bridge.official_supports_all_sources(root)
        enabled = bridge.load_settings().get(bridge.INCLUDE_ALL_SOURCES) == "true"
        print(f"official all-sources context: {'supported' if supported else 'not supported'}, "
              f"{bridge.INCLUDE_ALL_SOURCES}={'true' if enabled else 'false'}")
    for host in HOSTS.values():
        hooks = "installed" if installer.is_installed(host) else "not installed"
        line = f"{host.name}: hooks {hooks}"
        if host.mcp_path is not None:
            line += f", mcp {'installed' if installer.is_mcp_installed(host) else 'not installed'}"
        print(line)


def cmd_mcp(args) -> None:
    root = bridge.plugin_root()
    server = root / "scripts/mcp-server.cjs" if root else None
    if server is None or not server.is_file():
        sys.exit("claude-mem-bridge: claude-mem MCP server not found; install the claude-mem Claude Code plugin")
    os.execvp("node", ["node", str(server)])


def main() -> None:
    parser = argparse.ArgumentParser(prog="claude-mem-bridge", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    hook = sub.add_parser("hook", help="handle a hook event read from stdin (called by the agents)")
    hook.add_argument("--host", choices=list(HOSTS), required=True)
    hook.set_defaults(func=cmd_hook)

    for name, func, help_text in (
        ("install", cmd_install, "register the bridge's hooks and MCP server"),
        ("uninstall", cmd_uninstall, "remove the bridge's hooks and MCP server"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("hosts", nargs="*", metavar="HOST", help=", ".join(HOSTS))
        p.add_argument("--all", action="store_true", help="apply to every host")
        p.set_defaults(func=func)

    sub.add_parser("status", help="show what is installed").set_defaults(func=cmd_status)
    sub.add_parser("mcp", help="run claude-mem's MCP server (called by the agents)").set_defaults(func=cmd_mcp)

    args = parser.parse_args()
    args.func(args)
