# Knowledge base — build notes and operating manual

Built 2026-07-30. A local, searchable, self-maintaining archive of everything
I've worked on, indexed by DEVONthink and updated daily without intervention.

- **Archive** (indexed by DEVONthink): `~/Library/Mobile Documents/com~apple~CloudDocs/Documents/Devon Capture/`
- **Database**: `~/Documents/Knowledge.dtBase2`
- **Scripts and scratch** (this folder): `~/claude-cli/`

Current size: **512 markdown files, 957 files, 220 MB, 956 indexed records.**

---

## 1. What it holds

| area | files | source |
|---|---:|---|
| `Projects/` | 415 | GitHub threads, AI Studio and Claude conversations, hand-written notes |
| `Reference/` | 55 | Java curriculum, syntax notes, snippet libraries, coverage map, topic digests |
| `Personal/` | 33 | career, life, anything non-technical — kept out of technical search |
| `Compliance/` | 11 | SOC 2 policies and audit material |
| `Tooling/` | 4 | how the tools themselves work |
| `_assets/` | 439 | screenshots and raw attachments from AI Studio |

### Filing is topic first, provenance second

```
Projects/log4j/          278   ai-studio/ · claude-web/ · claude-code/ · oss-github/ · loose hand-written notes
Projects/jreleaser/       16
Projects/kafka/           15
Projects/spring/          13   … and 17 more topics
```

It used to be the other way round — `Projects/ai-studio/log4j/` — which meant
the top level of the archive answered "which tool captured this", a question
nobody asks, while the topic sat one level down where no browse could see it.
The visible cost was that Log4j looked like a 13-note folder when the real
figure was 278, and 60 Log4j pastes sat in a `pastes/` bucket filed by *kind*,
unreachable by any topic search. `topic-refile.py` inverted it; the harvesters
now write to the new layout directly.

The source folder still earns its place one level down, because it says what
kind of evidence a note is: `oss-github/` is what was said publicly and merged,
the rest is the reasoning that got there.

Harvested from four places:

- **GitHub** — 130 issue/PR threads + 29 commits across 16 repos, every comment
  and review of mine with the surrounding thread for context
- **Google AI Studio** — 111 conversations (8,978 turns), 68 pastes, 33 documents
- **Claude** — 63 web conversations (2,582 messages) + 10 Claude Code sessions
- **Obsidian** — 85 notes, migrated and retired

Topic coverage, multi-label, from `Reference/00-knowledge-map.md`:

```
log4j 322 · java 290 · java-concurrency 179 · build-tooling 151
apache-process 133 · spring 115 · spring-data 111 · kafka 108 · testing 78
```

Plus **4,633 unique code blocks** in ten snippet libraries: `java`,
`java-concurrency`, `log4j`, `spring`, `spring-data`, `kafka`, `databases`,
`testing`, `build-tooling`, `aws-infra`.

---

## 2. Daily use

### It runs itself

`com.ramanathan.oss-harvest` fires at **09:15** every day and does five things:

