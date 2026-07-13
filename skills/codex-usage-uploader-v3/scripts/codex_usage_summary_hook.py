#!/usr/bin/env python3
"""Fail-open Codex hook that queues locally redacted turn summaries."""

import datetime
import hashlib
import hmac
import json
import os
import pathlib
import re
import sqlite3
import sys
import unicodedata
import urllib.parse


TABLE_NAME = "turn_summary_queue"
TITLE_LIMIT = 80
SUMMARY_LIMIT = 240
REDACTION_VERSION = "redact-v1"
SUMMARY_SCHEMA_VERSION = 1
HMAC_KEY_MIN_LENGTH = 32
STOP_EVENTS = {"Stop", "SubagentStop"}
SUPPORTED_EVENTS = {"UserPromptSubmit", "Stop", "SubagentStart", "SubagentStop"}

ERR_JSON = "HOOK_ERR_JSON"
ERR_EVENT = "HOOK_ERR_EVENT"
ERR_INPUT = "HOOK_ERR_INPUT"
ERR_HMAC_KEY = "HOOK_ERR_HMAC_KEY"
ERR_QUEUE = "HOOK_ERR_QUEUE"
ERR_INTERNAL = "HOOK_ERR_INTERNAL"

REQUIREMENT_URL_RE = re.compile(r"https?://[^\s<>{}\[\]\"']+", re.IGNORECASE)
REQUIREMENT_MARKER_RE = re.compile(
    r"(?:\b(?:new\s+requirement|switch(?:ing)?\s+to|change(?:d)?\s+to|requirement\s+(?:link|url))\b"
    r"|新需求|需求链接|切换到|改为|换成)",
    re.IGNORECASE,
)
AGENT_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
SAFE_ID_RE = re.compile(r"[A-Za-z0-9._-]+")
SENSITIVE_PATTERNS = (
    re.compile(r"\b(?:https?|ftp)://[^\s<>{}\[\]\"']+|\bwww\.[^\s<>{}\[\]\"']+", re.IGNORECASE),
    re.compile(
        r"\b(?:authorization|proxy-authorization|(?:[A-Za-z0-9_.-]*[-_.])?(?:api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|token|secret|password|passwd))\b[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^}]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<!\w)--(?:api[-_]?key|access[-_]?token|refresh[-_]?token|token|secret|password|passwd)\s+(?:\"[^\"]*\"|'[^']*'|\S+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|token|secret|password|passwd)\b\s+is\s+(?:\"[^\"]*\"|'[^']*'|\S+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:pwd|aws[-_]?access[-_]?key[-_]?id|aws[-_]?secret[-_]?access[-_]?key)\b\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^}]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:\u5bc6\u7801|\u53e3\u4ee4|\u5bc6\u94a5|\u8bbf\u95ee\u4ee4\u724c|\u5237\u65b0\u4ee4\u724c|\u4ee4\u724c|\u51ed\u636e)\s*(?::|=|\u662f)\s*(?:\"[^\"]*\"|'[^']*'|[^}]+)"
    ),
    re.compile(r"\b(?:cookie|set-cookie)\b\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^}]+)", re.IGNORECASE),
    re.compile(r'"(?:[a-z]:[\\/]|\\\\[^\\/\s]+[\\/]|/)[^\"]*"', re.IGNORECASE),
    re.compile(r"'(?:[a-z]:[\\/]|\\\\[^\\/\s]+[\\/]|/)[^']*'", re.IGNORECASE),
    re.compile(r"(?<![\w$])(?:\$[A-Za-z_][A-Za-z0-9_]*|~)[\\/][^<>{}\[\]\"']*"),
    re.compile(r"(?<![\w./\\$])(?:[\w.-]+[\\/])+[\w.-]+\.[A-Za-z0-9]{1,12}\b"),
    re.compile(r"(?<![\w./\\$])(?:[\w.-]+[\\/]){2,}[\w.-]+\b"),
    re.compile(r"(?<![\w])(?:[a-z]:[\\/]|\\\\[^\\/\s]+[\\/])[^<>{}\[\]\"']*", re.IGNORECASE),
    re.compile(r"(?<![\w:])/(?:[^<>{}\[\]\"']*)"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),
    re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])"),
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE),
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bbasic\s+[A-Za-z0-9+/=]{8,}", re.IGNORECASE),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:sk|rk|pk|gh[pousr]|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b", re.IGNORECASE),
    re.compile(r"\b[0-9a-f]{32,}\b", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/_-]{32,}={0,2}(?![A-Za-z0-9+/=_-])"),
)

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    summary_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    work_item_ref TEXT NOT NULL DEFAULT '',
    match_method TEXT NOT NULL,
    match_state TEXT NOT NULL,
    drift_state TEXT NOT NULL,
    outcome TEXT NOT NULL,
    summary_schema_version INTEGER NOT NULL DEFAULT 1,
    redaction_version TEXT NOT NULL,
    ready INTEGER NOT NULL DEFAULT 0,
    uploaded INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(session_id, turn_id, agent_id)
)
"""

UPSERT_SQL = f"""
INSERT INTO {TABLE_NAME} (
    summary_id, session_id, turn_id, agent_id, timestamp, title, summary,
    work_item_ref, match_method, match_state, drift_state, outcome,
    summary_schema_version, redaction_version, ready, uploaded, created_at, updated_at
) VALUES (
    :summary_id, :session_id, :turn_id, :agent_id, :timestamp, :title, :summary,
    :work_item_ref, :match_method, :match_state, :drift_state, :outcome,
    :summary_schema_version, :redaction_version, :ready, :uploaded, :created_at, :updated_at
)
ON CONFLICT(session_id, turn_id, agent_id) DO UPDATE SET
    timestamp = excluded.timestamp,
    title = excluded.title,
    summary = excluded.summary,
    work_item_ref = excluded.work_item_ref,
    match_method = excluded.match_method,
    match_state = excluded.match_state,
    drift_state = excluded.drift_state,
    outcome = excluded.outcome,
    summary_schema_version = excluded.summary_schema_version,
    redaction_version = excluded.redaction_version,
    ready = excluded.ready,
    uploaded = excluded.uploaded,
    updated_at = excluded.updated_at
