#!/usr/bin/env python3
import hashlib
import hmac
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import codex_usage_summary_hook as hook


SCRIPT = pathlib.Path(__file__).with_name("codex_usage_summary_hook.py")
TEST_KEY = "0123456789abcdef" * 4


class SummaryHookTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.queue = pathlib.Path(self.temp_dir.name) / "queue.sqlite3"

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_hook(self, payload, key=TEST_KEY, queue=None, raw_input=None):
        env = os.environ.copy()
        env["CODEX_USAGE_SUMMARY_QUEUE"] = str(queue or self.queue)
        if key is None:
            env.pop("CODEX_USAGE_SUMMARY_HMAC_KEY", None)
        else:
            env["CODEX_USAGE_SUMMARY_HMAC_KEY"] = key
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=raw_input if raw_input is not None else json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=env,
            check=False,
        )

    def rows(self):
        with sqlite3.connect(self.queue) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute("SELECT * FROM turn_summary_queue ORDER BY rowid")]

    def test_url_normalization_hmac_and_redaction(self):
        raw_url = "HTTPS://User:Pass@ExAmPle.COM:443/issues/42?token=secret#private"
        canonical = "https://example.com/issues/42?token=secret#private"
        self.assertEqual(canonical, hook.normalize_requirement_url(raw_url))
        expected = "hmac:" + hmac.new(TEST_KEY.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(expected, hook.work_item_ref(canonical, TEST_KEY))

        raw = (
            "Ｆｉｘ\t" + raw_url + " user@example.com C:\\Users\\alice\\secret.txt "
            "/home/alice/secret 10.2.3.4 550e8400-e29b-41d4-a716-446655440000 "
            "Bearer abcdefghijklmnop token=topsecret \"CODEX_TOKEN\": \"quotedsecret\" "
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123 "
            "0123456789abcdef0123456789abcdef QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo="
        )
        redacted = hook.sanitize_text(raw, hook.SUMMARY_LIMIT)
        self.assertLessEqual(len(redacted), hook.SUMMARY_LIMIT)
        self.assertTrue(redacted.startswith("Fix"))
        for secret in ("User:Pass", "user@example.com", "alice", "10.2.3.4", "topsecret", "quotedsecret", "eyJhbGci"):
            self.assertNotIn(secret, redacted)
        self.assertFalse(hook.contains_sensitive(redacted))

        spaced = hook.sanitize_text(
            'Open "C:\\Users\\Alice Smith\\Top Secret\\result.txt"; '
            'Password: "correct,horse; battery staple"; '
            'Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ==',
            hook.SUMMARY_LIMIT,
        )
        for secret in ("Alice Smith", "Top Secret", "correct,horse", "battery staple", "QWxhZGRp"):
            self.assertNotIn(secret, spaced)
        self.assertNotEqual("", spaced)

        json_secret = hook.sanitize_text('{"password":"abc,def; ghi"}', hook.SUMMARY_LIMIT)
        for secret in ("abc", "def", "ghi"):
            self.assertNotIn(secret, json_secret)

        unquoted_paths = hook.sanitize_text(
            "Open C:\\Users\\Alice Smith\\Top,Secret\\result.txt; "
            "\\\\server\\Alice Smith\\Top;Secret\\result.txt; "
            "/home/alice/Top,Secret/result.txt",
            hook.SUMMARY_LIMIT,
        )
        for secret in ("Alice Smith", "Top,Secret", "Top;Secret", "result.txt"):
            self.assertNotIn(secret, unquoted_paths)

        relative_paths = hook.sanitize_text(
            "Updated clients/acme_secret/payroll.py, clients\\acme_secret\\payroll.py, "
            "$HOME/acme_secret/result.txt and src/app.py",
            hook.SUMMARY_LIMIT,
        )
        for secret in ("acme_secret", "payroll.py", "result.txt", "src/app.py"):
            self.assertNotIn(secret, relative_paths)

        natural_credentials = hook.sanitize_text(
            'The password is hunter2; tool --password "cli secret"; API key is shortsecret; '
            'pwd=hunter3; \u5bc6\u7801\uff1ahunter4\uff1b AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE; '
            'Basic dXNlcjpwYXNz; \u5bc6\u94a5\uff1ashortSecret9; \u8bbf\u95ee\u4ee4\u724c\uff1ashortTok9; '
            '\u53e3\u4ee4\uff1ahunter5; Cookie: sessionid=short123',
            hook.SUMMARY_LIMIT,
        )
        for secret in (
            "hunter2", "cli secret", "shortsecret", "hunter3", "hunter4", "hunter5",
            "AKIAIOSFODNN7EXAMPLE", "dXNlcj", "shortSecret9", "shortTok9", "short123",
        ):
            self.assertNotIn(secret, natural_credentials)

        self.assertNotEqual(
            hook.normalize_requirement_url("https://tracker.example/view?id=1"),
            hook.normalize_requirement_url("https://tracker.example/view?id=2"),
        )
        self.assertEqual(
            hook.normalize_requirement_url("https://tracker.example/req/1"),
            hook.normalize_requirement_url("https://tracker.example/req/1。"),
        )
        self.assertTrue(hook.explicit_requirement_prompt("新需求：https://tracker.example/req/2"))

    def test_main_turn_inheritance_drift_and_multiple_links(self):
        first = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "prompt": "Implement https://user:pw@Example.com:443/req/7?x=secret#frag for dev@example.com",
        }
        result = self.run_hook(first)
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual("", result.stderr)

        row = self.rows()[0]
        canonical = "https://example.com/req/7?x=secret#frag"
        expected_ref = "hmac:" + hmac.new(TEST_KEY.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(expected_ref, row["work_item_ref"])
        self.assertEqual(("explicit_link", "assigned", "not_evaluated"), (row["match_method"], row["match_state"], row["drift_state"]))
        self.assertEqual("in_progress", row["outcome"])
        self.assertEqual(0, row["ready"])
        self.assertLessEqual(len(row["title"]), 80)
        self.assertNotIn("dev@example.com", row["title"])

        stop = {
            "hook_event_name": "Stop",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "last_assistant_message": "Done; output at /home/alice/result.txt and token=verysecret",
        }
        result = self.run_hook(stop)
        self.assertEqual({}, json.loads(result.stdout))
        self.assertEqual("", result.stderr)
        row = self.rows()[0]
        self.assertEqual("turn_complete", row["outcome"])
        self.assertEqual(1, row["ready"])
        self.assertEqual(expected_ref, row["work_item_ref"])
        self.assertNotIn("alice", row["summary"])
        self.assertNotIn("verysecret", row["summary"])
        self.assertLessEqual(len(row["summary"]), 240)

        original_summary = row["summary"]
        duplicate = dict(stop, last_assistant_message="A replay must not replace the uploaded payload")
        self.run_hook(duplicate)
        self.assertEqual(original_summary, self.rows()[0]["summary"])

        inherited = dict(first, turn_id="turn-2", prompt="Continue with the same requirement")
        self.assertEqual(0, self.run_hook(inherited).returncode)
        inherited_row = self.rows()[1]
        self.assertEqual(expected_ref, inherited_row["work_item_ref"])
        self.assertEqual(("inherited", "assigned", "stable"), (inherited_row["match_method"], inherited_row["match_state"], inherited_row["drift_state"]))

        changed = dict(first, turn_id="turn-3", prompt="Switch to https://example.com/req/8")
        self.assertEqual(0, self.run_hook(changed).returncode)
        changed_row = self.rows()[2]
        self.assertEqual("changed", changed_row["drift_state"])
        self.assertNotEqual(expected_ref, changed_row["work_item_ref"])

        documentation = dict(
            first,
            turn_id="turn-doc",
            prompt="For this requirement, follow docs https://docs.example.com/api",
        )
        self.run_hook(documentation)
        docs_row = self.rows()[3]
        self.assertEqual((changed_row["work_item_ref"], "inherited", "assigned", "stable"), (
            docs_row["work_item_ref"], docs_row["match_method"], docs_row["match_state"], docs_row["drift_state"],
        ))

        ambiguous = dict(first, turn_id="turn-4", prompt="Compare https://example.com/a and https://example.com/b")
        self.assertEqual(0, self.run_hook(ambiguous).returncode)
        ambiguous_row = self.rows()[4]
        self.assertEqual("", ambiguous_row["work_item_ref"])
        self.assertEqual(("multiple_links", "ambiguous", "not_evaluated"), (ambiguous_row["match_method"], ambiguous_row["match_state"], ambiguous_row["drift_state"]))

        after_ambiguous = dict(first, turn_id="turn-5", prompt="Continue")
        self.run_hook(after_ambiguous)
        row = self.rows()[5]
        self.assertEqual(("", "none", "unassigned"), (
            row["work_item_ref"], row["match_method"], row["match_state"],
        ))

        with sqlite3.connect(self.queue) as connection:
            dump = "\n".join(connection.iterdump())
            columns = {row[1] for row in connection.execute("PRAGMA table_info(turn_summary_queue)")}
        self.assertNotIn("prompt", columns)
        self.assertNotIn("last_assistant_message", columns)
        for secret in ("user:pw", "x=secret", "dev@example.com", "verysecret"):
            self.assertNotIn(secret, dump.lower())

    def test_subagent_key_and_same_link_across_sessions(self):
        prompt = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-a",
            "turn_id": "turn-a",
            "prompt": "构建 https://Example.com/task/9?ignored=yes",
        }
        self.run_hook(prompt)
        parent_ref = self.rows()[0]["work_item_ref"]
        self.assertTrue(self.rows()[0]["title"].startswith("构建"))

        agent_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        start = {
            "hook_event_name": "SubagentStart",
            "session_id": "session-a",
            "turn_id": "child-turn-a",
            "agent_id": agent_id,
        }
        self.assertEqual("", self.run_hook(start).stdout)
        subagent = self.rows()[1]
        self.assertEqual(parent_ref, subagent["work_item_ref"])
        self.assertEqual(agent_id, subagent["agent_id"])
        self.assertEqual("inherited", subagent["match_method"])

        sibling_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        child_prompt = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-a",
            "turn_id": "child-turn-a",
            "agent_id": agent_id,
            "prompt": "Explore a child-only direction without a requirement link",
        }
        self.assertEqual("", self.run_hook(child_prompt).stdout)
        self.assertEqual(2, len(self.rows()))
        self.run_hook({
            "hook_event_name": "SubagentStart",
            "session_id": "session-a",
            "turn_id": "child-turn-b",
            "agent_id": sibling_id,
        })
        sibling = self.rows()[2]
        self.assertEqual((parent_ref, "inherited", "assigned"), (
            sibling["work_item_ref"], sibling["match_method"], sibling["match_state"],
        ))

        stop = dict(start, hook_event_name="SubagentStop", last_assistant_message="Finished safely")
        result = self.run_hook(stop)
        self.assertEqual({}, json.loads(result.stdout))
        rows = self.rows()
        self.assertEqual(3, len(rows))
        self.assertEqual((1, "turn_complete", "Finished safely"), (rows[1]["ready"], rows[1]["outcome"], rows[1]["summary"]))
        expected_id = hashlib.sha256(f"session-a\0child-turn-a\0{agent_id}".encode()).hexdigest()
        self.assertEqual(expected_id, rows[1]["summary_id"])

        same_link = dict(prompt, session_id="session-b", turn_id="turn-b", prompt="Build https://example.com/task/9?ignored=yes")
        self.run_hook(same_link)
        other = self.rows()[3]
        self.assertEqual(parent_ref, other["work_item_ref"])
        self.assertEqual("not_evaluated", other["drift_state"])

    def test_missing_key_and_fixed_fail_open_errors(self):
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "prompt": "Do https://example.com/req/1",
        }
        result = self.run_hook(payload, key=None)
        self.assertEqual((0, "", hook.ERR_HMAC_KEY + "\n"), (result.returncode, result.stdout, result.stderr))
        row = self.rows()[0]
        self.assertEqual("", row["work_item_ref"])
        self.assertEqual(("explicit_link", "unassigned"), (row["match_method"], row["match_state"]))

        weak = dict(payload, session_id="session-weak", turn_id="turn-weak")
        result = self.run_hook(weak, key="password")
        self.assertEqual(hook.ERR_HMAC_KEY + "\n", result.stderr)
        self.assertEqual("unassigned", self.rows()[1]["match_state"])

        malformed = self.run_hook({}, raw_input="not-json")
        self.assertEqual((0, "", hook.ERR_JSON + "\n"), (malformed.returncode, malformed.stdout, malformed.stderr))

        missing_id = self.run_hook({"hook_event_name": "Stop", "turn_id": "turn-2"})
        self.assertEqual(0, missing_id.returncode)
        self.assertEqual({}, json.loads(missing_id.stdout))
        self.assertEqual(hook.ERR_INPUT + "\n", missing_id.stderr)

        bad_queue = pathlib.Path(self.temp_dir.name) / "directory.sqlite3"
        bad_queue.mkdir()
        queue_error = self.run_hook(
            {"hook_event_name": "Stop", "session_id": "session-1", "turn_id": "turn-3"},
            queue=bad_queue,
        )
        self.assertEqual(0, queue_error.returncode)
        self.assertEqual({}, json.loads(queue_error.stdout))
        self.assertEqual(hook.ERR_QUEUE + "\n", queue_error.stderr)

        unsafe_id = self.run_hook({
            "hook_event_name": "UserPromptSubmit",
            "session_id": "password=hunter2",
            "turn_id": "https://private.example/turn",
            "prompt": "Continue",
        })
        self.assertEqual(hook.ERR_INPUT + "\n", unsafe_id.stderr)

    def test_same_turn_requirement_change_stays_ambiguous(self):
        base = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-steer",
            "turn_id": "turn-steer",
            "prompt": "Start https://example.com/req/a",
        }
        self.run_hook(base)
        original_ref = self.rows()[0]["work_item_ref"]
        self.assertTrue(original_ref)

        self.run_hook(dict(base, prompt="Actually switch to https://example.com/req/b"))
        row = self.rows()[0]
        self.assertEqual(("", "multiple_links", "ambiguous", "changed"), (
            row["work_item_ref"], row["match_method"], row["match_state"], row["drift_state"],
        ))

        self.run_hook(dict(base, prompt="Continue after the steer"))
        row = self.rows()[0]
        self.assertEqual(("", "multiple_links", "ambiguous"), (
            row["work_item_ref"], row["match_method"], row["match_state"],
        ))

        vague = dict(base, session_id="session-vague", prompt="Explore an unclear request")
        self.run_hook(vague)
        self.assertEqual("unassigned", self.rows()[1]["match_state"])
        self.run_hook(dict(vague, prompt="New requirement: https://example.com/req/c"))
        row = self.rows()[1]
        self.assertEqual(("", "multiple_links", "ambiguous", "changed"), (
            row["work_item_ref"], row["match_method"], row["match_state"], row["drift_state"],
        ))

    def test_link_only_prompt_gets_safe_summary_title(self):
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-link-only",
            "turn_id": "turn-link-only",
            "prompt": "https://example.com/req/only",
        }
        self.run_hook(payload)
        self.assertEqual("[REDACTED]", self.rows()[0]["title"])
        self.run_hook({
            "hook_event_name": "Stop",
            "session_id": "session-link-only",
            "turn_id": "turn-link-only",
            "last_assistant_message": "Implemented the reporting filter safely",
        })
        self.assertEqual("Implemented the reporting filter safely", self.rows()[0]["title"])

    def test_subagent_uses_current_parent_without_rewinding_main_requirement(self):
        base = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-1",
            "turn_id": "turn-a",
            "prompt": "Do https://example.com/req/a",
        }
        self.run_hook(base)
        ref_a = self.rows()[0]["work_item_ref"]
        agent_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        self.run_hook({
            "hook_event_name": "SubagentStart",
            "session_id": "session-1",
            "turn_id": "child-turn-a",
            "agent_id": agent_a,
        })

        self.run_hook(dict(base, turn_id="turn-b", prompt="Switch to https://example.com/req/b"))
        ref_b = self.rows()[2]["work_item_ref"]
        self.assertNotEqual(ref_a, ref_b)
        self.run_hook({
            "hook_event_name": "SubagentStop",
            "session_id": "session-1",
            "turn_id": "child-turn-a",
            "agent_id": agent_a,
            "last_assistant_message": "Late completion for A",
        })
        self.run_hook(dict(base, turn_id="turn-c", prompt="Continue"))
        self.assertEqual(ref_b, self.rows()[3]["work_item_ref"])

        ambiguous = dict(
            base,
            turn_id="turn-d",
            prompt="Compare https://example.com/req/c and https://example.com/req/d",
        )
        self.run_hook(ambiguous)
        self.run_hook({
            "hook_event_name": "SubagentStart",
            "session_id": "session-1",
            "turn_id": "child-turn-d",
            "agent_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        })
        child = self.rows()[-1]
        self.assertEqual(("", "multiple_links", "ambiguous"), (
            child["work_item_ref"], child["match_method"], child["match_state"],
        ))


if __name__ == "__main__":
    unittest.main()
