#!/usr/bin/env python3
"""Aggregate token usage from Claude Code session logs into a Markdown report.

Reads ~/.claude/projects/<project>/*.jsonl (main threads) and
<session>/subagents/*.jsonl (subagents). Prints Markdown to stdout;
--json PATH also writes the raw aggregates.

Cost unit: ITE (input-token equivalents), weighting each token class by its
price relative to one uncached input token:
  input 1, cache write 5m 1.25, cache write 1h 2, cache read 0.1, output 5.
"""
import argparse
import collections
import datetime as dt
import glob
import json
import os
import re
import sys

W_IN, W_CW5, W_CW1H, W_CR, W_OUT = 1.0, 1.25, 2.0, 0.1, 5.0
CHARS_PER_TOKEN = 4


def project_dir_for(cwd):
    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", slug)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--project-dir", help="session log folder (default: derived from --cwd)")
    p.add_argument("--cwd", default=os.getcwd(), help="project path used to derive --project-dir")
    p.add_argument("--since", help="only API calls at or after this UTC date/time (YYYY-MM-DD[THH:MM])")
    p.add_argument("--until", help="only API calls before this UTC date/time; a bare date includes that whole day")
    p.add_argument("--days", type=float, help="shorthand for --since <now - N days>")
    p.add_argument("--session", action="append", help="only this session id (repeatable)")
    p.add_argument("--top", type=int, default=15, help="rows per ranking table")
    p.add_argument("--json", help="also write raw aggregates to this path")
    args = p.parse_args()
    if args.days and not args.since:
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)
        args.since = since.strftime("%Y-%m-%dT%H:%M:%S")
    if args.until and len(args.until) == 10:
        args.until += "T99"  # sorts after every timestamp on that day
    return args


class Window:
    """Timestamp filter; log timestamps are ISO-8601 UTC strings, so string order is time order."""

    def __init__(self, since, until):
        self.since, self.until = since, until

    def __contains__(self, ts):
        if not ts:
            return not (self.since or self.until)
        return (not self.since or ts >= self.since) and (not self.until or ts < self.until)


