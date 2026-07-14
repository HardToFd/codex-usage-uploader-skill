#!/usr/bin/env python3
import argparse
import copy
import hashlib
import ipaddress
import json
import os
import pathlib
import re
import socket
import sqlite3
import sys
import time
import unicodedata
import uuid
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import date, datetime, time as datetime_time, timedelta, timezone

try:
    from zoneinfo import ZoneInfo as _ZoneInfo, ZoneInfoNotFoundError
except ModuleNotFoundError:
    try:
        from backports.zoneinfo import ZoneInfo as _ZoneInfo, ZoneInfoNotFoundError
    except ModuleNotFoundError:
        _ZoneInfo = None
        ZoneInfoNotFoundError = KeyError


def ZoneInfo(name):
    fixed_offsets = {
        "UTC": timezone.utc,
        "Asia/Shanghai": timezone(timedelta(hours=8), name),
    }
    if _ZoneInfo is not None:
        try:
            return _ZoneInfo(name)
        except ZoneInfoNotFoundError:
            pass
    if name in fixed_offsets:
        return fixed_offsets[name]
    raise RuntimeError("Timezone data is unavailable; install tzdata for this timezone.")


VERSION = "3.0.0"
DEFAULT_BATCH_SIZE = 500
DEFAULT_SUMMARY_QUEUE = pathlib.Path.home() / ".codex" / "codex_usage_summary_queue.sqlite3"
SUMMARY_TASK_FIELDS = (
    "title",
    "summary",
    "work_item_ref",
    "match_method",
    "match_state",
    "drift_state",
    "outcome",
    "summary_schema_version",
    "redaction_version",
)
SUMMARY_MATCH_METHODS = {"explicit_link", "inherited", "none", "multiple_links"}
SUMMARY_MATCH_STATES = {"assigned", "unassigned", "ambiguous"}
SUMMARY_DRIFT_STATES = {"not_evaluated", "stable", "changed"}
SUMMARY_OUTCOMES = {"in_progress", "turn_complete", "turn_aborted"}
WORK_ITEM_REF_RE = re.compile(r"hmac:[0-9a-f]{64}")
SUMMARY_ID_RE = re.compile(r"[0-9a-f]{64}")
SUMMARY_FORBIDDEN_PATTERNS = (
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
SESSION_ID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
SUMMARY_SAFE_ID_RE = re.compile(r"[A-Za-z0-9._-]+")
TOKEN_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
CONTENT_EVENT_TYPES = {
    "user_message",
    "message",
    "agent_message",
    "reasoning",
    "compacted",
    "context_compacted",
}


def trusted_endpoint(value):
    try:
        parts = urllib.parse.urlsplit(value)
    except (TypeError, ValueError):
        return False
    if not parts.hostname or parts.username or parts.password:
        return False
    if parts.scheme.lower() == "https":
        return True
    if parts.scheme.lower() != "http":
        return False
    host = parts.hostname.rstrip(".").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_args():
    parser = argparse.ArgumentParser(description="Upload sanitized Codex usage metadata.")
    parser.add_argument("--endpoint", default=os.environ.get("CODEX_USAGE_INGEST_URL"))
    parser.add_argument("--source-name", default=os.environ.get("CODEX_USAGE_SOURCE_NAME"))
    parser.add_argument("--token", default=os.environ.get("CODEX_USAGE_BEARER_TOKEN"))
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME") or str(pathlib.Path.home() / ".codex"))
    parser.add_argument("--timezone", default=os.environ.get("CODEX_USAGE_TIMEZONE") or "Asia/Shanghai")
    parser.add_argument("--state-file", default=None)
    parser.add_argument(
        "--summary-mode",
        choices=("off", "local"),
        default=os.environ.get("CODEX_USAGE_SUMMARY_MODE") or "off",
    )
    parser.add_argument(
        "--summary-queue",
        default=os.environ.get("CODEX_USAGE_SUMMARY_QUEUE") or str(DEFAULT_SUMMARY_QUEUE),
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--since", default=None, help="Only emit events at or after this local date/time.")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=1.0)
    args = parser.parse_args()

    if not args.endpoint and not args.dry_run:
        parser.error("--endpoint or CODEX_USAGE_INGEST_URL is required")
    if args.endpoint and not args.dry_run and not trusted_endpoint(args.endpoint):
        parser.error("--endpoint must use HTTPS (loopback HTTP is allowed for local testing)")
    if not args.source_name:
        parser.error("--source-name or CODEX_USAGE_SOURCE_NAME is required")
    if not args.dry_run and not args.token:
        parser.error("--token or CODEX_USAGE_BEARER_TOKEN is required unless --dry-run is used")
    if args.batch_size <= 0:
        parser.error("--batch-size must be greater than 0")
    return args


def stable_hash(value, length=16):
    return hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:length]


def sanitize_repository_url(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        parts = urllib.parse.urlsplit(value)
        if parts.scheme and parts.hostname:
            host = parts.hostname.rstrip(".").lower().encode("idna").decode("ascii")
            host = f"[{host}]" if ":" in host else host
            if parts.port:
                host = f"{host}:{parts.port}"
            return urllib.parse.urlunsplit((parts.scheme.lower(), host, parts.path, "", ""))
    except (UnicodeError, ValueError):
        return None
    scp_remote = re.fullmatch(r"[^@\s]+@([^:\s]+):(.+)", value)
    if scp_remote:
        path = re.split(r"[?#]", scp_remote.group(2), maxsplit=1)[0]
        return f"{scp_remote.group(1).lower()}:{path}" if path else None
    return None


def machine_id(codex_home):
    raw = "|".join([
        socket.gethostname(),
        os.environ.get("USERNAME") or os.environ.get("USER") or "",
        str(pathlib.Path(codex_home).expanduser()),
    ])
    return stable_hash(raw.lower(), 24)


def session_id_from_path(path):
    match = SESSION_ID_RE.search(path.name)
    return match.group(1).lower() if match else path.stem


def normalize_session_id(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value.lower() if value else None


def event_type(obj):
    payload = obj.get("payload") if isinstance(obj, dict) else None
    if isinstance(payload, dict) and payload.get("type"):
        return payload.get("type")
    return obj.get("type") if isinstance(obj, dict) else None


def iter_jsonl_files(codex_home):
    roots = [codex_home / "sessions", codex_home / "archived_sessions"]
    for root in roots:
        if root.exists():
            for path in sorted(root.rglob("*.jsonl")):
                yield path


def parse_datetime(value, tz):
    if not value:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        day = date.fromisoformat(value)
        return datetime.combine(day, datetime_time.min, tzinfo=tz)
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def clean(value):
    if isinstance(value, dict):
        cleaned = {key: clean(item) for key, item in value.items()}
        return {key: item for key, item in cleaned.items() if item is not None and item != {} and item != []}
    if isinstance(value, list):
        return [clean(item) for item in value if item is not None]
    return value


def duration_payload(value):
    if not isinstance(value, dict):
        return None
    return clean({
        "secs": value.get("secs"),
        "nanos": value.get("nanos"),
    })


def argument_keys(value):
    parsed = None
    if isinstance(value, dict):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"length": len(value)}
    if isinstance(parsed, dict):
        return {"keys": sorted(str(key) for key in parsed.keys()), "length": len(parsed)}
    if isinstance(parsed, list):
        return {"list_length": len(parsed)}
    return None


def first_program(value):
    if not value or not isinstance(value, str):
        return None
    stripped = value.strip()
    token = stripped.split()[0] if stripped else ""
    if not token:
        return None
    if token[0] in {"\"", "'"} and (len(token) == 1 or not token.endswith(token[0])):
        return None
    token = token.strip("\"'")
    if "=" in token:
        return None
    if "\\" in token or "/" in token:
        token = pathlib.PureWindowsPath(token).name
    return token if re.fullmatch(r"[A-Za-z0-9_.@+-]{1,80}", token) else None


def summarize_command(payload):
    parsed = payload.get("parsed_cmd")
    command_types = []
    programs = []
    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue
            if item.get("type"):
                command_types.append(str(item.get("type")))
            program = first_program(item.get("cmd"))
            if program:
                programs.append(program)
    return clean({
        "cwd": payload.get("cwd"),
        "exit_code": payload.get("exit_code"),
        "duration": duration_payload(payload.get("duration")),
        "status": payload.get("status"),
        "command_count": len(parsed) if isinstance(parsed, list) else None,
        "command_types": sorted(set(command_types)) if command_types else None,
        "programs": sorted(set(programs))[:20] if programs else None,
    })


def summarize_patch(payload):
    changes = payload.get("changes")
    if not isinstance(changes, dict):
        return None
    change_types = {}
    extensions = {}
    for path_text, change in changes.items():
        if isinstance(change, dict):
            change_type = change.get("type") or "unknown"
        else:
            change_type = "unknown"
        change_types[change_type] = change_types.get(change_type, 0) + 1
        suffix = pathlib.PureWindowsPath(str(path_text)).suffix.lower() or "<none>"
        extensions[suffix] = extensions.get(suffix, 0) + 1
    return clean({
        "changed_files_count": len(changes),
        "change_types": dict(sorted(change_types.items())),
        "file_extensions": dict(sorted(extensions.items())),
        "success": payload.get("success"),
        "status": payload.get("status"),
    })


def summarize_result(result):
    if not isinstance(result, dict):
        return None
    if "Ok" in result and isinstance(result["Ok"], dict):
        ok = result["Ok"]
        content = ok.get("content")
        return clean({
            "ok": True,
            "is_error": ok.get("isError"),
            "content_items_count": len(content) if isinstance(content, list) else None,
            "has_meta": isinstance(ok.get("_meta"), dict),
        })
    if "Err" in result:
        return {"ok": False}
    return None


def summarize_tool(event_type_name, payload):
    if event_type_name == "function_call":
        return clean({
            "call_id": payload.get("call_id"),
            "name": payload.get("name"),
            "namespace": payload.get("namespace"),
            "arguments": argument_keys(payload.get("arguments")),
        })
    if event_type_name == "function_call_output":
        output = payload.get("output")
        return clean({
            "call_id": payload.get("call_id"),
            "output_type": type(output).__name__,
            "output_length": len(output) if isinstance(output, (str, list)) else None,
        })
    if event_type_name == "custom_tool_call":
        value = payload.get("input")
        return clean({
            "call_id": payload.get("call_id"),
            "name": payload.get("name"),
            "status": payload.get("status"),
            "input_length": len(value) if isinstance(value, str) else None,
        })
    if event_type_name == "custom_tool_call_output":
        output = payload.get("output")
        return clean({
            "call_id": payload.get("call_id"),
            "output_length": len(output) if isinstance(output, str) else None,
        })
    if event_type_name == "mcp_tool_call_end":
        invocation = payload.get("invocation") if isinstance(payload.get("invocation"), dict) else {}
        return clean({
            "call_id": payload.get("call_id"),
            "server": invocation.get("server"),
            "name": invocation.get("tool"),
            "arguments": argument_keys(invocation.get("arguments")),
            "duration": duration_payload(payload.get("duration")),
            "result": summarize_result(payload.get("result")),
        })
    if event_type_name in {"dynamic_tool_call_request", "dynamic_tool_call_response"}:
        return clean({
            "call_id": payload.get("call_id") or payload.get("callId"),
            "turn_id": payload.get("turn_id") or payload.get("turnId"),
            "name": payload.get("tool"),
            "namespace": payload.get("namespace"),
            "arguments": argument_keys(payload.get("arguments")),
            "success": payload.get("success"),
            "duration": duration_payload(payload.get("duration")),
            "content_items_count": len(payload.get("content_items")) if isinstance(payload.get("content_items"), list) else None,
        })
    if event_type_name in {"tool_search_call", "tool_search_output"}:
        return clean({
            "call_id": payload.get("call_id"),
            "status": payload.get("status"),
            "execution": payload.get("execution"),
            "arguments": argument_keys(payload.get("arguments")),
            "tools_count": len(payload.get("tools")) if isinstance(payload.get("tools"), list) else None,
        })
    if event_type_name in {"web_search_call", "web_search_end"}:
        action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        query = payload.get("query") or action.get("query")
        queries = action.get("queries")
        return clean({
            "call_id": payload.get("call_id"),
            "status": payload.get("status"),
            "action_type": action.get("type"),
            "query_length": len(query) if isinstance(query, str) else None,
            "queries_count": len(queries) if isinstance(queries, list) else None,
        })
    if event_type_name == "view_image_tool_call":
        path_value = payload.get("path")
        return clean({
            "call_id": payload.get("call_id"),
            "path_extension": pathlib.PureWindowsPath(path_value).suffix.lower() if isinstance(path_value, str) else None,
        })
    return None


def update_context(context, event_type_name, payload):
    next_context = copy.deepcopy(context or {})
    if event_type_name == "session_meta":
        for key in ("cwd", "originator", "cli_version", "model_provider", "forked_from_id", "memory_mode"):
            if payload.get(key) is not None:
                next_context[key] = payload.get(key)
        parent_thread_id = payload.get("parent_thread_id") or source_parent_thread_id(payload)
        if parent_thread_id:
            next_context["parent_thread_id"] = parent_thread_id
        git = payload.get("git")
        if isinstance(git, dict):
            next_context["git"] = clean({
                "repository_url": sanitize_repository_url(git.get("repository_url")),
                "commit_hash": git.get("commit_hash"),
                "branch": git.get("branch"),
            })
    elif event_type_name == "turn_context":
        for key in ("turn_id", "cwd", "current_date", "timezone", "model", "effort"):
            if payload.get(key) is not None:
                next_context[key] = payload.get(key)
        mode = payload.get("collaboration_mode")
        settings = mode.get("settings") if isinstance(mode, dict) and isinstance(mode.get("settings"), dict) else {}
        if settings.get("model"):
            next_context["model"] = settings.get("model")
        if settings.get("reasoning_effort"):
            next_context["reasoning_effort"] = settings.get("reasoning_effort")
    elif event_type_name == "task_started":
        if payload.get("turn_id"):
            next_context["turn_id"] = payload.get("turn_id")
        if payload.get("model_context_window") is not None:
            next_context["model_context_window"] = payload.get("model_context_window")
        if payload.get("collaboration_mode_kind"):
            next_context["collaboration_mode_kind"] = payload.get("collaboration_mode_kind")
    elif event_type_name == "thread_name_updated":
        thread_name = payload.get("thread_name")
        next_context["thread"] = clean({
            "thread_id": payload.get("thread_id"),
            "thread_name_hash": stable_hash(thread_name) if isinstance(thread_name, str) else None,
            "thread_name_length": len(thread_name) if isinstance(thread_name, str) else None,
        })

    turn_id = payload.get("turn_id") or payload.get("turnId")
    if turn_id:
        next_context["turn_id"] = turn_id
    return clean(next_context)


def public_context(context):
    if not isinstance(context, dict):
        return {}
    allowed = {
        "cwd",
        "originator",
        "cli_version",
        "model_provider",
        "forked_from_id",
        "parent_thread_id",
        "memory_mode",
        "git",
        "turn_id",
        "current_date",
        "timezone",
        "model",
        "effort",
        "reasoning_effort",
        "model_context_window",
        "collaboration_mode_kind",
        "thread",
    }
    return clean({key: copy.deepcopy(value) for key, value in context.items() if key in allowed})


def event_accuracy(event_type_name):
    if event_type_name == "token_count":
        return "token_exact_context_linked"
    if event_type_name in {"session_meta", "turn_context", "thread_name_updated"}:
        return "context_exact"
    if event_type_name in {"task_started", "task_complete", "turn_aborted", "error"}:
        return "task_exact_context_linked"
    if event_type_name in {"exec_command_end", "patch_apply_end"}:
        return "event_exact_context_linked"
    return "event_metadata_context_linked"


def build_event_identity(source_name, mid, session_id, line_no, timestamp, event_type_name, payload):
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    usage = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
    identity = {
        "source_name": source_name,
        "machine_id": mid,
        "session_id": session_id,
        "line_no": line_no,
        "timestamp": timestamp,
        "event_type": event_type_name,
        "call_id": payload.get("call_id") or payload.get("callId"),
        "token_total": usage.get("total_tokens"),
        "token_input": usage.get("input_tokens"),
        "token_output": usage.get("output_tokens"),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def token_usage_fingerprint(usage):
    if not isinstance(usage, dict):
        return None
    return tuple(usage.get(key, 0) or 0 for key in TOKEN_KEYS)


def token_total_fingerprint(payload):
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    return token_usage_fingerprint(info.get("total_token_usage"))


def terminal_task_fingerprint(payload):
    if not isinstance(payload, dict) or payload.get("type") not in {"task_complete", "turn_aborted"}:
        return None
    turn_id = payload.get("turn_id")
    if not isinstance(turn_id, str) or not turn_id:
        return None
    identity = {
        "type": payload.get("type"),
        "turn_id": turn_id,
        "completed_at": payload.get("completed_at"),
        "duration_ms": payload.get("duration_ms"),
        "time_to_first_token_ms": payload.get("time_to_first_token_ms"),
        "reason": payload.get("reason"),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def source_parent_thread_id(payload):
    source = payload.get("source")
    if not isinstance(source, dict):
        return None
    subagent = source.get("subagent")
    if not isinstance(subagent, dict):
        return None
    thread_spawn = subagent.get("thread_spawn")
    if not isinstance(thread_spawn, dict):
        return None
    return thread_spawn.get("parent_thread_id")


def session_parent_ids(payload):
    parents = set()
    for key in ("forked_from_id", "parent_thread_id"):
        parent = normalize_session_id(payload.get(key))
        if parent:
            parents.add(parent)
    source_parent = normalize_session_id(source_parent_thread_id(payload))
    if source_parent:
        parents.add(source_parent)
    return parents


def build_session_lineage_index(codex_home):
    sessions = {}
    files = {}
    for path in iter_jsonl_files(codex_home):
        path_session_id = normalize_session_id(session_id_from_path(path))
        current_session_id = path_session_id
        parent_ids = set()
        token_fingerprints = set()
        terminal_task_fingerprints = set()
        saw_current_meta = False
        try:
            handle = path.open("rb")
        except OSError:
            continue
        with handle:
            for raw_line in handle:
                try:
                    obj = json.loads(raw_line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                if event_type(obj) == "session_meta" and not saw_current_meta:
                    meta_session_id = normalize_session_id(payload.get("id"))
                    if meta_session_id:
                        current_session_id = meta_session_id
                    parent_ids.update(session_parent_ids(payload))
                    saw_current_meta = True
                fingerprint = token_total_fingerprint(payload)
                if fingerprint is not None:
                    token_fingerprints.add(fingerprint)
                task_fingerprint = terminal_task_fingerprint(payload)
                if task_fingerprint is not None:
                    terminal_task_fingerprints.add(task_fingerprint)
        if not current_session_id:
            continue
        files[str(path)] = current_session_id
        entry = sessions.setdefault(current_session_id, {
            "parents": set(),
            "token_total_fingerprints": set(),
            "terminal_task_fingerprints": set(),
        })
        entry["parents"].update(parent_ids)
        entry["token_total_fingerprints"].update(token_fingerprints)
        entry["terminal_task_fingerprints"].update(terminal_task_fingerprints)
    return {"sessions": sessions, "files": files}


def session_id_for_path(path, lineage_index):
    if isinstance(lineage_index, dict):
        files = lineage_index.get("files") if isinstance(lineage_index.get("files"), dict) else {}
        session_id = normalize_session_id(files.get(str(path)))
        if session_id:
            return session_id
    return normalize_session_id(session_id_from_path(path))


def inherited_token_total_fingerprints(session_id, lineage_index):
    return inherited_fingerprints(session_id, lineage_index, "token_total_fingerprints")


def inherited_terminal_task_fingerprints(session_id, lineage_index):
    return inherited_fingerprints(session_id, lineage_index, "terminal_task_fingerprints")


def inherited_fingerprints(session_id, lineage_index, field):
    if not session_id or not isinstance(lineage_index, dict):
        return set()
    sessions = lineage_index.get("sessions") if isinstance(lineage_index.get("sessions"), dict) else {}
    inherited = set()
    visited = set()
    stack = list(sessions.get(session_id, {}).get("parents", set()))
    while stack:
        parent_id = normalize_session_id(stack.pop())
        if not parent_id or parent_id in visited:
            continue
        visited.add(parent_id)
        parent = sessions.get(parent_id)
        if not isinstance(parent, dict):
            continue
        inherited.update(parent.get(field, set()))
        stack.extend(parent.get("parents", set()))
    return inherited


def build_event(obj, path, line_no, context, source_name, mid):
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    event_type_name = event_type(obj)
    if not event_type_name or event_type_name in CONTENT_EVENT_TYPES:
        return None

    timestamp = obj.get("timestamp")
    sid = session_id_from_path(path)
    event = {
        "event_id": build_event_identity(source_name, mid, sid, line_no, timestamp, event_type_name, payload),
        "session_id": sid,
        "timestamp": timestamp,
        "event_type": event_type_name,
        "line_no": line_no,
        "accuracy": event_accuracy(event_type_name),
        "context": public_context(context),
    }

    if event_type_name == "token_count":
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        usage = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
        if not usage:
            return None
        event["token"] = {key: usage.get(key, 0) or 0 for key in TOKEN_KEYS}
        if info.get("model_context_window") is not None:
            event["context"]["model_context_window"] = info.get("model_context_window")
        if isinstance(payload.get("rate_limits"), dict):
            event["rate_limits"] = payload.get("rate_limits")
    elif event_type_name == "session_meta":
        event["session"] = clean({
            "id": payload.get("id"),
            "originator": payload.get("originator"),
            "cli_version": payload.get("cli_version"),
            "model_provider": payload.get("model_provider"),
            "forked_from_id": payload.get("forked_from_id"),
            "parent_thread_id": payload.get("parent_thread_id") or source_parent_thread_id(payload),
            "memory_mode": payload.get("memory_mode"),
        })
    elif event_type_name in {"turn_context", "thread_name_updated"}:
        event["context_event"] = True
    elif event_type_name == "task_started":
        event["task"] = clean({
            "status": "started",
            "turn_id": payload.get("turn_id"),
            "started_at": payload.get("started_at"),
            "model_context_window": payload.get("model_context_window"),
            "collaboration_mode_kind": payload.get("collaboration_mode_kind"),
        })
    elif event_type_name == "task_complete":
        event["task"] = clean({
            "status": "complete",
            "turn_id": payload.get("turn_id"),
            "completed_at": payload.get("completed_at"),
            "duration_ms": payload.get("duration_ms"),
            "time_to_first_token_ms": payload.get("time_to_first_token_ms"),
            "last_agent_message_length": len(payload.get("last_agent_message")) if isinstance(payload.get("last_agent_message"), str) else None,
        })
    elif event_type_name == "turn_aborted":
        event["task"] = clean({
            "status": "aborted",
            "turn_id": payload.get("turn_id"),
            "reason": payload.get("reason"),
            "completed_at": payload.get("completed_at"),
            "duration_ms": payload.get("duration_ms"),
        })
    elif event_type_name == "error":
        event["task"] = clean({
            "status": "error",
            "message_length": len(payload.get("message")) if isinstance(payload.get("message"), str) else None,
            "codex_error_info_length": len(payload.get("codex_error_info")) if isinstance(payload.get("codex_error_info"), str) else None,
        })
    elif event_type_name == "exec_command_end":
        event["shell"] = summarize_command(payload)
    elif event_type_name == "patch_apply_end":
        event["patch"] = summarize_patch(payload)
    elif event_type_name == "item_completed":
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        event["task"] = clean({
            "status": "item_completed",
            "thread_id": payload.get("thread_id"),
            "turn_id": payload.get("turn_id"),
            "item_type": item.get("type"),
            "item_id": item.get("id"),
            "item_text_length": len(item.get("text")) if isinstance(item.get("text"), str) else None,
        })
    else:
        tool = summarize_tool(event_type_name, payload)
        if tool:
            event["tool"] = tool
        else:
            return None

    return clean(event)


def load_state(path):
    if not path.exists():
        return {"version": 1, "files": {}}
    try:
        with path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "files": {}}
    if not isinstance(state, dict):
        return {"version": 1, "files": {}}
    state.setdefault("version", 1)
    state.setdefault("files", {})
    return state


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
    tmp.replace(path)


def reconstruct_last_token_total_fingerprint(path, offset):
    if not offset:
        return None
    current_offset = 0
    last_fingerprint = None
    try:
        handle = path.open("rb")
    except OSError:
        return None
    with handle:
        for raw_line in handle:
            next_offset = current_offset + len(raw_line)
            if next_offset > offset:
                break
            current_offset = next_offset
            try:
                obj = json.loads(raw_line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
            fingerprint = token_total_fingerprint(payload)
            if fingerprint is not None:
                last_fingerprint = fingerprint
    return last_fingerprint


def read_file_events(
    path,
    record,
    tz,
    since_dt,
    source_name,
    mid,
    inherited_token_fingerprints=None,
    inherited_task_fingerprints=None,
):
    record = record if isinstance(record, dict) else {}
    stat = path.stat()
    offset = int(record.get("offset") or 0)
    line_no = int(record.get("line_no") or 0)
    context = record.get("context") if isinstance(record.get("context"), dict) else {}
    last_token_total_fingerprint = record.get("last_token_total_fingerprint")
    if isinstance(last_token_total_fingerprint, list):
        last_token_total_fingerprint = tuple(last_token_total_fingerprint)
    else:
        last_token_total_fingerprint = None
    if stat.st_size < offset:
        offset = 0
        line_no = 0
        context = {}
        last_token_total_fingerprint = None
    elif offset and last_token_total_fingerprint is None:
        last_token_total_fingerprint = reconstruct_last_token_total_fingerprint(path, offset)

    inherited_token_fingerprints = inherited_token_fingerprints or set()
    inherited_task_fingerprints = inherited_task_fingerprints or set()
    events = []
    last_timestamp = record.get("last_timestamp")
    current_offset = offset
    with path.open("rb") as handle:
        if offset:
            handle.seek(offset)
        for raw_line in handle:
            if not raw_line.endswith(b"\n"):
                break
            next_offset = current_offset + len(raw_line)
            line = raw_line.decode("utf-8", errors="replace")
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                current_offset = next_offset
                line_no += 1
                continue
            current_offset = next_offset
            line_no += 1
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
            type_name = event_type(obj)
            if type_name:
                context = update_context(context, type_name, payload)
            timestamp = obj.get("timestamp")
            if timestamp:
                last_timestamp = timestamp
            token_fingerprint = token_total_fingerprint(payload)
            task_fingerprint = terminal_task_fingerprint(payload)
            is_repeated_token_snapshot = False
            is_inherited_token_snapshot = False
            if token_fingerprint is not None:
                is_repeated_token_snapshot = token_fingerprint == last_token_total_fingerprint
                is_inherited_token_snapshot = token_fingerprint in inherited_token_fingerprints
                last_token_total_fingerprint = token_fingerprint
            if since_dt and timestamp:
                try:
                    if parse_datetime(timestamp, tz) < since_dt:
                        continue
                except ValueError:
                    continue
            if (
                is_repeated_token_snapshot
                or is_inherited_token_snapshot
                or task_fingerprint in inherited_task_fingerprints
            ):
                continue
            event = build_event(obj, path, line_no, context, source_name, mid)
            if event:
                events.append(event)

    next_record = {
        "offset": current_offset,
        "line_no": line_no,
        "last_timestamp": last_timestamp,
        "context": context,
        "last_token_total_fingerprint": list(last_token_total_fingerprint) if last_token_total_fingerprint is not None else None,
    }
    return events, next_record


def collect_events(codex_home, state, tz, since_dt, source_name, mid):
    codex_home = pathlib.Path(codex_home).expanduser()
    next_state = copy.deepcopy(state or {"version": 1, "files": {}})
    next_state.setdefault("version", 1)
    next_state.setdefault("files", {})
    lineage_index = build_session_lineage_index(codex_home)
    all_events = []
    seen_ids = set()
    files_scanned = 0
    for path in iter_jsonl_files(codex_home):
        files_scanned += 1
        key = str(path)
        session_id = session_id_for_path(path, lineage_index)
        inherited_fingerprints = inherited_token_total_fingerprints(session_id, lineage_index)
        inherited_task_fingerprints = inherited_terminal_task_fingerprints(session_id, lineage_index)
        file_events, next_record = read_file_events(
            path,
            next_state["files"].get(key, {}),
            tz,
            since_dt,
            source_name,
            mid,
            inherited_fingerprints,
            inherited_task_fingerprints,
        )
        next_state["files"][key] = next_record
        for event in file_events:
            if event["event_id"] in seen_ids:
                continue
            seen_ids.add(event["event_id"])
            all_events.append(event)
    stats = {
        "files_scanned": files_scanned,
        "events_collected": len(all_events),
    }
    return all_events, next_state, stats, lineage_index


def summary_event_id(summary_id):
    return hashlib.sha256(f"turn_summary:{summary_id}".encode("utf-8")).hexdigest()


def valid_summary_text(value, limit):
    normalized = unicodedata.normalize("NFKC", value) if isinstance(value, str) else ""
    return (
        isinstance(value, str)
        and len(value) <= limit
        and not any(pattern.search(normalized) for pattern in SUMMARY_FORBIDDEN_PATTERNS)
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


def valid_summary_timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validated_summary_row(row):
    values = {key: row[key] for key in row.keys()}
    for field in ("summary_id", "session_id", "turn_id", "timestamp", "agent_id"):
        value = values.get(field)
        if (
            not isinstance(value, str)
            or len(value) > 256
            or (field != "agent_id" and not value.strip())
            or any(unicodedata.category(char).startswith("C") for char in value)
        ):
            raise RuntimeError(f"summary queue row has invalid {field}")
        values[field] = value.strip()
    for field in ("summary_id", "session_id", "turn_id", "agent_id"):
        if values[field] and not SUMMARY_SAFE_ID_RE.fullmatch(values[field]):
            raise RuntimeError(f"summary queue row has invalid {field}")
    if not SESSION_ID_RE.fullmatch(values["session_id"]):
        raise RuntimeError("summary queue row has invalid session_id")
    if not SESSION_ID_RE.fullmatch(values["turn_id"]):
        raise RuntimeError("summary queue row has invalid turn_id")
    if not SUMMARY_ID_RE.fullmatch(values["summary_id"]):
        raise RuntimeError("summary queue row has invalid summary_id")
    if not valid_summary_timestamp(values["timestamp"]):
        raise RuntimeError("summary queue row has invalid timestamp")
    if values["agent_id"] and not SESSION_ID_RE.fullmatch(values["agent_id"]):
        raise RuntimeError("summary queue row has invalid agent_id")
    if not valid_summary_text(values.get("title"), 80) or not valid_summary_text(values.get("summary"), 240):
        raise RuntimeError("summary queue row has invalid redacted text")
    ref = values.get("work_item_ref")
    if not isinstance(ref, str) or (ref and not WORK_ITEM_REF_RE.fullmatch(ref)):
        raise RuntimeError("summary queue row has invalid work_item_ref")
    if values.get("match_method") not in SUMMARY_MATCH_METHODS:
        raise RuntimeError("summary queue row has invalid match_method")
    if values.get("match_state") not in SUMMARY_MATCH_STATES:
        raise RuntimeError("summary queue row has invalid match_state")
    if values.get("drift_state") not in SUMMARY_DRIFT_STATES:
        raise RuntimeError("summary queue row has invalid drift_state")
    if values.get("outcome") not in SUMMARY_OUTCOMES:
        raise RuntimeError("summary queue row has invalid outcome")
    if values["match_state"] == "assigned" and not ref:
        raise RuntimeError("assigned summary queue row has no work_item_ref")
    if values["match_state"] != "assigned" and ref:
        raise RuntimeError("unassigned summary queue row has a work_item_ref")
    if values["match_method"] == "multiple_links" and values["match_state"] != "ambiguous":
        raise RuntimeError("multiple-link summary queue row is not ambiguous")
    schema_version = values.get("summary_schema_version")
    if isinstance(schema_version, bool) or schema_version != 1:
        raise RuntimeError("summary queue row has invalid summary_schema_version")
    if values.get("redaction_version") != "redact-v1":
        raise RuntimeError("summary queue row has invalid redaction_version")
    return values


def event_turn_key(event):
    if not isinstance(event, dict) or not isinstance(event.get("session_id"), str):
        return None
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    task = event.get("task") if isinstance(event.get("task"), dict) else {}
    turn_id = context.get("turn_id") or task.get("turn_id")
    return (event["session_id"], turn_id) if isinstance(turn_id, str) and turn_id else None


def terminal_summary_outcomes(events):
    outcomes = {}
    for event in events:
        if event.get("event_type") != "turn_aborted":
            continue
        key = event_turn_key(event)
        if key:
            outcomes[key] = ("turn_aborted", event.get("timestamp"))
    return outcomes


def summary_parent_session(child_session_id, root_session_id, lineage_index):
    if not child_session_id or not isinstance(lineage_index, dict):
        return root_session_id, False
    sessions = lineage_index.get("sessions") if isinstance(lineage_index.get("sessions"), dict) else {}
    parents = sessions.get(child_session_id, {}).get("parents", set())
    parents = {normalize_session_id(parent) for parent in parents}
    parents.discard(None)
    if not parents:
        return root_session_id, False
    if len(parents) == 1:
        parent = next(iter(parents))
        return parent, parent != normalize_session_id(root_session_id)
    return root_session_id, True


def read_summary_events(queue_path, terminal_outcomes=None, lineage_index=None):
    queue_path = pathlib.Path(queue_path).expanduser()
    if not queue_path.exists():
        return [], []
    terminal_outcomes = terminal_outcomes or {}
    uri = queue_path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("""
            SELECT summary_id, session_id, turn_id, agent_id, timestamp,
                   title, summary, work_item_ref, match_method, match_state,
                   drift_state, outcome, summary_schema_version, redaction_version, ready
            FROM turn_summary_queue
            WHERE uploaded = 0
            ORDER BY timestamp, summary_id
        """).fetchall()

    summary_ids = []
    events = []
    for row in rows:
        usage_key = (row["agent_id"] or row["session_id"], row["turn_id"])
        terminal = terminal_outcomes.get(usage_key)
        if not row["ready"] and not terminal:
            continue
        values = validated_summary_row(row)
        if terminal:
            values["outcome"] = terminal[0]
            if isinstance(terminal[1], str) and terminal[1]:
                values["timestamp"] = terminal[1]
        summary_id = values["summary_id"]
        summary_ids.append(summary_id)
        child_session_id = values["agent_id"]
        context = {"turn_id": values["turn_id"]}
        if child_session_id:
            parent_session_id, nested_or_ambiguous = summary_parent_session(
                child_session_id,
                values["session_id"],
                lineage_index,
            )
            context["parent_session_id"] = parent_session_id
            if nested_or_ambiguous:
                # Hook input exposes the root lineage but not the immediate
                # spawning agent. Never attribute a nested child to a newer
                # root requirement merely because it was latest at spawn time.
                values.update(
                    work_item_ref="",
                    match_method="none",
                    match_state="unassigned",
                    drift_state="not_evaluated",
                )
        events.append(clean({
            "event_id": summary_event_id(summary_id),
            "session_id": child_session_id or values["session_id"],
            "timestamp": values["timestamp"],
            "event_type": "turn_summary",
            "line_no": 0,
            "accuracy": "summary_local",
            "context": context,
            "task": {field: values[field] for field in SUMMARY_TASK_FIELDS},
        }))
    return events, summary_ids


def finalize_terminal_summaries(queue_path, terminal_outcomes):
    if not terminal_outcomes:
        return 0
    queue_path = pathlib.Path(queue_path).expanduser()
    if not queue_path.exists():
        return 0
    updated = 0
    with closing(sqlite3.connect(str(queue_path))) as connection:
        for (usage_session_id, turn_id), (outcome, timestamp) in terminal_outcomes.items():
            cursor = connection.execute(
                """UPDATE turn_summary_queue
                   SET outcome = ?,
                       timestamp = COALESCE(?, timestamp),
                       ready = 1,
                       updated_at = COALESCE(?, updated_at)
                   WHERE turn_id = ? AND ready = 0 AND uploaded = 0
                     AND ((agent_id = '' AND session_id = ?) OR agent_id = ?)""",
                (outcome, timestamp, timestamp, turn_id, usage_session_id, usage_session_id),
            )
            updated += cursor.rowcount
        connection.commit()
    return updated


def build_attribution_preview(events, summary_events):
    refs_by_turn = {}
    child_turns = set()
    for event in summary_events:
        key = event_turn_key(event)
        task = event.get("task") if isinstance(event.get("task"), dict) else {}
        ref = task.get("work_item_ref")
        if not key or task.get("match_state") != "assigned" or not isinstance(ref, str) or not ref:
            continue
        refs_by_turn.setdefault(key, set()).add(ref)
        context = event.get("context") if isinstance(event.get("context"), dict) else {}
        if context.get("parent_session_id"):
            child_turns.add(key)

    assignments = {key: next(iter(refs)) for key, refs in refs_by_turn.items() if len(refs) == 1}
    totals = {}
    for ref in sorted(set(assignments.values())):
        totals[ref] = {
            "work_item_ref": ref,
            "turns": sum(assigned_ref == ref for assigned_ref in assignments.values()),
            "token": {key: 0 for key in TOKEN_KEYS},
            "wall_time_ms": 0,
            "agent_time_ms": 0,
        }

    coverage = {
        "summary_turns_assigned": len(assignments),
        "summary_turn_conflicts": sum(len(refs) > 1 for refs in refs_by_turn.values()),
        "token_events_total": 0,
        "token_events_attributed": 0,
        "terminal_duration_events_total": 0,
        "terminal_duration_events_attributed": 0,
    }
    for event in events:
        key = event_turn_key(event)
        ref = assignments.get(key)
        token = event.get("token") if isinstance(event.get("token"), dict) else None
        if token is not None:
            coverage["token_events_total"] += 1
            if ref:
                coverage["token_events_attributed"] += 1
                for token_key in TOKEN_KEYS:
                    value = token.get(token_key, 0)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        totals[ref]["token"][token_key] += value

        if event.get("event_type") in {"task_complete", "turn_aborted"}:
            coverage["terminal_duration_events_total"] += 1
            task = event.get("task") if isinstance(event.get("task"), dict) else {}
            duration = task.get("duration_ms")
            if ref and isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
                coverage["terminal_duration_events_attributed"] += 1
                totals[ref]["agent_time_ms"] += duration
                if key not in child_turns:
                    totals[ref]["wall_time_ms"] += duration

    return {"coverage": coverage, "requirements": list(totals.values())}


def mark_summary_uploaded(queue_path, summary_ids):
    if not summary_ids:
        return
    queue_path = pathlib.Path(queue_path).expanduser()
    if not queue_path.exists():
        raise RuntimeError(f"summary queue disappeared: {queue_path}")
    with closing(sqlite3.connect(str(queue_path))) as connection:
        connection.executemany(
            "UPDATE turn_summary_queue SET uploaded = 1 WHERE summary_id = ? AND ready = 1 AND uploaded = 0",
            ((summary_id,) for summary_id in summary_ids),
        )
        connection.commit()


def chunked(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def build_batch(endpoint_args, codex_home, tz_name, mid, events):
    return {
        "source_name": endpoint_args.source_name,
        "machine_id": mid,
        "codex_home": str(codex_home),
        "collector_version": VERSION,
        "collected_at": datetime.now(ZoneInfo(tz_name)).isoformat(timespec="seconds"),
        "timezone": tz_name,
        "batch_id": str(uuid.uuid4()),
        "events": events,
    }


class RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_json(endpoint, token, body, timeout):
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"codex-usage-uploader-v3/{VERSION}",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(RejectRedirect())
    with opener.open(request, timeout=timeout) as response:
        response_body = response.read().decode("utf-8", errors="replace")
        if not response_body:
            return {}
        try:
            return json.loads(response_body)
        except json.JSONDecodeError:
            return {"raw_response_length": len(response_body)}


def validate_upload_response(response, expected_count):
    if not isinstance(response, dict):
        raise RuntimeError("upload response must be a JSON object")
    accepted = response.get("accepted")
    duplicates = response.get("duplicates")
    errors = response.get("errors")
    if (
        isinstance(accepted, bool)
        or not isinstance(accepted, int)
        or accepted < 0
        or isinstance(duplicates, bool)
        or not isinstance(duplicates, int)
        or duplicates < 0
        or not isinstance(errors, list)
    ):
        raise RuntimeError("upload response must contain non-negative accepted/duplicates and an errors list")
    if errors:
        raise RuntimeError(f"upload response contained {len(errors)} error(s)")
    if accepted + duplicates != expected_count:
        raise RuntimeError(
            f"upload response accounted for {accepted + duplicates} of {expected_count} event(s)"
        )


def upload_events(args, codex_home, tz_name, mid, events):
    responses = []
    for batch_events in chunked(events, args.batch_size):
        body = build_batch(args, codex_home, tz_name, mid, batch_events)
        last_error = None
        for attempt in range(max(args.retries, 1)):
            try:
                response = post_json(args.endpoint, args.token, body, args.timeout)
                validate_upload_response(response, len(batch_events))
                responses.append(response)
                last_error = None
                break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt + 1 < max(args.retries, 1):
                    time.sleep(args.retry_delay)
        if last_error:
            raise RuntimeError(f"upload failed after {max(args.retries, 1)} attempt(s): {last_error}")
    return responses


def main():
    args = parse_args()
    tz = ZoneInfo(args.timezone)
    codex_home = pathlib.Path(args.codex_home).expanduser()
    state_file = pathlib.Path(args.state_file).expanduser() if args.state_file else codex_home / "codex_usage_uploader_state.json"
    since_dt = parse_datetime(args.since, tz) if args.since else None
    mid = machine_id(codex_home)
    state = load_state(state_file)
    events, next_state, stats, lineage_index = collect_events(
        codex_home, state, tz, since_dt, args.source_name, mid
    )

    if args.dry_run:
        terminal_outcomes = terminal_summary_outcomes(events)
        summary_events, _ = (
            read_summary_events(args.summary_queue, terminal_outcomes, lineage_index)
            if args.summary_mode == "local"
            else ([], [])
        )
        summary_match_states = {}
        for event in summary_events:
            match_state = event.get("task", {}).get("match_state", "unknown")
            summary_match_states[match_state] = summary_match_states.get(match_state, 0) + 1
        print(json.dumps({
            "dry_run": True,
            "source_name": args.source_name,
            "machine_id": mid,
            "codex_home": str(codex_home),
            "events_ready": len(events),
            "summary_events_ready": len(summary_events),
            "summary_match_states": summary_match_states,
            "attribution_preview": build_attribution_preview(events, summary_events),
            "stats": stats,
            "sample_event": events[0] if events else None,
            "sample_summary_event": summary_events[0] if summary_events else None,
            "summary_events": summary_events,
        }, ensure_ascii=True, indent=2))
        return 0

    responses = upload_events(args, codex_home, args.timezone, mid, events) if events else []
    if args.summary_mode == "local":
        finalize_terminal_summaries(args.summary_queue, terminal_summary_outcomes(events))
    save_state(state_file, next_state)
    summary_events, summary_ids = (
        read_summary_events(args.summary_queue, lineage_index=lineage_index)
        if args.summary_mode == "local"
        else ([], [])
    )
    summary_responses = (
        upload_events(args, codex_home, args.timezone, mid, summary_events) if summary_events else []
    )
    mark_summary_uploaded(args.summary_queue, summary_ids)
    print(json.dumps({
        "uploaded": len(events),
        "batches": len(responses),
        "responses": responses,
        "summary_uploaded": len(summary_events),
        "summary_batches": len(summary_responses),
        "summary_responses": summary_responses,
        "state_file": str(state_file),
        "stats": stats,
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
