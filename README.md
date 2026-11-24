# token-audit

A Claude Code skill that audits the token spend of your past Claude Code sessions and recommends the levers that cut cost in future sessions. It reads only your own session transcripts under `~/.claude/projects/`.

## Install

```
/plugin marketplace add kolejniczak/token-audit
/plugin install token-audit@token-audit
```

## Use

```
/token-audit:token-audit --days 7
```

Requires Python 3 on `PATH`.
