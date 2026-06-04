---
name: codex-usage-uploader-v2
description: Upload sanitized Codex Desktop or CLI usage metadata to a configured ingest API using the V2 token snapshot de-duplication contract. Use when Codex needs to configure, run, backfill, troubleshoot, or automate local Codex session JSONL collection while avoiding duplicate token usage from cumulative `total_token_usage` and re-emitted `token_count` events.
---

# Codex Usage Uploader V2

## Overview

Use this skill to collect local Codex session JSONL usage metadata and upload it to a dashboard ingest API. V2 keeps the original privacy boundary and adds a strict token snapshot de-duplication contract so dashboards can safely sum uploaded `token` fields without counting cumulative Codex snapshots twice.

## Quick Start

Run from the skill directory or pass the absolute script path:

```bash
python scripts/codex_usage_uploader.py --endpoint https://collector.example.com/api/codex/usage-v2 --source-name alice --token <bearer-token>
```

Environment variables are supported:

```bash
set CODEX_USAGE_INGEST_URL=https://collector.example.com/api/codex/usage-v2
set CODEX_USAGE_SOURCE_NAME=alice
set CODEX_USAGE_BEARER_TOKEN=<bearer-token>
python scripts/codex_usage_uploader.py
```

Useful options:

```bash
python scripts/codex_usage_uploader.py --dry-run --endpoint https://collector.example.com/api/codex/usage-v2 --source-name alice
python scripts/codex_usage_uploader.py --since 2026-05-01 --endpoint https://collector.example.com/api/codex/usage-v2 --source-name alice --token <bearer-token>
python scripts/codex_usage_uploader.py --codex-home C:\Users\admin\.codex --timezone Asia/Shanghai --batch-size 500 --endpoint https://collector.example.com/api/codex/usage-v2 --source-name alice --token <bearer-token>
```

If `python` is not on PATH, use the bundled Codex runtime if available.

## Automation

When the user asks to keep the dashboard updated, create a Codex cron automation that runs hourly and executes this skill's script with the configured environment variables. The default schedule is hourly in `Asia/Shanghai`.

The automation prompt should be self-contained:

```text
Use $codex-usage-uploader-v2 to run the Codex usage uploader with the configured CODEX_USAGE_INGEST_URL, CODEX_USAGE_SOURCE_NAME, and CODEX_USAGE_BEARER_TOKEN environment variables. Upload new local Codex usage metadata only with V2 token snapshot de-duplication.
```

## Privacy Boundary

Do not upload raw message text, agent replies, reasoning content, shell stdout/stderr, full commands, tool output, or unified diffs. The script uploads counts, IDs, timestamps, status, durations, token fields, rate-limit fields, cwd/model/git context, command program summaries, and patch change-type statistics only.

Accuracy tiers:

- Exact: token fields, session ID, timestamp, event type, line number, rate limits, explicit duration/status fields.
- Context linked: cwd, model, effort, git, and turn context attached from the latest same-session context event.
- External: `source_name` is supplied by configuration. If it represents a person, configure a stable person or account label.

Token counting contract:

- Codex `token_count` log entries can include both `last_token_usage` and `total_token_usage`.
- `total_token_usage` is cumulative within the session and must not be summed across log entries.
- Codex can re-emit `token_count` when rate-limit state changes, sometimes with the same token usage snapshot.
- The uploader sends `token` from `last_token_usage` only for the first observed `total_token_usage` snapshot; repeated snapshots are skipped so dashboards can sum uploaded `token` fields as per-call increments.

## API Reference

Read `references/ingest_api.md` when implementing or validating the server-side ingest API. The server must treat `event_id` as an idempotency key and should return `accepted`, `duplicates`, and `errors` counts.

## Troubleshooting

- Missing endpoint: set `CODEX_USAGE_INGEST_URL` or pass `--endpoint`.
- Missing source: set `CODEX_USAGE_SOURCE_NAME` or pass `--source-name`.
- Missing token: set `CODEX_USAGE_BEARER_TOKEN` or pass `--token`; `--dry-run` does not require a token.
- Duplicate data: expected during file moves or re-scans; de-duplicate by `event_id`. Token usage snapshots are also filtered client-side when Codex re-emits the same cumulative usage with new rate-limit metadata.
- No events found: verify `$CODEX_HOME\sessions` or `$CODEX_HOME\archived_sessions` contains JSONL logs.
