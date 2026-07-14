#!/usr/bin/env python3
import importlib.util
import copy
import json
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


SCRIPT = Path(__file__).with_name("codex_usage_uploader.py")
SESSION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PARENT_SESSION_ID = "11111111-2222-3333-4444-555555555555"
CHILD_SESSION_ID = "66666666-7777-8888-9999-000000000000"
SUBAGENT_SESSION_ID = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"


def load_module():
    spec = importlib.util.spec_from_file_location("codex_usage_uploader", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event(timestamp, outer_type, payload):
    return {"timestamp": timestamp, "type": outer_type, "payload": payload}


def write_session(codex_home, events):
    return write_session_with_id(codex_home, SESSION_ID, "10-00-00", events)


def write_session_with_id(codex_home, session_id, time_part, events):
    session_dir = codex_home / "sessions" / "2026" / "05" / "06"
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"rollout-2026-05-06T{time_part}-{session_id}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for item in events:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return path


def session_meta(session_id, timestamp="2026-05-06T01:00:00.000Z", **extra):
    payload = {
        "id": session_id,
        "cwd": "D:\\repo",
        "originator": "codex_desktop",
        "cli_version": "1.2.3",
        "model_provider": "openai",
    }
    payload.update(extra)
    return event(timestamp, "session_meta", payload)


def token_count(timestamp, last_total, total_total):
    return event(timestamp, "event_msg", {
        "type": "token_count",
        "info": {
            "last_token_usage": {
                "input_tokens": last_total,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_output_tokens": 0,
                "total_tokens": last_total,
            },
            "total_token_usage": {
                "input_tokens": total_total,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_output_tokens": 0,
                "total_tokens": total_total,
            },
        },
    })


def sample_events():
    return [
        event("2026-05-06T01:00:00.000Z", "session_meta", {
            "id": SESSION_ID,
            "cwd": "D:\\repo",
            "originator": "codex_desktop",
            "cli_version": "1.2.3",
            "model_provider": "openai",
            "git": {
                "repository_url": "https://alice:PAT_SECRET@git.example.com/team/repo.git?token=LEAK",
                "commit_hash": "abc123",
                "branch": "main",
            },
        }),
        event("2026-05-06T01:00:01.000Z", "turn_context", {
            "turn_id": "turn-1",
            "cwd": "D:\\repo",
            "current_date": "2026-05-06",
            "timezone": "Asia/Shanghai",
            "model": "gpt-5.4",
            "effort": "medium",
            "collaboration_mode": {
                "settings": {
                    "model": "gpt-5.4",
                    "reasoning_effort": "medium",
                    "developer_instructions": "DO_NOT_UPLOAD",
                }
            },
        }),
        event("2026-05-06T01:00:02.000Z", "event_msg", {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 30,
                    "output_tokens": 20,
                    "reasoning_output_tokens": 5,
                    "total_tokens": 120,
                },
                "total_token_usage": {
                    "input_tokens": 9999,
                    "cached_input_tokens": 9999,
                    "output_tokens": 9999,
                    "reasoning_output_tokens": 9999,
                    "total_tokens": 9999,
                },
                "model_context_window": 258400,
            },
            "rate_limits": {
                "limit_id": "primary",
                "primary": {"used_percent": 12.5, "window_minutes": 300, "resets_at": 123},
                "secondary": None,
                "credits": {"has_credits": True, "unlimited": False, "balance": None},
                "plan_type": "team",
            },
        }),
        event("2026-05-06T01:00:03.000Z", "event_msg", {
            "type": "task_complete",
            "turn_id": "turn-1",
            "completed_at": 1000,
            "duration_ms": 2500,
            "time_to_first_token_ms": 300,
            "last_agent_message": "AGENT_SECRET",
        }),
        event("2026-05-06T01:00:04.000Z", "event_msg", {
            "type": "exec_command_end",
            "call_id": "call-shell",
            "turn_id": "turn-1",
            "command": ["powershell", "-Command", "Write-Host STDOUT_SECRET"],
            "parsed_cmd": [{"type": "shell", "cmd": "python secret_script.py --password STDOUT_SECRET"}],
            "cwd": "D:\\repo",
            "stdout": "STDOUT_SECRET",
            "stderr": "STDERR_SECRET",
            "aggregated_output": "STDOUT_SECRET",
            "exit_code": 0,
            "duration": {"secs": 1, "nanos": 2},
            "formatted_output": "STDOUT_SECRET",
            "status": "completed",
        }),
        event("2026-05-06T01:00:05.000Z", "event_msg", {
            "type": "patch_apply_end",
            "call_id": "call-patch",
            "turn_id": "turn-1",
            "stdout": "PATCH_STDOUT_SECRET",
            "stderr": "PATCH_STDERR_SECRET",
            "success": True,
            "status": "completed",
            "changes": {
                "D:\\repo\\main.py": {
                    "type": "update",
                    "unified_diff": "@@ DIFF_SECRET @@",
                    "move_path": None,
                }
            },
        }),
        event("2026-05-06T01:00:06.000Z", "event_msg", {
            "type": "function_call",
            "name": "shell_command",
            "namespace": "functions",
            "call_id": "call-tool",
            "arguments": "{\"command\":\"echo ARG_SECRET\",\"api_key\":\"ARG_SECRET\"}",
        }),
        event("2026-05-06T01:00:07.000Z", "event_msg", {
            "type": "function_call_output",
            "call_id": "call-tool",
            "output": "TOOL_OUTPUT_SECRET",
        }),
        event("2026-05-06T01:00:08.000Z", "user_message", {
            "type": "user_message",
            "message": "USER_MESSAGE_SECRET",
            "images": [],
            "local_images": [],
            "text_elements": [],
        }),
    ]


