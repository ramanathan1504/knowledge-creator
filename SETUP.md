# SETUP.md — the whole system, end to end

**Audience:** anyone who wants to run this. No prior knowledge of either repo assumed.

This describes two programs that work together. You can run either one alone.

- **knowledge-creator** (this repo, Python) — the **archive**. Harvests everything you
  have worked on into plain Markdown, redacts secrets on the way in, and builds
  derived indexes. Uses no AI at all.
- **self-analyse** (Java) — the **workbench**. Reads that Markdown, embeds it,
  and answers questions about your own history. Uses whatever model you point
  it at.

Companion reading: [`AI-OPTIONAL.md`](AI-OPTIONAL.md) for how much works with no
AI, [`DEVELOPING.md`](DEVELOPING.md) if you intend to change the code.

---

## 1. The idea in one picture

```
  raw sources                    the archive                    the workbench
  ───────────                    ───────────                    ─────────────
  GitHub threads    ┐                                      ┌─ DEVONthink index
  AI Studio export  ├─ harvest ─► Markdown files ─ embed ─►┤   (search, no AI)
  claude.ai export  ┘  (redact)   = source of truth        └─ vectors + Q&A
  Claude Code logs                                             (local or cloud)
```

Two rules the whole design rests on:

1. **Markdown is the source of truth.** Everything else — the DEVONthink
   database, the SQLite vectors — is derived and can be rebuilt from it.
2. **Secrets are removed once, on the way in.** Nothing downstream re-scrubs, so
   nothing downstream may read from a raw source directly.

---

## 2. What you need

| | Required for | Notes |
|---|---|---|
| Python 3.9+ | the archive | standard library only |
| Java 17 + Maven | the workbench | |
| An embedding model server | the workbench | Ollama by default; any HTTP-compatible endpoint |
| `gh` CLI, authenticated | harvesting GitHub | `gh auth login` |
| DEVONthink (macOS) | **optional** | search UI; the base works without it |

**Model choice is yours.** The workbench stores a model name in config, not in
code. Small embedding models are fast and cheap; larger ones retrieve better.
Pick whatever your machine and budget support — the system records which model
produced each vector so you can change your mind later without corrupting
anything (see §6).

---

## 3. Setting up the archive

Point the harvesters at whatever sources you have. Each is **dry-run by
default** and prints what it would do; `--apply` commits.

```bash
./oss-harvest.py --full          # GitHub issues, PRs and review threads
./aistudio-extract.py --apply    # Google AI Studio conversations
./claude-harvest.py --apply      # claude.ai export + Claude Code sessions
./knowledge-map.py --apply       # coverage map, snippet libraries, mind map
./topic-digest.py --apply        # per-topic "problem -> what resolved it"
./coverage-gap.py --apply        # your notes vs. the official manual
```

`oss-harvest-daily.sh` runs the common set on a schedule.

What you end up with:

```
Projects/<topic>/<source>/    harvested notes, filed topic-first
Reference/topics/<topic>.md   digests — conclusions, not links
Reference/00-knowledge-map.md index — which notes touch what
Reference/snippets/<topic>.md every fenced code block, deduplicated
Reference/gaps/<tech>.md      what the official docs cover and you don't
```

**Redaction is automatic and non-negotiable.** Every harvester runs the same
rules over everything it writes — AWS keys, GitHub tokens, Google API keys,
private keys, bearer tokens, passwords, and database URLs with embedded
credentials. Each run prints a tally:

```
REDACTED:
       2x  aws-access-key
      13x  github-token
       4x  jdbc-credentials
      19x  password
```

If you add a source, it must go through a harvester. Never point anything
downstream at a raw export.

### Optional: DEVONthink

```bash
./devon-index.sh              # report what is indexed (safe, default)
./devon-index.sh --apply      # index the folder, first time only
./devon-index.sh --sync       # re-read files changed on disk
```

Run `--sync` after any harvest so the search index matches what is on disk.

---

## 4. Setting up the workbench

```bash
cd self-analyse
mvn clean package
java -jar target/self-analyse-1.1.0.jar setup      # interactive wizard
```

The wizard writes your GitHub username, model names and paths into SQLite. To
install it as a global command, put a one-line wrapper on your `PATH`:

```bash
#!/usr/bin/env bash
exec java -jar /absolute/path/to/target/self-analyse-1.1.0.jar "$@"
```

Everything lives in `~/.self-analyse/`. If you used a build from before the
rename, your data is carried over from `~/.issue-ai/` automatically on first
run — it moves only when the new location is empty, so it can never overwrite
anything.

