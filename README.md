# token-audit

**English** | [Polski](README.pl.md)

A Claude Code skill that audits the token spend of your past Claude Code sessions in a project and recommends the levers that cut cost in future sessions: plugins and MCP servers you never use, sessions that grow too long, costly tool output, expensive subagents and more. Every recommendation comes with the numbers behind it and an estimated saving.

The audit reads only your own session transcripts under `~/.claude/projects/` on your machine. Nothing is sent anywhere, and the skill changes nothing until you pick a recommendation.

## Requirements

- Claude Code
- Python 3 on `PATH` (`python --version` should work)

## Install

### As a plugin (recommended)

In Claude Code, run:

```
/plugin marketplace add kolejniczak/token-audit
/plugin install token-audit@token-audit
```

Then restart Claude Code.

To update later, run `/plugin marketplace update token-audit`. To remove it, run `/plugin uninstall token-audit@token-audit`.

### For a whole team, through a project

To have Claude Code offer the plugin to everyone who opens a repository, add this to that repository's committed `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "token-audit": {
      "source": { "source": "github", "repo": "kolejniczak/token-audit" }
    }
  },
  "enabledPlugins": {
    "token-audit@token-audit": true
  }
}
```

### Manually, as a personal skill

Copy the folder `plugins/token-audit/skills/token-audit` to `~/.claude/skills/token-audit`. The skill is then called `/token-audit` instead of `/token-audit:token-audit`.

## Use

Open Claude Code in the project you want to audit and run the skill. It audits the sessions of the current project.

| Command | What it does |
|---|---|
| `/token-audit:token-audit --days 7` | Audits the last 7 days |
| `/token-audit:token-audit --since 2026-11-01 --until 2026-11-15` | Audits a date range (UTC; a bare `--until` date includes that whole day) |
| `/token-audit:token-audit --session <session-id>` | Audits a single session |
| `/token-audit:token-audit verify <lever> <date>` | Checks whether a change you applied on that date paid off, by comparing the periods before and after it |

You can add a focus in plain words, for example `/token-audit:token-audit --days 14 subagents`.

### What you get

1. **A report** of the period: totals and the cost split, models, the costliest sessions, startup context, tool output ranked by cost, exploration before the first edit, repeated reads, subagents, and the MCP servers and skills used.
2. **The cost drivers**, each with its cause.
3. **Recommendations** ranked by estimated saving, each with the evidence, the concrete change and the arithmetic behind the saving.

Nothing is applied until you choose a recommendation.

### Reading the numbers

Costs are given in **ITE** (input-token equivalents): each token class is weighted relative to an uncached input token (input 1, cache write 1.25 or 2, cache read 0.1, output 5). Treat ITE as a relative cost for comparing levers, not as a bill.

### Running the script on its own

The aggregator also works without Claude Code:

```
python plugins/token-audit/skills/token-audit/scripts/token_audit.py --cwd "<project path>" --days 7
```

Run it with `--help` to list all options.
