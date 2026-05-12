#!/usr/bin/env python3
import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


SCRIPT = Path(__file__).with_name("codex_usage_uploader.py")
SESSION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def load_module():
    spec = importlib.util.spec_from_file_location("codex_usage_uploader", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event(timestamp, outer_type, payload):
    return {"timestamp": timestamp, "type": outer_type, "payload": payload}


def write_session(codex_home, events):
    session_dir = codex_home / "sessions" / "2026" / "05" / "06"
    session_dir.mkdir(parents=True)
    path = session_dir / f"rollout-2026-05-06T10-00-00-{SESSION_ID}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for item in events:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return path


def sample_events():
    return [
        event("2026-05-06T01:00:00.000Z", "session_meta", {
            "id": SESSION_ID,
            "cwd": "D:\\repo",
            "originator": "codex_desktop",
            "cli_version": "1.2.3",
            "model_provider": "openai",
            "git": {
                "repository_url": "https://git.example.com/team/repo.git",
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
    ]:
        assert secret not in serialized


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
    test_dry_run_cli_does_not_require_token()
    test_http_upload_uses_bearer_header_and_retries()
    print("tests passed")
