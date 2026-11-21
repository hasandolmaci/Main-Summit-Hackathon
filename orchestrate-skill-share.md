# "Orchestrate" skill for Claude Code: share version

## What it does (summary)

`/orchestrate` turns Claude Code into a **team lead that splits a task across several AI models**: Claude models (Opus, Haiku, Fable) and OpenAI models run through the Codex app (GPT-6 Luna, Sol, Astra).

| Task kind | Who does it |
|---|---|
| Planning and checking results (always) | **Claude Opus** (the main session) |
| Simple data cleaning | **GPT-6-Luna or Claude Haiku**, whichever provider has more usage left (it checks both) |
| Intermediate backend work | **GPT-6.1-Sol** |
| Advanced tasks | **GPT-6-Astra** |
| Images and anything visual | **GPT-6-Astra** |
| Hard or uncertain decisions | **Claude Fable + GPT-6-Astra in parallel** with the same brief; Opus compares them and makes the final call |

How it works:
1. **Plan:** Opus breaks the task into pieces and tells you which model gets which piece.
2. **Dispatch:** each sub-agent gets a self-contained brief; independent pieces run in parallel.
3. **Control:** Opus checks every result. A wrong result is sent back, or moved up one level (Luna/Haiku → Sol → Astra).
4. **Report:** what was done, by which model, and what wasn't verified.

Safety: Codex runs read-only by default, and file edits are only allowed inside the project folder. Two agents never edit the same file at the same time.

## Requirements

- **Windows**, with the **Codex desktop app** (Microsoft Store) installed and signed in. The skill uses the `codex.exe` bundled inside it.
- **Claude Code** with a plan that can use the Opus, Haiku and Fable models. If Fable isn't available on your plan, the skill falls back to Opus.
- The model names below are the ones available in Nov 2026. If yours differ, edit the table.

## How to install

Copy **everything inside the box below** and paste it into Claude Code as one message. Claude will create the 3 files. Then type `/orchestrate <your task>`, or just say "use orchestrate for this".

~~~~text
Please install two Claude Code skills for me by creating these 3 files with EXACTLY the content given (create folders as needed, use my home directory ~ = %USERPROFILE%). Don't change the content. When done, list the files you created.

=== FILE 1: ~/.claude/skills/codex/SKILL.md ===
---
name: codex
description: Ask OpenAI Codex a question or give it a task, using the codex.exe bundled inside the user's Codex desktop app (Microsoft Store). Use whenever the user says "use codex", "ask codex", "have codex do/check/review X", or asks for a second opinion from Codex/GPT. Supports choosing the model and reasoning effort.
---

# Using Codex

The user has the Codex desktop app (Microsoft Store package `OpenAI.Codex`). It ships a CLI,
`codex.exe`, which is NOT on PATH. It is logged in with the user's account (`~/.codex/auth.json`),
so no login is needed.

## 1. Find codex.exe (path changes on every app update — always resolve it, never hardcode)

PowerShell:

```powershell
$codex = Join-Path (Get-AppxPackage OpenAI.Codex).InstallLocation 'app\resources\codex.exe'
```

If `Get-AppxPackage OpenAI.Codex` returns nothing, tell the user the Codex app isn't installed.

## 2. Run a non-interactive prompt

```powershell
'' | & $codex exec --skip-git-repo-check --sandbox read-only -m <MODEL> -c model_reasoning_effort=<EFFORT> "<PROMPT>"
```

- The leading `'' |` gives Codex empty stdin so it never waits for extra input.
- Use a timeout of at least 300000 ms; for long tasks run in the background.
- Add `-C <dir>` to run Codex in a specific project folder.
- Add `-o <file>` to save only Codex's final answer to a file (handy for long replies).
- Add `-c tools.web_search=true` when the task needs web research.
- Omit `-m` / `-c model_reasoning_effort` to use the user's defaults from `~/.codex/config.toml`.

### Sandbox
- Default to `--sandbox read-only` (questions, reviews, explanations).
- Use `--sandbox workspace-write` only when the user explicitly wants Codex to change files, and
  always pair it with `-C <the project folder>`.
- Never use `danger-full-access` unless the user explicitly asks for it.

## 3. Models