"""


class HookInputError(Exception):
    pass


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def normalize_text(value):
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value)
    value = "".join(" " if unicodedata.category(char).startswith("C") else char for char in value)
    return re.sub(r"\s+", " ", value).strip()


def contains_sensitive(value):
    return any(pattern.search(value) for pattern in SENSITIVE_PATTERNS)


def sanitize_text(value, limit):
    value = normalize_text(value)
    for pattern in SENSITIVE_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    value = re.sub(r"\s+", " ", value).strip()
    if contains_sensitive(value):
        return ""
    value = value[:limit].rstrip()
    return "" if contains_sensitive(value) else value


def redaction_only(value):
    return not re.sub(r"(?:\[REDACTED\]|[\s:;,.-])+", "", value or "")


def trim_url(value):
    value = value.rstrip(".,;:!?。，、；：！？")
    for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
        while value.endswith(closing) and value.count(closing) > value.count(opening):
            value = value[:-1]
    return value


def normalize_requirement_url(value):
    try:
        parts = urllib.parse.urlsplit(trim_url(unicodedata.normalize("NFKC", value)))
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"} or not parts.hostname:
            return None
        host = parts.hostname.rstrip(".").lower().encode("idna").decode("ascii")
        host = f"[{host}]" if ":" in host else host
        port = parts.port
        if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
            host = f"{host}:{port}"
        path = parts.path or "/"
        path = re.sub(r"%[0-9a-fA-F]{2}", lambda match: match.group(0).upper(), path)
        path = urllib.parse.quote(path, safe="/%:@!$&'()*+,;=-._~")
        return urllib.parse.urlunsplit((scheme, host, path, parts.query, parts.fragment))
    except (UnicodeError, ValueError):
        return None


def requirement_urls(value):
    if not isinstance(value, str):
        return []
    normalized = []
    for match in REQUIREMENT_URL_RE.finditer(unicodedata.normalize("NFKC", value)):
        url = normalize_requirement_url(match.group(0))
        if url and url not in normalized:
            normalized.append(url)
    return normalized


def work_item_ref(url, key):
    key = valid_hmac_key(key)
    if not url or not key:
        return ""
    digest = hmac.new(key.encode("utf-8"), url.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"hmac:{digest}"


def valid_hmac_key(value):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if len(value) < HMAC_KEY_MIN_LENGTH or len(set(value)) < 8:
        return ""
    return value


def explicit_requirement_prompt(raw_prompt):
    normalized = normalize_text(raw_prompt)
    without_urls = REQUIREMENT_URL_RE.sub("", normalized)
    remainder = re.sub(r"[\s:：=,，;；.!！?？()\[\]{}<>\"'`~_\-—]+", "", without_urls)
    return not remainder or bool(REQUIREMENT_MARKER_RE.search(without_urls))


def attribution(raw_prompt, key, previous_ref, allow_unmarked_link=False):
    urls = requirement_urls(raw_prompt)
    if len(urls) > 1:
        return "", "multiple_links", "ambiguous", "not_evaluated"
    if len(urls) == 1:
        if not allow_unmarked_link and not explicit_requirement_prompt(raw_prompt):
            if previous_ref:
                return previous_ref, "inherited", "assigned", "stable"
            return "", "none", "unassigned", "not_evaluated"
        current_ref = work_item_ref(urls[0], key)
        if not current_ref:
            return "", "explicit_link", "unassigned", "not_evaluated"
        drift = "not_evaluated" if not previous_ref else ("stable" if current_ref == previous_ref else "changed")
        return current_ref, "explicit_link", "assigned", drift
    if previous_ref:
        return previous_ref, "inherited", "assigned", "stable"
    return "", "none", "unassigned", "not_evaluated"


def safe_id(value, required=True):
    if not isinstance(value, str):
        if required:
            raise HookInputError
        return ""
    value = value.strip()
    if (
        (required and not value)
        or len(value) > 256
        or (value and not SAFE_ID_RE.fullmatch(value))
        or any(unicodedata.category(char).startswith("C") for char in value)
    ):
        raise HookInputError
    return value


def queue_path():
    configured = os.environ.get("CODEX_USAGE_SUMMARY_QUEUE")
    return pathlib.Path(configured).expanduser() if configured else pathlib.Path.home() / ".codex" / "codex_usage_summary_queue.sqlite3"


def open_queue(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=5.0)
    try:
        if os.name == "posix":
            os.chmod(path, 0o600)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute(CREATE_TABLE_SQL)
        return connection
    except Exception:
        connection.close()
        raise


def summary_id(session_id, turn_id, agent_id):
    raw = "\0".join((session_id, turn_id, agent_id)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def current_row(connection, session_id, turn_id, agent_id):
    return connection.execute(
        f"SELECT * FROM {TABLE_NAME} WHERE session_id = ? AND turn_id = ? AND agent_id = ?",
        (session_id, turn_id, agent_id),
    ).fetchone()


def latest_main_row(connection, session_id):
    return connection.execute(
        f"""SELECT * FROM {TABLE_NAME}
            WHERE session_id = ? AND agent_id = ''
            ORDER BY updated_at DESC, rowid DESC LIMIT 1""",
        (session_id,),
    ).fetchone()


def apply_parent_attribution(row, parent):
    if not parent:
        return
    row["title"] = parent["title"]
    row["work_item_ref"] = parent["work_item_ref"]
    row["match_method"] = "inherited" if parent["match_state"] == "assigned" else parent["match_method"]
    row["match_state"] = parent["match_state"]
    row["drift_state"] = parent["drift_state"]


def base_row(session_id, turn_id, agent_id, now):
    return {
        "summary_id": summary_id(session_id, turn_id, agent_id),
        "session_id": session_id,
        "turn_id": turn_id,
        "agent_id": agent_id,
        "timestamp": now,
        "title": "",
        "summary": "",
        "work_item_ref": "",
        "match_method": "none",
        "match_state": "unassigned",
        "drift_state": "not_evaluated",
        "outcome": "in_progress",
        "summary_schema_version": SUMMARY_SCHEMA_VERSION,
        "redaction_version": REDACTION_VERSION,
        "ready": 0,
        "uploaded": 0,
        "created_at": now,
        "updated_at": now,
    }


def handle_event(payload, path=None, key=None):
    if not isinstance(payload, dict):
        raise HookInputError
    event = payload.get("hook_event_name")
    if event not in SUPPORTED_EVENTS:
        raise HookInputError

    session_id = safe_id(payload.get("session_id"))
    turn_id = safe_id(payload.get("turn_id"))
    agent_id = safe_id(payload.get("agent_id"), required=event in {"SubagentStart", "SubagentStop"})
    if agent_id and not AGENT_ID_RE.fullmatch(agent_id):
        raise HookInputError
    if event not in {"SubagentStart", "SubagentStop"}:
        # Child turns also emit UserPromptSubmit with their child thread ID.
        # SubagentStart/SubagentStop own their queue row, so ignore the child
        # prompt instead of letting it masquerade as the root's latest turn.
        if agent_id:
            return None
        agent_id = ""

    now = utc_now()
    with open_queue(path or queue_path()) as connection:
        existing = current_row(connection, session_id, turn_id, agent_id)
        if existing and existing["ready"]:
            return None
        latest_main = latest_main_row(connection, session_id)
        previous_ref = (
            latest_main["work_item_ref"]
            if latest_main and latest_main["match_state"] == "assigned"
            else ""
        )
        parent = latest_main if event == "SubagentStart" else None
        row = base_row(session_id, turn_id, agent_id, now)
        warning = None

        if event == "UserPromptSubmit":
            prompt = payload.get("prompt")
            effective_key = valid_hmac_key(
                key if key is not None else os.environ.get("CODEX_USAGE_SUMMARY_HMAC_KEY", "")
            )
            allow_unmarked_link = latest_main is None
            if (
                len(requirement_urls(prompt)) == 1
                and (allow_unmarked_link or explicit_requirement_prompt(prompt))
                and not effective_key
            ):
                warning = ERR_HMAC_KEY
            ref, method, state, drift = attribution(
                prompt,
                effective_key,
                previous_ref,
                allow_unmarked_link=allow_unmarked_link,
            )
            if existing and existing["match_state"] == "ambiguous":
                ref, method, state, drift = "", "multiple_links", "ambiguous", existing["drift_state"]
            elif (
                existing
                and state == "assigned"
                and (
                    existing["match_state"] != "assigned"
                    or existing["work_item_ref"] != ref
                )
            ):
                # A steer within one Codex turn can reuse turn_id after work has
                # already happened. Without segment timestamps, assigning the
                # whole turn to either link would be false precision.
                ref, method, state, drift = "", "multiple_links", "ambiguous", "changed"
            row.update(
                title=sanitize_text(prompt, TITLE_LIMIT),
                work_item_ref=ref,
                match_method=method,
                match_state=state,
                drift_state=drift,
            )
        elif event == "SubagentStart":
            apply_parent_attribution(row, parent)
        else:
            if existing:
                for field in ("title", "work_item_ref", "match_method", "match_state", "drift_state", "created_at"):
                    row[field] = existing[field]
            elif event == "SubagentStop":
                apply_parent_attribution(row, parent)
            final_summary = sanitize_text(payload.get("last_assistant_message"), SUMMARY_LIMIT)
            row.update(
                title=final_summary[:TITLE_LIMIT] if redaction_only(row["title"]) else row["title"],
                summary=final_summary,
                outcome="turn_complete",
                ready=1,
            )

        connection.execute(UPSERT_SQL, row)
        return warning


def emit_error(code):
    try:
        sys.stderr.write(code + "\n")
        sys.stderr.flush()
    except OSError:
        pass


def emit_stop_output():
    try:
        sys.stdout.write("{}\n")
        sys.stdout.flush()
    except OSError:
        pass


def main():
    event = None
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
        event = payload.get("hook_event_name")
    except (json.JSONDecodeError, TypeError, ValueError):
        emit_error(ERR_JSON)
        return 0

    try:
        if event not in SUPPORTED_EVENTS:
            emit_error(ERR_EVENT)
        else:
            warning = handle_event(payload)
            if warning:
                emit_error(warning)
    except HookInputError:
        emit_error(ERR_INPUT)
    except (OSError, sqlite3.Error):
        emit_error(ERR_QUEUE)
    except Exception:
        emit_error(ERR_INTERNAL)
    finally:
        if event in STOP_EVENTS:
            emit_stop_output()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
