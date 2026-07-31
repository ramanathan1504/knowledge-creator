#!/usr/bin/env python3
"""
topic-digest.py — write Reference/topics/<topic>.md: what I actually know.

The difference from knowledge-map.py
------------------------------------
`00-knowledge-map.md` answers "which notes mention log4j" — 312 links sorted by
regex hit count. That is an index. It does not tell you anything; you still have
to open 312 files to find out what you learned.

This reads the notes instead of counting them. Every harvester writes the same
three headings — `## The Problem (What & Where)`, `## The Solution (How)`,
`## The "Why" (Review Discussions)` — which is a structure worth mining: 356 of
the notes carry it. Pulling the problem and the resolution out of each one and
grouping by subtopic produces a page you can read top to bottom and come away
knowing what was solved, not just where it was discussed.

Evidence ranking is deliberate. A `oss-github` note is what was said publicly on
the thread and how it was resolved; an `ai-studio` or `claude-*` note is the
reasoning that got there. Both matter, and which is which matters, so they are
labelled and the public one sorts first.

  ./topic-digest.py                    # dry run: what each digest would contain
  ./topic-digest.py --apply
  ./topic-digest.py --apply log4j java # only these topics
"""

import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
from kbpaths import ARCHIVE as DEVON
PROJ = DEVON / "Projects"
OUT = DEVON / "Reference/topics"
NOW = datetime.now(timezone.utc)

APPLY = "--apply" in sys.argv
ONLY = [a for a in sys.argv[1:] if not a.startswith("-")]

# Subtopics carve a big topic into readable sections. A topic with no entry here
# gets one flat list, which is fine for the small ones.
SUB = {
    "log4j": {
        "Plugins & annotation processor": r"pluginprocessor|@plugin\b|pluginbuilderattribute|plugin descriptor|@pluginfactory",
        "Layouts":            r"patternlayout|jsontemplatelayout|\blayout\b|htmllayout|csvlayout",
        "Appenders":          r"appender|rollingfile|asyncappender|jmsappender|smtpappender",
        "Filters":            r"\bfilter\b|stringmatchfilter|regexfilter|levelmatchfilter|thresholdfilter",
        "Configuration":      r"xmlconfiguration|configurationscheduler|log4j2\.xml|xinclude|properties configuration",
        "MDC / ThreadContext": r"\bmdc\b|threadcontext|contextdata|contextmap",
        "Exceptions & stack traces": r"throwableproxy|stack trace|stacktrace|throwable|exception rendering",
        "Async & performance": r"disruptor|async logger|jmh|benchmark|throughput|garbage-free",
        "Release & porting":  r"cherry-pick|changelog|milestone|\b2\.x\b|\bport\b|release candidate",
        "Build & baseline":   r"\bbnd\b|baseline|api compatibility|spotless|surefire",
    },
    "java": {
        "Collections":        r"collection|hashmap|arraylist|treemap|linkedlist|\bset\b",
        "Streams & functional": r"\bstream\b|lambda|functional interface|optional|collector",
        "Generics":           r"generic|type erasure|wildcard|bounded type",
        "Records & modern":   r"\brecord\b|sealed|pattern matching|switch expression|text block",
        "Concurrency":        r"concurren|executor|completablefuture|synchroniz|volatile|deadlock",
        "JNI / FFM / native": r"\bjni\b|\bffm\b|foreign function|panama|memorysegment",
        "JVM & memory":       r"\bgc\b|heap|garbage collect|classloader|jvm flag|oom",
    },
    "spring": {
        "Core & DI":          r"@autowired|@bean\b|@component|dependency injection|applicationcontext",
        "AOP & proxies":      r"\baop\b|@aspect|pointcut|\bproxy\b",
        "Web & servlet":      r"servlet|@restcontroller|@requestmapping|dispatcher|filter chain",
        "Data & JPA":         r"\bjpa\b|hibernate|@entity|repository|@transactional|lockmodetype",
        "Boot & autoconfig":  r"spring boot|autoconfigur|application\.yml|starter|@conditionalon",
    },
    "kafka": {
        "Producers":          r"producer|acks|idempoten|batch\.size|linger",
        "Consumers & groups": r"consumer|group\.id|rebalance|offset|poll\b",
        "Connect & CDC":      r"debezium|connect\b|\bcdc\b|sink|source connector",
        "Spring integration": r"@kafkalistener|spring-kafka|kafkatemplate",
    },
}

