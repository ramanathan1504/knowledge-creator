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
coverage-queue.py — the queue of what is left to learn, and what is done.

`coverage-gap.py` answers "what does this base not know", by scoring the notes
against the official manual of each technology. That is a measurement, and a
measurement is not a plan: it says 12 areas are studied-but-never-shipped and
stops there.

This is the plan. One markdown note per area still to learn, each carrying the
doc link, a workout that runs against real code, and the checks that say it is
done. Two folders:

    Reference/coverage/to-cover/   still to do
    Reference/coverage/covered/    you moved it here when you did it

THE MOVE IS THE RECORD. Nothing sets a flag in a database, because a database
is a thing to keep in sync and a folder is a thing you can see. `done` performs
the move for you and rewrites the frontmatter to match; doing it by hand with
`mv` is equally valid and the index will agree either way.

The queue is driven by the MANUAL, never by whatever arrived this week. A pull
request can point at an area; it cannot add one. That is what keeps a curriculum
stable while a review queue churns.

    ./coverage-queue.py                     what is left, grouped by topic
    ./coverage-queue.py next                the one to do next, and why that one
    ./coverage-queue.py show log4j-markers  print a note
    ./coverage-queue.py done log4j-markers --apply
    ./coverage-queue.py refresh --apply     add notes for newly-measured gaps
    ./coverage-queue.py index --apply       rewrite 00-INDEX.md

Dry run by default, like everything else here.
"""

import re
import sys
from datetime import date
from pathlib import Path

from kbpaths import ARCHIVE as DEVON

COV = DEVON / "Reference/coverage"
TODO = COV / "to-cover"
DONE = COV / "covered"
GAPS = DEVON / "Reference/gaps"

# The four scores coverage-gap.py assigns. Only the first three are a gap;
# "applied" means it was worked in a public thread, which is the bar.
SCORE = {
    "○": "nothing — 0 notes",
    "◐": "thin — one or two notes",
    "◑": "studied — notes but no shipped work",
}
# Do the ones you know least about first. Within a score, fewest notes first:
# the area with 4 notes is closer to unknown than the one with 48.
ORDER = {"○": 0, "◐": 1, "◑": 2}

RED, GREEN, DIM, BOLD, OFF = "\033[31m", "\033[32m", "\033[2m", "\033[1m", "\033[0m"


def die(msg):
    print(f"{RED}error{OFF} {msg}", file=sys.stderr)
    raise SystemExit(1)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def note_name(tech, area):
    """`Log4j IOStreams` under log4j is log4j-iostreams, not log4j-log4j-iostreams.

    The manual names several of its own areas after the product -- "Log4j IOStreams",
    "Log4j Spring Cloud Config" -- and prefixing those again reads like a typo and
    sorts away from its siblings.
    """
    s = slug(area)
    prefix = tech + "-"
    return tech + "-" + (s[len(prefix):] if s.startswith(prefix) else s)


def field(text, key):
    m = re.search(rf'^{key}: *"?(.*?)"?$', text, re.M)
    return m.group(1) if m else ""


def read(folder):
    """Every note in a folder, as (topic, area, section, gap, status, path)."""
    out = []
    for f in sorted(folder.glob("*.md")):
        if f.name.startswith("00-"):
            continue
        t = f.read_text()
        out.append((field(t, "topic"), field(t, "area"), field(t, "section"),
                    field(t, "gap"), field(t, "status"), f))
    return out


def rank(row):
    """Sort key: least-known first, then fewest notes, then alphabetical."""
    gap = row[3]
    symbol = gap[:1]
    notes = re.search(r"(\d+) notes", gap)
    return (ORDER.get(symbol, 9), int(notes.group(1)) if notes else 0, row[1])


# ---------------------------------------------------------------------------
# The measured gaps, read back out of coverage-gap.py's own output.
#
# Parsed rather than recomputed on purpose: two implementations of "what counts
# as covered" would drift, and the one that drifts silently is the one nobody
# runs. Reference/gaps/<tech>.md is the yardstick, and if it is stale the answer
# here is to re-run coverage-gap.py, not to second-guess it.
# ---------------------------------------------------------------------------
ROW = re.compile(r"^\| ([◐○◑●]) \| (.+?) \| *(\d*) *\| *(\d*) *\| *(\d*) *\| (.*) \|$")


def measured(tech):
    path = GAPS / f"{tech}.md"
    if not path.exists():
        return []
    out, section = [], ""
    for line in path.read_text().splitlines():
        head = re.match(r"^## (.+?)\s+\(", line)
        if head:
            section = head.group(1)
        m = ROW.match(line)
        if m and m.group(1) in SCORE:
            link = re.search(r"\(([^)]*)\)", m.group(6))
            out.append({
                "symbol": m.group(1), "area": m.group(2).strip(), "section": section,
                "notes": m.group(3) or "0",
                "strongest": (link.group(1) if link else "").replace("../../", ""),
            })
    return out


SKELETON = """---
tags: [coverage, to-cover, {tech}, {slug}, learning, curriculum]
topic: {tech}
area: {area}
section: {section}
status: to-cover
gap: "{symbol} {score}"
created: {today}
---

