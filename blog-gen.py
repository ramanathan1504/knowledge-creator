#!/usr/bin/env python3
"""
blog-gen.py — turn finished OSS work into publishable write-ups.

The harvested notes answer "what happened in thread X". A blog post answers
"here is a problem you might also hit, and how it was actually solved". Same
material, different shape: narrative, self-contained, no assumed context.

    ./blog-gen.py --list                    # rank candidates, write nothing
    ./blog-gen.py apache/logging-log4j2#4133 --apply
    ./blog-gen.py --top 5 --apply           # best five not yet written
    ./blog-gen.py --top 5 --apply --ai      # let Claude draft the prose

Output goes to `Devon Capture/Blog/`, which DEVONthink already indexes, so
drafts are searchable alongside everything else.

Two modes
---------
Default is DETERMINISTIC: it assembles a scaffold from the note — real title,
real problem statement, real review discussion, real commits, real diffstat,
with the code and links already in place — and leaves clearly marked TODOs
where a human voice is needed. Nothing is invented.

`--ai` additionally calls `claude -p` to draft the connective prose from that
scaffold. It is told not to invent facts, only to narrate the ones present.
Costs tokens; skip it if you would rather write the prose yourself.

Blog-worthiness is scored, not guessed: a one-line dependency bump is not an
article no matter how many words the bot-generated thread contains.
"""

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
from kbpaths import ARCHIVE as DEVON
SRC = DEVON / "Projects"          # harvested threads now live at Projects/<topic>/oss-github/
OUT = DEVON / "Blog"
USER = "ramanathan1504"
NOW = datetime.now(timezone.utc)

APPLY = "--apply" in sys.argv
AI = "--ai" in sys.argv

# A thread is worth an article when it has a real problem, a real argument
# about the fix, and real code. Dependency bumps and typo fixes have none of
# that however long the thread is.
BORING = re.compile(
    r"\bbump\b|\bdependabot\b|update .* to v?\d|^chore|typo|^\[?docs?\]?:|"
    r"upgrade .* from .* to", re.I)