---

## 5. Connecting the two

This is the only step that joins the halves. Point the workbench's ingestion at
your **archive**, never at a raw export:

```
drive.paths = <archive>/Projects,<archive>/Tooling,<archive>/Compliance
```

Then:

```bash
self-analyse sync --me
```

It walks those folders, embeds each note, and stores the vector with the name of
the model that produced it.

**Why exclude some folders.** `Personal/` is deliberately kept out of technical
search. `Reference/` is derived from `Projects/` — embedding it would duplicate
signal and it contains very large generated files. Include them if you disagree;
it is one config value.

**Re-run after every harvest.** Unchanged files are skipped by content
comparison, so it is cheap.

---

## 6. Changing your embedding model

Vectors from different models are not comparable. A 384-dimension vector and a
768-dimension one produce meaningless similarity scores rather than an error, so
mixing them degrades results with no visible failure.

Each vector is therefore stored with the model that produced it. Change the
model and the next sync notices, re-embeds, and tells you:

```
Embedding model changed (all-minilm -> nomic-embed-text) — re-embedding 'note.md'...
```

Nothing to clean up by hand. Rows written before this existed have no recorded
model, are treated as unknown, and get re-embedded once.

---

## 7. Daily use

```bash
self-analyse critical              # fast offline ranking, no AI
self-analyse search "<question>"   # semantic search
self-analyse prompt <issue>        # answer locally, or build an expert prompt
self-analyse inspect <issue>       # show what context was retrieved and why
self-analyse backup                # timestamped archive of the database
```

`prompt` answers locally when the retrieved context fits your configured limit
and the model is confident. Otherwise it assembles a structured prompt for you
to paste into any AI you like, or send directly with `--send-*`. That escalation
is the design, not a fallback: you decide what leaves your machine.

---

## 8. If something looks wrong

| Symptom | Cause |
|---|---|
| Empty database after upgrading | Both old and new locations exist; the new one wins. Move the empty one aside. |
| Every question escalates | Configured model not installed, or retrieved context exceeds your limit. |
| Local answers are always empty | Some reasoning models return text in a separate field. Disable thinking mode or choose a non-reasoning model. |
| Search misses notes you know exist | Embedding windows are small. A long note is represented by its opening only. |
| A secret appears in the base | Report it — the redaction rules missed a shape. Rotate the credential; scrubbing storage does not un-expose it. |

---

## 9. What this is not

It does not replace your memory, and it is not a chatbot with your files
attached. It is an archive you own, in a format that outlives every tool that
reads it, with retrieval bolted on top. Turn the AI off and the archive is still
there, still searchable, still yours.

## The daily job and GitHub authentication

`oss-harvest-daily.sh` runs at 09:15 from launchd. `gh` keeps its token in the
macOS keyring, and a launchd job that fires while the Mac is asleep runs on wake
— **before the screen is unlocked**, when the login keychain cannot be read. That
is what a `STAGES FAILED: github(auth)` line means; it is not an expired token.

Three things handle it, in order of preference:

### 1. A read-only token file (recommended for a scheduled job)

Removes the keychain from the picture entirely — `gh` prefers `GH_TOKEN` from the
environment and never consults the keyring when it is set.

```bash
mkdir -p ~/.config/knowledge-creator
printf '%s' 'ghp_your_token_here' > ~/.config/knowledge-creator/gh-token
chmod 600 ~/.config/knowledge-creator/gh-token
```

**Give it read-only scopes.** This job only ever reads: `public_repo` (or
`repo` only if you harvest private repositories), and nothing else. A token that
cannot write is one that cannot post by accident — which matches the rule that
nothing writes upstream without being named and confirmed.

The file must be mode **600**; the script refuses to read it otherwise and says
so, rather than quietly using a token any process on the machine could read.
`KB_GH_TOKEN_FILE` moves it.

> Deliberately **not** the plist's `EnvironmentVariables`: a launchd plist is
> world-readable, lands in backups, and is printed by `launchctl print`.

### 2. Waiting for the keychain (the fallback)

With no token file, the job waits up to **30 minutes** for the keychain, polling
every 30 seconds, and logs that it is waiting. `KB_GH_WAIT_SECONDS` tunes it.

### 3. Catching up afterwards

If the window is missed, the GitHub stage is skipped and a marker is left. Run
the one skipped stage without waiting for tomorrow, and without redoing the four
sources that already succeeded:

```bash
./oss-harvest-daily.sh --catch-up      # no-op if nothing is pending
./kb doctor                            # is it scheduled, and did it fail?
```
