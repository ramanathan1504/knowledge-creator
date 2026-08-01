#!/usr/bin/env python3
"""
pr-review-file.py — file a hand-written PR review into the knowledge base.

A review write-up is the one artefact the harvesters cannot produce. `oss-harvest.py`
collects what was *said publicly* on a thread; a review is the reasoning that got
there, including the parts that were deliberately never posted. Those were living
in ~/log4j-pr-review/ where nothing indexed them.

This takes the markdown you wrote, derives a retrieval header from the PR itself,
and files it under the topic that owns the repository:

    Projects/<topic>/pr-reviews/YYYYMMDD-<topic>-pr<N>-<slug>.md

Usage
-----
    ./pr-review-file.py ~/log4j-pr-review/pr-4217.md            # dry run
    ./pr-review-file.py ~/log4j-pr-review/*.md --apply          # write
    ./pr-review-file.py notes.md --pr 4217 --apply              # number not in filename
    ./pr-review-file.py review.md --repo jreleaser/jreleaser --apply

Dry-run by default, like every other script here.

The source file is copied, never deleted: it is your working copy and re-running
this must stay possible. Delete it yourself once you are happy with what landed.

Idempotent. A second run for the same PR overwrites the note it wrote before and
keeps that note's original date prefix, so re-filing after an edit does not leave
two dated copies of the same review behind.
"""

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from kbpaths import ARCHIVE, SCRIPTS

PROJECTS = ARCHIVE / "Projects"
DEFAULT_REPO = "apache/logging-log4j2"

# The archive's conventions live in oss-harvest.py: how a title becomes a filename,
# what counts as a code-ish keyword, and which topic folder owns a repository.
# Imported rather than copied -- two definitions of "slug" would drift, and a note
# filed under a different rule is a note nobody finds again.
def _harvester():
    spec = importlib.util.spec_from_file_location("ossharvest", SCRIPTS / "oss-harvest.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ossharvest"] = mod
    spec.loader.exec_module(mod)
    return mod


OH = _harvester()
slug, CODEISH, REPO_TOPIC = OH.slug, OH.CODEISH, OH.REPO_TOPIC


def gh_json(args):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"gh {' '.join(args[:4])}… failed: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout) if r.stdout.strip() else None


def pr_number(path, explicit):
    if explicit:
        return explicit
    m = re.search(r"(?:pr[-_]?)(\d{2,6})", path.stem, re.I) or re.search(r"(\d{2,6})", path.stem)
    if not m:
        raise SystemExit(f"{path.name}: no PR number in the filename -- pass --pr N")
    return int(m.group(1))


def fetch(repo, num):
    # No "merged" field: `gh pr view` does not expose one -- a merged PR reports
    # state MERGED, and asking for it makes gh reject the whole request.
    fields = ("number,title,author,state,url,createdAt,updatedAt,isDraft,"
              "baseRefName,headRefName,additions,deletions,changedFiles,labels,"
              "closingIssuesReferences")
    return gh_json(["pr", "view", str(num), "--repo", repo, "--json", fields])


def build_tags(review_text, pr, repo, topic, issues):
    owner, name = repo.split("/")
    # Pinned: never ranked, never dropped. These are the handles you actually search
    # by ("every pr-review I wrote", "what did I say about issue 4181"), and none of
    # them occur often enough in the prose to survive a frequency cut -- the first
    # version of this lost `pr-review` and `issue-4181` from every note it filed.
    pinned = {owner.lower(), name.lower(), topic, "pr-review", "codereview",
              f"pr-{pr['number']}"}
    pinned |= {f"issue-{i}" for i in issues}
    pinned |= {lb["name"] for lb in pr.get("labels") or []}

    # The review prose is the richest source of identifiers -- it names the classes,
    # methods and config attributes the reader will search for years later. The PR
    # title alone is far too thin.
    text = review_text + " " + (pr.get("title") or "")
    found = set()
    for m in CODEISH.finditer(text):
        found.add((m.group(1) or m.group(0)).split(".")[-1].split("(")[0])

    def clean(raw):
        out = set()
        for t in raw:
            t = re.sub(r"[^\w.-]", "-", str(t)).strip("-")
            if 2 < len(t) <= 40:
                out.add(t)
        return out

    pinned, found = clean(pinned), clean(found) - clean(pinned)
    # CamelCase matching inside a longer identifier yields fragments: `timeToLive`
    # also produces `ToLive`, `cleanupIntervalMillis` produces `IntervalMillis`.
    # A fragment is never what someone searches for, and it crowds out a real name.
    #
    # Being a suffix is not enough to convict, though: `PurgePolicy` is a suffix of
    # `IdlePurgePolicy` and is also the interface people look up by name. The test is
    # whether it EVER stands alone -- a true fragment occurs exactly as often as the
    # identifier containing it, a real name occurs more often than that.
    def is_fragment(t):
        return any(o != t and o.endswith(t) and text.count(t) <= text.count(o)
                   for o in found | pinned)

    found = {t for t in found if not is_fragment(t)}

    ranked = sorted(found, key=lambda t: (-text.count(t), t.lower()))
    return sorted(pinned) + ranked[: max(0, 30 - len(pinned))]


def existing_note(folder, num):
    """The note a previous run wrote for this PR, if any."""
    hits = sorted(folder.glob(f"*-pr{num}-*.md"))
    return hits[0] if hits else None


