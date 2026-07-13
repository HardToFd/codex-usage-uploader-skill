---
name: codex-usage-uploader-v3
description: Upload sanitized Codex Desktop or CLI usage metadata with V2.1 exact token snapshot, fork, and subagent de-duplication plus optional local Hook turn summaries for work-item attribution. Use when Codex needs to configure, run, backfill, troubleshoot, or automate V3 usage collection, opt into locally redacted turn summaries, or attribute token and time totals to requirements without uploading full unredacted conversation content.
---

# Codex Usage Uploader V3

## Overview

Use V3 for the V2.1 exact token contract plus optional turn-level work-item attribution. Keep summary mode off unless the user explicitly opts in. When enabled, run a local Hook that redacts lifecycle input into structured SQLite rows, then let the uploader send ready `turn_summary` rows to the same ingest endpoint in a batch separate from usage events.

## Upload Usage

Run from the skill directory or pass the absolute script path:

```bash
python scripts/codex_usage_uploader.py --endpoint https://collector.example.com/api/codex/usage --source-name alice --token <bearer-token>
```

Environment variables are supported:

```powershell
$env:CODEX_USAGE_INGEST_URL = "https://collector.example.com/api/codex/usage"
$env:CODEX_USAGE_SOURCE_NAME = "alice"
$env:CODEX_USAGE_BEARER_TOKEN = "<bearer-token>"
python scripts/codex_usage_uploader.py
```

Run `--dry-run` first. A dry-run needs `--source-name` but no endpoint or token. Use `--since`, `--codex-home`, `--timezone`, `--batch-size`, `--timeout`, and `--retries` only when needed.

## Opt In to Turn Summaries

Summary mode defaults to `off`. Opt in by generating a high-entropy HMAC secret, installing the Hook, and enabling local summary upload. On Windows, persist the secret before restarting Codex Desktop; a temporary `$env:` value in an unrelated PowerShell process is not inherited by an already running app:

```powershell
$summaryKey = python -c "import secrets; print(secrets.token_hex(32))"
[Environment]::SetEnvironmentVariable("CODEX_USAGE_SUMMARY_HMAC_KEY", $summaryKey, "User")
$env:CODEX_USAGE_SUMMARY_HMAC_KEY = $summaryKey
$env:CODEX_USAGE_SUMMARY_MODE = "local"
```

Use the same secret and stable `source_name` on every machine that should share one requirement bucket. The Hook rejects blank or weakly shaped keys; use at least 32 characters with at least 8 distinct characters (the generated 64-character hex key satisfies this). Rotating the secret intentionally starts a new attribution namespace. The default queue is `~/.codex/codex_usage_summary_queue.sqlite3`; both Hook and uploader honor `CODEX_USAGE_SUMMARY_QUEUE`, and `--summary-queue` overrides the uploader.