Available models are listed in `~/.codex/models_cache.json` (`.models[]`, entries with
`visibility = "list"`). Check it if the user asks what's available or names an unknown model:

```powershell
(Get-Content "$env:USERPROFILE\.codex\models_cache.json" -Raw | ConvertFrom-Json).models |
  Where-Object visibility -eq 'list' | Select-Object slug, display_name
```

Known at time of writing: `gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-astra`, `gpt-6-sol`,
`gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`.

Reasoning effort values: `minimal`, `low`, `medium`, `high`.

## 4. Report back

The output starts with a header (version, workdir, model, session id) and ends with
`codex` + the answer, followed by `tokens used`. Show the user Codex's answer (quoted), and mention
the model used. Don't dump the header. Note that `exec` runs don't show up as chats in the
Codex app window.

=== FILE 2: ~/.claude/skills/orchestrate/SKILL.md ===
---
name: orchestrate
description: The user's multi-model agent orchestration policy. Decides which model (Claude Opus/Haiku/Fable or Codex GPT Luna/Sol/Astra) handles each part of a task, and how to run it. Use when the user says "orchestrate", "use the orchestration skill", "use agents/models for this", or asks to split work across Claude and GPT/Codex models.
---

# Agent orchestration policy

When this skill is invoked, the user has explicitly asked for sub-agents, so spawning Claude
sub-agents (Agent tool) and running Codex (via the `codex` skill) is authorized for this task.

## Roles at a glance

| Task kind | Who does it | How to run it |
|---|---|---|
| Control & planning (always) | **Claude Opus 5.5** | You, the main session. If this session isn't on Opus, do planning via `Agent` with `model: "opus"`. |
| Simple data cleaning | **GPT-6-Luna** *or* **Claude Haiku 5.5**, whichever provider has more usage left | Codex `-m gpt-6-luna`, or `Agent` with `model: "haiku"` |
| Intermediate backend work | **GPT-6.1-Sol** | Codex `-m gpt-6.1-sol` |
| Advanced tasks | **GPT-6-Astra** | Codex `-m gpt-6-astra` |
| Anything visual (esp. image generation) | **GPT-6-Astra** as a sub-agent | Codex `-m gpt-6-astra`, `--sandbox workspace-write` |
| Everything else, especially uncertain technical decisions or hard architecture choices | **Claude Fable 5.2 and GPT-6-Astra in parallel**, then you decide | `Agent` with `model: "fable"` + Codex `-m gpt-6-astra`, same brief |

Codex is run exactly as the `codex` skill describes (resolve `codex.exe` from the
`OpenAI.Codex` Appx package; `'' | & $codex exec --skip-git-repo-check ...`). Load that skill
before the first Codex call.

If a Fable sub-agent fails (for example "requires usage credits"), rerun the same brief with
`model: "opus"` and tell the user in one line.

## Workflow

1. **Plan (Opus).** Break the request into pieces and label each one with a category from the
   table. Tell the user the split in a few lines: piece → model. Pieces that don't depend on each
   other run in parallel.
2. **Dispatch.** Give every sub-agent a self-contained brief: goal, exact files/folder, input
   and output format, constraints, what "done" looks like. Sub-agents can't see this
   conversation.
3. **Control (Opus).** Check every result yourself before using it: read the diff/output, run the
   tests or a quick sanity check. If a result is wrong, send it back with specific feedback, or
   escalate one level (Luna/Haiku → Sol → Astra).
4. **Report.** Tell the user what was done, which model did each piece, and anything not verified.

## Category guidance

- **Simple data cleaning:** reformatting, deduping, normalizing columns, fixing encodings, CSV/JSON
  tidying, regex-level transforms. No design decisions.
- **Intermediate backend:** a typical endpoint, a DB query/migration, a bug fix in one module,
  a small service or script with clear requirements.
- **Advanced:** changes across many files, performance/concurrency work, security-sensitive code,
  complex algorithms, tricky debugging.
- **Uncertain / architectural:** "which approach/library/schema should we use?", trade-offs with
  no clear answer, or when you notice you're unsure. This goes to the Fable + Astra double check.

If a piece could fit two categories, choose the higher one.

## Choosing Luna vs Haiku (data cleaning only)

Check both providers right before you dispatch (not once per session):