def collect_once(codex_home, state=None):
    module = load_module()
    state = state or {"version": 1, "files": {}}
    return module.collect_events(
        codex_home,
        state,
        module.ZoneInfo("Asia/Shanghai"),
        None,
        "alice",
        "machine-1",
    )


def test_collects_token_and_metadata_without_private_content():
    module = load_module()
    system_zoneinfo = module._ZoneInfo
    try:
        def unavailable_timezone(name):
            raise module.ZoneInfoNotFoundError(name)
        module._ZoneInfo = unavailable_timezone
        assert module.ZoneInfo("Asia/Shanghai").utcoffset(None).total_seconds() == 8 * 3600
    finally:
        module._ZoneInfo = system_zoneinfo
    assert module.sanitize_repository_url(
        "git@git.example.com:team/repo.git?token=LEAK#fragment"
    ) == "git.example.com:team/repo.git"
    with tempfile.TemporaryDirectory() as temp:
        codex_home = Path(temp)
        write_session(codex_home, sample_events())
        events, next_state, stats = collect_once(codex_home)

    assert stats["files_scanned"] == 1
    assert len(events) == 8
    token = next(item for item in events if item["event_type"] == "token_count")
    assert token["token"]["total_tokens"] == 120
    assert token["token"]["input_tokens"] == 100
    assert "total_token_usage" not in json.dumps(token)
    assert token["context"]["model"] == "gpt-5.4"
    assert token["context"]["cwd"] == "D:\\repo"
    assert token["context"]["git"]["repository_url"] == "https://git.example.com/team/repo.git"
    assert token["rate_limits"]["primary"]["used_percent"] == 12.5

    shell = next(item for item in events if item["event_type"] == "exec_command_end")
    assert shell["shell"]["programs"] == ["python"]
    assert shell["shell"]["exit_code"] == 0

    patch = next(item for item in events if item["event_type"] == "patch_apply_end")
    assert patch["patch"]["changed_files_count"] == 1
    assert patch["patch"]["change_types"] == {"update": 1}

    serialized = json.dumps(events, ensure_ascii=False)
    for secret in [
        "AGENT_SECRET",
        "STDOUT_SECRET",
        "STDERR_SECRET",
        "PATCH_STDOUT_SECRET",
        "PATCH_STDERR_SECRET",
        "DIFF_SECRET",
        "ARG_SECRET",
        "TOOL_OUTPUT_SECRET",
        "USER_MESSAGE_SECRET",
        "DO_NOT_UPLOAD",
        "PAT_SECRET",
        "token=LEAK",
    ]:
        assert secret not in serialized

    module = load_module()
    assert module.first_program("API_TOKEN=secret python app.py") is None
    assert module.first_program("$env:API_TOKEN = 'secret'; python app.py") is None


