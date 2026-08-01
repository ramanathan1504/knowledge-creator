#!/usr/bin/env python3
"""
topic-refile.py — invert Projects/ from source-first to topic-first.

Before                                  After
------                                  -----
Projects/ai-studio/log4j/x.md           Projects/log4j/ai-studio/x.md
Projects/claude-web/log4j/y.md          Projects/log4j/claude-web/y.md
Projects/claude-code/log4j/z.md         Projects/log4j/claude-code/z.md
Projects/oss-github/apache__…log4j2/    Projects/log4j/oss-github/apache__…log4j2/
Projects/ai-studio/pastes/p.md          Projects/log4j/ai-studio/pastes/p.md
Projects/log4j2/                        Projects/log4j/

Why
---
The harvesters each owned a tree and filed by provenance, so the top level of
Projects/ answered "where did this come from" — a question nobody asks. The
topic was already computed and already sat at level 2; this pass makes it the
top axis. Provenance survives one level down, which is where it belongs: it
distinguishes what was said publicly (oss-github) from the reasoning behind it
(ai-studio, claude-web, claude-code).

The pastes are the reason this matters most. 75 AI Studio pastes and documents
were filed under `pastes/` and `documents/` by KIND, throwing away the topic
the extractor had already computed and written into their front matter — 60 of
them say `topic: log4j`. One of them outscores every note in the log4j folder.

  ./topic-refile.py            # dry run: the full move plan
  ./topic-refile.py --apply    # move, and write topic-refile-undo.sh
"""

import re
import shlex
import sys
from collections import Counter, defaultdict
from pathlib import Path

HOME = Path.home()
from kbpaths import ARCHIVE as DEVON, SCRIPTS
PROJ = DEVON / "Projects"
APPLY = "--apply" in sys.argv


def next_undo() -> Path:
    """One undo script per apply, numbered. A file moved by pass 1 and again by
    pass 2 can only be put back by running the passes in reverse, so these must
    never share a filename — run the HIGHEST number first."""
    d = SCRIPTS
    n = 1
    while (d / f"topic-refile-undo-{n}.sh").exists():
        n += 1
    return d / f"topic-refile-undo-{n}.sh"

# Trees the harvesters own and rewrite. Level 2 of each is already the topic.
SOURCES = ["ai-studio", "claude-web", "claude-code"]

# Filed by kind, not topic — their front matter carries the real topic.
BY_KIND = {"pastes", "documents", "misc"}

# One repository is about one thing, so this is a lookup, not a classifier.
# Anything unlisted lands in oss-misc rather than being guessed at.
REPO_TOPIC = {
    "apache__logging-log4j2":        "log4j",
    "apache__logging-log4j-samples": "log4j",
    "apache__logging-parent":        "log4j",
    "apache__logging-site":          "log4j",
    "apache__spark":                 "big-data",
    "aws__aws-lambda-java-libs":     "aws-infra",
    "canonical__cloud-init":         "aws-infra",
    "canonical__devpack-for-spring":     "spring",
    "canonical__devpack-for-spring-cli": "spring",
    "canonical__openssl-fips-java":  "security",
    "openssl__openssl":              "security",
    "elastic__elasticsearch":        "observability",
    "micrometer-metrics__tracing":   "observability",
    "opensearch-project__OpenSearch": "observability",
    "jreleaser__jreleaser":          "jreleaser",
    "spring-projects__spring-kafka": "kafka",
}

# Folders that were already a topic and just need merging. Same failure as
# log4j/log4j2: one subject, two folders, so a browse shows half the material
# and a grep for the folder name finds the wrong half.
MERGE = {
    "log4j2":       "log4j",
    "spring-kafka": "kafka",
    "aws-rds":      "databases",
}

FM_TOPIC = re.compile(r"^topic:\s*(\S+)\s*$", re.M)