- **Claude:** call `mcp__ccd_session_mgmt__get_usage` (load it with ToolSearch if it's deferred).
  Claude remaining = 100 − the highest `percentUsed` among the "5-hour limit" and "Weekly · all
  models" windows. If that tool isn't available, treat Claude as `unknown`.
- **Codex:** run `powershell -NoProfile -File "$env:USERPROFILE\.claude\skills\orchestrate\codex-usage.ps1"`.
  Use `percentRemaining`. This reading comes from the last Codex run (`readingTakenAt`), so it can
  be a bit old. Windows whose reset time has passed already count as 0% used.
- Choose the provider with the **higher remaining percentage**. If they're equal, or Codex is
  `unknown`, choose Haiku. If one provider has `limitReached` or is at 0%, use the other.
- Tell the user in one line which provider you chose and why (e.g. "Haiku: Claude 84% left vs Codex 60%").

## Visual content / image generation (GPT-6-Astra)

- Run Codex with `-m gpt-6-astra --sandbox workspace-write -C <output folder>` and ask it to
  generate the image and **save it into that folder with a specific filename**.
- Codex also keeps its own copy under `~/.codex/generated_images/<session-id>/`. If the file isn't
  in the folder you named, look there (the session id is printed in the Codex output header).
- Check the image (open it with Read) before you hand it to the user.

## Dual opinion for uncertain decisions (Fable + Astra)

1. Write **one** brief: the context, the decision to make, the options you know of, constraints,
   and ask for a recommendation with reasons, risks and a confidence level.
2. Send the same brief to both, in parallel: `Agent` (`model: "fable"`), and Codex
   `-m gpt-6-astra -c model_reasoning_effort=high --sandbox read-only` (add `-C <project>` so it
   can read the code).
3. Compare: where they agree, where they disagree, and the reasons behind each.
4. **You (Opus) make the final call.** Tell the user both recommendations in short form, your
   decision, and why. If they disagree on something risky or irreversible, give the user the
   choice instead of deciding.

## Sandbox and safety

- Codex runs `read-only` by default. Use `workspace-write` only for pieces that must change files,
  and always pair it with `-C <that project folder>`. Never use `danger-full-access`.
- Two writers must never edit the same files at the same time. Run them one after another, or
  give them separate files.

=== FILE 3: ~/.claude/skills/orchestrate/codex-usage.ps1 ===
# Prints the user's latest known Codex (ChatGPT plan) rate-limit usage as JSON.
# Source: the newest ~/.codex/sessions/**/rollout-*.jsonl that contains a rate_limits event.
# A window whose reset time has already passed is reported as 0% used.
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:USERPROFILE '.codex\sessions'
$now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$files = Get-ChildItem $root -Recurse -Filter 'rollout-*.jsonl' -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending | Select-Object -First 20
foreach ($f in $files) {
  $hit = Select-String -Path $f.FullName -Pattern '"rate_limits":\{' | Select-Object -Last 1
  if (-not $hit) { continue }
  $rl = ($hit.Line | ConvertFrom-Json).payload.rate_limits
  $win = foreach ($w in @(@{n='5-hour'; d=$rl.primary}, @{n='weekly'; d=$rl.secondary})) {
    if (-not $w.d) { continue }
    $used = if ($w.d.resets_at -and $w.d.resets_at -lt $now) { 0 } else { [double]$w.d.used_percent }
    [pscustomobject]@{ window = $w.n; percentUsed = $used
      resetsAt = [DateTimeOffset]::FromUnixTimeSeconds($w.d.resets_at).ToLocalTime().ToString('yyyy-MM-dd HH:mm') }
  }
  $maxUsed = ($win | Measure-Object percentUsed -Maximum).Maximum
  [pscustomobject]@{
    provider = 'codex'; plan = $rl.plan_type; windows = $win
    percentRemaining = 100 - $maxUsed
    limitReached = [bool]$rl.rate_limit_reached_type
    readingTakenAt = $f.LastWriteTime.ToString('yyyy-MM-dd HH:mm')
  } | ConvertTo-Json -Depth 4
  exit 0
}
Write-Output '{"provider":"codex","status":"unknown","note":"No Codex run with rate-limit data found yet."}'
~~~~