# {tech} — {area}

**Search Tags/Keywords:** #coverage #to-cover #{tech} #{slug} #whattolearnnext

**Status:** `to-cover`. When the *Done when* boxes are all ticked, change `status:` to `covered`
and **move this file to `Reference/coverage/covered/`** — or run
`coverage-queue.py done {tech}-{slug} --apply`, which does both.

**Where this came from:** `Reference/gaps/{tech}.md`, section *{section}*, scored **{symbol} {score}**.
Not from any single PR — this is the manual's own list of what the technology documents.

## What it is

_Not written yet. This note was created by `coverage-queue.py refresh` because the area appeared
in the measured gaps. Fill in what it is in your own words — that is most of the learning._

## Why it bites

_Where this shows up in real work: which class, which config, which review argument._

## Read

- The manual section for **{area}**
{strongest}
## Workout — prove it, don't just read it

_A command that runs against `~/apache/log4j2-workout` or a real clone. Not a tutorial._

## Done when

- [ ] _the check that would fail if you had only read about it_
"""


def cmd_refresh(apply):
    """Create a skeleton note for any measured gap that has no note yet."""
    existing = {f.stem for f in TODO.glob("*.md")} | {f.stem for f in DONE.glob("*.md")}
    created = []
    for tech in ("log4j", "java"):
        for gap in measured(tech):
            name = note_name(tech, gap["area"])
            # A covered note is not recreated. Learning something and then having
            # the tool hand it back next week is how a queue loses its meaning.
            if name in existing:
                continue
            strongest = (f"- Strongest existing note in the base: `{gap['strongest']}` "
                         f"({gap['notes']} notes mention this area)\n") if gap["strongest"] \
                else "- Nothing in the base yet — this one starts from zero.\n"
            body = SKELETON.format(
                tech=tech, area=gap["area"], section=gap["section"],
                slug=name[len(tech) + 1:],
                symbol=gap["symbol"], score=SCORE[gap["symbol"]], today=date.today().isoformat(),
                strongest=strongest)
            created.append((name, body))
    if not created:
        print("nothing to add — every measured gap already has a note")
        return 0
    for name, body in created:
        print(f"  create to-cover/{name}.md")
        if apply:
            (TODO / f"{name}.md").write_text(body)
    if apply:
        cmd_index(True, quiet=True)
        print(f"{GREEN}✓{OFF} {len(created)} note(s) created")
    else:
        print("\ndry run — nothing written. Re-run with --apply.")
    return 0


def cmd_list(_apply):
    todo, done = sorted(read(TODO), key=rank), read(DONE)
    if not todo and not done:
        die(f"no queue at {COV} — run `coverage-queue.py refresh --apply` first")
    by_topic = {}
    for row in todo:
        by_topic.setdefault(row[0], []).append(row)
    for topic in sorted(by_topic):
        rows = by_topic[topic]
        print(f"\n{BOLD}{topic}{OFF}  {DIM}{len(rows)} to cover{OFF}")
        for topic_, area, section, gap, _status, f in rows:
            print(f"  {gap[:1]}  {area:<26} {DIM}{section:<30} {f.stem}{OFF}")
    print(f"\n{BOLD}covered{OFF}  {len(done)}")
    for _t, area, _s, _g, _st, f in sorted(done, key=lambda r: r[1]):
        print(f"  {GREEN}✓{OFF}  {area}")
    print(f"\n  {DIM}○ nothing · ◐ thin · ◑ studied but never shipped{OFF}")
    print(f"  {DIM}coverage-queue.py next{OFF}")
    return 0


def cmd_next(_apply):
    todo = sorted(read(TODO), key=rank)
    if not todo:
        print(f"{GREEN}✓{OFF} nothing left in to-cover/")
        return 0
    topic, area, section, gap, _status, f = todo[0]
    print(f"\n{BOLD}{topic} — {area}{OFF}   {DIM}({section}){OFF}")
    print(f"  scored {gap}")
    print(f"  {f}")
    # Say why THIS one, so the order is auditable rather than magic.
    print(f"\n  {DIM}chosen because it is the least-known area left: "
          f"{'nothing at all' if gap[:1] == '○' else 'thin' if gap[:1] == '◐' else 'studied but never shipped'},"
          f" and the fewest notes among those.{OFF}")
    print(f"\n  coverage-queue.py show {f.stem}")
    return 0


def cmd_show(_apply, name=None):
    if not name:
        die("show needs a note name, e.g. log4j-markers")
    for folder in (TODO, DONE):
        f = folder / f"{name}.md"
        if f.exists():
            print(f.read_text())
            return 0
    die(f'no note named "{name}" in to-cover/ or covered/')


def cmd_done(apply, name=None):
    if not name:
        die("done needs a note name, e.g. log4j-markers")
    src = TODO / f"{name}.md"
    if not src.exists():
        if (DONE / f"{name}.md").exists():
            print(f"{GREEN}✓{OFF} {name} is already covered")
            return 0
        die(f'no note named "{name}" in to-cover/')
    text = src.read_text()
    open_boxes = text.count("- [ ]")
    if open_boxes:
        # A warning, not a refusal. The boxes are a memory aid the owner wrote
        # for themselves, and a tool that argues with its owner gets stopped
        # being run. Say it, then do what was asked.
        print(f"  {DIM}note: {open_boxes} unticked \"Done when\" box(es) — marking covered anyway{OFF}")
    print(f"  to-cover/{name}.md → covered/{name}.md")
    if not apply:
        print("\ndry run — nothing moved. Re-run with --apply.")
        return 0
    (DONE / f"{name}.md").write_text(re.sub(r"^status: .*$", "status: covered", text, flags=re.M))
    src.unlink()
    cmd_index(True, quiet=True)
    print(f"{GREEN}✓{OFF} covered")
    return 0


def table(rows, rel):
    if not rows:
        return "_(empty)_\n"
    out = "| topic | area | manual section | gap score | note |\n|---|---|---|---|---|\n"
    for topic, area, section, gap, _status, f in rows:
        out += f"| {topic} | **{area}** | {section} | {gap} | [{f.stem}]({rel}/{f.name}) |\n"
    return out


def cmd_index(apply, quiet=False):
    todo, done = sorted(read(TODO), key=rank), sorted(read(DONE), key=lambda r: r[1])
    index = COV / "00-INDEX.md"
    if not index.exists():
        die(f"{index} is missing — it carries the prose this only refreshes the tables in")
    old = index.read_text()
    n_log4j = sum(1 for r in todo if r[0] == "log4j")
    n_java = sum(1 for r in todo if r[0] == "java")
    new = re.sub(
        r"## To cover — .*?(?=## Scoring legend)",
        f"## To cover — {len(todo)} ({n_log4j} log4j · {n_java} java)\n\n{table(todo, 'to-cover')}\n"
        f"## Covered — {len(done)}\n\n{table(done, 'covered')}\n",
        old, flags=re.S)
    mismatched = [f.name for *_r, status, f in todo if status != "to-cover"] \
        + [f.name for *_r, status, f in done if status != "covered"]
    if mismatched:
        print(f"  {DIM}frontmatter disagrees with the folder: {', '.join(mismatched)}{OFF}")
    if apply:
        index.write_text(new)
        if not quiet:
            print(f"{GREEN}✓{OFF} {index} — {len(todo)} to cover, {len(done)} covered")
    else:
        print(f"to cover: {len(todo)}   covered: {len(done)}")
        print("dry run — nothing written. Re-run with --apply.")
    return 0


COMMANDS = {"list": cmd_list, "next": cmd_next, "show": cmd_show,
            "done": cmd_done, "refresh": cmd_refresh, "index": cmd_index}


def main():
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply = "--apply" in sys.argv
    verb = args[0] if args else "list"
    if verb in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if verb not in COMMANDS:
        die(f"unknown command: {verb} (try: {', '.join(COMMANDS)})")
    rest = args[1:]
    if verb in ("show", "done"):
        return COMMANDS[verb](apply, rest[0] if rest else None)
    return COMMANDS[verb](apply)


if __name__ == "__main__":
    raise SystemExit(main())