def field(text, key):
    m = re.search(rf"^{key}:\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else ""


def section(text, heading):
    """Body of one '## heading' up to the next '## '."""
    m = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else ""


def title_of(text, fallback):
    for ln in text.splitlines():
        if ln.startswith("# "):
            return ln[2:].strip()
    return fallback


def score(note, text):
    """How much of an article is actually here."""
    s = 0
    title = title_of(text, "")
    subject = title.split("—", 1)[-1].strip()
    if BORING.search(subject):
        return 0

    state = field(text, "state")
    if "merged" in state:
        s += 30
    elif "CLOSED" in state:
        s += 15

    role = field(text, "my_role")
    if "author" in role:
        s += 25
    if "inline-reviewer" in role:
        s += 10

    problem = section(text, "The Problem (What & Where)")
    why = section(text, 'The "Why" (Review Discussions)')
    s += min(len(problem.split()) // 20, 20)      # a substantial problem statement
    s += min(len(why.split()) // 40, 25)          # a substantial argument

    s += min(text.count("```") * 4, 20)           # code present
    if re.search(r"RESOLVED|OUTDATED", text):
        s += 10                                   # inline review threads happened

    d = re.search(r"\+(\d+) −(\d+) across (\d+) files", text)
    if d:
        churn = int(d.group(1)) + int(d.group(2))
        if 20 <= churn <= 800:
            s += 10                               # meaty but comprehensible
    return s


def candidates():
    # Harvested threads sit at Projects/<topic>/oss-github/<repo>/ since the
    # re-filing. Globbing all of Projects/ instead would pull in conversation
    # notes, which are working material and not mine alone to publish.
    out = []
    for p in sorted(SRC.glob("*/oss-github/*/*.md")):
        if p.name in ("00-INDEX.md", "commits.md"):
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        sc = score(p, t)
        if sc > 0:
            out.append((sc, p, t))
    out.sort(key=lambda r: -r[0])
    return out


def slug(s, n=70):
    s = re.sub(r"[^\w\s-]", "", s.lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s[:n].rstrip("-") or "post"


def scaffold(text, path):
    """Deterministic draft. Every fact here comes from the note."""
    full = title_of(text, path.stem)
    subject = full.split("—", 1)[-1].strip()
    gh = field(text, "github")
    url = field(text, "url")
    state = field(text, "state")
    role = field(text, "my_role")
    problem = section(text, "The Problem (What & Where)")
    why = section(text, 'The "Why" (Review Discussions)')
    sol = section(text, "The Solution (How)")

    diff = re.search(r"\*\*Diff:\*\*(.+)", text)
    commits = re.findall(r"^- `([0-9a-f]{7,10})` (.+)$", sol, re.M)
    # Reviewers who actually pushed back, minus me.
    # Match only the RENDERED participant form `**@login**` that the harvester
    # emits for each comment. A bare `@word` scan pulled Java annotations out of
    # code blocks and credited the review to "@Version" and "@BaselineIgnore".
    people = [u for u in dict.fromkeys(re.findall(r"\*\*@([\w-]+)\*\*", why))
              if u != USER][:6]
    tags = field(text, "tags")

    L = [
        "---",
        f"tags: [blog, draft, {tags.strip('[]')}]",
        f"github: {gh}",
        f"source_note: {path.relative_to(DEVON)}",
        f"status: {state}",
        f"generated: {NOW.isoformat(timespec='seconds')}",
        "---",
        "",
        f"# {subject}",
        "",
        # Subject lines are often lowercase ("Remove `jvmrunargs` lookup"), so
        # CamelCase extraction alone produced an almost empty keyword line.
        # Fall back to the harvested tags, which are the real retrieval handles.
        # dict.fromkeys dedupes while preserving order: the subject and the
        # harvested tags overlap, and "#ListAppender #ListAppender" in a
        # published post looks like a bug because it is one.
        f"**Search Tags/Keywords:** "
        + " ".join("#" + w for w in dict.fromkeys(
            ["blog", gh.split("/")[0]]
            + re.findall(r"[A-Z][a-z]+(?:[A-Z][a-z]+)+", subject)
            + [t.strip() for t in tags.strip("[]").split(",") if t.strip()]))[:400],
        "",
        f"**GitHub Context:** [{gh}]({url}) · {state}"
        + (f" · reviewers: {', '.join('@'+p for p in people)}" if people else ""),
        "",
        "> _Draft. Generated from the harvested thread; every fact below is from "
        "the real discussion. Add the narrative voice before publishing._",
        "",
        "---",
        "",
        "## The problem",
        "",
        problem or "_(no problem statement captured)_",
        "",
        "## What made it non-obvious",
        "",
    ]
    if people:
        L.append(f"Reviewed by {', '.join('@'+p for p in people)}. The discussion "
                 "that shaped the fix:\n")
    L.append(why or "_(no review discussion captured)_")
    L += ["", "## The fix", ""]
    if diff:
        L += [f"**Diff:**{diff.group(1)}", ""]
    if commits:
        L.append("Commits:\n")
        for sha, msg in commits:
            L.append(f"- `{sha}` {msg}")
        L.append("")
    code = re.findall(r"```(\w*)\n(.*?)```", sol, re.S)
    if code:
        L.append("Key change:\n")
        lang, body = max(code, key=lambda c: len(c[1]))
        L += [f"```{lang}", body.strip(), "```", ""]
    L += [
        "## What I'd take away",
        "",
        "<!-- TODO: the generalisable lesson. What would you tell someone",
        "     hitting this in a different codebase? -->",
        "",
        "---",
        "",
        f"_Originally worked through in [{gh}]({url})._",
        "",
    ]
    return "\n".join(L)


AI_PROMPT = """You are drafting a technical blog post from a real, completed \
open-source contribution. The scaffold below contains ONLY facts taken from the \
actual GitHub thread.

Rules:
- Do NOT invent facts, numbers, names, APIs or outcomes. If something is not in \
the scaffold, leave it out.
- Keep every code block, commit SHA, diffstat, link and @mention exactly as given.
- Write the connective prose: an opening that states the symptom concretely, \
transitions that explain WHY each step followed, and a closing takeaway that \
generalises beyond this codebase.
- Aim for 600-900 words. Technical peers are the audience; skip the throat-clearing.
- Keep the YAML front matter unchanged at the top.
- Output the finished Markdown only. No preamble, no commentary.

SCAFFOLD:
"""


def enrich(draft):
    try:
        r = subprocess.run(
            ["claude", "-p", AI_PROMPT + draft,
             "--output-format", "text", "--max-turns", "1"],
            capture_output=True, text=True, timeout=300)
        if r.returncode == 0 and len(r.stdout.strip()) > 400:
            return r.stdout.strip()
        print(f"      AI draft unavailable (rc={r.returncode}) — keeping scaffold")
    except Exception as e:
        print(f"      AI draft failed: {str(e)[:80]} — keeping scaffold")
    return draft


def main():
    # Skip the VALUE after --top as well as the flag itself, or "3" from
    # `--top 3` gets treated as a positional repo#number and matches nothing.
    argv, top, skip = sys.argv[1:], None, False
    args = []
    for i, a in enumerate(argv):
        if skip:
            skip = False
            continue
        if a == "--top":
            top = int(argv[i + 1]); skip = True
        elif not a.startswith("--"):
            args.append(a)

    cands = candidates()
    if not cands:
        sys.exit("no candidates — has oss-harvest.py run?")

    if "--list" in sys.argv or (not args and top is None):
        print(f"{len(cands)} candidates, best first "
              f"(score = merged + authored + real discussion + code)\n")
        for sc, p, t in cands[:25]:
            gh = field(t, "github")
            written = "✓" if (OUT / f"{slug(title_of(t,'').split('—',1)[-1])}.md").exists() else " "
            print(f"  {written} {sc:4d}  {gh:34s} "
                  f"{title_of(t,'').split('—',1)[-1].strip()[:56]}")
        print("\n  ✓ = already drafted.  --top N --apply to generate.")
        return

    picked = []
    if args:
        for a in args:
            hit = [c for c in cands if field(c[2], "github") == a]
            if not hit:
                print(f"  not found (or scored 0): {a}")
            picked += hit
    else:
        picked = [c for c in cands
                  if not (OUT / f"{slug(title_of(c[2],'').split('—',1)[-1])}.md").exists()][:top]

    if not APPLY:
        print(f"DRY RUN — would write {len(picked)}:")
        for sc, p, t in picked:
            print(f"  {sc:4d}  {field(t,'github')}")
        print("\n  --apply to write.")
        return

    OUT.mkdir(parents=True, exist_ok=True)
    for sc, p, t in picked:
        subject = title_of(t, p.stem).split("—", 1)[-1].strip()
        draft = scaffold(t, p)
        print(f"  [{sc}] {field(t,'github')} — {subject[:52]}")
        if AI:
            print("      drafting prose with claude…")
            draft = enrich(draft)
        f = OUT / f"{slug(subject)}.md"
        f.write_text(draft, encoding="utf-8")
        print(f"      -> Blog/{f.name}")
    print(f"\nwrote {len(picked)} draft(s) to {OUT}")


if __name__ == "__main__":
    main()
