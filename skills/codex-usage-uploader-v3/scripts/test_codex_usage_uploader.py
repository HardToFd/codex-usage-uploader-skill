#!/usr/bin/env python3
import importlib.util
import copy
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


SCRIPT = Path(__file__).with_name("codex_usage_uploader.py")
SESSION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PARENT_SESSION_ID = "11111111-2222-3333-4444-555555555555"
CHILD_SESSION_ID = "66666666-7777-8888-9999-000000000000"
SUBAGENT_SESSION_ID = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
TURN_ID = "10000000-0000-4000-8000-000000000001"
SUBAGENT_TURN_ID = "10000000-0000-4000-8000-000000000002"
ABORTED_TURN_ID = "10000000-0000-4000-8000-000000000003"
SUMMARY_ID = "f" * 64


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


def write_summary_queue(
    path,
    *,
    summary_id=SUMMARY_ID,
    session_id=SESSION_ID,
    turn_id=TURN_ID,
    agent_id="",
    ready=1,
):
    with closing(sqlite3.connect(str(path))) as connection:
        connection.execute("""
            CREATE TABLE turn_summary_queue (
                summary_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                title TEXT,
                summary TEXT,
                work_item_ref TEXT,
                match_method TEXT,
                match_state TEXT,
                drift_state TEXT,
                outcome TEXT,
                summary_schema_version INTEGER,
                redaction_version TEXT,
                ready INTEGER NOT NULL DEFAULT 1,
                uploaded INTEGER NOT NULL DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        connection.execute("""
            INSERT INTO turn_summary_queue (
                summary_id, session_id, turn_id, agent_id, timestamp,
                title, summary, work_item_ref, match_method, match_state,
                drift_state, outcome, summary_schema_version, redaction_version, ready
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            summary_id, session_id, turn_id, agent_id, "2026-05-06T01:00:09.000Z",
            "Safe title", "Safe summary", "hmac:" + "a" * 64, "explicit_link", "assigned",
            "not_evaluated", "turn_complete" if ready else "in_progress", 1, "redact-v1", ready,
        ))
        connection.commit()


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


def task_complete(timestamp, turn_id, duration_ms):
    return event(timestamp, "event_msg", {
        "type": "task_complete",
        "turn_id": turn_id,
        "completed_at": timestamp,
        "duration_ms": duration_ms,
        "time_to_first_token_ms": 100,
        "last_agent_message": "PRIVATE_RESULT",
    })