def test_state_incremental_and_truncated_rescan_event_id_stability():
    with tempfile.TemporaryDirectory() as temp:
        codex_home = Path(temp)
        path = write_session(codex_home, sample_events())
        events, state, _ = collect_once(codex_home)
        second_events, _, _ = collect_once(codex_home, state)
        assert second_events == []

        original_token_id = next(item["event_id"] for item in events if item["event_type"] == "token_count")
        truncated = sample_events()[:3]
        with path.open("w", encoding="utf-8") as handle:
            for item in truncated:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        replayed_events, _, _ = collect_once(codex_home, state)
        replayed_token_id = next(item["event_id"] for item in replayed_events if item["event_type"] == "token_count")
        assert replayed_token_id == original_token_id


def test_partial_trailing_jsonl_line_is_retried():
    with tempfile.TemporaryDirectory() as temp:
        codex_home = Path(temp)
        path = write_session(codex_home, sample_events()[:2])
        complete_size = path.stat().st_size
        token_json = json.dumps(sample_events()[2], ensure_ascii=False).encode("utf-8")
        split = len(token_json) // 2
        with path.open("ab") as handle:
            handle.write(token_json[:split])

        first_events, state, _ = collect_once(codex_home)
        record = state["files"][str(path)]
        assert record["offset"] == complete_size
        assert not [item for item in first_events if item["event_type"] == "token_count"]

        with path.open("ab") as handle:
            handle.write(token_json[split:])
        complete_but_unterminated_events, state, _ = collect_once(codex_home, state)
        assert state["files"][str(path)]["offset"] == complete_size
        assert complete_but_unterminated_events == []

        next_line = (json.dumps(sample_events()[3], ensure_ascii=False) + "\n").encode("utf-8")
        with path.open("ab") as handle:
            handle.write(b"\n" + next_line)
        completed_events, next_state, _ = collect_once(codex_home, state)
        fresh_events, _, _ = collect_once(codex_home)

    tokens = [item for item in completed_events if item["event_type"] == "token_count"]
    assert [item["token"]["total_tokens"] for item in tokens] == [120]
    expected_ids = [item["event_id"] for item in fresh_events[2:4]]
    assert [item["event_id"] for item in completed_events] == expected_ids
    assert next_state["files"][str(path)]["offset"] == complete_size + len(token_json) + 1 + len(next_line)


def test_repeated_token_snapshot_is_not_uploaded_twice():
    with tempfile.TemporaryDirectory() as temp:
        codex_home = Path(temp)
        events = sample_events()[:3]
        duplicate = copy.deepcopy(events[2])
        duplicate["timestamp"] = "2026-05-06T01:00:02.500Z"
        duplicate["payload"]["rate_limits"]["primary"]["used_percent"] = 13.0
        advanced = copy.deepcopy(events[2])
        advanced["timestamp"] = "2026-05-06T01:00:03.000Z"
        advanced["payload"]["info"]["last_token_usage"] = {
            "input_tokens": 40,
            "cached_input_tokens": 10,
            "output_tokens": 8,
            "reasoning_output_tokens": 2,
            "total_tokens": 48,
        }
        advanced["payload"]["info"]["total_token_usage"] = {
            "input_tokens": 140,
            "cached_input_tokens": 40,
            "output_tokens": 28,
            "reasoning_output_tokens": 7,
            "total_tokens": 168,
        }
        write_session(codex_home, events + [duplicate, advanced])

        collected, _, _ = collect_once(codex_home)

    token_events = [item for item in collected if item["event_type"] == "token_count"]
    assert [item["token"]["total_tokens"] for item in token_events] == [120, 48]


def test_legacy_state_reconstructs_token_snapshot_before_offset():
    with tempfile.TemporaryDirectory() as temp:
        codex_home = Path(temp)
        path = write_session(codex_home, sample_events()[:3])
        _, state, _ = collect_once(codex_home)
        legacy_state = copy.deepcopy(state)
        for record in legacy_state["files"].values():
            record.pop("last_token_total_fingerprint", None)

        duplicate = copy.deepcopy(sample_events()[2])
        duplicate["timestamp"] = "2026-05-06T01:00:03.000Z"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(duplicate, ensure_ascii=False) + "\n")

        collected, _, _ = collect_once(codex_home, legacy_state)

    assert [item for item in collected if item["event_type"] == "token_count"] == []


