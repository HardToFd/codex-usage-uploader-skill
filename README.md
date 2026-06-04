# Codex Usage Uploader Skill

Codex skill for uploading sanitized local Codex Desktop or CLI usage metadata to an ingest API. It scans local Codex JSONL session logs incrementally, filters private content, sends metadata batches with Bearer authentication, and stores local progress state.

This repository contains two skill paths:

- `skills/codex-usage-uploader`: original uploader.
- `skills/codex-usage-uploader-v2`: V2 uploader with token snapshot de-duplication. Use this when dashboards sum uploaded token fields and must avoid over-counting Codex cumulative `total_token_usage` or re-emitted `token_count` snapshots.

## Install

Install with Codex's built-in skill installer:

```powershell
python C:\Users\<user>\.codex\skills\.system\skill-installer\scripts\install-skill-from-github.py `
  --repo HardToFd/codex-usage-uploader-skill `
  --path skills/codex-usage-uploader
```

Restart Codex after installation so the skill is discovered.

Install the V2 skill:

```powershell
python C:\Users\<user>\.codex\skills\.system\skill-installer\scripts\install-skill-from-github.py `
  --repo HardToFd/codex-usage-uploader-skill `
  --path skills/codex-usage-uploader-v2 `
  --branch develop
```

## Configure

The uploader needs three values:

```powershell
$env:CODEX_USAGE_INGEST_URL = "https://collector.example.com/api/codex/usage"
$env:CODEX_USAGE_SOURCE_NAME = "alice"
$env:CODEX_USAGE_BEARER_TOKEN = "<bearer-token>"
```

Run a dry-run first:

```powershell
python C:\Users\<user>\.codex\skills\codex-usage-uploader\scripts\codex_usage_uploader.py --dry-run
```

For V2, use the V2 skill directory:

```powershell
python C:\Users\<user>\.codex\skills\codex-usage-uploader-v2\scripts\codex_usage_uploader.py --dry-run
```

Then run the upload:

```powershell
python C:\Users\<user>\.codex\skills\codex-usage-uploader\scripts\codex_usage_uploader.py
```

For a first historical backfill with many events, use larger batches and longer timeouts:

```powershell
python C:\Users\<user>\.codex\skills\codex-usage-uploader\scripts\codex_usage_uploader.py `
  --batch-size 1000 `
  --timeout 60 `
  --retries 2
```

## Privacy

The uploader does not send raw user messages, assistant replies, reasoning content, shell stdout or stderr, full shell commands, tool output, API argument values, or unified diffs. It sends counts, IDs, timestamps, status, durations, token fields, rate-limit metadata, selected cwd/model/git context, command program summaries, and patch change-type statistics.

## Server Contract

See [skills/codex-usage-uploader/references/ingest_api.md](skills/codex-usage-uploader/references/ingest_api.md).

For V2 token snapshot de-duplication semantics, see [skills/codex-usage-uploader-v2/references/ingest_api.md](skills/codex-usage-uploader-v2/references/ingest_api.md).