def turn_aborted(timestamp, turn_id, duration_ms):
    return event(timestamp, "event_msg", {
        "type": "turn_aborted",
        "turn_id": turn_id,
        "completed_at": timestamp,
        "duration_ms": duration_ms,
        "reason": "interrupted",
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
            "turn_id": TURN_ID,
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
            "turn_id": TURN_ID,
            "completed_at": 1000,
            "duration_ms": 2500,
            "time_to_first_token_ms": 300,
            "last_agent_message": "AGENT_SECRET",
        }),
        event("2026-05-06T01:00:04.000Z", "event_msg", {
            "type": "exec_command_end",
            "call_id": "call-shell",
            "turn_id": TURN_ID,
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
            "turn_id": TURN_ID,
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
    events, next_state, stats, _ = module.collect_events(
        codex_home,
        state,
        module.ZoneInfo("Asia/Shanghai"),
        None,
        "alice",
        "machine-1",
    )
    return events, next_state, stats


def test_collects_token_and_metadata_without_private_content():
    module = load_module()
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
            task_complete("2026-05-06T01:00:03.000Z", "parent-turn", 4000),
            turn_aborted("2026-05-06T01:00:04.000Z", "parent-aborted", 700),
        ]
        child_events = [
            session_meta(
                CHILD_SESSION_ID,
                "2026-05-06T02:00:00.000Z",
                forked_from_id=PARENT_SESSION_ID,
            ),
            *parent_events,
            token_count("2026-05-06T02:00:01.000Z", 30, 180),
            task_complete("2026-05-06T02:00:02.000Z", "child-turn", 1200),
        ]
        write_session_with_id(codex_home, PARENT_SESSION_ID, "10-00-00", parent_events)
        write_session_with_id(codex_home, CHILD_SESSION_ID, "11-00-00", child_events)

        collected, _, _ = collect_once(codex_home)

    token_events = [item for item in collected if item["event_type"] == "token_count"]
    parent_tokens = [item["token"]["total_tokens"] for item in token_events if item["session_id"] == PARENT_SESSION_ID]
    child_tokens = [item["token"]["total_tokens"] for item in token_events if item["session_id"] == CHILD_SESSION_ID]
    assert parent_tokens == [100, 50]
    assert child_tokens == [30]
    child_durations = [
        item["task"]["duration_ms"]
        for item in collected
        if item["session_id"] == CHILD_SESSION_ID and item["event_type"] == "task_complete"
    ]
    assert child_durations == [1200]
    assert not [
        item for item in collected
        if item["session_id"] == CHILD_SESSION_ID and item["event_type"] == "turn_aborted"
    ]
    child_meta = next(item for item in collected if item.get("session", {}).get("id") == CHILD_SESSION_ID)
    assert child_meta["session"]["forked_from_id"] == PARENT_SESSION_ID
    assert child_meta["context"]["forked_from_id"] == PARENT_SESSION_ID


def test_subagent_child_does_not_upload_parent_thread_token_snapshots():
    with tempfile.TemporaryDirectory() as temp:
        codex_home = Path(temp)
        parent_events = [
            session_meta(PARENT_SESSION_ID),
            token_count("2026-05-06T01:00:01.000Z", 80, 80),
            task_complete("2026-05-06T01:00:02.000Z", "parent-turn", 5000),
        ]
        subagent_events = [
            session_meta(
                SUBAGENT_SESSION_ID,
                "2026-05-06T02:00:00.000Z",
                source={"subagent": {"thread_spawn": {"parent_thread_id": PARENT_SESSION_ID}}},
                thread_source="subagent",
            ),
            *parent_events,
            token_count("2026-05-06T02:00:01.000Z", 25, 105),
            task_complete("2026-05-06T02:00:02.000Z", SUBAGENT_TURN_ID, 900),
        ]
        write_session_with_id(codex_home, PARENT_SESSION_ID, "10-00-00", parent_events)
        write_session_with_id(codex_home, SUBAGENT_SESSION_ID, "11-00-00", subagent_events)

        collected, _, _ = collect_once(codex_home)

    token_events = [item for item in collected if item["event_type"] == "token_count"]
    subagent_tokens = [item["token"]["total_tokens"] for item in token_events if item["session_id"] == SUBAGENT_SESSION_ID]
    assert subagent_tokens == [25]
    subagent_durations = [
        item["task"]["duration_ms"]
        for item in collected
        if item["session_id"] == SUBAGENT_SESSION_ID and item["event_type"] == "task_complete"
    ]
    assert subagent_durations == [900]
    subagent_meta = next(item for item in collected if item.get("session", {}).get("id") == SUBAGENT_SESSION_ID)
    assert subagent_meta["session"]["parent_thread_id"] == PARENT_SESSION_ID
    assert subagent_meta["context"]["parent_thread_id"] == PARENT_SESSION_ID


def test_summary_mode_defaults_off_and_dry_run_does_not_update_queue():
    with tempfile.TemporaryDirectory() as temp:
        codex_home = Path(temp)
        queue = Path(temp) / "summary.sqlite3"
        write_session(codex_home, sample_events()[:4])
        write_summary_queue(queue)
        command = [
            sys.executable,
            str(SCRIPT),
            "--codex-home",
            str(codex_home),
            "--summary-queue",
            str(queue),
            "--source-name",
            "alice🚀",
            "--dry-run",
        ]
        env = os.environ.copy()
        env.pop("CODEX_USAGE_SUMMARY_MODE", None)
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=True,
            env=env,
        )
        data = json.loads(result.stdout)
        assert data["dry_run"] is True
        assert data["source_name"] == "alice🚀"
        assert data["events_ready"] == 4
        assert data["summary_events_ready"] == 0
        assert data["sample_summary_event"] is None

        local_result = subprocess.run(
            command + ["--summary-mode", "local"],
            text=True,
            capture_output=True,
            check=True,
            env=env,
        )
        local_data = json.loads(local_result.stdout)
        assert local_data["summary_events_ready"] == 1
        assert local_data["summary_match_states"] == {"assigned": 1}
        assert len(local_data["summary_events"]) == 1
        preview = local_data["attribution_preview"]
        assert preview["coverage"] == {
            "summary_turns_assigned": 1,
            "summary_turn_conflicts": 0,
            "token_events_total": 1,
            "token_events_attributed": 1,
            "terminal_duration_events_total": 1,
            "terminal_duration_events_attributed": 1,
        }
        assert preview["requirements"] == [{
            "work_item_ref": "hmac:" + "a" * 64,
            "turns": 1,
            "token": {
                "input_tokens": 100,
                "cached_input_tokens": 30,
                "output_tokens": 20,
                "reasoning_output_tokens": 5,
                "total_tokens": 120,
            },
            "wall_time_ms": 2500,
            "agent_time_ms": 2500,
        }]
        sample = local_data["sample_summary_event"]
        assert sample["event_type"] == "turn_summary"
        assert sample["context"] == {"turn_id": TURN_ID}
        assert set(sample["task"]) == set(load_module().SUMMARY_TASK_FIELDS)
        with closing(sqlite3.connect(str(queue))) as connection:
            assert connection.execute("SELECT uploaded FROM turn_summary_queue").fetchone()[0] == 0