# What the top-level folder tells you about a note's standing as evidence.
PROVENANCE = {
    "oss-github":  ("public thread", 0),
    "claude-code": ("working session", 1),
    "ai-studio":   ("worked out with Gemini", 2),
    "claude-web":  ("worked out with Claude", 2),
}
HAND = ("hand-written note", 0)

PROBLEM = "## The Problem (What & Where)"
SOLUTION = "## The Solution (How)"
WHY = '## The "Why" (Review Discussions)'

# Pastes render a fixed sentence as their Problem. Quoting it 60 times would
# fill the digest with the same line and teach nothing.
BOILERPLATE = re.compile(r"^raw paste captured in ai studio", re.I)

CAMEL = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z0-9]+)+)\b")

# CamelCase brand and tooling names. They are frequent everywhere and so
# distinguish nothing — `GitHub` topped the log4j vocabulary at 2,729 hits,
# which tells you the notes came from GitHub and not one thing about Log4j.
IDENT_STOP = {
    "GitHub", "GitLab", "StackOverflow", "JavaScript", "TypeScript", "IntelliJ",
    "JetBrains", "AndroidStudio", "VsCode", "MacOS", "OpenAi", "ChatGpt",
    "ClaudeCode", "AiStudio", "GoogleDrive", "DevonThink", "ReadMe", "JavaDoc",
    "PullRequest", "MarkDown", "JsonSchema", "ToDo", "IPhone", "MacBook",
}
FENCE = re.compile(r"```.*?```", re.S)
# The chat extractors fold model reasoning into <details> blocks. That is the
# model thinking out loud, not a resolution — quoting it as one gave entries
# that read "Resolved by. <details><summary>model reasoning</summary>".
DETAILS = re.compile(r"<details>.*?</details>", re.S | re.I)
TAG = re.compile(r"<[^>]{1,80}>")
FRONT = re.compile(r"\A---\n.*?\n---\n", re.S)
LINK = re.compile(r"\[([^\]]*)\]\((?:[^)]*)\)")
MAX_ENTRIES = 60          # per subtopic; digests are meant to be read

# Every note repeats the same boilerplate lines. Scanning them for identifiers
# made `GitHub` the single most frequent term in the log4j digest at 2,836
# occurrences — one per "**GitHub Context:**" header, and nothing to do with
# what any note is about.
BOILER_LINE = re.compile(
    r"^\s*\*\*(GitHub Context|Search Tags/Keywords|Labels)\*\*.*$", re.M)

# The GitHub notes prefix every quoted comment with its timestamp and a
# permalink. That is a citation, not a finding — taking it as the first
# paragraph made half the digest read "**2026-07-28T07:24:59Z** · link".
NOISE_PARA = re.compile(
    r"^(\*\*)?\d{4}-\d{2}-\d{2}T[\d:]+Z"          # bare timestamp
    r"|^@[\w-]+\**\s*(\*\*)?\(me\)"               # comment byline
    r"|^(COMMENTED|APPROVED|CHANGES_REQUESTED|DISMISSED)\b"   # review-state byline
    r"|^_?Conversation of \d+ turns"              # chat-extractor footer
    r"|^_?(from|link|edit|see)\b.{0,20}$",        # stub cross-references
    re.I)


def section(text: str, heading: str) -> str:
    i = text.find(heading)
    if i < 0:
        return ""
    rest = text[i + len(heading):]
    j = rest.find("\n## ")
    return (rest if j < 0 else rest[:j]).strip()


