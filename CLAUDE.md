# Working on knowledge-creator

Notes for anyone — human or model — changing this repository.

## What this is

A deterministic harvester. It turns work you have already done into a plain
Markdown archive: GitHub threads you took part in, assistant exports, notes.
Python, standard library only, and **no AI anywhere in it**. The archive is the
product; anything that reads it is somebody else's job.

It attaches to `oss` as a `memory` extension named `devon`, declared in
`oss-ext.json`. Attaching records a path — nothing is uploaded or copied.

## The contract you must not break

`oss` reads two fields out of the frontmatter this repository writes, and uses
them to decide whether a note is the user's own work or material they merely
collected:

```
my_role: none          what you did in the thread
source: repo-scan      how the note was found
```

A note counts as *collected* only when **both** say you had no part in it.
Anything else — including a thread found by scanning that you authored or
reviewed — is the user's own work and ranks higher in retrieval.

Rename or restructure those fields and retrieval silently reclassifies the whole
corpus, with nothing anywhere to notice. `test_oss_harvest.py` holds that
agreement from this side. Run it:

```bash
python3 -m unittest discover -p 'test_*.py'
```

## Traps already paid for

**A named repository cannot carry the default exclusion.** `EXCLUDE` is
`-user:<me>`, which keeps your own repositories out of an archive of
contributions to other people's. Combined with `repo:<mine>` it contradicts
itself, and GitHub answers a contradiction by dropping the `repo:` filter and
returning a thousand threads from everywhere. Pass `exclude=False` for any
qualifier that already names its repository.

**One unreadable file must not end its folder.** These files usually live in a
cloud-synced directory that fetches bytes on demand, so a single stalled
download used to abort the whole folder and leave a partial index reported as a
complete one. Reads fail per file, and say so.

**Harvesting and embedding are two steps joined by a path.** This writes
Markdown; `oss sync --me` reads the configured folders and embeds it. Without
that second step the day's work is on disk and invisible to every answer, which
looks exactly like a harvest that never ran. The daily script runs both.

## Reading only

Nothing here writes to any repository. It fetches through `gh` and writes
Markdown locally. Keep it that way.

## Scope

`KB_HARVEST_REPOS` names repositories to harvest whole; unset by default,
because scanning somebody's repository is a decision with a rate-limit cost
rather than something to infer.

Environment overrides, all honoured: `KB_GH_USER`, `KB_ARCHIVE`, `KB_SOURCES`,
`KB_EXCLUDE`, `KB_WINDOW_START`. Tests set the first two so importing a module
never reaches the network.
