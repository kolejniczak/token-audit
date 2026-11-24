---
name: token-audit
description: Audit token usage across past Claude Code sessions for this project and recommend savings.
disable-model-invocation: true
argument-hint: "[--days N | --since DATE --until DATE] [--session ID] [verify <lever> <DATE>] [focus]"
---

You are auditing the token spend of past Claude Code sessions to find the **levers** that cut cost in future sessions. Every recommendation is backed by a number from the report or a quoted moment from a session log.

If the user asks to verify a lever they already applied, follow [Verify a lever](#verify-a-lever) instead of the steps.

## Steps

1. **Measure.** Run the aggregator from this skill's base directory, passing through any window or session the user gave:

   ```
   python "<skill base directory>/scripts/token_audit.py" --cwd "<project root>" --json "<scratchpad>/token-audit.json" [--days N | --since DATE --until DATE] [--session ID]
   ```

   It prints a Markdown report: totals, models, costliest sessions, startup overhead, tool results by carried cost, Bash command heads, exploration before the first edit per prompt, repeated Reads, subagents, MCP and Skill usage. `--help` lists the options. Done when the report is in front of you.

2. **Locate the drivers.** From the report, name the top cost drivers: the ITE split, the 2–3 costliest sessions, the tool rows and single results with the highest carried ITE, and the prompts with the largest exploration phase. For each driver, answer *why* using the JSON (`sessions[].tools`, `prompts`, `subagents`, `models`), queried with short Python one-liners. Session `.jsonl` files run to tens of MB: read them only through targeted extraction (one message, one tool call), never whole. Done when every driver has a cause, not just a size.

3. **Inspect the fixed overhead.** The startup context is paid on every turn of every session. Attribute it: the project and user `CLAUDE.md`, `MEMORY.md`, enabled plugins and MCP servers (`~/.claude/settings.json` `enabledPlugins`, project `.claude/settings*.json`, `.mcp.json`), and the descriptions of model-invoked skills. Cross-check against "MCP servers called" and "Skills invoked": a server or plugin that is loaded but never called is a candidate to disable for this project. Then read the always-loaded files sentence by sentence and flag each **no-op** (an instruction the model already follows by default) and each duplicate as a deletion candidate. Files reached only through a pointer cost nothing until read; leave them out. Done when every always-loaded source has a size and a list of its no-ops.

4. **Recommend.** List the candidates ranked by estimated ITE saved over the audited period. Give each one:
   - the evidence (the numbers, plus a session id and tool call where it applies),
   - the concrete change, placed per [Where the fix lands](#where-the-fix-lands),
   - the estimated saving, with the arithmetic, minus the cost of any always-loaded text the change adds.

   Before proposing a new script, flag or setting, check whether the repo or tool already has one (`package.json` scripts, the test runner's reporter options, per-project plugin settings, `--help` of the CLI in question). An existing lever the sessions never use is the finding: wire it in. When a recommendation includes text for a steering file (`CLAUDE.md`, a skill, a pointer), load the `writing-for-agents` skill and write that text to its rules.

   When a driver turns out to be a mistake loop (repeated failing runs, debugging a bug the agent introduced), name it, give its cost, and point to `/retro`, which fixes the mistake's cause. Stop at the proposal. Apply a change only once the user picks it.

## Verify a lever

Use this when the user names a lever and the date it was applied.

1. Run the aggregator twice over windows of comparable activity: `--until <DATE>` for before and `--since <DATE>` for after. Narrow with `--since` / `--until` so the two windows cover similar work. The filter applies to each API call's timestamp, so a session that spans the change is split exactly at it.
2. Compare the metrics the lever targets, normalized per session and per API call: startup context, carried ITE of the tool or command, exploration per prompt, subagent share. Raw totals track how much work was done, not how efficient it was.
3. Report whether the lever paid off, with the numbers. A lever that did not move its metric is a candidate to revert.

## Reference

### Reading the numbers

- **ITE** (input-token equivalents) prices each token class relative to an uncached input token: input 1, cache write 5m 1.25, cache write 1h 2, cache read 0.1, output 5. Treat it as relative cost, not a bill.
- **Carried ITE** of a tool result is its tokens × the later API calls in that thread before the next compaction × 0.1. A result taken early in a long session costs far more than its size. This is the primary ranking for tool output.
- **Exploration** is the run of Read/Glob/Grep and read-only shell commands between a user prompt (or a subagent's brief) and its first Edit/Write. A large phase that ends in an edit means the agent had to search for its target: the navigation lever. Read-only prompts are listed apart, since searching is their whole job.
- **Context window**: p90 and the share of calls above 100k/200k show how long a session ran with a large window; every one of those calls re-reads it. The **makeup at peak** says what filled it (estimated; a large *other* share is retained thinking and system reminders). **Carried into a new prompt** is context left over from earlier work; judge from the prompt text whether it began a new task, since only then would `/clear` have been free.
- **Cache reads** dominating the split means context size × turn count is the lever: shorter sessions, smaller results, earlier `/clear` between tasks.
- **Cache writes** high relative to reads means the prefix keeps getting rebuilt: idle gaps beyond the cache TTL, a changing system prompt, frequent model or effort switches, subagent spawns (each starts a fresh cache).
- **Output** high means verbose answers, large Write/Edit payloads, or long thinking.
- **Subagent share** is spend outside the main thread. A subagent pays its own startup context; for small lookups it costs more than doing the work inline.

### Where the fix lands

An always-loaded line costs its tokens on every turn of every session, so a fix that adds one pays for itself only if it saves more than that. In order of preference:

1. **The environment**: a project script or config change that makes the cheap path the default (a quiet test reporter, a check command that prints only failures, a plugin disabled for this project).
2. **A pointer** in a file the agent already reads, leading to material loaded only when needed.
3. **A new always-loaded line**, only when nothing above works, with its per-session cost subtracted in the estimate.

### Lever catalogue

Match each driver to a lever. These are starting points; the evidence decides.

| Driver | Lever |
|---|---|
| High startup context | Disable unused plugins/MCP servers per project; move `CLAUDE.md` content behind pointers; delete no-ops; prune `MEMORY.md` |
| Bash/Read results with high carried ITE | Narrower reads (`offset`/`limit`, `grep -n` then targeted `sed -n`), quieter test reporters, `\| tail` on long logs, a project script that prints only failures |
| Large exploration before the first edit | A navigation pointer from the ticket or recipe to the files it touches; a module map in the doc the agent reads first |
| Repeated full-file Reads | Navigation pointer to the right section; read once, then edit |
| Expensive subagent type | Do small lookups inline; give subagents tighter briefs and a narrower scope |
| New task started with a large carried context | `/clear` (or a new session) at task boundaries, carrying over a short written brief |
| Long sessions with high max ctx | `/clear` between tickets; hand off with a short written brief instead of carrying context |
| Opus on mechanical work | Route that work to a cheaper model (subagent `model`, `/model`) |
| Skill or doc read every session | Shrink it, or split by branch so each path loads only what it needs |