def gist(block: str, cap: int = 260) -> str:
    """First paragraph that says something, collapsed to one line.

    Code, blockquote markers, link targets and citation lines all come out —
    what is left is prose or nothing.
    """
    block = TAG.sub(" ", DETAILS.sub(" ", FENCE.sub(" ", block)))
    for para in block.split("\n\n"):
        para = " ".join(ln.strip().lstrip(">").lstrip("-*").strip()
                        for ln in para.splitlines())
        para = LINK.sub(r"\1", para)
        para = re.sub(r"\s+", " ", para).strip()
        # Chat turns open with "Answer** — 2026-06-17 05:20:57". Keeping the
        # header meant every entry started with a speaker label and a clock.
        para = re.sub(r"^(Answer|Prompt|Question|Reply)\*{0,2}\s*[—-]\s*"
                      r"[\d:\sT/-]{8,25}\s*", "", para)
        if len(para) < 25 or para.startswith("#") or para.startswith("|"):
            continue
        if NOISE_PARA.match(para):
            continue
        # A line that is mostly bold markup and punctuation is a header dressed
        # up as a sentence.
        if len(re.sub(r"[^\w\s]", "", para)) < len(para) * 0.6:
            continue
        return para[:cap].rstrip() + ("…" if len(para) > cap else "")
    return ""


def title_of(p: Path, text: str) -> str:
    for ln in text.splitlines():
        if ln.startswith("# "):
            return ln[2:].strip()
    return p.stem


def subtopic_weights(topic: str, hays: list[str]) -> dict[str, float]:
    """Inverse document frequency per subtopic.

    Raw counts do not work. Every Log4j pull-request note mentions a changelog
    and a milestone, so "Release & porting" won 70 of 198 entries on words that
    say nothing about what the note is about. A pattern that matches almost
    every document carries almost no information; weight it accordingly, and
    `PatternLayout` — rare, therefore meaningful — beats `changelog` on a note
    that mentions both.
    """
    subs = SUB.get(topic, {})
    n = max(1, len(hays))
    w = {}
    for label, pat in subs.items():
        df = sum(1 for h in hays if re.search(pat, h))
        w[label] = math.log(n / df) if df else 0.0
    return w


def subtopic_of(topic: str, hay: str, weights: dict[str, float]) -> str:
    subs = SUB.get(topic)
    if not subs:
        return ""
    best, best_score = "", 0.0
    for label, pat in subs.items():
        score = len(re.findall(pat, hay)) * weights.get(label, 0.0)
        if score > best_score:
            best, best_score = label, score
    return best or "Other"


def collect():
    """Topic comes from the folder now — the whole point of the re-filing. No
    regex guessing, no multi-label fog: one note, one home, and the digest for
    that topic is exactly the folder's contents."""
    topics = defaultdict(list)
    for p in PROJ.rglob("*.md"):
        rel = p.relative_to(PROJ)
        topic = rel.parts[0]
        if topic in ("oss-github", "misc"):
            continue                       # index file and the unplaceable pile
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        src = rel.parts[1] if len(rel.parts) > 2 else None
        label, rank = PROVENANCE.get(src, HAND)
        topics[topic].append((p, rel, text, label, rank))
    return topics


def build(topic, notes):
    entries = defaultdict(list)   # subtopic -> [(rank, title, rel, prob, sol, why, label)]
    idents = Counter()
    raw = []                      # captures with no prose worth quoting
    weights = subtopic_weights(topic, [t.lower() for _, _, t, _, _ in notes])

    for p, rel, text, label, rank in notes:
        title = title_of(p, text)
        hay = text.lower()
        body = BOILER_LINE.sub("", FRONT.sub("", FENCE.sub(" ", text)))
        body = LINK.sub(r"\1", body)
        for m in CAMEL.findall(body):
            if m not in IDENT_STOP:
                idents[m] += 1

        prob = gist(section(text, PROBLEM))
        sol = gist(section(text, SOLUTION))
        why = gist(section(text, WHY), 200)
        # Some notes repeat the same comment under both headings; printing it
        # twice under two different labels reads as two separate findings.
        if why and sol and why[:80] == sol[:80]:
            why = ""

        if not prob and not sol:
            raw.append((title, rel, label))
            continue
        if BOILERPLATE.match(prob or ""):
            raw.append((title, rel, label))
            continue
        entries[subtopic_of(topic, hay, weights)].append(
            (rank, title, rel, prob, sol, why, label))

    return entries, idents, raw