def test_forked_child_does_not_upload_copied_parent_token_snapshots():
    with tempfile.TemporaryDirectory() as temp:
        codex_home = Path(temp)
        parent_events = [
            session_meta(PARENT_SESSION_ID),
            token_count("2026-05-06T01:00:01.000Z", 100, 100),
            token_count("2026-05-06T01:00:02.000Z", 50, 150),
        ]
        child_events = [
            session_meta(
                CHILD_SESSION_ID,
                "2026-05-06T02:00:00.000Z",
                forked_from_id=PARENT_SESSION_ID,
            ),
            *parent_events,
            token_count("2026-05-06T02:00:01.000Z", 30, 180),
        ]
        write_session_with_id(codex_home, PARENT_SESSION_ID, "10-00-00", parent_events)
        write_session_with_id(codex_home, CHILD_SESSION_ID, "11-00-00", child_events)

        collected, _, _ = collect_once(codex_home)

    token_events = [item for item in collected if item["event_type"] == "token_count"]
    parent_tokens = [item["token"]["total_tokens"] for item in token_events if item["session_id"] == PARENT_SESSION_ID]
    child_tokens = [item["token"]["total_tokens"] for item in token_events if item["session_id"] == CHILD_SESSION_ID]
    assert parent_tokens == [100, 50]
    assert child_tokens == [30]


def test_subagent_child_does_not_upload_parent_thread_token_snapshots():
    with tempfile.TemporaryDirectory() as temp:
        codex_home = Path(temp)
        parent_events = [
            session_meta(PARENT_SESSION_ID),
            token_count("2026-05-06T01:00:01.000Z", 80, 80),
        ]
        subagent_events = [
            session_meta(
                SUBAGENT_SESSION_ID,
                "2026-05-06T02:00:00.000Z",
                parent_thread_id=PARENT_SESSION_ID,
                thread_source="subagent",
            ),
            *parent_events,
            token_count("2026-05-06T02:00:01.000Z", 25, 105),
        ]
        write_session_with_id(codex_home, PARENT_SESSION_ID, "10-00-00", parent_events)
        write_session_with_id(codex_home, SUBAGENT_SESSION_ID, "11-00-00", subagent_events)

        collected, _, _ = collect_once(codex_home)

    token_events = [item for item in collected if item["event_type"] == "token_count"]
    subagent_tokens = [item["token"]["total_tokens"] for item in token_events if item["session_id"] == SUBAGENT_SESSION_ID]
    assert subagent_tokens == [25]


def test_dry_run_cli_does_not_require_token():
    with tempfile.TemporaryDirectory() as temp:
        codex_home = Path(temp)
        write_session(codex_home, sample_events()[:3])
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--codex-home",
                str(codex_home),
                "--endpoint",
                "http://127.0.0.1:1/ingest",
                "--source-name",
                "alice",
                "--dry-run",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
    data = json.loads(result.stdout)
    assert data["dry_run"] is True
    assert data["events_ready"] == 3


def test_http_upload_uses_bearer_header_and_retries():
    captured = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            body = self.rfile.read(length)
            captured.append((self.headers.get("Authorization"), json.loads(body.decode("utf-8"))))
            if len(captured) == 1:
                self.send_response(500)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"accepted": 3, "duplicates": 0, "errors": []}')

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp) / "codex"
            state_file = Path(temp) / "state.json"
            write_session(codex_home, sample_events()[:3])
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--codex-home",
                    str(codex_home),
                    "--state-file",
                    str(state_file),
                    "--endpoint",
                    f"http://127.0.0.1:{server.server_port}/ingest",
                    "--source-name",
                    "alice",
                    "--token",
                    "TOKEN123",
                    "--retries",
                    "2",
                    "--retry-delay",
                    "0",
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            output = json.loads(result.stdout)
            assert output["uploaded"] == 3
            assert state_file.exists()
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert len(captured) == 2
    assert captured[0][0] == "Bearer TOKEN123"
    assert captured[1][0] == "Bearer TOKEN123"
    assert captured[1][1]["source_name"] == "alice"
    assert len(captured[1][1]["events"]) == 3


if __name__ == "__main__":
    test_collects_token_and_metadata_without_private_content()
    test_state_incremental_and_truncated_rescan_event_id_stability()
    test_partial_trailing_jsonl_line_is_retried()
    test_repeated_token_snapshot_is_not_uploaded_twice()
    test_legacy_state_reconstructs_token_snapshot_before_offset()
    test_forked_child_does_not_upload_copied_parent_token_snapshots()
    test_subagent_child_does_not_upload_parent_thread_token_snapshots()
    test_dry_run_cli_does_not_require_token()
    test_http_upload_uses_bearer_header_and_retries()
    print("tests passed")