# Same vocabulary and the same most-specific-first ordering as the extractors,
# for notes that predate the front-matter convention (hand-dropped documents,
# mostly). Reordering this so `java` came earlier once swallowed every Spring
# note in aistudio-extract.py — keep it in sync with that list, not tidy.
BODY_TOPICS = [
    ("log4j",          r"log4j|logback|jsontemplatelayout|patternlayout|\bmdc\b|slf4j|appender"),
    ("spring",         r"\bspring\b|spring boot|\baop\b|servlet|@autowired|@bean\b"),
    ("kafka",          r"kafka|confluent|debezium"),
    ("compliance",     r"soc\s*2|gdpr|audit"),
    ("security",       r"openssl|captcha|turnstile|hardening|\bcve\b|vulnerab"),
    ("observability",  r"opentelemetry|tracing|metrics|prometheus"),
    ("ai-ml",          r"ollama|qwen|fine-tun|local ai|copilot|\bllm\b"),
    ("aws-infra",      r"\baws\b|\bec2\b|\beks\b|\brds\b|kubernetes|keda"),
    ("databases",      r"\bsql\b|postgres|mysql|redis|jdbc"),
    ("system-design",  r"system design|architecture|rate limiting|scaling|tradeoff"),
    ("java",           r"\bjava\b|\bjvm\b|collection|functional interface|generics|\bffm\b|\bjni\b"),
    ("apache-process", r"\basf\b|committer|dependabot|milestone|release vote"),
    ("tooling",        r"intellij|datagrip|maven|gradle|github|\bide\b"),
]

# A body classifier will happily file a resume under `spring` because the
# skills section lists Spring. Name-matching these keeps them out of the
# technical topics; they land in misc and get printed for you to place.
PERSONAL_HINT = re.compile(
    r"resume|passport|visa|workout|roadmap|road-map|memories|users\.json", re.I)

# Raw attachments, not notes: no headings, no front matter, nothing to search.
ASSET_SUFFIXES = {".pdf", ".zip", ".json", ".png", ".jpg", ".jpeg", ".docx", ".rtf"}


def front_matter_topic(p: Path) -> str | None:
    """The extractors write `topic:` into every note they produce. Trust it —
    it was computed from the body, not from the filename, which is the whole
    reason a note titled "Paste June 08, 2026 - 2:33PM" is knowable at all."""
    try:
        head = p.read_text(encoding="utf-8", errors="replace")[:4000]
    except Exception:
        return None
    m = FM_TOPIC.search(head)
    return m.group(1) if m else None


def topic_of(p: Path) -> str:
    """Front matter first, then the body, then give up honestly."""
    t = front_matter_topic(p)
    if PERSONAL_HINT.search(p.name):
        return "misc"
    if t:
        return t
    try:
        body = p.read_text(encoding="utf-8", errors="replace")[:8000].lower()
    except Exception:
        return "misc"
    for name, pat in BODY_TOPICS:
        if re.search(pat, body):
            return name
    return "misc"


