# claude-mem-bridge

Share [claude-mem](https://github.com/thedotmack/claude-mem) memory across Claude Code, Codex CLI, GitHub Copilot CLI, and IBM Bob Shell, so whichever agent you start, it begins with what every agent has done.

## Why

claude-mem stores every agent's memory in one database, but it injects SessionStart context per `platformSource`: Claude Code only sees Claude Code's memory, Codex only sees Codex's.
It also has no adapter for Copilot CLI or Bob Shell.

The bridge closes both gaps:

| Agent | Recording | SessionStart context |
| --- | --- | --- |
| Claude Code | claude-mem plugin | plugin injects `claude-code`; the bridge adds `codex`, `copilot`, `bob` |
| Codex CLI | claude-mem (`npx claude-mem install --ide codex`) | claude-mem injects `codex`; the bridge adds `claude-code`, `copilot`, `bob` |
| Copilot CLI | the bridge, as `copilot` | the bridge injects every source |
| Bob Shell | the bridge, as `bob` | the bridge injects every source |

For Copilot and Bob, the bridge calls claude-mem's own hook handlers with the agent name as the platform, which records under that name through claude-mem's raw adapter.
Summaries are posted to the worker API directly, because the raw adapter does not read the last assistant message.
The bridge also registers claude-mem's MCP server (search, timeline, ...) for Copilot and Bob.
When Claude Code itself runs Copilot or Bob, for example to delegate a task, the run is part of the Claude Code session, so the bridge neither records it nor injects context.
It tells these runs apart by the `CLAUDECODE=1` variable that Claude Code sets for its commands.

Newer claude-mem builds add `CLAUDE_MEM_SESSION_START_INCLUDE_ALL_SOURCES`.
When the installed claude-mem supports it and it is set to `"true"` in `~/.claude-mem/settings.json`, the official hooks inject every source themselves, and the bridge stops adding sources to avoid duplicates.
Run `claude-mem-bridge status` to see which mode is active.

## Requirements

- [uv](https://docs.astral.sh/uv/)
- Node.js
- The claude-mem Claude Code plugin, with its worker running. The bridge uses the plugin's scripts under `~/.claude/plugins/cache/thedotmack/claude-mem/` (or `$CLAUDE_CONFIG_DIR`)
- For Codex, claude-mem's own Codex integration

## Install

```sh
uv tool install git+https://github.com/seiya-koji/claude-mem-bridge
claude-mem-bridge install --all     # or: claude-mem-bridge install copilot bob
claude-mem-bridge status
```

`install` adds hook entries to each agent's settings and leaves everything else untouched.
Hosts whose configuration directory does not exist are skipped.
Running it again changes nothing; entries the bridge owns are updated in place.

| Host | Hooks | MCP server |
| --- | --- | --- |
| `claude` | `$CLAUDE_CONFIG_DIR/settings.json` (default `~/.claude/settings.json`) | — |
| `codex` | `$CODEX_HOME/hooks.json` (default `~/.codex/hooks.json`) | — |
| `copilot` | `~/.copilot/settings.json` | `~/.copilot/mcp-config.json` |
| `bob` | `~/.bob/settings/settings.json` | `~/.bob/settings/mcp.json` |

Commands are written with `$HOME` rather than an absolute path, for example `"$HOME/.local/bin/claude-mem-bridge" hook --host bob`, so the settings files carry no user-specific paths.
Before each change, the previous version of the file is saved next to it as `<file>.claude-mem-bridge.bak`.

> [!NOTE]
> Installing replaces an existing `claude-mem` MCP server entry for Copilot and Bob.
> Codex tracks hook trust per entry, so it may ask you to trust the updated hook on the next start.

## Update and uninstall

```sh
uv tool upgrade claude-mem-bridge
claude-mem-bridge uninstall --all
uv tool uninstall claude-mem-bridge
```

`uninstall` removes only the hooks and the `claude-mem` MCP server entry that the bridge registered.

## Development

```sh
uv sync
uv run pytest
```

## License

MIT