def test_summary_validation_and_child_session_mapping():
    module = load_module()
    with tempfile.TemporaryDirectory() as temp:
        queue = Path(temp) / "summary.sqlite3"
        write_summary_queue(
            queue,
            session_id=PARENT_SESSION_ID,
            turn_id=SUBAGENT_TURN_ID,
            agent_id=SUBAGENT_SESSION_ID,
        )
        events, ids = module.read_summary_events(queue)
        assert ids == [SUMMARY_ID]
        assert events[0]["session_id"] == SUBAGENT_SESSION_ID
        assert events[0]["context"] == {
            "turn_id": SUBAGENT_TURN_ID,
            "parent_session_id": PARENT_SESSION_ID,
        }
        assert "agent_id" not in json.dumps(events[0])

        nested_lineage = {
            "sessions": {
                SUBAGENT_SESSION_ID: {"parents": {CHILD_SESSION_ID}},
            },
        }
        nested, _ = module.read_summary_events(queue, lineage_index=nested_lineage)
        assert nested[0]["context"]["parent_session_id"] == CHILD_SESSION_ID
        assert nested[0]["task"]["work_item_ref"] == ""
        assert nested[0]["task"]["match_state"] == "unassigned"

        with closing(sqlite3.connect(str(queue))) as connection:
            connection.execute(
                "UPDATE turn_summary_queue SET session_id = ?, turn_id = ?",
                ("password=hunter2", "https://private.example/turn"),
            )
            connection.commit()
        try:
            module.read_summary_events(queue)
            raise AssertionError("unsafe summary identifiers should be rejected")
        except RuntimeError as exc:
            assert "invalid session_id" in str(exc)
        with closing(sqlite3.connect(str(queue))) as connection:
            connection.execute(
                "UPDATE turn_summary_queue SET session_id = ?, turn_id = ?",
                (PARENT_SESSION_ID, SUBAGENT_TURN_ID),
            )
            connection.commit()

        with closing(sqlite3.connect(str(queue))) as connection:
            connection.execute("UPDATE turn_summary_queue SET title = ?", ("Leaked https://secret.example/req/1",))
            connection.commit()
        try:
            module.read_summary_events(queue)
            raise AssertionError("unsafe summary text should be rejected")
        except RuntimeError as exc:
            assert "invalid redacted text" in str(exc)

        for leaked in (
            'Password: "correct,horse battery staple"',
            "token=topsecret",
            "The password is hunter2",
            "tool --password cli-secret",
            "pwd=hunter2 next task",
            "\u5bc6\u7801\uff1ahunter2\uff1b\u7ee7\u7eed\u90e8\u7f72",
            "\u5bc6\u94a5\uff1ashortSecret9",
            "\u8bbf\u95ee\u4ee4\u724c\uff1ashortTok9",
            "\u53e3\u4ee4\uff1ahunter2",
            "Cookie: sessionid=short123",
            "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE",
            "Basic dXNlcjpwYXNz",
            "Updated clients/acme_secret/payroll.py",
            "Updated clients\\acme_secret\\payroll.py",
            "$HOME/acme_secret/result.txt",
            "10.2.3.4",
            "550e8400-e29b-41d4-a716-446655440000",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123",
            "0123456789abcdef0123456789abcdef",
        ):
            with closing(sqlite3.connect(str(queue))) as connection:
                connection.execute("UPDATE turn_summary_queue SET title = ?", (leaked,))
                connection.commit()
            try:
                module.read_summary_events(queue)
                raise AssertionError("sensitive summary text should be rejected")
            except RuntimeError as exc:
                assert "invalid redacted text" in str(exc)

        with closing(sqlite3.connect(str(queue))) as connection:
            connection.execute(
                "UPDATE turn_summary_queue SET title = ?, work_item_ref = ?",
                ("Safe title", "hmac:123"),
            )
            connection.commit()
        try:
            module.read_summary_events(queue)
            raise AssertionError("invalid work_item_ref should be rejected")
        except RuntimeError as exc:
            assert "invalid work_item_ref" in str(exc)


