#!/usr/bin/env python3
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
pick-for-me.py — "what should I work on next", scored against MY knowledge base.

Companion to triage.sh, not a replacement.

    triage.sh      answers "what is the state of this repo's backlog?"
                   Scores issues by COMMUNITY signal: reactions x2 + comments
                   + label boost. The same ranking for everybody.

    pick-for-me.py answers "what should *I* pick up?"
                   Scores the same backlog against evidence of what I already
                   know, from 359 harvested notes: my PR reviews, my comments,
                   my commits, my AI Studio and Claude conversations.

It reuses triage.sh's cache (.triage-cache/<owner>-<repo>/) when present, so
running both costs one fetch, not two.

    ./pick-for-me.py apache/logging-log4j2
    ./pick-for-me.py apache/logging-log4j2 --refresh   # re-fetch from GitHub
    ./pick-for-me.py apache/logging-log4j2 --write     # also save into the KB

Four ranked sections, in the order a maintainer actually works:

  1. Finish what you started  — my open PRs, worst-stalled first
  2. Awaiting my reply        — threads I engaged in that moved on without me
  3. Best new picks           — unclaimed issues matching my strongest areas
  4. Review queue             — open PRs I am well placed to review

Every recommendation names the notes in my own knowledge base that make me the
right person for it. That is the point: not "this issue is popular" but "you
already solved this in PR #4152 and wrote it up".
"""

import json
import math
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
from kbpaths import ARCHIVE as DEVON, SCRIPTS
CACHE = SCRIPTS / ".triage-cache"
USER = "ramanathan1504"
NOW = datetime.now(timezone.utc)

STOP = {
    "The", "This", "That", "There", "These", "When", "With", "From", "What",
    "Note", "String", "System", "Object", "Class", "Version", "Github",
    "GitHub", "Apache", "Java", "However", "Please", "Should", "Would",
    # frontmatter vocabulary — these are metadata values, not expertise.
    # Leaving them in put "main", "MERGED", "CLOSED" and "null" among my
    # strongest terms, which then matched almost every open issue.
    "main", "MERGED", "CLOSED", "OPEN", "null", "true", "false", "none",
    "issue", "pull", "commit", "master", "draft",
}

# Derived aggregations are not evidence of expertise: they are assembled FROM
# the evidence. Citing Reference/snippets/java.md as "your note about this"
# is circular and crowds out the real source.
EXCLUDE_AS_EVIDENCE = re.compile(
    r"^Reference/snippets/|^Reference/00-knowledge-map|^Reference/mindmap|"
    r"^Reference/topics/|"          # topic digests: assembled from the notes below
    r"00-INDEX\.md$|00-best-picks")

FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)

CAMEL = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z0-9]+)+)\b")
TICKED = re.compile(r"`([\w.$]{4,40})`")
GHREF = re.compile(r"^github:\s*(.+)$", re.M)
ROLE = re.compile(r"^my_role:\s*(.+)$", re.M)


# ------------------------------------------------------- expertise profile --
def build_profile():
    """Weighted vocabulary of what I demonstrably know.

    Weighting matters: a term inside a PR I authored or reviewed is far
    stronger evidence than the same term appearing in a thread I merely
    watched. Without it, popular repo-wide jargon drowns out my real areas.
    """
    vocab = Counter()
    df = Counter()                   # document frequency, for IDF
    notes_for = defaultdict(set)     # term -> {note paths}
    touched = {}                     # "owner/repo#123" -> note path
    my_roles = {}
    n_docs = 0

    for p in DEVON.rglob("*.md"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = str(p.relative_to(DEVON))
        n_docs += 1

        rm = ROLE.search(text)
        role = rm.group(1).strip() if rm else ""
        weight = 1
        if "author" in role:
            weight = 5
        elif "reviewer" in role or "inline-reviewer" in role:
            weight = 4
        elif "commenter" in role:
            weight = 2
        if "/ai-studio/" in rel or "/claude-web/" in rel or "/claude-code/" in rel:
            weight = max(weight, 3)   # I chose to research it: real intent

        for m in GHREF.finditer(text):
            for ref in re.findall(r"[\w.-]+/[\w.-]+#\d+", m.group(1)):
                touched[ref] = rel
                my_roles[ref] = role

        # terms come from the BODY only; frontmatter is metadata, not knowledge
        body = FRONTMATTER.sub("", text)
        is_derived = bool(EXCLUDE_AS_EVIDENCE.search(rel))

        terms = set()
        for m in CAMEL.finditer(body):
            if m.group(1) not in STOP:
                terms.add(m.group(1))
        for m in TICKED.finditer(body):
            t = m.group(1).split(".")[-1]
            if len(t) > 3 and t not in STOP:
                terms.add(t)
        for t in terms:
            vocab[t] += weight
            df[t] += 1
            if not is_derived and len(notes_for[t]) < 6:
                notes_for[t].add(rel)

    # Raw frequency makes "java", "version" and "message" my top skills, because
    # they appear everywhere. Scale by inverse document frequency so a term that
    # shows up in 3 notes outranks one in 300, and drop anything present in more
    # than a quarter of the base as too generic to be evidence of anything.
    spec = {}
    for t, w in vocab.items():
        if df[t] > n_docs * 0.25:
            continue
        spec[t] = w * math.log(n_docs / max(df[t], 1))
    return spec, notes_for, touched, my_roles


# ------------------------------------------------------------------- input --
def load(repo, refresh):
    slug = repo.replace("/", "-")
    d = CACHE / slug
    prs, issues = d / "prs.json", d / "issues.json"
    if refresh or not (prs.exists() and issues.exists()):
        d.mkdir(parents=True, exist_ok=True)
        print(f"fetching {repo} …")
        subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--state", "open", "--limit", "200",
             "--json", "number,title,body,author,labels,updatedAt,url,isDraft,"
                       "additions,deletions,reviewDecision,mergeable,reviews"],
            stdout=prs.open("w"), check=True)
        subprocess.run(
            ["gh", "issue", "list", "--repo", repo, "--state", "open", "--limit", "400",
             "--json", "number,title,body,labels,updatedAt,url,comments,"
                       "reactionGroups,assignees"],
            stdout=issues.open("w"), check=True)
    else:
        print(f"using triage.sh cache: {d}")
    return json.loads(prs.read_text()), json.loads(issues.read_text())


def age_days(iso):
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (NOW - t).days
    except Exception:
        return 0


def terms_of(text):
    out = set()
    for m in CAMEL.finditer(text or ""):
        if m.group(1) not in STOP:
            out.add(m.group(1))
    for m in TICKED.finditer(text or ""):
        t = m.group(1).split(".")[-1]
        if len(t) > 3 and t not in STOP:
            out.add(t)
    return out


def affinity(item, vocab, notes_for):
    """Overlap between this item and my demonstrated vocabulary."""
    t = terms_of(f"{item.get('title','')} {(item.get('body') or '')[:6000]}")
    hits = [(vocab[x], x) for x in t if vocab.get(x)]
    hits.sort(reverse=True)
    score = int(sum(w for w, _ in hits[:12]))
    why = [x for _, x in hits[:6]]
    notes = []
    for _, term in hits[:4]:
        for n in sorted(notes_for.get(term, []))[:2]:
            if n not in notes:
                notes.append(n)
    return score, why, notes[:5]


def labels_of(item):
    return [l["name"] for l in (item.get("labels") or [])]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__.strip().split("\n\n")[0] + "\n\nusage: pick-for-me.py OWNER/REPO [--refresh] [--write]")
    repo = args[0]
    refresh = "--refresh" in sys.argv
    write = "--write" in sys.argv

    print("building expertise profile from the knowledge base …")
    vocab, notes_for, touched, my_roles = build_profile()
    print(f"  {len(vocab)} weighted terms from {len(list(DEVON.rglob('*.md')))} notes")
    print(f"  {len(touched)} GitHub threads already on record")
    top = ", ".join(t for t, _ in vocab.most_common(12))
    print(f"  strongest: {top}\n")

    prs, issues = load(repo, refresh)
    print(f"  {len(prs)} open PRs · {len(issues)} open issues\n")

    mine, awaiting, picks, reviewq = [], [], [], []

    for pr in prs:
        num = pr["number"]
        ref = f"{repo}#{num}"
        aff, why, notes = affinity(pr, vocab, notes_for)
        age = age_days(pr.get("updatedAt", ""))
        author = (pr.get("author") or {}).get("login", "")
        rec = {"n": num, "title": pr.get("title", ""), "url": pr.get("url", ""),
               "aff": aff, "why": why, "notes": notes, "age": age,
               "labels": labels_of(pr), "author": author,
               "size": (pr.get("additions", 0) + pr.get("deletions", 0)),
               "decision": pr.get("reviewDecision") or "—",
               "mergeable": pr.get("mergeable") or "—",
               "draft": pr.get("isDraft")}
        if author == USER:
            rec["score"] = age * 2 + (40 if rec["decision"] == "CHANGES_REQUESTED" else 0) \
                           + (30 if rec["mergeable"] == "CONFLICTING" else 0)
            mine.append(rec)
        elif ref in touched:
            # age is a nudge, not the ranking. Uncapped it put an 825-day-old
            # thread above everything actionable.
            rec["score"] = aff + min(age, 45)
            rec["prior"] = touched[ref]
            rec["role"] = my_roles.get(ref, "") or "engaged"
            awaiting.append(rec)
        elif not rec["draft"]:
            rec["score"] = aff - min(age, 60) * 0.5
            reviewq.append(rec)

    for iss in issues:
        num = iss["number"]
        ref = f"{repo}#{num}"
        aff, why, notes = affinity(iss, vocab, notes_for)
        age = age_days(iss.get("updatedAt", ""))
        labs = labels_of(iss)
        boost = 0
        for l in labs:
            ll = l.lower()
            if "good first issue" in ll or "help wanted" in ll:
                boost += 25
            if "bug" in ll:
                boost += 10
        assigned = bool(iss.get("assignees"))
        rec = {"n": num, "title": iss.get("title", ""), "url": iss.get("url", ""),
               "aff": aff, "why": why, "notes": notes, "age": age,
               "labels": labs, "assigned": assigned}
        if ref in touched:
            rec["score"] = aff + min(age, 45)
            rec["prior"] = touched[ref]
            rec["role"] = my_roles.get(ref, "") or "engaged"
            awaiting.append(rec)
        elif not assigned:
            rec["score"] = aff + boost - min(age, 90) * 0.3
            picks.append(rec)

    for lst in (mine, awaiting, picks, reviewq):
        lst.sort(key=lambda r: -r["score"])

    out = []
    A = out.append
    A("---")
    A("tags: [best-picks, triage, backlog, "
      + ", ".join(t.lower() for t, _ in vocab.most_common(10)) + "]")
    A(f"github: {repo}")
    A(f"generated: {NOW.isoformat(timespec='seconds')}")
    A("---\n")
    A(f"# Best picks for me — {repo}\n")
    A("**Search Tags/Keywords:** #best-picks #triage #backlog #"
      + " #".join(t for t, _ in vocab.most_common(14)) + "\n")
    A(f"**GitHub Context:** {repo} · {len(prs)} open PRs · {len(issues)} open issues "
      f"· scored against {len(touched)} threads I have already worked on\n")
    A("Generated by `knowledge-creator/pick-for-me.py`. Complements `triage.sh`, which "
      "ranks the same backlog by community signal rather than by my history.\n")
    A("---\n")

    def table(title, rows, cols, n=12):
        A(f"\n## {title}\n")
        if not rows:
            A("_nothing here — good._\n")
            return
        A("| " + " | ".join(c[0] for c in cols) + " |")
        A("|" + "|".join("---" for _ in cols) + "|")
        for r in rows[:n]:
            A("| " + " | ".join(str(c[1](r)) for c in cols) + " |")
        A("")
        for r in rows[:n]:
            if r.get("notes") or r.get("prior"):
                srcs = ([r["prior"]] if r.get("prior") else []) + r.get("notes", [])
                links = " · ".join(f"[{Path(s).stem[:44]}]({s.replace(' ', '%20')})"
                                   for s in dict.fromkeys(srcs))
                A(f"- **#{r['n']}** why you: {', '.join(r['why'][:5]) or 'general fit'}")
                A(f"  - your notes: {links}")
        A("")

    table("1. Finish what you started", mine,
          [("PR", lambda r: f"[#{r['n']}]({r['url']})"),
           ("title", lambda r: r["title"][:60]),
           ("idle", lambda r: f"{r['age']}d"),
           ("review", lambda r: r["decision"]),
           ("merge", lambda r: r["mergeable"]),
           ("size", lambda r: r["size"])])

    table("2. Awaiting my reply", awaiting,
          [("#", lambda r: f"[#{r['n']}]({r['url']})"),
           ("title", lambda r: r["title"][:60]),
           ("my role", lambda r: r.get("role", "")[:28]),
           ("idle", lambda r: f"{r['age']}d"),
           ("fit", lambda r: r["aff"])])

    table("3. Best new picks", picks,
          [("issue", lambda r: f"[#{r['n']}]({r['url']})"),
           ("title", lambda r: r["title"][:60]),
           ("fit", lambda r: r["aff"]),
           ("labels", lambda r: ", ".join(r["labels"][:3])),
           ("idle", lambda r: f"{r['age']}d")])

    table("4. Review queue", reviewq,
          [("PR", lambda r: f"[#{r['n']}]({r['url']})"),
           ("title", lambda r: r["title"][:60]),
           ("by", lambda r: "@" + r["author"]),
           ("fit", lambda r: r["aff"]),
           ("size", lambda r: r["size"])])

    text = "\n".join(out) + "\n"
    print(text[:4000])

    if write:
        owner, name = repo.split("/")
        d = DEVON / "Projects" / (name.replace("logging-", "") or name)
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"00-best-picks-{repo.replace('/', '-')}.md"
        f.write_text(text, encoding="utf-8")
        print(f"\nwritten: {f}")
    else:
        print("\n(--write to save this into the knowledge base)")


if __name__ == "__main__":
    main()
