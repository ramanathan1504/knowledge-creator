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
pr-review-file.py — file hand-written markdown into the knowledge base.

A write-up is the one artefact the harvesters cannot produce. `oss-harvest.py` collects what
was *said publicly* on a thread; a write-up is the reasoning that got there, including the
parts that were deliberately never posted.

Two destinations, chosen by whether the file is about one pull request:

    Projects/<topic>/pr-reviews/YYYYMMDD-<topic>-pr<N>-<slug>.md   a review
    <Topic>/<slug>.md                                             everything else

A pull request is recognised only from a DELIBERATE marker -- `pr1234`, `pr-1234` or
`#1234` -- or from `--pr`. There used to be a fallback to any 2-6 digit run in the filename,
which read `oss-1.7.2-macos26-jvm-crash` as a review of PR 26 and filed it under that
unrelated pull request's title and tags. Anything without a marker is now filed as a note
rather than forced through `gh pr view`, which also means a general note finally has a home:
before this, `oss memory file` on one simply refused.

Usage
-----
    ./pr-review-file.py ~/log4j-pr-review/pr-4217.md            # dry run
    ./pr-review-file.py ~/log4j-pr-review/*.md --apply          # write
    ./pr-review-file.py notes.md --pr 4217 --apply              # number not in filename
    ./pr-review-file.py review.md --repo jreleaser/jreleaser --apply
    ./pr-review-file.py architecture.md --apply                 # a note, into Tooling/
    ./pr-review-file.py design.md --topic Reference --apply     # a note, elsewhere

Reviews and notes can be mixed in one call; each file goes where it belongs.

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
    """The pull request this file reviews, or None if it does not review one.

    Only a DELIBERATE marker counts: `pr1234`, `pr-1234`, `pr_1234` or `#1234`. There used to
    be a fallback to any 2-6 digit run in the name, and it was actively harmful -- it read
    `oss-1.7.2-macos26-jvm-crash` as a review of PR 26 and `20260812-notes` as PR 202608, then
    filed the note under that unrelated pull request's title with its tags. A wrong answer
    delivered confidently is worse than no answer, and this archive exists to be trusted a year
    from now.

    Returning None rather than raising is what lets a general note be filed as a general note.
    """
    if explicit:
        return explicit
    m = re.search(r"(?:pr[-_]?|#)(\d{2,6})(?!\d)", path.stem, re.I)
    if m:
        return int(m.group(1))
    # A LEADING run of 3-5 digits is the other deliberate spelling, and the one the reviews
    # written by hand actually use: `4218-loggercontextadmin-stream-leak.md`. Anchored to the
    # start and capped at five digits so it cannot swallow a date -- `20260812-notes` is eight,
    # and `oss-1.7.2-macos26-jvm-crash` does not begin with a digit at all. Both of those were
    # read as pull requests before, which is the bug this whole function exists to not have.
    m = re.match(r"(\d{3,5})-", path.stem)
    return int(m.group(1)) if m else None


def note_header(path, title, tags, topic, related, filed_on):
    """Retrieval header for a note that is not about one pull request.

    Same shape as a review's header so one search finds both, minus the fields that only mean
    something for a pull request. Nothing is invented to fill them.
    """
    L = [
        "---",
        f"tags: [{', '.join(tags)}]",
        "kind: note",
        f"topic: {topic}",
        f"filed: {filed_on}",
        "---",
        "",
        f"# {title}",
        "",
        "**Search Tags/Keywords:** " + " ".join("#" + t for t in tags),
    ]
    if related:
        L += ["", "**Related:** " + " · ".join(f"[[{r}]]" for r in related)]
    L += ["", "---", ""]
    return "\n".join(L)


def note_title(text, path):
    """The first heading, else the filename made readable."""
    for line in text.splitlines()[:40]:
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem.replace("-", " ").replace("_", " ")


def note_tags(text, title, topic):
    """Tags for a general note: the topic, words from the title, and code-ish terms from the body.

    Reuses oss-harvest.py's CODEISH so a note and a review tag the same identifier the same way --
    two tagging schemes in one archive means two searches to find one thing.
    """
    pinned = [topic, "note"]
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", title)]
    body = [m.group(0) for m in CODEISH.finditer(text)]
    seen, out = set(pinned), list(pinned)
    for w in words + body:
        t = slug(w, 40)
        if t and t not in seen and len(t) > 2:
            seen.add(t)
            out.append(t)
        if len(out) >= 18:
            break
    return out


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
    ap.add_argument("--topic", default="Tooling",
                    help="folder for notes that are not PR reviews (default: Tooling)")
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
    notes = []
    for src in args.files:
        if not src.is_file():
            print(f"  skip  {src} (not a file)")
            continue
        num = pr_number(src, args.pr)
        if num is None:
            # Not about one pull request, so do not pretend it is. It gets a note header and a
            # topic folder instead of being forced through `gh pr view` -- which is how a crash
            # write-up once landed under an unrelated PR's title.
            notes.append(src)
            continue
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

    if notes:
        nfolder = ARCHIVE / args.topic
        existing_notes = [q.stem for q in nfolder.glob("*.md")] if nfolder.is_dir() else []
        for src in notes:
            text = src.read_text(encoding="utf-8")
            title = note_title(text, src)
            tags = note_tags(text, title, args.topic.lower())
            dest = nfolder / f"{slug(title, 60)}.md"
            action = "update" if dest.exists() else "create"
            related = [n for n in existing_notes if n != dest.stem][:6]
            body = note_header(src, title, tags, args.topic.lower(), related,
                               str(date.today())) + text
            print(f"  {action:6} {dest.relative_to(ARCHIVE)}")
            print(f"         {len(tags)} tags · note '{title[:60]}'")
            if args.apply:
                nfolder.mkdir(parents=True, exist_ok=True)
                dest.write_text(body, encoding="utf-8")

    if not planned:
        if args.apply and notes:
            print(f"\nfiled into {ARCHIVE / args.topic}")
            print("source files left in place; delete them yourself once you are happy.")
        elif not notes:
            print("nothing to file")
        else:
            print("\ndry run — nothing written. Re-run with --apply.")
        return

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