def test_aborted_turn_summary_is_previewed_then_finalized_for_root_and_child():
    module = load_module()
    terminal_timestamp = "2026-05-06T01:00:10.000Z"
    with tempfile.TemporaryDirectory() as temp:
        for queue_name, session_id, agent_id, usage_session_id in (
            ("root.sqlite3", SESSION_ID, "", SESSION_ID),
            ("child.sqlite3", PARENT_SESSION_ID, SUBAGENT_SESSION_ID, SUBAGENT_SESSION_ID),
        ):
            queue = Path(temp) / queue_name
            write_summary_queue(
                queue,
                session_id=session_id,
                turn_id=ABORTED_TURN_ID,
                agent_id=agent_id,
                ready=0,
            )
            usage_event = {
                "session_id": usage_session_id,
                "timestamp": terminal_timestamp,
                "event_type": "turn_aborted",
                "context": {"turn_id": ABORTED_TURN_ID},
                "task": {"turn_id": ABORTED_TURN_ID, "duration_ms": 750},
            }
            outcomes = module.terminal_summary_outcomes([usage_event])
            preview_events, ids = module.read_summary_events(queue, outcomes)
            assert ids == [SUMMARY_ID]
            assert preview_events[0]["task"]["outcome"] == "turn_aborted"
            assert preview_events[0]["timestamp"] == terminal_timestamp
            with closing(sqlite3.connect(str(queue))) as connection:
                assert connection.execute(
                    "SELECT ready, outcome FROM turn_summary_queue"
                ).fetchone() == (0, "in_progress")

            assert module.finalize_terminal_summaries(queue, outcomes) == 1
            finalized, _ = module.read_summary_events(queue)
            assert finalized[0]["task"]["outcome"] == "turn_aborted"
            with closing(sqlite3.connect(str(queue))) as connection:
                assert connection.execute(
                    "SELECT ready, outcome, timestamp FROM turn_summary_queue"
                ).fetchone() == (1, "turn_aborted", terminal_timestamp)


def test_production_endpoint_requires_https_except_loopback():
    module = load_module()
    assert module.trusted_endpoint("https://collector.example.com/ingest")
    assert module.trusted_endpoint("http://localhost:8080/ingest")
    assert module.trusted_endpoint("http://127.0.0.1:8080/ingest")
    assert module.trusted_endpoint("http://[::1]:8080/ingest")
    assert not module.trusted_endpoint("http://collector.example.com/ingest")
    assert not module.trusted_endpoint("https://user:password@collector.example.com/ingest")
    assert not module.trusted_endpoint("file:///tmp/usage.json")


def test_post_json_rejects_redirect_without_forwarding_bearer():
    module = load_module()
    received_authorization = []

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            received_authorization.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        do_POST = do_GET

        def log_message(self, format, *args):
            return

    target = HTTPServer(("127.0.0.1", 0), TargetHandler)

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/capture")
            self.end_headers()

        def log_message(self, format, *args):
            return

    redirect = HTTPServer(("127.0.0.1", 0), RedirectHandler)
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (target, redirect)
    ]
    for thread in threads:
        thread.start()
    try:
        try:
            module.post_json(
                f"http://127.0.0.1:{redirect.server_port}/ingest",
                "SECRETTOKEN",
                {"events": []},
                2,
            )
            raise AssertionError("redirect should be rejected")
        except module.urllib.error.HTTPError as exc:
            assert exc.code == 302
    finally:
        for server in (redirect, target):
            server.shutdown()
        for thread in threads:
            thread.join(timeout=5)
    assert received_authorization == []