def plan_moves():
    moves = []           # (src, dst)
    skipped = []         # (path, reason)

    # -------------------------------------------- conversation harvester trees
    for src_name in SOURCES:
        root = PROJ / src_name
        if not root.is_dir():
            continue
        for sub in sorted(root.iterdir()):
            if sub.name.startswith("."):
                continue
            if sub.is_file():
                # A loose note at the source root has no topic folder to read
                # from; fall back to its front matter, then its body.
                moves.append((sub, PROJ / topic_of(sub) / src_name / sub.name))
                continue
            if sub.name in BY_KIND:
                for f in sorted(sub.rglob("*")):
                    if f.is_dir() or f.name.startswith("."):
                        continue
                    if f.suffix.lower() in ASSET_SUFFIXES:
                        moves.append((f, DEVON / "_assets" / src_name / sub.name / f.name))
                        continue
                    moves.append((f, PROJ / topic_of(f) / src_name / sub.name / f.name))
            else:
                # sub.name IS the topic — move the whole folder's contents.
                for f in sorted(sub.rglob("*")):
                    if f.is_dir() or f.name.startswith("."):
                        continue
                    moves.append((f, PROJ / sub.name / src_name / f.relative_to(sub)))

    # --------------------------------------------------- generated GitHub tree
    # Scans the pre-migration location AND the post-migration one, so editing
    # REPO_TOPIC and re-running relocates a repo that was already moved. Without
    # the second glob, correcting a mapping meant undoing the whole pass.
    oss_repos = [p for p in (PROJ / "oss-github").glob("*") if p.is_dir()]
    oss_repos += [p for p in PROJ.glob("*/oss-github/*") if p.is_dir()]
    for repo in sorted(set(oss_repos)):
        if repo.name.startswith("."):
            continue              # 00-INDEX.md stays put as the cross-repo entry
        topic = REPO_TOPIC.get(repo.name)
        if topic is None:
            skipped.append((repo, "no REPO_TOPIC entry — add one and re-run"))
            continue
        dest_root = PROJ / topic / "oss-github" / repo.name
        if dest_root == repo:
            continue                                  # already where it belongs
        for f in sorted(repo.rglob("*")):
            if f.is_dir() or f.name.startswith("."):
                continue
            moves.append((f, dest_root / f.relative_to(repo)))

    # ------------------------------------------------------ duplicate topic dirs
    for old, new in MERGE.items():
        d = PROJ / old
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if f.is_dir() or f.name.startswith("."):
                continue
            moves.append((f, PROJ / new / f.relative_to(d)))

    # Collisions would silently destroy a note. Refuse them instead.
    seen = {}
    final, collide = [], []
    for s, d in moves:
        if d.exists() or d in seen:
            collide.append((s, d))
            continue
        seen[d] = s
        final.append((s, d))
    return final, collide, skipped


def main():
    if not PROJ.is_dir():
        sys.exit(f"not found: {PROJ}")

    moves, collide, skipped = plan_moves()

    by_topic = Counter()
    detail = defaultdict(Counter)
    review = []
    for s, d in moves:
        rel = d.relative_to(DEVON).parts
        bucket = "/".join(rel[:2])                      # Projects/log4j, _assets/ai-studio
        by_topic[bucket] += 1
        detail[bucket][rel[2] if len(rel) > 3 else "(loose)"] += 1
        if rel[:2] == ("Projects", "misc"):
            review.append((s, d))

    print(f"{'APPLY' if APPLY else 'DRY RUN'}   {len(moves)} files to move\n")
    for t, n in by_topic.most_common():
        print(f"  {n:4d}  {t}/")
        for src, m in detail[t].most_common():
            print(f"        {m:4d}  {src}/")
    if collide:
        print(f"\n  !! {len(collide)} destination collisions — NOT moved:")
        for s, d in collide[:20]:
            print(f"     {s.relative_to(DEVON)}  ->  {d.relative_to(DEVON)}")
    if review:
        print(f"\n  ?? {len(review)} landed in misc — no topic in front matter and "
              "nothing recognisable in the body. Place these by hand:")
        for s, _ in review:
            print(f"     {s.relative_to(PROJ)}")
    if skipped:
        print("\n  !! skipped:")
        for p, why in skipped:
            print(f"     {p.relative_to(PROJ)}: {why}")

    if not APPLY:
        print("\nNothing moved. Re-run with --apply.")
        return

    undo = ["#!/bin/bash",
            "# Generated by topic-refile.py --apply. Reverses the re-filing.",
            "set -euo pipefail", ""]
    moved = 0
    for s, d in moves:
        d.parent.mkdir(parents=True, exist_ok=True)
        s.rename(d)
        undo.append(f"mkdir -p {shlex.quote(str(s.parent))} && "
                    f"mv {shlex.quote(str(d))} {shlex.quote(str(s))}")
        moved += 1

    # Prune the folders the moves emptied, deepest first so parents follow.
    emptied = []
    for d in sorted(PROJ.rglob("*"), key=lambda p: -len(p.parts)):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            emptied.append(d)
    for d in emptied:
        undo.append(f"mkdir -p {shlex.quote(str(d))}")

    undo_path = next_undo()
    undo_path.write_text("\n".join(undo) + "\n", encoding="utf-8")
    undo_path.chmod(0o755)
    print(f"\nmoved {moved} files, pruned {len(emptied)} empty folders")
    print(f"undo:  {undo_path}  (run undo scripts highest-numbered first)")


if __name__ == "__main__":
    main()
