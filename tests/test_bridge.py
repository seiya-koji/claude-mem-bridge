import json

import pytest

from claude_mem_bridge import bridge
from claude_mem_bridge.hosts import HOSTS


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("claude", ["codex", "copilot", "bob"]),
        ("codex", ["claude-code", "copilot", "bob"]),
        ("copilot", ["claude-code", "codex", "copilot", "bob"]),
        ("bob", ["claude-code", "codex", "copilot", "bob"]),
    ],
)
def test_context_platforms_fill_in_missing_sources(host, expected):
    assert bridge.context_platforms(HOSTS[host], official_includes_all=False) == expected


@pytest.mark.parametrize(("host", "expected"), [("claude", []), ("codex", []), ("copilot", ["copilot"]), ("bob", ["bob"])])
def test_context_platforms_defer_to_official_all_sources(host, expected):
    assert bridge.context_platforms(HOSTS[host], official_includes_all=True) == expected


def test_official_all_sources_needs_setting_and_support(home):
    root = home / "plugin"
    (root / "scripts").mkdir(parents=True)
    worker = root / "scripts/worker-service.cjs"
    settings = home / ".claude-mem/settings.json"
    settings.parent.mkdir()

    worker.write_text("no support here")
    settings.write_text(json.dumps({bridge.INCLUDE_ALL_SOURCES: "true"}))
    assert not bridge.official_includes_all_sources(root)

    worker.write_text(f"reads {bridge.INCLUDE_ALL_SOURCES}")
    assert bridge.official_includes_all_sources(root)

    settings.write_text(json.dumps({bridge.INCLUDE_ALL_SOURCES: "false"}))
    assert not bridge.official_includes_all_sources(root)


def test_session_start_output_shape():
    assert bridge.session_start_output(HOSTS["copilot"], "ctx") == {"additionalContext": "ctx"}
    assert bridge.session_start_output(HOSTS["bob"], "ctx") == {
        "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "ctx"}
    }


def test_copilot_tool_result_becomes_tool_response():
    payload = {"hook_event_name": "PostToolUse", "tool_result": {"text_result_for_llm": "out", "x": 1}}
    assert bridge.normalize_payload(payload) == {"hook_event_name": "PostToolUse", "tool_response": "out"}
    assert "tool_result" in payload


def test_summary_strips_private_tags():
    payload = {"last_assistant_message": "keep <private>secret</private> this"}
    assert bridge.summary_message(HOSTS["bob"], payload) == "keep  this"


def test_copilot_summary_reads_last_assistant_message_from_transcript(home):
    transcript = home / "events.jsonl"
    lines = [
        {"type": "assistant.message", "data": {"content": "first"}},
        {"type": "user.message", "data": {"content": "question"}},
        {"type": "assistant.message", "data": {"content": "last"}},
    ]
    transcript.write_text("\n".join(json.dumps(line) for line in lines) + "\nnot json\n")
    assert bridge.summary_message(HOSTS["copilot"], {"transcript_path": str(transcript)}) == "last"


def test_copilot_summary_ignores_reply_from_previous_turn(home, monkeypatch):
    monkeypatch.setattr(bridge, "COPILOT_TRANSCRIPT_WAIT_SECONDS", 0)
    transcript = home / "events.jsonl"
    lines = [
        {"type": "assistant.message", "data": {"content": "previous turn"}},
        {"type": "user.message", "data": {"content": "question"}},
    ]
    transcript.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    assert bridge.summary_message(HOSTS["copilot"], {"transcript_path": str(transcript)}) == ""


def test_plugin_root_picks_newest_live_version(home):
    cache = home / ".claude/plugins/cache/thedotmack/claude-mem"
    for version in ("13.9.0", "13.28.0", "14.0.0"):
        (cache / version / "scripts").mkdir(parents=True)
        (cache / version / "scripts/worker-service.cjs").write_text("")
    (cache / "14.0.0/.orphaned_at").write_text("")
    assert bridge.plugin_root() == cache / "13.28.0"


@pytest.mark.parametrize("host", ["copilot", "bob"])
def test_runs_launched_by_claude_code_are_ignored(host, home, monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, "plugin_root", lambda: home)
    monkeypatch.setattr(bridge, "run_hook", lambda *args: calls.append(args) or {})
    monkeypatch.setenv("CLAUDECODE", "1")
    for event in ("SessionStart", "UserPromptSubmit", "PostToolUse"):
        assert bridge.handle(HOSTS[host], {"hook_event_name": event, "session_id": "s"}) is None
    assert calls == []

    monkeypatch.delenv("CLAUDECODE")
    bridge.handle(HOSTS[host], {"hook_event_name": "UserPromptSubmit", "session_id": "s"})
    assert len(calls) == 1
