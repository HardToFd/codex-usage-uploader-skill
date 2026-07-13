# Codex Usage Uploader Skills

Upload sanitized local Codex Desktop or CLI usage metadata to one ingest API. The uploaders scan Codex JSONL logs incrementally and keep local progress state. V2 is metadata-only; V3 can additionally upload opt-in, locally redacted and truncated turn summaries.

## Versions

- `skills/codex-usage-uploader-v2`: exact per-call token accounting with cumulative snapshot, re-emission, fork, and subagent de-duplication.
- `skills/codex-usage-uploader-v3`: the V2.1 accounting contract plus optional local Hook turn summaries and `work_item_ref` attribution. Summary upload is off by default.

## Install

Install V3 from the repository's default `main` branch:

```powershell
python C:\Users\<user>\.codex\skills\.system\skill-installer\scripts\install-skill-from-github.py `
  --repo HardToFd/codex-usage-uploader-skill `
  --path skills/codex-usage-uploader-v3
```

Install V2 when turn attribution is not needed:

```powershell
python C:\Users\<user>\.codex\skills\.system\skill-installer\scripts\install-skill-from-github.py `
  --repo HardToFd/codex-usage-uploader-skill `
  --path skills/codex-usage-uploader-v2
```

Restart Codex after installation so the skill is discovered.

## Configure

Both versions use the same three values:

```powershell
$env:CODEX_USAGE_INGEST_URL = "https://collector.example.com/api/codex/usage"
$env:CODEX_USAGE_SOURCE_NAME = "alice"
$env:CODEX_USAGE_BEARER_TOKEN = "<bearer-token>"
```

Run a dry-run before uploading:

```powershell
python C:\Users\<user>\.codex\skills\codex-usage-uploader-v3\scripts\codex_usage_uploader.py `
  --source-name alice --summary-mode local --dry-run
```

V3 uploads usage only unless summary upload is explicitly enabled:

```powershell
$env:CODEX_USAGE_SUMMARY_MODE = "local"
$env:CODEX_USAGE_SUMMARY_HMAC_KEY = "<persistent-high-entropy-secret>"
python C:\Users\<user>\.codex\skills\codex-usage-uploader-v3\scripts\codex_usage_uploader.py
```

Configure and trust the optional local Hook before sending the first requirement link; see [the V3 skill guide](skills/codex-usage-uploader-v3/SKILL.md). Put the same single requirement link in the first prompt of every independent Codex task that belongs to it. Later turns in one task inherit locally, but unlinked tasks are not guessed semantically. If one running turn changes requirements, V3 marks that turn ambiguous instead of guessing how to split it. The Hook writes redacted structured summaries to local SQLite; the uploader sends them to the same endpoint in a batch separate from usage events.

## Privacy

V2 does not upload conversation or tool content. Normal metadata does include local `cwd`/`codex_home`, session/model identifiers, and a credential-stripped Git remote. V3 summary mode additionally uploads the allowlisted `turn_summary` structure derived from the prompt and final assistant message. Redaction covers common URLs, paths, email/IP addresses, identifiers, credentials, and opaque secrets, but it cannot identify every business-sensitive fact. Review the complete `--summary-mode local --dry-run` summary list and attribution preview; production V3 uploads require HTTPS.

## Server Contracts

- [V2 ingest contract](skills/codex-usage-uploader-v2/references/ingest_api.md)
- [V3 ingest and turn-summary contract](skills/codex-usage-uploader-v3/references/ingest_api.md)