def header(pr, repo, tags, issues, related, filed_on):
    nwo = repo
    author = (pr.get("author") or {}).get("login", "unknown")
    state = pr.get("state", "?")
    if pr.get("isDraft"):
        state += " (draft)"

    issue_txt = ""
    if issues:
        issue_txt = " · closes " + ", ".join(
            f"[#{i}](https://github.com/{nwo}/issues/{i})" for i in issues)

    L = [
        "---",
        f"tags: [{', '.join(tags)}]",
        f"github: {nwo}#{pr['number']}",
        f"url: {pr['url']}",
        "kind: pr-review",
        f"pr_state: {state}",
        f"filed: {filed_on}",
        "---",
        "",
        f"# {nwo} PR #{pr['number']} — {pr.get('title')}",
        "",
        "**Search Tags/Keywords:** " + " ".join("#" + t for t in tags),
        "",
        f"**GitHub Context:** [{nwo}#{pr['number']}]({pr['url']}) · opened by @{author} · "
        f"state `{state}`{issue_txt}",
        "",
        f"**Diff:** `{pr.get('baseRefName')}` ← `{pr.get('headRefName')}` · "
        f"+{pr.get('additions')} −{pr.get('deletions')} across {pr.get('changedFiles')} files",
    ]
    if related:
        L += ["", "**Related:** " + " · ".join(f"[[{r}]]" for r in related) + " · [[00-INDEX]]"]
    L += ["", "---", ""]
    return "\n".join(L)


def write_index(folder, apply):
    """
    Index of this folder only.

    Deliberately NOT Projects/oss-github/00-INDEX.md: that one is regenerated
    wholesale by oss-harvest.py on every run and says so in its own footer, so
    anything written there is lost within a day.
    """
    rows = []
    for p in sorted(folder.glob("*.md")):
        if p.name == "00-INDEX.md":
            continue
        num, title, filed = None, p.stem, ""
        for ln in p.read_text(encoding="utf-8").splitlines()[:40]:
            if ln.startswith("# ") and "PR #" in ln:
                title = ln[2:].strip()
                m = re.search(r"PR #(\d+)", ln)
                num = int(m.group(1)) if m else None
            elif ln.startswith("filed: "):
                filed = ln.split(":", 1)[1].strip()
        rows.append((num or 0, title, p.name, filed))
    rows.sort(reverse=True)

    L = ["---", "tags: [pr-review, index, log4j, oss, github]",
         f"generated: {datetime.now().isoformat(timespec='seconds')}", "---", "",
         "# PR reviews — index", "",
         "**Search Tags/Keywords:** #pr-review #index #codereview #oss #github",
         "", f"**{len(rows)} review(s).** Hand-written review write-ups: the reasoning "
         "behind a review, including the parts never posted publicly.", "",
         "Generated by `knowledge-creator/pr-review-file.py`. The notes themselves are "
         "hand-written and are never rewritten by any harvester.", "", "---", ""]
    for _, title, fname, filed in rows:
        L.append(f"- [{title}]({fname})" + (f" — filed {filed}" if filed else ""))
    L.append("")
    out = folder / "00-INDEX.md"
    if apply:
        out.write_text("\n".join(L), encoding="utf-8")
    return out, len(rows)


def main():
    ap = argparse.ArgumentParser(description="File a PR review write-up into the knowledge base.")
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--repo", default=DEFAULT_REPO, help=f"default: {DEFAULT_REPO}")
    ap.add_argument("--pr", type=int, help="PR number, when it is not in the filename")
    ap.add_argument("--apply", action="store_true", help="write; otherwise dry run")
    args = ap.parse_args()

    if args.pr and len(args.files) > 1:
        raise SystemExit("--pr applies to a single file")

    topic = REPO_TOPIC.get(args.repo, "oss-misc")
    folder = PROJECTS / topic / "pr-reviews"
    if topic == "oss-misc":
        print(f"note: {args.repo} is not in oss-harvest.py's REPO_TOPIC map, "
              f"filing under 'oss-misc'")

    planned = []
    for src in args.files:
        if not src.is_file():
            print(f"  skip  {src} (not a file)")
            continue
        num = pr_number(src, args.pr)
        pr = fetch(args.repo, num)
        review = src.read_text(encoding="utf-8")
        issues = [i["number"] for i in (pr.get("closingIssuesReferences") or [])]
        tags = build_tags(review, pr, args.repo, topic, issues)

        prev = existing_note(folder, num)
        if prev:
            dest, action = prev, "update"
        else:
            stamp = date.today().strftime("%Y%m%d")
            dest = folder / f"{stamp}-{topic}-pr{num}-{slug(pr.get('title'), 50)}.md"
            action = "create"
        planned.append((src, dest, pr, tags, issues, action))

    # Cross-links need every destination known first, so this is a second pass.
    names = [d.stem for _, d, _, _, _, _ in planned]
    existing = [p.stem for p in folder.glob("*.md") if p.name != "00-INDEX.md"]
    for src, dest, pr, tags, issues, action in planned:
        related = [n for n in dict.fromkeys(names + existing) if n != dest.stem][:6]
        filed = dest.stem[:8]
        filed = f"{filed[:4]}-{filed[4:6]}-{filed[6:8]}" if filed.isdigit() else str(date.today())
        body = header(pr, args.repo, tags, issues, related, filed) + src.read_text(encoding="utf-8")
        print(f"  {action:6} {dest.relative_to(ARCHIVE)}")
        print(f"         {len(tags)} tags · PR '{pr.get('title')[:60]}'")
        if args.apply:
            folder.mkdir(parents=True, exist_ok=True)
            dest.write_text(body, encoding="utf-8")

    idx, n = write_index(folder, args.apply)
    print(f"  index  {idx.relative_to(ARCHIVE)} ({n} review(s))")
    if not args.apply:
        print("\ndry run — nothing written. Re-run with --apply.")
    else:
        print(f"\nfiled into {folder}")
        print("source files left in place; delete them yourself once you are happy.")


if __name__ == "__main__":
    main()