Merge this command Hook configuration into `~/.codex/hooks.json`; do not overwrite existing Hook event arrays. Replace `<user>` in the Windows path and confirm the installed script exists:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.codex/skills/codex-usage-uploader-v3/scripts/codex_usage_summary_hook.py",
            "commandWindows": "py -3 \"C:\\Users\\<user>\\.codex\\skills\\codex-usage-uploader-v3\\scripts\\codex_usage_summary_hook.py\""
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.codex/skills/codex-usage-uploader-v3/scripts/codex_usage_summary_hook.py",
            "commandWindows": "py -3 \"C:\\Users\\<user>\\.codex\\skills\\codex-usage-uploader-v3\\scripts\\codex_usage_summary_hook.py\""
          }
        ]
      }
    ],
    "SubagentStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.codex/skills/codex-usage-uploader-v3/scripts/codex_usage_summary_hook.py",
            "commandWindows": "py -3 \"C:\\Users\\<user>\\.codex\\skills\\codex-usage-uploader-v3\\scripts\\codex_usage_summary_hook.py\""
          }
        ]
      }
    ],
    "SubagentStop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.codex/skills/codex-usage-uploader-v3/scripts/codex_usage_summary_hook.py",
            "commandWindows": "py -3 \"C:\\Users\\<user>\\.codex\\skills\\codex-usage-uploader-v3\\scripts\\codex_usage_summary_hook.py\""
          }
        ]
      }
    ]
  }
}
```

Restart Codex, then review and trust the command with `/hooks`. Each event passes UTF-8 JSON on stdin to the same script. The Hook fails open, reports fixed error codes only, writes local redacted structure, and never calls the ingest API. This configuration follows the official [Codex Hooks lifecycle](https://learn.chatgpt.com/docs/hooks), including separate root `Stop` and child `SubagentStop` events.

## What the User Does

1. Complete the one-time secret and Hook setup before sending the first requirement link.
2. Put exactly one requirement link in the first prompt of every independent Codex task that belongs to that requirement. No separate requirement name is needed.
3. Within the same task, later prompts may omit the link and inherit it. A different task cannot inherit it; repeat the same link there.
4. If the Hook was installed after the link was sent, send the link again in a new prompt. V3 does not parse unstable transcript files or semantically guess an unlinked cross-task match.
5. When a running turn is steered from a vague request to a link, or from link A to link B, V3 marks that whole turn ambiguous because it has no trustworthy timestamp boundary. Start a new turn with the new link when a clean token/time split matters.
6. In a later turn, an unrelated documentation URL does not become a new requirement. To switch, send the new URL by itself or use an explicit phrase such as `New requirement: <url>` / `新需求：<url>` / `Switch to <url>`.

Run the uploader after Hook rows exist:

```powershell
python scripts/codex_usage_uploader.py --source-name alice --summary-mode local --dry-run
python scripts/codex_usage_uploader.py --summary-mode local
```

The dry-run lists every ready summary, assigned/unassigned/ambiguous counts, and an `attribution_preview` showing pending token coverage plus per-ref token, wall-time, and agent-time totals. It does not open a network connection or change state. Inspect the text before the first upload. A real run uploads normal usage and saves its offset first, then uploads ready summaries as an independent batch. A failed summary batch remains queued without rolling back successful usage.

## Attribution Rules

- Canonicalize one explicit requirement link, remove URL userinfo, and HMAC the result as `work_item_ref`; the same complete link and secret produce the same ref across tasks. Query and fragment are retained so query-routed work items do not collide.
- Inherit only when the immediately preceding main turn in the same session is assigned. An intervening ambiguous or unassigned turn prevents fallback to an older requirement.
- Mark a first unlinked or otherwise vague requirement `unassigned`; do not claim semantic auto-matching.
- Mark multiple links, or any material attribution change within one reused `turn_id`, `ambiguous`; leave `work_item_ref` empty and do not split one turn's usage across requirements.
- Treat a different later URL as a switch only when it is URL-only or carries an explicit new-requirement/switch marker; otherwise keep the current ref so documentation links do not create false requirement buckets. Mark an explicit switch on a new turn as `drift_state=changed`.
- Treat `outcome=turn_complete` as the end of one turn, not completion of the requirement. If JSONL reports `turn_aborted`, the uploader safely finalizes the pending Hook row as `outcome=turn_aborted` so its tokens and duration are not lost.
- A direct subagent copies the current root turn's attribution. Its uploaded summary uses the child thread ID as `session_id`, allowing an exact join to child usage while retaining `context.parent_session_id`. For a nested child whose immediate parent differs from the root lineage, the uploader records the immediate parent from JSONL but conservatively clears attribution instead of risking a wrong root requirement.
- For a link-only prompt, the opaque URL is still removed; after Stop, a safe final-summary prefix becomes the display title when available.

## Privacy Boundary

Normal usage events never contain user messages, assistant replies, reasoning, shell stdout/stderr, full commands, tool output, API argument values, or unified diffs. They do include local `cwd`/`codex_home`, model/session IDs, and a credential-stripped Git remote. Production uploads require HTTPS; loopback HTTP is accepted only for local testing, endpoint credentials are rejected, and redirects are never followed. Opt-in summary mode derives an allowlisted, truncated title and synopsis from the prompt and final assistant message. It does not summarize the full tool trace. Local redaction targets URLs, absolute and path-like relative paths, email/IP addresses, identifiers, credentials, and opaque secrets, but cannot guarantee removal of every business-sensitive name or fact.

The SQLite queue retains redacted rows after upload because later turns need the latest same-session ref. On POSIX the Hook sets the database to mode `0600`; on Windows it relies on the user profile ACL. `--summary-mode off` stops upload but does not stop an installed Hook from writing locally. Disable the Hook with `/hooks` or remove its configuration when local collection is no longer wanted.

## Token Contract

- Emit `last_token_usage` only for a new cumulative `total_token_usage` snapshot.
- Skip same-file re-emissions and snapshots inherited through `forked_from_id` or `parent_thread_id` histories.
- Skip inherited `task_complete` and `turn_aborted` records so copied fork/subagent history cannot multiply duration.
- Let dashboards sum uploaded `token` fields as exact per-call increments; never sum cumulative `total_token_usage`.
- Define requirement wall time as root-turn terminal duration from `task_complete` or `turn_aborted`. Child terminal duration may be reported separately as overlapping agent-time; never add shell/tool/subagent durations to root wall time.

## Automation

When the user asks to keep the dashboard updated, create an hourly automation in `Asia/Shanghai` unless another schedule is requested. Keep summary mode off unless the user also opts into turn attribution.

```text
Use $codex-usage-uploader-v3 to upload new sanitized Codex usage metadata with exact token, fork, and subagent de-duplication. Keep turn-summary upload off unless CODEX_USAGE_SUMMARY_MODE is explicitly set to local.
```

## API Reference

Read `references/ingest_api.md` when implementing or validating the server. Require `event_id` idempotency, strict `turn_summary.task` field validation, and `session_id + turn_id` association.

## Troubleshooting

- Missing endpoint/source/token: set `CODEX_USAGE_INGEST_URL`, `CODEX_USAGE_SOURCE_NAME`, and `CODEX_USAGE_BEARER_TOKEN`; dry-run requires only a source name.
- Rejected endpoint: use HTTPS; plain HTTP is allowed only for `localhost` or a loopback IP during local tests.
- No usage events: verify `$CODEX_HOME/sessions` or `$CODEX_HOME/archived_sessions` contains JSONL logs.
- Unassigned explicit link or `HOOK_ERR_HMAC_KEY`: persist a high-entropy key meeting the minimum above, restart Codex, and send the link again in a new prompt.
- No summaries: verify the Hook is installed and trusted, both processes use the same queue, and summary mode is `local`.
- Duplicate events: keep retrying safely; the server must de-duplicate by `event_id`.
