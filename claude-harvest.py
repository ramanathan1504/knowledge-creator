#!/usr/bin/env python3
"""
claude-harvest.py — pull Claude knowledge into the DEVONthink base, the same
way aistudio-extract.py does for Google AI Studio.

Four sources, all local:

  1. claude.ai data export   ~/Downloads/data-*batch-*/conversations.json
     Full web conversations. NOTE this is a point-in-time snapshot: anything
     after the export date is simply not in it. Request a fresh one from
     claude.ai > Settings > Privacy > Export data and drop the zip in
     Downloads; this script always uses the newest export it finds.
  2. claude.ai memories      memories.json from the same bundle
  3. Claude Code sessions    ~/.claude/projects/*/*.jsonl
     Only recent sessions survive locally; older ones are cleaned up.
  4. Prompt history          ~/.claude/history.jsonl

  ./claude-harvest.py            # dry run
  ./claude-harvest.py --apply

Claude Code transcripts are filtered, not dumped. A session is mostly tool
traffic: raw tool results would bury the reasoning under thousands of lines of
build output. Tool calls become one summary line each, results are capped, and
the prose survives intact.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
import kbpaths
from kbpaths import ARCHIVE as DEVON
from kbpaths import CLAUDE_EXPORT
PROJ = DEVON / "Projects"
PERS = DEVON / "Personal/claude"

# Topic first, provenance second: Projects/log4j/claude-web/. The source still
# matters — a claude-web note is the reasoning, an oss-github note is what was
# actually said on the thread — but it is a qualifier, not the filing key.
def out_dir(topic: str, source: str) -> Path:
    return PROJ / topic / source
NOW = datetime.now(timezone.utc)
APPLY = "--apply" in sys.argv

TOOL_RESULT_CAP = 800          # chars of any single tool result to keep

# ---------------------------------------------------------------- redaction --
REDACTIONS = [
    ("aws-access-key",   re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token",     re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}")),
    ("google-api-key",   re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack-token",      re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private-key",      re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    # `(?!\$\{)` skips template references -- ${DB_PASSWORD}, Log4j's
    # ${secure:sys:...} -- which are pointers to a secret, not the secret.
    ("password",         re.compile(r"(\bpass(?:word|wd)\s*[=:]\s*['\"]?)(?!\$\{)[^\s'\"]{8,}", re.I)),
    ("bearer-token",     re.compile(r"(\bBearer\s+)[A-Za-z0-9._\-]{25,}")),
    ("jdbc-credentials", re.compile(r"((?:jdbc:)?[a-z][a-z0-9+.\-]*://[^\s:/@]+:)[^\s@/]+(@)")),
]
counts = {}


def redact(text):
    if not text:
        return text or ""
    for label, rx in REDACTIONS:
        def sub(m):
            counts[label] = counts.get(label, 0) + 1
            g = [x for x in m.groups() if x is not None]
            if len(g) >= 2:
                return f"{g[0]}[REDACTED:{label}]{g[1]}"
            if len(g) == 1:
                return f"{g[0]}[REDACTED:{label}]"
            return f"[REDACTED:{label}]"
        text = rx.sub(sub, text)
    return text


# ------------------------------------------------------------ classification --
# Widened after the 63-conversation export arrived: the first pass filed
# "job applications at NatWest…", "career transition to remote contract work"
# and "from cricket dreams to open source" under log4j, purely because their
# bodies mention Apache. Titles here are descriptive, so match them first.
PERSONAL = re.compile(
    r"llr\b|driving licen|visa\b|refund|recruit|resume|salary|workout|"
    r"job opening|job application|linkedin|stripped screw|broken screen|"
    r"career transition|remote contract|interview|achievement goal|cricket|"
    r"sender address|gmail|personal journey|dreams", re.I)

TOPICS = [
    ("log4j",         r"log4j|logback|slf4j|patternlayout|jsontemplatelayout|\bmdc\b|appender"),
    ("spring",        r"\bspring\b|@autowired|@bean|servlet|spring boot"),
    ("kafka",         r"kafka|confluent|debezium"),
    ("aws-infra",     r"\baws\b|lambda|\bec2\b|\beks\b|kubernetes|websocket"),
    ("databases",     r"postgres|mysql|\bjdbc\b|\bsql\b|redis"),
    ("apache-process", r"\basf\b|apache|committer|\bpmc\b|release vote"),
    ("java",          r"\bjava\b|\bjvm\b|hashmap|collection|generics|stream api"),
    ("tooling",       r"intellij|datagrip|\bide\b|maven|gradle|\bgit\b"),
    ("career",        r"career|interview|growth|journey|mentor"),
]


def classify(title, body):
    """Title outranks body for the personal/technical call.

    Matching PERSONAL anywhere in the body misfiled the 799-message
    "From first PR to Apache committer in 8 months" thread as personal, on the
    strength of one passing mention of a CV. A long technical conversation
    will brush against career words; that does not make it a personal note.
    So: personal only if the TITLE says so, or if the body says so repeatedly
    AND nothing technical is competing.
    """
    low_title = title.lower()
    hay = f"{title} {body[:8000]}".lower()

    tech_topic = next((name for name, pat in TOPICS if re.search(pat, hay)), None)

    if PERSONAL.search(low_title):
        return "personal", "personal"
    if len(PERSONAL.findall(hay)) >= 3 and tech_topic in (None, "career"):
        return "personal", "personal"
    return "technical", tech_topic or "misc"


def slug(text, n=70):
    s = re.sub(r"[^\w\s-]", "", (text or "").lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s[:n].rstrip("-") or "untitled"


CAMEL = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z0-9]+)+)\b")
TICKED = re.compile(r"`([\w.$#()]{3,40})`")
GHURL = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)")
HASHN = re.compile(r"(?:^|[\s(\[])#(\d{3,5})\b")


def keywords(title, text, topic, extra=()):
    tags = {topic, "claude"} | set(extra)
    for m in CAMEL.finditer(f"{title} {text}"):
        tags.add(m.group(1))
    for m in TICKED.finditer(text):
        tags.add(m.group(1).split(".")[-1].split("(")[0])
    clean = [re.sub(r"[^\w.-]", "-", str(t)).strip("-") for t in tags]
    clean = [t for t in clean if 2 < len(t) <= 40]
    return sorted(set(clean), key=lambda t: (-text.count(t), t.lower()))[:26]


def gh_refs(text):
    r = {f"{o}#{n}" for o, n in GHURL.findall(text)} | {"#" + n for n in HASHN.findall(text)}
    return sorted(r)[:30]


def header(title, topic, tags, refs, meta, source):
    return (
        "---\n"
        f"tags: [{', '.join(tags)}]\n"
        f"topic: {topic}\n"
        f"github: {' · '.join(refs) if refs else 'none identified'}\n"
        f"source: {source}\n{meta}"
        f"extracted: {NOW.isoformat(timespec='seconds')}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"**Search Tags/Keywords:** {' '.join('#' + t for t in tags)}\n\n"
        f"**GitHub Context:** {' · '.join(refs) if refs else 'none identified'}\n\n"
        f"**Source:** {source}\n\n---\n\n")


def quote(text):
    return "\n".join("> " + ln for ln in (text or "").rstrip().split("\n"))


# ------------------------------------------------------------- claude.ai web --
def newest_export():
    """Any conversations.json under Downloads, newest first.

    Deliberately not tied to the `data-<uuid>-batch-NNNN` name Anthropic ships:
    that folder gets renamed or tidied (this one became `claude-export`), and a
    hard-coded glob would silently find nothing and report an empty harvest.
    Sorted by the newest CONTENT date rather than mtime, because unzipping can
    leave misleading timestamps — this bundle carries a 1980 mtime.
    """
    # Where to look depends on whether the user has declared a sources root.
    #
    # KB_SOURCES set  -> look ONLY there. Declaring a folder and then silently
    #                    harvesting from Downloads as well would mean the tool
    #                    ignores the one thing it asked the user to configure.
    # KB_SOURCES unset-> Downloads, which is where a browser drops the zip, so
    #                    the common case still needs no configuration at all.
    #
    # A sandbox test caught this: with KB_SOURCES pointed at a temp folder the
    # function still globbed Downloads exclusively and harvested the real export.
    if kbpaths.SOURCES:
        roots = [CLAUDE_EXPORT] if CLAUDE_EXPORT.is_dir() else []
    else:
        roots = [HOME / "Downloads"]

    cands = []
    for p in [q for root in roots if root.is_dir() for q in root.glob("**/conversations.json")]:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            latest = max((c.get("created_at", "") for c in d), default="")
            cands.append((latest, len(d), p))
        except Exception:
            continue
    if not cands:
        return None
    cands.sort(reverse=True)
    return cands[0][2]


def msg_text(m):
    if m.get("text"):
        return m["text"]
    out = []
    for blk in (m.get("content") or []):
        if isinstance(blk, dict):
            if blk.get("type") == "text":
                out.append(blk.get("text", ""))
            elif blk.get("type") == "thinking":
                out.append("_(thinking)_\n" + blk.get("thinking", ""))
    return "\n".join(out)


def render_web(conv):
    title = conv.get("name") or "(untitled)"
    msgs = conv.get("chat_messages") or []
    body = "\n".join(msg_text(m) for m in msgs)
    kind, topic = classify(title, body)
    tags = keywords(title, body, topic, extra=("claude-ai", "conversation"))
    meta = (f"date: {conv.get('created_at','')[:10]}\n"
            f"messages: {len(msgs)}\n")
    L = [header(title, topic, tags, gh_refs(body), meta,
                f"claude.ai conversation `{title}`")]
    A = L.append
    first = next((msg_text(m) for m in msgs if m.get("sender") == "human"), "")
    A("## The Problem (What & Where)\n\n" + redact(first).strip() + "\n")
    A(f"\n## The \"Why\" (Review Discussions)\n\n_{len(msgs)} messages, "
      f"{conv.get('created_at','')[:10]} to {conv.get('updated_at','')[:10]}. "
      f"Topic `{topic}`._\n")
    if conv.get("summary"):
        A("\n" + redact(conv["summary"]) + "\n")
    A("\n## The Solution (How)\n")
    qn = 0
    for m in msgs:
        t = redact(msg_text(m)).rstrip()
        if not t:
            continue
        stamp = (m.get("created_at") or "")[:19].replace("T", " ")
        if m.get("sender") == "human":
            qn += 1
            A(f"\n### Q{qn} — {stamp}\n\n" + quote(t) + "\n")
        else:
            A(f"\n**Claude** — {stamp}\n\n" + t + "\n")
    return "".join(L), kind, topic, title


# ---------------------------------------------------------- claude code CLI --
def render_session(path):
    recs = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            continue

    turns = [r for r in recs if r.get("type") in ("user", "assistant")
             and isinstance(r.get("message"), dict)]
    if not turns:
        return None
    cwd = next((r.get("cwd") for r in recs if r.get("cwd")), "?")
    branch = next((r.get("gitBranch") for r in recs if r.get("gitBranch")), "")
    stamps = [r.get("timestamp", "") for r in recs if r.get("timestamp")]
    title = next((r.get("title") for r in recs if r.get("type") == "ai-title" and r.get("title")),
                 None) or f"Claude Code session — {Path(cwd).name} {stamps[0][:10] if stamps else ''}"

    parts, tools = [], []
    for r in turns:
        role = r["message"].get("role")
        side = r.get("isSidechain")
        c = r["message"].get("content")
        blocks = c if isinstance(c, list) else [{"type": "text", "text": c}]
        for b in blocks:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text" and (b.get("text") or "").strip():
                parts.append((role, side, b["text"]))
            elif bt == "thinking" and (b.get("thinking") or "").strip():
                parts.append(("thinking", side, b["thinking"]))
            elif bt == "tool_use":
                name = b.get("name", "?")
                inp = json.dumps(b.get("input", {}))[:160]
                tools.append(name)
                parts.append(("tool", side, f"`{name}` {inp}"))
            elif bt == "tool_result":
                cc = b.get("content")
                txt = cc if isinstance(cc, str) else json.dumps(cc)[:TOOL_RESULT_CAP]
                parts.append(("result", side, (txt or "")[:TOOL_RESULT_CAP]))

    body = "\n".join(p[2] for p in parts if p[0] in ("user", "assistant"))
    kind, topic = classify(title, body)
    from collections import Counter
    tc = Counter(tools)
    tags = keywords(title, body, topic,
                    extra=("claude-code", "session", Path(cwd).name))
    meta = (f"date: {stamps[0][:10] if stamps else 'unknown'}\n"
            f"cwd: {cwd}\n" + (f"branch: {branch}\n" if branch else "")
            + f"turns: {len(turns)}\ntool_calls: {len(tools)}\n")

    L = [header(title, topic, tags, gh_refs(body), meta,
                f"Claude Code session `{path.name}`")]
    A = L.append
    first = next((t for r, s, t in parts if r == "user"), "")
    A("## The Problem (What & Where)\n\n" + redact(first).strip() + "\n")
    A(f"\n## The \"Why\" (Review Discussions)\n\n"
      f"_Working directory `{cwd}`"
      + (f", branch `{branch}`" if branch else "")
      + f". {len(turns)} turns, {len(tools)} tool calls._\n\n")
    if tc:
        A("**Tools used:** " + ", ".join(f"`{k}`×{v}" for k, v in tc.most_common(12)) + "\n")
    A("\n## The Solution (How)\n")
    qn = 0
    for role, side, text in parts:
        text = redact(text).rstrip()
        if not text:
            continue
        tagsfx = " _(subagent)_" if side else ""
        if role == "user":
            qn += 1
            A(f"\n### Q{qn}{tagsfx}\n\n" + quote(text) + "\n")
        elif role == "assistant":
            A(f"\n**Claude**{tagsfx}\n\n" + text + "\n")
        elif role == "thinking":
            A("\n<details><summary>reasoning</summary>\n\n" + text + "\n\n</details>\n")
        elif role == "tool":
            A(f"\n- 🔧 {text}\n")
        else:
            A(f"\n<details><summary>tool result</summary>\n\n```\n{text}\n```\n\n</details>\n")
    return "".join(L), kind, topic, title


# --------------------------------------------------------------------- main --
def main():
    exp = newest_export()
    convs = []
    if exp:
        try:
            convs = json.loads(exp.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"could not read export: {e}")
    sessions = sorted(HOME.glob(".claude/projects/*/*.jsonl"))

    print(f"{'APPLY' if APPLY else 'DRY RUN'}\n")
    if exp:
        dates = sorted(c.get("created_at", "")[:10] for c in convs if c.get("created_at"))
        print(f"  claude.ai export : {exp.parent.name}")
        print(f"    conversations  : {len(convs)}  "
              f"({sum(len(c.get('chat_messages') or []) for c in convs)} messages)")
        print(f"    covers         : {dates[0]} .. {dates[-1]}")
        print(f"    !! anything after {dates[-1]} is NOT in this export")
    else:
        print("  claude.ai export : none found in ~/Downloads")
    print(f"  Claude Code      : {len(sessions)} session files")

    if not APPLY:
        print("\nNothing written. Re-run with --apply.")
        return

    PERS.mkdir(parents=True, exist_ok=True)

    n = 0
    for c in convs:
        try:
            md, kind, topic, title = render_web(c)
            root = PERS if kind == "personal" else out_dir(topic, "claude-web")
            root.mkdir(parents=True, exist_ok=True)
            (root / f"{slug(title)}.md").write_text(md, encoding="utf-8")
            n += 1
        except Exception as e:
            print(f"  FAILED web {c.get('name')}: {str(e)[:90]}")

    for s in sessions:
        try:
            out = render_session(s)
            if not out:
                continue
            md, kind, topic, title = out
            root = PERS if kind == "personal" else out_dir(topic, "claude-code")
            root.mkdir(parents=True, exist_ok=True)
            (root / f"{slug(title)}-{s.stem[:8]}.md").write_text(md, encoding="utf-8")
            n += 1
        except Exception as e:
            print(f"  FAILED session {s.name}: {str(e)[:90]}")

    # claude.ai memories, if present
    if exp and (exp.parent / "memories.json").exists():
        try:
            mem = json.loads((exp.parent / "memories.json").read_text(encoding="utf-8"))
            text = redact(json.dumps(mem, indent=2))
            tags = keywords("Claude memories", text, "misc", extra=("memory", "claude-ai"))
            mem_dir = out_dir("misc", "claude-web")
            mem_dir.mkdir(parents=True, exist_ok=True)
            (mem_dir / "claude-ai-memories.md").write_text(
                header("Claude.ai conversation memories", "misc", tags, [],
                       "kind: memories\n", "claude.ai export memories.json")
                + "## The Solution (How)\n\n```json\n" + text + "\n```\n",
                encoding="utf-8")
            n += 1
        except Exception as e:
            print(f"  FAILED memories: {str(e)[:90]}")

    print(f"\nwrote {n} notes")
    if counts:
        print("REDACTED:")
        for k, v in sorted(counts.items()):
            print(f"    {v:4d}x  {k}")


if __name__ == "__main__":
    main()