def test_summary_upload_is_separate_and_idempotent():
    captured = []
    seen = set()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            captured.append(body)
            event_ids = [item["event_id"] for item in body["events"]]
            duplicates = sum(event_id in seen for event_id in event_ids)
            seen.update(event_ids)
            response = {
                "accepted": len(event_ids) - duplicates,
                "duplicates": duplicates,
                "errors": [],
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode("utf-8"))

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp) / "codex"
            state_file = Path(temp) / "state.json"
            queue = Path(temp) / "summary.sqlite3"
            write_session(codex_home, sample_events()[:3])
            write_summary_queue(queue)
            command = [
                sys.executable,
                str(SCRIPT),
                "--codex-home", str(codex_home),
                "--state-file", str(state_file),
                "--summary-mode", "local",
                "--summary-queue", str(queue),
                "--endpoint", f"http://127.0.0.1:{server.server_port}/ingest",
                "--source-name", "alice",
                "--token", "TOKEN123",
            ]
            first = subprocess.run(command, text=True, capture_output=True, check=True)
            first_output = json.loads(first.stdout)
            assert first_output["uploaded"] == 3
            assert first_output["summary_uploaded"] == 1
            assert len(captured) == 2
            assert all(item["event_type"] != "turn_summary" for item in captured[0]["events"])
            assert [item["event_type"] for item in captured[1]["events"]] == ["turn_summary"]
            summary_event = captured[1]["events"][0]
            assert summary_event["event_id"] == load_module().summary_event_id(SUMMARY_ID)
            assert "agent_id" not in json.dumps(summary_event)
            assert captured[1]["collector_version"] == "3.0.0"

            with closing(sqlite3.connect(str(queue))) as connection:
                assert connection.execute("SELECT uploaded FROM turn_summary_queue").fetchone()[0] == 1
                connection.execute("UPDATE turn_summary_queue SET uploaded = 0")
                connection.commit()

            second = subprocess.run(command, text=True, capture_output=True, check=True)
            second_output = json.loads(second.stdout)
            assert second_output["uploaded"] == 0
            assert second_output["summary_responses"][0]["duplicates"] == 1
            assert captured[2]["events"][0]["event_id"] == summary_event["event_id"]
            with closing(sqlite3.connect(str(queue))) as connection:
                assert connection.execute("SELECT uploaded FROM turn_summary_queue").fetchone()[0] == 1
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_summary_failure_does_not_roll_back_usage_state():
    captured = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            captured.append(body)
            is_summary = body["events"][0]["event_type"] == "turn_summary"
            response = (
                {"accepted": 0, "duplicates": 0, "errors": [{"message": "rejected"}]}
                if is_summary
                else {"accepted": len(body["events"]), "duplicates": 0, "errors": []}
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode("utf-8"))

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp) / "codex"
            state_file = Path(temp) / "state.json"
            queue = Path(temp) / "summary.sqlite3"
            write_session(codex_home, sample_events()[:3])
            write_summary_queue(queue)
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--codex-home", str(codex_home),
                    "--state-file", str(state_file),
                    "--summary-mode", "local",
                    "--summary-queue", str(queue),
                    "--endpoint", f"http://127.0.0.1:{server.server_port}/ingest",
                    "--source-name", "alice",
                    "--token", "TOKEN123",
                ],
                text=True,
                capture_output=True,
            )
            assert result.returncode != 0
            assert state_file.exists()
            assert any(record["offset"] > 0 for record in json.loads(state_file.read_text())["files"].values())
            with closing(sqlite3.connect(str(queue))) as connection:
                assert connection.execute("SELECT uploaded FROM turn_summary_queue").fetchone()[0] == 0
            assert len(captured) == 2
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_usage_response_errors_do_not_save_offset_state():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"accepted": 0, "duplicates": 0, "errors": [{"message": "bad event"}]}')

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
                    sys.executable, str(SCRIPT),
                    "--codex-home", str(codex_home),
                    "--state-file", str(state_file),
                    "--summary-mode", "off",
                    "--endpoint", f"http://127.0.0.1:{server.server_port}/ingest",
                    "--source-name", "alice",
                    "--token", "TOKEN123",
                ],
                text=True,
                capture_output=True,
            )
            assert result.returncode != 0
            assert not state_file.exists()
    finally:
        server.shutdown()
        thread.join(timeout=5)


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
    test_repeated_token_snapshot_is_not_uploaded_twice()
    test_legacy_state_reconstructs_token_snapshot_before_offset()
    test_forked_child_does_not_upload_copied_parent_token_snapshots()
    test_subagent_child_does_not_upload_parent_thread_token_snapshots()
    test_summary_mode_defaults_off_and_dry_run_does_not_update_queue()
    test_summary_validation_and_child_session_mapping()
    test_aborted_turn_summary_is_previewed_then_finalized_for_root_and_child()
    test_production_endpoint_requires_https_except_loopback()
    test_post_json_rejects_redirect_without_forwarding_bearer()
    test_summary_upload_is_separate_and_idempotent()
    test_summary_failure_does_not_roll_back_usage_state()
    test_usage_response_errors_do_not_save_offset_state()
    test_http_upload_uses_bearer_header_and_retries()
    print("tests passed")
