"""Relay agent hook events to claude-mem.

claude-mem scopes SessionStart context by platformSource, so each agent only sees its own memory.
The bridge fills in the missing sources so every agent starts with the memory of all agents.

- bob / copilot: claude-mem has no adapter for them. Every hook event is relayed by calling claude-mem's
  hook with an unknown platform name, which selects its raw adapter and records under that name.
  The raw adapter does not read last_assistant_message, so summaries are posted to the worker API directly.
  SessionStart returns the context of every source.
- claude / codex: claude-mem's own hooks record and inject their own source. The bridge only runs on
  SessionStart and returns the other sources.

Once the installed claude-mem supports CLAUDE_MEM_SESSION_START_INCLUDE_ALL_SOURCES and it is enabled,
the official hooks inject every source themselves, so claude / codex return nothing and bob / copilot
only request their own source.

When Claude Code runs bob / copilot as a subprocess, for example to delegate a task, the run is part of the
Claude Code session that claude-mem already records, so the bridge neither records it nor injects context.
"""
import glob
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from .hosts import Host, claude_config_dir

ALL_PLATFORMS = ("claude-code", "codex", "copilot", "bob")
INCLUDE_ALL_SOURCES = "CLAUDE_MEM_SESSION_START_INCLUDE_ALL_SOURCES"
HOOK_HANDLERS = {
    "SessionStart": "context",
    "UserPromptSubmit": "session-init",
    "PostToolUse": "observation",
    "SessionEnd": "session-end",
}
# Tags that claude-mem's own hooks strip before summarizing, so they never reach the memory.
STRIPPED_TAGS = re.compile(r"<(private|claude-mem-context)>.*?</\1>", re.DOTALL)
EMPTY_CONTEXT_MARKER = "This project has no memory yet"
COPILOT_TRANSCRIPT_WAIT_SECONDS = 5


def plugin_root() -> Path | None:
    def version_key(path):
        parts = os.path.basename(path).split("-")[0].split(".")
        return tuple(int(p) if p.isdigit() else 0 for p in parts)

    candidates = [
        d
        for d in glob.glob(str(claude_config_dir() / "plugins/cache/thedotmack/claude-mem/*"))
        if os.path.isfile(os.path.join(d, "scripts/worker-service.cjs"))
        and not os.path.exists(os.path.join(d, ".orphaned_at"))
    ]
    return Path(max(candidates, key=version_key)) if candidates else None


def load_settings() -> dict:
    try:
        with open(Path.home() / ".claude-mem/settings.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def official_includes_all_sources(root: Path) -> bool:
    if load_settings().get(INCLUDE_ALL_SOURCES) != "true":
        return False
    return official_supports_all_sources(root)


def official_supports_all_sources(root: Path) -> bool:
    try:
        return INCLUDE_ALL_SOURCES in (root / "scripts/worker-service.cjs").read_text(encoding="utf-8")
    except OSError:
        return False


def context_platforms(host: Host, official_includes_all: bool) -> list[str]:
    if official_includes_all:
        return [] if host.official_platform else [host.name]
    return [p for p in ALL_PLATFORMS if p != host.official_platform]


def worker_url(path: str) -> str:
    settings = load_settings()
    host = settings.get("CLAUDE_MEM_WORKER_HOST") or "127.0.0.1"
    port = settings.get("CLAUDE_MEM_WORKER_PORT") or "37777"
    return f"http://{host}:{port}{path}"


def copilot_last_assistant_message(transcript_path: str) -> str | None:
    """Return the assistant reply to the latest user message, or None if it has not been written yet."""
    message = None
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "user.message":
                    message = None
                elif event.get("type") == "assistant.message":
                    content = (event.get("data") or {}).get("content")
                    if content:
                        message = content
    except OSError:
        pass
    return message


def wait_for_copilot_message(transcript_path: str) -> str | None:
    # Copilot runs the Stop hook a few milliseconds after the reply, sometimes before it reaches the transcript.
    deadline = time.monotonic() + COPILOT_TRANSCRIPT_WAIT_SECONDS
    while True:
        message = copilot_last_assistant_message(transcript_path)
        if message or time.monotonic() >= deadline:
            return message
        time.sleep(0.2)


def run_hook(root: Path, platform: str, handler: str, payload: dict) -> dict:
    env = dict(os.environ)
    if platform == "codex":
        env["CLAUDE_MEM_CODEX_HOOK"] = "1"
    result = subprocess.run(
        ["node", str(root / "scripts/bun-runner.js"), str(root / "scripts/worker-service.cjs"),
         "hook", platform, handler],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    try:
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except ValueError:
        return {}


def session_context(root: Path, payload: dict, platforms: list[str]) -> str:
    contexts = []
    for platform in platforms:
        output = run_hook(root, platform, "context", payload)
        context = (output.get("hookSpecificOutput") or {}).get("additionalContext")
        if context and EMPTY_CONTEXT_MARKER not in context:
            contexts.append(context)
    return "\n\n".join(contexts)


def session_start_output(host: Host, context: str) -> dict:
    if host.name == "copilot":
        # Copilot reads a top-level additionalContext instead of Claude's hookSpecificOutput.
        return {"additionalContext": context}
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}


def summary_message(host: Host, payload: dict) -> str:
    message = payload.get("last_assistant_message")
    if host.name == "copilot":
        message = wait_for_copilot_message(payload.get("transcript_path", ""))
    return STRIPPED_TAGS.sub("", message or "").strip()


def summarize(host: Host, payload: dict) -> None:
    if str(payload.get("stop_hook_active")).lower() == "true" or not payload.get("session_id"):
        return
    message = summary_message(host, payload)
    if not message:
        return
    body = json.dumps({"contentSessionId": payload["session_id"], "last_assistant_message": message,
                       "platformSource": host.name}).encode()
    request = urllib.request.Request(worker_url("/api/sessions/summarize"), data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(request, timeout=10).close()
    except OSError:
        pass


def normalize_payload(payload: dict) -> dict:
    if payload.get("hook_event_name") == "PostToolUse" and "tool_result" in payload:
        payload = dict(payload)
        result = payload.pop("tool_result")
        payload["tool_response"] = result.get("text_result_for_llm", result) if isinstance(result, dict) else result
    return payload


def launched_by_claude_code() -> bool:
    return os.environ.get("CLAUDECODE") == "1"


def handle(host: Host, payload: dict) -> dict | None:
    """Process one hook event and return the JSON to print, if any."""
    if not host.official_platform and launched_by_claude_code():
        return None
    event = payload.get("hook_event_name", "")
    root = plugin_root()
    if not root:
        return None

    if event == "SessionStart":
        platforms = context_platforms(host, official_includes_all_sources(root))
        context = session_context(root, payload, platforms)
        return session_start_output(host, context) if context else None
    if host.official_platform:
        return None

    if event == "Stop":
        summarize(host, payload)
        return None
    handler = HOOK_HANDLERS.get(event)
    if handler:
        run_hook(root, host.name, handler, normalize_payload(payload))
    return None