def render(topic, entries, idents, raw, total):
    n_entries = sum(len(v) for v in entries.values())
    tags = sorted({topic} | {s.lower().replace(" ", "-").replace("/", "-")
                             for s in entries if s})
    L = ["---",
         f"tags: [digest, {topic}, " + ", ".join(t for t in tags if t != topic) + "]",
         f"topic: {topic}",
         f"generated: {NOW.isoformat(timespec='seconds')}",
         "---", "",
         f"# {topic} — what I know", "",
         "**Search Tags/Keywords:** #digest #" + topic + " "
         + " ".join("#" + i for i, _ in idents.most_common(30)), "",
         "**GitHub Context:** synthesised from every note filed under "
         f"`Projects/{topic}/`.", "",
         f"Generated by `~/claude-cli/topic-digest.py` from {total} notes "
         f"({n_entries} with a stated problem and resolution). Regenerated on "
         "demand — do not hand-edit.", "",
         "Each entry is _problem → what resolved it_. The label says what kind "
         "of evidence it is: a **public thread** is what was actually said and "
         "merged; the rest is the reasoning that got there.", "", "---", ""]

    if idents:
        L += ["## Vocabulary", "",
              "The identifiers that keep coming up, by frequency — the surface "
              "area of this topic as I have actually touched it.", "",
              " · ".join(f"`{i}` ({n})" for i, n in idents.most_common(25)),
              "", "---", ""]

    order = list(SUB.get(topic, {})) + ["Other", ""]
    for sub in sorted(entries, key=lambda s: order.index(s) if s in order else 99):
        items = sorted(entries[sub], key=lambda e: (e[0], e[1]))[:MAX_ENTRIES]
        L += [f"## {sub or 'Notes'}  ({len(entries[sub])})", ""]
        for rank, title, rel, prob, sol, why, label in items:
            link = str(rel).replace(" ", "%20")
            L += [f"### [{title}](../../Projects/{link})", "",
                  f"_{label}_", ""]
            if prob:
                L += [f"**Problem.** {prob}", ""]
            if sol:
                L += [f"**Resolved by.** {sol}", ""]
            if why:
                L += [f"**Why.** {why}", ""]
        if len(entries[sub]) > MAX_ENTRIES:
            L += [f"_… and {len(entries[sub]) - MAX_ENTRIES} more in "
                  f"`Projects/{topic}/`._", ""]

    if raw:
        L += ["---", "", f"## Raw captures ({len(raw)})", "",
              "Stack traces and source dumps with no written problem statement. "
              "Not readable as knowledge, but they are the primary evidence "
              "behind several entries above — grep them for an identifier.", ""]
        for title, rel, label in sorted(raw)[:80]:
            L.append(f"- [{title}](../../Projects/{str(rel).replace(' ', '%20')}) · _{label}_")
        if len(raw) > 80:
            L.append(f"- _… and {len(raw) - 80} more_")
        L.append("")

    return "\n".join(L) + "\n"


def main():
    topics = collect()
    if not topics:
        sys.exit(f"no notes found under {PROJ}")
    wanted = [t for t in topics if not ONLY or t in ONLY]
    if ONLY:
        missing = set(ONLY) - set(topics)
        if missing:
            print(f"  !! no such topic: {', '.join(sorted(missing))}\n")

    print(f"{'APPLY' if APPLY else 'DRY RUN'}\n")
    built = {}
    for t in sorted(wanted, key=lambda t: -len(topics[t])):
        entries, idents, raw = build(t, topics[t])
        n = sum(len(v) for v in entries.values())
        built[t] = (entries, idents, raw, len(topics[t]))
        subs = ", ".join(f"{s or 'Notes'} {len(v)}"
                         for s, v in sorted(entries.items(), key=lambda kv: -len(kv[1])))
        print(f"  {t:16s} {len(topics[t]):4d} notes -> {n:4d} entries, "
              f"{len(raw):3d} raw captures")
        if subs:
            print(f"                   {subs}")

    if not APPLY:
        print("\nNothing written. Re-run with --apply.")
        return

    OUT.mkdir(parents=True, exist_ok=True)
    for t, (entries, idents, raw, total) in built.items():
        (OUT / f"{t}.md").write_text(render(t, entries, idents, raw, total),
                                     encoding="utf-8")
    print(f"\nwrote {len(built)} digests to {OUT}")


if __name__ == "__main__":
    main()