def records(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def result_chars(content):
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        n = 0
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    n += len(block.get("text", ""))
                elif block.get("type") == "image":
                    n += 6000  # ~1.5k tokens per image, rough
        return n
    return 0


def tool_family(name):
    if name.startswith("mcp__"):
        parts = name.split("__")
        return "mcp:" + (parts[1] if len(parts) > 1 else "?")
    return name


def summarize_input(name, inp):
    if not isinstance(inp, dict):
        return ""
    for key in ("file_path", "path", "pattern", "command", "skill", "description", "query", "url", "prompt"):
        if key in inp and inp[key]:
            return f"{key}={str(inp[key]).replace(chr(10), ' ')[:90]}"
    return json.dumps(inp)[:90]


class Usage:
    __slots__ = ("calls", "inp", "cw5", "cw1h", "cr", "out")

    def __init__(self):
        self.calls = self.inp = self.cw5 = self.cw1h = self.cr = self.out = 0

    def add(self, u):
        self.calls += 1
        self.inp += u.get("input_tokens") or 0
        cc = u.get("cache_creation") or {}
        cw1h = cc.get("ephemeral_1h_input_tokens")
        cw5 = cc.get("ephemeral_5m_input_tokens")
        if cw1h is None and cw5 is None:
            cw5, cw1h = u.get("cache_creation_input_tokens") or 0, 0
        self.cw5 += cw5 or 0
        self.cw1h += cw1h or 0
        self.cr += u.get("cache_read_input_tokens") or 0
        self.out += u.get("output_tokens") or 0

    def merge(self, o):
        for k in self.__slots__:
            setattr(self, k, getattr(self, k) + getattr(o, k))

    @property
    def ite(self):
        return self.inp * W_IN + self.cw5 * W_CW5 + self.cw1h * W_CW1H + self.cr * W_CR + self.out * W_OUT

    def as_dict(self):
        d = {k: getattr(self, k) for k in self.__slots__}
        d["ite"] = round(self.ite)
        return d


EXPLORE_TOOLS = {"Read", "Glob", "Grep"}
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
EXPLORE_CMDS = {"grep", "rg", "cat", "head", "tail", "ls", "find", "wc", "less", "Get-Content", "Select-String", "Get-ChildItem"}
GIT_EXPLORE = {"log", "show", "diff", "grep", "ls-files", "status", "blame"}


def command_head(command):
    command = re.sub(r"^(cd \S+? *(&&|;) *)+", "", command.strip())
    return command.split()


def classify(name, inp):
    """'explore', 'edit' or None for one tool call."""
    if name in EXPLORE_TOOLS:
        return "explore"
    if name in EDIT_TOOLS:
        return "edit"
    if name in ("Bash", "PowerShell") and isinstance(inp, dict):
        words = command_head(str(inp.get("command", "")))
        if not words:
            return None
        if words[0] == "sed":
            return "edit" if "-i" in words[1:3] else "explore"
        if words[0] in EXPLORE_CMDS:
            return "explore"
        if words[0] == "git" and len(words) > 1 and words[1] in GIT_EXPLORE:
            return "explore"
    return None


def prompt_text(d):
    """The user's own prompt text for a user record, or None if it is not one."""
    if d.get("isMeta"):
        return None
    content = (d.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        content = " ".join(texts)
    if not isinstance(content, str) or not content.strip():
        return None
    m = re.search(r"<command-name>(.*?)</command-name>", content)
    if m:
        args = re.search(r"<command-args>(.*?)</command-args>", content, re.S)
        return (m.group(1) + " " + (args.group(1) if args else "")).strip()
    stripped = content.lstrip()
    if stripped.startswith("<") and not stripped.startswith("<pasted_content"):
        return None  # caveats, task notifications, hook output
    return re.sub(r"<pasted_content[^>]*>.*?</pasted_content[^>]*>", "[paste]", content, flags=re.S).strip()


def new_prompt(kind, text, ts):
    return {"thread": kind, "text": text[:100].replace("\n", " "), "ts": ts,
            "explore_calls": 0, "tools": [], "edited": False}


def scan_thread(path, sess, kind, window):
    """Scan one jsonl thread; mutate the session aggregate."""
    seen_msgs = set()
    tool_uses = {}  # tool_use_id -> (name, input)
    in_window = []  # one bool per deduped API call
    boundaries = []  # call index at each compaction
    thread_tools = []
    prompts = []
    current = None  # the prompt whose exploration phase is open
    acc = {"tool": 0, "out": 0, "user": 0}  # what entered context since the last compaction (main thread)
    starts = []  # (prompt text, call index) for each main-thread prompt, for carry-over
    for d in records(path):
        t = d.get("type")
        ts = d.get("timestamp")
        if t == "system" and d.get("subtype") == "compact_boundary":
            boundaries.append(len(in_window))
            acc = {"tool": 0, "out": 0, "user": 0}
            if ts in window:
                sess["compactions"] += 1
        if t == "assistant":
            m = d.get("message") or {}
            model = m.get("model")
            for block in m.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_uses[block.get("id")] = (block.get("name", "?"), block.get("input"))
            mid = m.get("id")
            u = m.get("usage")
            if not u or model == "<synthetic>" or mid in seen_msgs:
                continue
            seen_msgs.add(mid)
            ok = ts in window
            in_window.append(ok)
            if not ok:
                continue
            sess["usage"][kind].add(u)
            sess["models"][model].add(u)
            ctx = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
            sess["max_ctx"] = max(sess["max_ctx"], ctx)
            if kind == "main":
                if len(in_window) == 1:
                    sess["startup_ctx"] = ctx
                sess["ctx"].append(ctx)
                sess["call_ctx"][len(in_window) - 1] = ctx
                if ctx >= (sess["peak"] or {}).get("ctx", -1):
                    sess["peak"] = {"ctx": ctx, "tool": acc["tool"] / CHARS_PER_TOKEN,
                                    "out": acc["out"], "user": acc["user"] / CHARS_PER_TOKEN}
                thinking = (u.get("output_tokens_details") or {}).get("thinking_tokens") or 0
                acc["out"] += max((u.get("output_tokens") or 0) - thinking, 0)  # past thinking is dropped
            if ts:
                sess["start"] = min(sess["start"] or ts, ts)
                sess["end"] = max(sess["end"] or ts, ts)
        elif t == "user":
            text = prompt_text(d)
            if text is not None:
                if kind == "main" and sess["first_prompt"] is None and ts in window:
                    sess["first_prompt"] = text[:100].replace("\n", " ")
                current = new_prompt(kind, text, ts)
                prompts.append(current)
                if kind == "main":
                    acc["user"] += len(text)
                    if ts in window:
                        starts.append((current["text"], len(in_window)))
                continue
            content = (d.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                name, inp = tool_uses.get(block.get("tool_use_id"), ("?", None))
                tool = {
                    "at": len(in_window), "ts": ts,
                    "name": name, "kind": kind, "chars": result_chars(block.get("content")),
                    "input": summarize_input(name, inp),
                    "file": inp.get("file_path") if isinstance(inp, dict) and name == "Read" else None,
                    "partial": isinstance(inp, dict) and bool(inp.get("offset") or inp.get("limit")),
                }
                thread_tools.append(tool)
                if kind == "main":
                    acc["tool"] += tool["chars"]
                if current is None:
                    current = new_prompt(kind, "(no prompt text)", ts)
                    prompts.append(current)
                if not current["edited"]:
                    role = classify(name, inp)
                    if role == "edit":
                        current["edited"] = True
                    elif role == "explore":
                        current["explore_calls"] += 1
                        current["tools"].append(tool)
    later = [0] * (len(in_window) + 1)  # later[i] = in-window calls at index >= i
    for i in range(len(in_window) - 1, -1, -1):
        later[i] = later[i + 1] + in_window[i]
    for tool in thread_tools:
        at = tool.pop("at")
        end = next((b for b in boundaries if b > at), len(in_window))
        tool["carried"] = tool["chars"] / CHARS_PER_TOKEN * (later[at] - later[end]) * W_CR
        if tool.pop("ts") in window:
            sess["tools"].append(tool)
    startup = sess["startup_ctx"] or 0
    for i, (text, at) in enumerate(starts):
        if i == 0 or at not in sess["call_ctx"]:
            continue  # the first prompt carries nothing over
        carried = max(sess["call_ctx"][at] - startup, 0)
        next_prompt = starts[i + 1][1] if i + 1 < len(starts) else len(in_window)
        end = min(next((b for b in boundaries if b > at), len(in_window)), next_prompt)
        sess["carry"].append({"session": sess["id"], "prompt": text, "ctx": sess["call_ctx"][at],
                              "carried": carried, "ite": carried * (later[at] - later[end]) * W_CR})
    for p in prompts:
        tools = p.pop("tools")
        if p["ts"] not in window or not p["explore_calls"]:
            continue
        p["explore_carried"] = sum(tl["carried"] for tl in tools)
        p["session"] = sess["id"]
        sess["prompts"].append(p)


def new_session(sid, path):
    return {
        "id": sid, "path": path, "start": None, "end": None, "first_prompt": None,
        "usage": {"main": Usage(), "sub": Usage()},
        "models": collections.defaultdict(Usage),
        "tools": [], "subagents": [], "prompts": [], "compactions": 0, "max_ctx": 0, "startup_ctx": None,
        "ctx": [], "call_ctx": {}, "peak": None, "carry": [],
    }


def load_sessions(args):
    pdir = args.project_dir or project_dir_for(args.cwd)
    if not os.path.isdir(pdir):
        sys.exit(f"No session folder: {pdir}")
    window = Window(args.since, args.until)
    since_epoch = None
    if args.since:
        since_epoch = dt.datetime.fromisoformat(args.since[:19]).replace(tzinfo=dt.timezone.utc).timestamp()
    sessions = []
    for path in sorted(glob.glob(os.path.join(pdir, "*.jsonl"))):
        sid = os.path.splitext(os.path.basename(path))[0]
        if args.session and sid not in args.session:
            continue
        if since_epoch and os.path.getmtime(path) < since_epoch:
            continue  # last written before the window opens
        sess = new_session(sid, path)
        scan_thread(path, sess, "main", window)
        for sub in sorted(glob.glob(os.path.join(pdir, sid, "subagents", "*.jsonl"))):
            before = sess["usage"]["sub"].ite
            scan_thread(sub, sess, "sub", window)
            delta = sess["usage"]["sub"].ite - before
            if not delta:
                continue
            meta = {}
            mpath = sub[:-len(".jsonl")] + ".meta.json"
            if os.path.exists(mpath):
                try:
                    with open(mpath, encoding="utf-8") as fh:
                        meta = json.load(fh)
                except (OSError, json.JSONDecodeError):
                    pass
            sess["subagents"].append({
                "type": meta.get("agentType", "?"),
                "description": meta.get("description", ""),
                "ite": delta,
            })
        if sess["usage"]["main"].calls or sess["usage"]["sub"].calls:
            sessions.append(sess)
    return pdir, sessions


def fmt(n):
    n = float(n)
    for unit, div in (("M", 1e6), ("k", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return f"{n:.0f}"


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in rows]
    return "\n".join(out)


def report(pdir, sessions, top, window):
    lines = []
    total = Usage()
    for s in sessions:
        total.merge(s["usage"]["main"])
        total.merge(s["usage"]["sub"])
    sub_ite = sum(s["usage"]["sub"].ite for s in sessions)
    lines.append(f"# Token audit\n\nSource: `{pdir}` — {len(sessions)} sessions. "
                 f"Window (UTC): {window.since or 'start'} → {(window.until or 'now').replace('T99', ' (whole day)')}. "
                 f"Cost unit ITE = input-token equivalents (in 1, cache-write 5m 1.25 / 1h 2, cache-read 0.1, out 5).\n")

    lines.append("## Totals\n")
    cache_total = total.cr + total.cw5 + total.cw1h + total.inp
    hit = total.cr / cache_total if cache_total else 0
    lines.append(table(["API calls", "input", "cache write 5m", "cache write 1h", "cache read", "output", "ITE", "cache hit", "subagent share"], [[
        fmt(total.calls), fmt(total.inp), fmt(total.cw5), fmt(total.cw1h), fmt(total.cr), fmt(total.out),
        fmt(total.ite), f"{hit:.0%}", f"{sub_ite / total.ite:.0%}" if total.ite else "-"]]))
    share = {
        "uncached input": total.inp * W_IN, "cache writes": total.cw5 * W_CW5 + total.cw1h * W_CW1H,
        "cache reads": total.cr * W_CR, "output": total.out * W_OUT,
    }
    lines.append("\nITE split: " + ", ".join(f"{k} {v / total.ite:.0%}" for k, v in share.items() if total.ite) + "\n")

    lines.append("## Models\n")
    models = collections.defaultdict(Usage)
    for s in sessions:
        for m, u in s["models"].items():
            models[m].merge(u)
    lines.append(table(["model", "calls", "output", "ITE", "share"], [
        [m, fmt(u.calls), fmt(u.out), fmt(u.ite), f"{u.ite / total.ite:.0%}"]
        for m, u in sorted(models.items(), key=lambda kv: -kv[1].ite)]) + "\n")

    lines.append(f"## Most expensive sessions (top {top})\n")
    ranked = sorted(sessions, key=lambda s: -(s["usage"]["main"].ite + s["usage"]["sub"].ite))
    lines.append(table(["session", "date", "first prompt", "calls", "ITE", "sub ITE", "max ctx", "startup ctx", "compactions"], [[
        s["id"][:8], (s["start"] or "")[:10], s["first_prompt"] or "-",
        s["usage"]["main"].calls + s["usage"]["sub"].calls,
        fmt(s["usage"]["main"].ite + s["usage"]["sub"].ite), fmt(s["usage"]["sub"].ite),
        fmt(s["max_ctx"]), fmt(s["startup_ctx"] or 0), s["compactions"]] for s in ranked[:top]]) + "\n")

    starts = sorted(s["startup_ctx"] for s in sessions if s["startup_ctx"])
    if starts:
        lines.append("## Startup overhead\n")
        lines.append(f"Context size at the first API call (system prompt, tool schemas, CLAUDE.md, memory, skill list): "
                     f"min {fmt(starts[0])}, median {fmt(starts[len(starts) // 2])}, max {fmt(starts[-1])}. "
                     f"Every turn re-reads this prefix.\n")

    lines.append(f"## Context window per session (main thread, top {top} by p90)\n")
    ctx_rows = []
    for s in sessions:
        c = sorted(s["ctx"])
        if not c:
            continue
        pct = lambda q: c[min(int(len(c) * q), len(c) - 1)]
        ctx_rows.append((pct(0.9), [s["id"][:8], len(c), fmt(pct(0.5)), fmt(pct(0.9)), fmt(c[-1]),
                                    f"{sum(x > 100_000 for x in c) / len(c):.0%}",
                                    f"{sum(x > 200_000 for x in c) / len(c):.0%}", s["compactions"]]))
    ctx_rows.sort(key=lambda r: -r[0])
    lines.append(table(["session", "calls", "median", "p90", "max", ">100k", ">200k", "compactions"],
                       [r[1] for r in ctx_rows[:top]]) + "\n")

    lines.append(f"## Context makeup at its peak (top {min(top, 5)} sessions by max ctx)\n")
    lines.append("Estimated since the last compaction: tool results and user text at ~4 chars/token, assistant output "
                 "without thinking; 'other' = system reminders, attachments, retained thinking and estimation error.\n")
    peaks = sorted((s for s in sessions if s["peak"]), key=lambda s: -s["peak"]["ctx"])[:min(top, 5)]
    mk = []
    for s in peaks:
        p, start = s["peak"], s["startup_ctx"] or 0
        other = max(p["ctx"] - start - p["tool"] - p["out"] - p["user"], 0)
        share = lambda v: f"{fmt(v)} ({v / p['ctx']:.0%})"
        mk.append([s["id"][:8], fmt(p["ctx"]), share(start), share(p["tool"]), share(p["out"]), share(p["user"]), share(other)])
    lines.append(table(["session", "peak ctx", "startup", "tool results", "assistant output", "user text", "other"], mk) + "\n")

    lines.append(f"## Context carried into a new prompt (top {top})\n")
    lines.append("Carried = context at the prompt's first call minus startup; ITE = carried × the calls this prompt made "
                 "(until the next prompt or compaction) × 0.1. High only matters when the prompt began a new task: "
                 "that is where /clear pays.\n")
    carry = sorted((c for s in sessions for c in s["carry"]), key=lambda c: -c["ite"])[:top]
    lines.append(table(["session", "prompt", "ctx at start", "carried", "carried ITE"], [
        [c["session"][:8], c["prompt"] or "-", fmt(c["ctx"]), fmt(c["carried"]), fmt(c["ite"])] for c in carry])
        if carry else "None.")
    lines.append("")

    tools = [t for s in sessions for t in s["tools"]]
    lines.append("## Tool results by tool\n")
    lines.append("Carried ITE = result tokens × later API calls in the same thread before the next compaction × 0.1 "
                 "(the cache-read cost of keeping the result in context).\n")
    by_tool = collections.defaultdict(lambda: [0, 0, 0, 0.0])
    for t in tools:
        agg = by_tool[tool_family(t["name"])]
        agg[0] += 1
        agg[1] += t["chars"]
        agg[2] = max(agg[2], t["chars"])
        agg[3] += t["carried"]
    all_carried = sum(v[3] for v in by_tool.values()) or 1
    lines.append(table(["tool", "calls", "result tokens ~", "avg", "max", "carried ITE", "carried share"], [
        [k, v[0], fmt(v[1] / CHARS_PER_TOKEN), fmt(v[1] / v[0] / CHARS_PER_TOKEN), fmt(v[2] / CHARS_PER_TOKEN),
         fmt(v[3]), f"{v[3] / all_carried:.0%}"]
        for k, v in sorted(by_tool.items(), key=lambda kv: -kv[1][3])[:top]]) + "\n")

    lines.append(f"## Costliest single tool results by carried ITE (top {top})\n")
    heavy = sorted(((t, s["id"]) for s in sessions for t in s["tools"]), key=lambda x: -x[0]["carried"])[:top]
    lines.append(table(["session", "thread", "tool", "tokens ~", "carried ITE", "input"], [
        [sid[:8], t["kind"], t["name"], fmt(t["chars"] / CHARS_PER_TOKEN), fmt(t["carried"]), t["input"]]
        for t, sid in heavy]) + "\n")

    lines.append(f"## Most frequent Bash commands by carried ITE (top {top})\n")
    by_cmd = collections.defaultdict(lambda: [0, 0.0])
    for t in tools:
        if t["name"] in ("Bash", "PowerShell") and t["input"].startswith("command="):
            words = re.sub(r"^(cd \S+? *(&&|;) *)+", "", t["input"][8:]).split()
            key = " ".join(words[:2]) if words else "?"
            by_cmd[key][0] += 1
            by_cmd[key][1] += t["carried"]
    lines.append(table(["command head", "calls", "carried ITE"], [
        [f"`{k}`", v[0], fmt(v[1])] for k, v in sorted(by_cmd.items(), key=lambda kv: -kv[1][1])[:top]]) + "\n")

    prompts = [p for s in sessions for p in s["prompts"]]
    lines.append(f"## Exploration before the first edit, per prompt (top {top})\n")
    lines.append("Exploration = Read/Glob/Grep and read-only shell commands (grep, sed -n, cat, git log/diff, ...) "
                 "between a prompt and its first Edit/Write. A large phase that ends in an edit points to a missing "
                 "navigation pointer. Subagent rows start at the subagent's brief.\n")
    for title, edited in (("Prompts that led to an edit", True), ("Read-only prompts (no edit followed)", False)):
        rows = sorted((p for p in prompts if p["edited"] == edited), key=lambda p: -p["explore_carried"])[:top]
        lines.append(f"**{title}**\n")
        lines.append(table(["session", "thread", "prompt", "explore calls", "carried ITE"], [
            [p["session"][:8], p["thread"], p["text"] or "-", p["explore_calls"], fmt(p["explore_carried"])]
            for p in rows]) if rows else "None.")
        lines.append("")

    lines.append("## Repeated full-file Reads (same file, same session)\n")
    rereads = []
    for s in sessions:
        c = collections.Counter(t["file"] for t in s["tools"] if t["file"] and not t["partial"])
        chars = collections.defaultdict(int)
        for t in s["tools"]:
            if t["file"] and not t["partial"]:
                chars[t["file"]] += t["chars"]
        rereads += [(s["id"], f, n, chars[f]) for f, n in c.items() if n > 1]
    rereads.sort(key=lambda r: -r[3])
    lines.append(table(["session", "file", "reads", "tokens ~"], [
        [sid[:8], f, n, fmt(ch / CHARS_PER_TOKEN)] for sid, f, n, ch in rereads[:top]]) if rereads else "None.")
    lines.append("")

    lines.append("## Subagents by type\n")
    by_type = collections.defaultdict(lambda: [0, 0.0])
    for s in sessions:
        for a in s["subagents"]:
            by_type[a["type"]][0] += 1
            by_type[a["type"]][1] += a["ite"]
    lines.append(table(["agent type", "runs", "ITE", "avg ITE"], [
        [k, v[0], fmt(v[1]), fmt(v[1] / v[0])] for k, v in sorted(by_type.items(), key=lambda kv: -kv[1][1])[:top]])
        if by_type else "No subagents.")
    lines.append("")

    lines.append("## MCP and Skill usage\n")
    fam = collections.Counter(tool_family(t["name"]) for t in tools)
    mcp = {k: v for k, v in fam.items() if k.startswith("mcp:")}
    lines.append("MCP servers called: " + (", ".join(f"{k[4:]} ×{v}" for k, v in sorted(mcp.items(), key=lambda kv: -kv[1])) or "none") +
                 ". Servers that are connected but absent here still cost startup context.\n")
    skills = collections.Counter(t["input"].split("=", 1)[1] for t in tools if t["name"] == "Skill" and t["input"].startswith("skill="))
    lines.append("Skills invoked: " + (", ".join(f"{k} ×{v}" for k, v in skills.most_common()) or "none") + "\n")
    return "\n".join(lines)


def to_json(pdir, sessions):
    return {
        "project_dir": pdir,
        "sessions": [{
            "id": s["id"], "start": s["start"], "end": s["end"], "first_prompt": s["first_prompt"],
            "main": s["usage"]["main"].as_dict(), "sub": s["usage"]["sub"].as_dict(),
            "models": {m: u.as_dict() for m, u in s["models"].items()},
            "max_ctx": s["max_ctx"], "startup_ctx": s["startup_ctx"], "compactions": s["compactions"],
            "subagents": s["subagents"],
            "prompts": s["prompts"],
            "tools": [{k: t[k] for k in ("name", "kind", "chars", "carried", "input")} for t in s["tools"]],
        } for s in sessions],
    }


def main():
    args = parse_args()
    pdir, sessions = load_sessions(args)
    if not sessions:
        sys.exit("No sessions with usage data matched.")
    sys.stdout.reconfigure(encoding="utf-8")
    print(report(pdir, sessions, args.top, Window(args.since, args.until)))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(to_json(pdir, sessions), fh, indent=1)


if __name__ == "__main__":
    main()
