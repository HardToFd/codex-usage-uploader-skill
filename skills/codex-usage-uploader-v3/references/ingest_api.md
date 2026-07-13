# Codex Usage Ingest API V3

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
  "collector_version": "3.0.0",
  "collected_at": "2026-07-13T12:00:00+08:00",
  "timezone": "Asia/Shanghai",
  "batch_id": "uuid",
  "events": []
}
```

Normal usage events retain the V2.1 token contract. V3 sends `turn_summary` events in a separate request batch to this same endpoint. Production clients reject non-HTTPS endpoints and endpoint userinfo; loopback HTTP exists only for local testing. Clients reject all redirects so a Bearer token is never forwarded to another origin.

## Normal Event Shape

Every event contains:

```json
{
  "event_id": "sha256-hex",
  "session_id": "uuid",
  "timestamp": "2026-07-13T04:00:00.000Z",
  "event_type": "token_count",
  "line_no": 42,
  "accuracy": "token_exact_context_linked",
  "context": {}
}
```

Optional normal-event sections remain `token`, `rate_limits`, `session`, `task`, `tool`, `shell`, and `patch`. Sum only uploaded `token` fields. Never ingest or sum cumulative Codex `total_token_usage`. V3 also filters ancestor `task_complete` and `turn_aborted` records copied into fork/subagent histories.

## `turn_summary` Event

Accept exactly this summary event shape:

```json
{
  "event_id": "sha256-hex",
  "session_id": "uuid",
  "timestamp": "2026-07-13T04:00:00.000Z",
  "event_type": "turn_summary",
  "line_no": 0,
  "accuracy": "summary_local",
  "context": {
    "turn_id": "turn-uuid"
  },
  "task": {
    "title": "Implement work-item attribution",
    "summary": "Added redacted turn attribution and isolated summary retry.",
    "work_item_ref": "hmac:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "match_method": "explicit_link",
    "match_state": "assigned",
    "drift_state": "stable",
    "outcome": "turn_complete",
    "summary_schema_version": 1,
    "redaction_version": "redact-v1"
  }
}
```

`turn_summary.task` uses this strict whitelist:

- `title`: redacted string, at most 80 Unicode characters.
- `summary`: redacted string, at most 240 Unicode characters.
- `work_item_ref`: empty or `hmac:` plus 64 lowercase HMAC-SHA256 hex characters; never an original URL.
- `match_method`: `explicit_link`, `inherited`, `none`, or `multiple_links`.
- `match_state`: `assigned`, `unassigned`, or `ambiguous`.
- `drift_state`: `not_evaluated`, `stable`, or `changed`.
- `outcome`: `in_progress`, `turn_complete`, or `turn_aborted`; `turn_complete` does not mean the requirement is complete.
- `summary_schema_version`: integer schema version.
- `redaction_version`: redaction policy identifier.

Reject a `turn_summary` whose `task` contains any field outside this whitelist or whose values violate the documented length, HMAC, version, and enum constraints. Validate `session_id`, `turn_id`, `event_id` source fields and the timezone-aware ISO timestamp before upload as well. In particular, reject raw prompts, replies, reasoning, tool output, command text, diffs, original requirement URLs, and arbitrary nested metadata rather than storing them in JSON unchanged.

Require `context.turn_id`. A root summary has no other context field. A subagent summary sets the event's `session_id` to the child thread ID and adds only `context.parent_session_id`; the local queue's `agent_id` field is mapped to `session_id` and is not uploaded as a separate field. For nested agents, `parent_session_id` is corrected from JSONL lineage to the immediate parent:

```json
{
  "session_id": "child-thread-uuid",
  "context": {
    "turn_id": "child-turn-uuid",
    "parent_session_id": "parent-thread-uuid"
  }
}
```

## Attribution Semantics

- Canonicalize an explicit requirement URL locally, remove userinfo, normalize scheme/host/default port, retain query and fragment, and HMAC the complete canonical value. Retaining query prevents distinct query-routed work items from colliding.
- Inherit only if the immediately preceding main turn in the same session is assigned. An intervening unassigned or ambiguous turn blocks fallback to an older ref.
- For multiple links or an attribution change within one reused `turn_id`, use `multiple_links` plus `ambiguous` and leave `work_item_ref` empty.
- A different later URL is a requirement switch only when the local Hook sees it as URL-only or with an explicit new-requirement/switch marker. Other single URLs are treated as supporting context and inherit the current ref. An explicit new-turn switch uses `drift_state=changed`.
- A subagent copies only the current parent turn's attribution. If the parent is unassigned or ambiguous, the child remains so; it must not fall back to an older requirement.
- If JSONL lineage proves a child is nested below another agent rather than directly below the root, V3 retains its immediate parent but clears the Hook's root-derived attribution until an exact parent mapping is available.
- Cross-machine grouping requires both the same stable `source_name` and the same high-entropy HMAC key. Rotating the key creates a new attribution namespace.

## Idempotency and Association

Treat `event_id` as a required idempotency key. For a summary, the client computes:

```text
sha256("turn_summary:" + summary_id)
```

The local `summary_id` is stable, so retries, duplicate responses, and uploader configuration changes produce the same event ID. A repeated event ID must not create another summary or increment any total.

After de-duplicating by `event_id`, build one attribution row per `(tenant, source_name, machine_id, session_id, turn_id)`. Keep an assignment only when that turn resolves to exactly one assigned `work_item_ref`; otherwise mark it unassigned/ambiguous. Join usage through this unique map or an `EXISTS`/semi-join. Never directly join raw summary rows to usage, join only on `session_id`, or attribute an entire session from one summary.

For subagents, the summary `session_id` is already the child thread ID, so the same exact join captures child token events. Use `(tenant, source_name, work_item_ref)` for the final cross-thread/cross-machine requirement grouping; `machine_id` scopes the local summary-to-usage join but is not part of the requirement identity.

Define requirement metrics as follows:

- Tokens: sum de-duplicated uploaded `token` fields for matching root and child turns.
- Wall time: sum terminal `task.duration_ms` from `task_complete` and `turn_aborted` for matching root turns whose summary lacks `context.parent_session_id`.
- Agent time, if exposed: sum root and child terminal duration separately and label it as overlapping agent-time. Never add shell/tool durations to wall time.

## Privacy Contract

Normal usage events must never contain user messages, assistant replies, reasoning, shell stdout/stderr, full shell commands, tool output, API argument values, or unified diffs. They do contain local `cwd`/`codex_home`, session/model identifiers, and a credential-stripped Git remote, so the endpoint must be trusted. Opt-in summary mode may send only the allowlisted, locally redacted and truncated structure derived from the prompt and final assistant message. Redaction is best effort and does not guarantee removal of every business-sensitive fact, so clients should inspect the complete local summary list from a dry-run before the first upload. Summary mode defaults to `off` and requires explicit opt-in.

## Response Body

Return JSON:

```json
{
  "accepted": 10,
  "duplicates": 2,
  "errors": []
}
```

Apply the same idempotent response contract independently to usage batches and summary batches. A failed summary batch must not invalidate a previously accepted usage batch.
