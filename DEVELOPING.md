# DEVELOPING.md — claude-cli internals

**Audience:** anyone changing the harvesters. For running the system, see
[`SETUP.md`](SETUP.md).

---

## The one invariant

**Markdown on disk is the source of truth. Everything else is derived.**

The DEVONthink database, the files under `Reference/`, and the vectors held by
self-analyse can all be rebuilt from the notes. The notes cannot be rebuilt from
them. Any change that makes a derived artifact authoritative is a bug.

Practical consequence: never edit generated files. `Reference/topics/`,
`Reference/snippets/`, `Reference/gaps/`, `00-knowledge-map.md` and `mindmap.md`
are all overwritten on the next run. Fix the generator.

---

## Layout

```
oss-harvest.py        GitHub issues/PRs/review threads -> Markdown
aistudio-extract.py   Google AI Studio archive        -> Markdown
claude-harvest.py     claude.ai export + Claude Code  -> Markdown
knowledge-map.py      all notes -> map, snippets, mindmap
topic-digest.py       all notes -> per-topic digests
coverage-gap.py       notes vs. official docs -> gap scorecards
pick-for-me.py        backlog ranked against your own history
triage.sh             repo backlog -> standalone HTML
devon-index.sh        DEVONthink indexing and refresh
```

Everything is standard-library Python 3. No dependencies, no virtualenv, no
network except `gh`. That is deliberate: a five-year archive should not rot
because a package moved.

---

## Conventions every script follows

**Dry run by default.** Running with no flags prints what would happen and
writes nothing. `--apply` commits. Keep this — it is what makes the scripts safe
to run when unsure.

**Print a summary, not a log.** Counts, topic splits, and what was skipped.

**Never drop input silently.** If a file is not handled, say so. This was a real
bug: `aistudio-extract.py` classified conversations with `"." not in p.name`,
meaning "extensionless file". Any conversation whose *title* contained a period —
version numbers, `vs.`, a trailing full stop — matched no branch and vanished
without a word. Release conversations always carry version numbers, so the loss
was systematic. The fix sniffs file content for AI Studio's own keys and prints
an `unclassified` list for anything left over. **Classify by content; report the
remainder.**

---

## Redaction

Four scripts carry the same `REDACTIONS` list. It must stay identical across
them, because it is the only thing standing between a raw export and the
archive.

```python
REDACTIONS = [
    ("aws-access-key",   ...),  ("github-token",  ...),
    ("google-api-key",   ...),  ("slack-token",   ...),
    ("private-key",      ...),  ("password",      ...),
    ("bearer-token",     ...),  ("jdbc-credentials", ...),
]
```

Each pattern captures the surrounding syntax and replaces only the secret, so
`user:secret@host` becomes `user:[REDACTED:label]@host` — the shape stays
readable, the value does not.

**Adding a rule:** add it to all four scripts, then re-harvest. Existing notes
are rewritten on the next run, so a new rule retroactively cleans the base.

**A lesson worth keeping.** The original database rule required a literal
`jdbc:` prefix. A real production credential sat in the base for months in a
sibling environment variable on the *same line* — `DATABASE_URL="postgresql://user:pass@host"` —
with no `jdbc:` prefix, so it was never scrubbed. The rule is now
scheme-agnostic. **Match the shape of the secret, not the context you happen to
have seen it in.**

Test any change against both positives and negatives before shipping: a rule
that also eats `jdbc:postgresql://host:5432/db` (no credential) is worse than no
rule.

---

## Filing

Notes are filed **topic first, source second**: `Projects/<topic>/<source>/`.
Topic is single-label — a note has one home. Coverage measurement is
multi-label, because a Log4j conversation that also teaches Java generics counts
toward both. Do not conflate the two.

`classify()` assigns topics by regex. It is imperfect by design; the digests and
gap reports are what make the base useful, not perfect filing.

---

## The derived layer

| Script | Writes | Answers |
|---|---|---|
| `knowledge-map.py` | `00-knowledge-map.md`, `snippets/`, `mindmap.md` | which notes touch a topic |
| `topic-digest.py` | `topics/<topic>.md` | what those notes concluded |
| `coverage-gap.py` | `gaps/<tech>.md` | what you have not covered |

The distinction between the first two matters. The map **counts matches** and
hands you links. The digest **reads the notes** — it mines the three headings
every harvester emits (`## The Problem`, `## The Solution`, `## The "Why"`) and
produces problem → resolution grouped by subtopic. Quote the digest to orient;
cite the underlying note as evidence.

`coverage-gap.py` is the only one that can answer "what am I missing", because
it scores against an outside yardstick — the official manual's table of
contents. The other two only see what is already there.

No LLM is involved in any of this. Verified: no SDK imports, no API keys, no
network calls outside `gh`.

---

## DEVONthink

`devon-index.sh` indexes rather than imports, so files stay editable on disk and
in your own format. Modes: report (default), `--sync` (re-read changed files),
`--apply` (first-time index), `--fresh --apply` (rebuild).

`--sync` is the routine one. Run it after any harvest.

Two hazards, both handled: `--purge-imported` skips the database's own
Inbox/Tags/Trash and smart groups, which all report as "not indexed" and would
otherwise be deleted; and `--fresh` archives rather than deletes, though the
archive keeps the *old* full-text index, so a secret scrubbed since is still
readable in it.

---

## Testing changes

There is no test suite. The scripts are idempotent and dry-run by default, which
is the substitute. Before `--apply`:

1. Run without flags and read the counts. Did the number of items change in a
   way you can explain?
2. After applying, sweep for credentials:
   `grep -rE "://[^:/@]+:[^@/]+@" <archive> --include='*.md' | grep -v REDACTED`
3. Confirm nothing landed in `unclassified` that should have been handled.

Step 2 is not optional. It is how the `jdbc` gap above was found.