1. GitHub — incremental, only threads changed since the last run
2. Google AI Studio
3. Claude — newest export in `~/Downloads` + local Claude Code sessions
   (warns if the export is more than 21 days stale — it's a manual action)
4. Rebuilds the coverage map, mind map and snippet libraries
5. Drafts blog scaffolds for newly-completed work, 2/day

then nudges DEVONthink to reindex, but only if it's already running.

```bash
oss-harvest-daily.sh --status      # loaded? when did it last run?
oss-harvest-daily.sh               # run it now
oss-harvest-daily.sh --uninstall   # stop it
```

Logs in `~/claude-cli/logs/`, auto-truncated at 2 MB.

### Finding things

Search DEVONthink normally. Every note carries a literal keyword line, because
DEVONthink Standard has no LLM to infer what a document is about — so the terms
have to physically be in the text. Searching `StringMatchFilter`,
`LockModeType` or `minAllowedMessageKind` finds the right notes.

Also useful, and available on Standard:

- **Tools > See Also & Classify** — text-similarity related documents, no AI needed
- **Tools > Create Concordance** — word index across the whole database
- `Reference/mindmap.md` — mermaid mind map, renders natively
- `Reference/00-knowledge-map.md` — which notes touch a topic, ranked, with links
- `Reference/topics/<topic>.md` — **what those notes concluded**, read start to finish
- `Reference/gaps/<tech>.md` — **what is missing**, measured against the official manual

The last two answer different questions and the difference is the point. The
knowledge map is an *index*: it counts regex matches and gives you 312 links.
The digests are *read*: `topic-digest.py` pulls the stated problem and the
resolution out of each note, groups them by subtopic and labels each one by
what kind of evidence it is. Centralising the notes was never the hard part —
this is the layer that makes them usable without opening 278 files.

```bash
topic-digest.py                    # dry run: what each digest would contain
topic-digest.py --apply            # all topics
topic-digest.py --apply log4j java # just these
```

### What I have *not* covered

Both of the above only ever look at what is already in the base, so neither can
see a hole — a base with nothing on Log4j lookups will happily report 100% of
its Log4j notes as Log4j notes. `coverage-gap.py` brings in an outside
yardstick: the table of contents of the official manual.

```bash
coverage-gap.py                    # scorecard for all three
coverage-gap.py --apply log4j      # write Reference/gaps/log4j.md
```

| yardstick | source |
|---|---|
| `log4j` | [Log4j 2.x manual](https://logging.apache.org/log4j/2.x/manual/) — 47 areas |
| `spring-boot` | [Spring Boot reference](https://docs.spring.io/spring-boot/) — 47 areas |
| `java` | [dev.java tutorials](https://dev.java/learn/) — 35 areas |

Two grades, and the split matters. **● applied** means at least one non-bot
GitHub thread under `<topic>/oss-github/` covers it — worked, reviewed, merged.
**◑ studied** means notes but no shipped work. Conflating them would have let a
767-turn conversation about JVM internals and a migrated study curriculum read
as production experience, which is a different and weaker claim.

Bot threads are excluded from ● on the same rule `blog-gen.py` uses: `JSON`
scored as applied on the strength of "Bump the maven-patch-updates group",
which is a robot changing a version number.

To add a technology, add its TOC to `YARDSTICKS` in the script. Keep patterns
narrow — `Filters` as a bare word scored every note mentioning a Java stream
filter; what makes it a Log4j filter is the class names.

### What to pick up next

```bash
pick-for-me.py apache/logging-log4j2 --write
```

Ranks the repo's open backlog against **my** history rather than community
popularity, in four sections: finish what you started · awaiting my reply ·
best new picks · review queue. Each recommendation names the notes that make me
the right person for it. Companion to `triage.sh`, which answers the different
question of "what is the state of this repo".

### Turning finished work into posts

```bash
blog-gen.py --list                     # 106 candidates, ranked
blog-gen.py --top 5 --apply            # scaffolds — free, instant
blog-gen.py --top 5 --apply --ai       # Claude drafts the prose
blog-gen.py apache/logging-log4j2#4133 --apply --ai
```

Drafts land in `Devon Capture/Blog/`, indexed like everything else.

Scoring is on substance, not length: merged + authored + a real review argument
+ code + a sane diff size. A dependency bump with a 1,800-word bot thread
scores **zero**, which is the point.

Two modes. **Deterministic** assembles the real title, problem, review
discussion, commits, diffstat and code, leaving a `TODO` where your voice
belongs — nothing invented. **`--ai`** additionally has Claude write the
connective prose, instructed not to add facts. A 996-word draft on the
`ListAppender` thread-safety work came out publishable with light editing.

The daily job runs the deterministic mode only. Prose costs tokens and the
voice should be a deliberate choice, not a cron side effect — so drafts
accumulate quietly and you add `--ai` when you actually want to publish one.

### Capturing something deliberately

Type **`save devon`** to Claude Code. It writes a structured note into
`Devon Capture/` with five sections — Search Tags/Keywords, GitHub Context, The
Problem, The "Why", The Solution — appending to an existing note on the same
subject rather than creating a near-duplicate.

---

## 3. The scripts

| script | what it does |
|---|---|
| `oss-harvest.py` | GitHub → Markdown. `--probe` sizes a run, `--full` re-reads the window, no flag = incremental |
| `oss-harvest-daily.sh` | the five-stage daily refresh + launchd installer |
| `aistudio-extract.py` | Google AI Studio → Markdown, redacted, personal split out |
| `claude-harvest.py` | claude.ai export + Claude Code sessions |
| `knowledge-map.py` | coverage map, mind map, snippet libraries — an index over the notes |
| `topic-digest.py` | `Reference/topics/<topic>.md` — problem → resolution, read out of the notes |
| `coverage-gap.py` | `Reference/gaps/<tech>.md` — the base vs. the official manual, what is missing |
| `topic-refile.py` | one-off: inverted `Projects/` from source-first to topic-first |
| `blog-gen.py` | finished OSS work → publishable drafts, scored and ranked |
| `pick-for-me.py` | personalised backlog ranking |
| `triage.sh` | repo backlog triage → self-contained HTML |
| `log4j-pr-review.sh` | PR review harness: feedback, diff, build, tests, spotless, pollution check |
| `devon-clean-existing.py` | one-off: tidied the original capture folder |
| `devon-migrate.py` | one-off: moved the Obsidian vault in |
| `devon-index.sh` | indexes `Knowledge.dtBase2`; `--sync` refreshes it |

`devon-clean-existing.py`, `devon-migrate.py` and `topic-refile.py` have run
and are one-offs. Keep them: they document how the layout came to be.

`devon-index.sh` is the exception — it stays useful. Run `--sync` after
anything that rewrites files underneath DEVONthink (a harvester run, a
credential scrub) so the search index matches the disk, and
`--fresh --apply` to rebuild the database if it's ever corrupted. The
Markdown on disk is the source of truth; the database is derived.

`topic-refile.py --apply` writes a numbered `topic-refile-undo-N.sh` per run.
Two exist. **Run them highest-numbered first** — a file moved by pass 1 and
again by pass 2 can only be put back in reverse.

Every script is **dry-run by default**. `--apply` commits.

---

## 4. Things that will bite you

**The claude.ai export is a snapshot.** It covers up to the date it was
generated and no further. For anything newer: claude.ai > Settings > Privacy >
Export data, unzip anywhere in `~/Downloads`. The script finds the newest
automatically — by newest *content date*, not file mtime, because unzipped
bundles carry a 1980 timestamp and an mtime sort silently keeps using the stale
one.

**Claude Code keeps only recent sessions.** Older ones are cleaned up locally,
so anything not yet harvested is gone. The daily job covers this.

**Generated output is not yours to edit.** `Projects/*/oss-github/`,
`Projects/oss-github/00-INDEX.md`, `Reference/snippets/`, `Reference/topics/`,
`Reference/gaps/`, `00-knowledge-map.md` and `mindmap.md` are rewritten on
every run. Hand-written
notes go loose in `Projects/<topic>/` or in `Reference/<topic>/` — never inside
a source subfolder, which belongs to a harvester.

**The derived layer must not read itself.** `knowledge-map.py` writes into
`Reference/` and then scans `Reference/` on the next run, so it was ingesting
its own output — a leaked credential in a snippet library got copied forward
every regeneration, and redacting the true source did nothing. Both generators
now skip everything they produce, `Reference/topics/` included. Any new
generator writing under `Reference/` must be added to `SELF_OUTPUT` in
`knowledge-map.py` and `EXCLUDE_AS_EVIDENCE` in `pick-for-me.py`.

`Blog/` is the exception: drafts are written once and never overwritten, so
edit them freely. `blog-gen.py` skips anything already drafted — delete a file
if you want it regenerated.

**Deleting a record in DEVONthink does not scrub its text.** The full-text index
keeps the extracted words after the document is gone. Discovered the hard way:
a deleted RTF's API token was still findable in the database metadata. If
something sensitive gets in, rebuilding the database is the fix, not deleting
the item.

**Secrets: redaction contains, rotation fixes.** All four harvesters replace
credentials with `[REDACTED:<type>]` before writing. They cannot un-expose
anything already leaked. Two real credentials were found and rotated during the
build. To re-check at any time:

```bash
python3 - <<'PY'
import re, pathlib, collections
D = pathlib.Path.home()/"Library/Mobile Documents/com~apple~CloudDocs/Documents/Devon Capture"
MARK = re.compile(r"\[REDACTED:[a-z-]+\]")          # don't match our own markers
PATS = {"aws-key": r"\bAKIA[0-9A-Z]{16}\b",
        "gh-token": r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}",
        "google-key": r"\bAIza[0-9A-Za-z_\-]{35}\b",
        "private-key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----"}
hits = collections.defaultdict(list)
for p in D.rglob("*"):
    if not p.is_file() or p.suffix.lower() in {".png",".jpg",".jpeg",".pdf",".zip"}: continue
    t = MARK.sub("X", p.read_text(encoding="utf-8", errors="replace"))
    for k, rx in PATS.items():
        if re.findall(rx, t): hits[k].append(str(p.relative_to(D)))
print("CLEAN" if not hits else dict(hits))
PY
```

Two shell traps that produced wrong answers during the build, worth avoiding:

- `for f in $(find …)` word-splits on paths with spaces. It reported a folder
  clean that contained live AWS keys. Use `find -print0` with `read -d ''`, or
  Python.
- `cmd | grep -q` with `set -o pipefail` reports failure even on a match:
  `grep -q` exits early, the upstream command dies of SIGPIPE. This made an
  installed launchd job report itself as not installed.

---

## 5. Rebuilding from nothing

The Markdown files are the archive. The database is a derived index.

```bash
devon-index.sh --fresh --apply     # archives any existing DB, creates a new
                                   # one at the same path, indexes the folder

```

Then `oss-harvest.py --full`, `aistudio-extract.py --apply`,
`claude-harvest.py --apply`, `knowledge-map.py --apply`, `topic-digest.py
--apply` to repopulate — or just run `oss-harvest-daily.sh`, which does all
four. `topic-refile.py` is not part of a rebuild: the harvesters write the
topic-first layout directly now.

Nothing here depends on DEVONthink continuing to exist. If it goes away, the
folder is still ~510 plain Markdown files with keyword headers, greppable
forever. That was the point.
