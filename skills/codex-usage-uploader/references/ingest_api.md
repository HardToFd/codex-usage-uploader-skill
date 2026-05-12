# Codex Usage Ingest API

## Endpoint

`POST <configured endpoint>`

Required headers:

```http
Authorization: Bearer <token>
Content-Type: application/json
```

## Request Body

```json
{
  "source_name": "alice",
  "machine_id": "stable-hash",
  "codex_home": "C:\\Users\\alice\\.codex",
  "collector_version": "1.0.0",
  "collected_at": "2026-05-06T12:00:00+08:00",
  "timezone": "Asia/Shanghai",
  "batch_id": "uuid",
  "events": []
}
```

## Event Shape

Every event contains:

```json
{
  "event_id": "sha256-prefix",
  "session_id": "uuid",
  "timestamp": "2026-05-06T12:00:00.000Z",
  "event_type": "token_count",
  "line_no": 42,
  "accuracy": "token_exact_context_linked",
  "context": {}
}
```

Optional event sections:

- `token`: `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens`, `total_tokens`.
- `rate_limits`: sanitized Codex rate-limit metadata.
- `session`: sanitized session metadata.
- `task`: task lifecycle, duration, TTFT, abort, or error metadata.
- `tool`: tool name, namespace/server, call ID, duration, status, argument keys/counts, and output lengths only.
- `shell`: cwd, exit code, duration, status, command count, command types, and executable names only.
- `patch`: changed file count, change type counts, and file extension counts only.

## Privacy Contract

Clients must not send raw user messages, agent messages, reasoning content, shell stdout/stderr, full shell commands, tool output, API arguments with values, or unified diffs.

## Response Body

The server should return JSON:

```json
{
  "accepted": 10,
  "duplicates": 2,
  "errors": []
}
```

The server must de-duplicate by `event_id`. A repeated `event_id` from the same source should not increment usage totals twice.
