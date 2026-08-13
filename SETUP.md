# SETUP.md — the whole system, end to end

**Audience:** anyone who wants to run this. No prior knowledge of either repo assumed.

This describes two programs that work together. You can run either one alone.

- **knowledge-creator** (this repo, Python) — the **archive**. Harvests everything you
  have worked on into plain Markdown, redacts secrets on the way in, and builds
  derived indexes. Uses no AI at all.
- **self-analyse** (Java) — the **workbench**. Reads that Markdown, embeds it
  in its own process, and answers questions about your own history with
  whatever model you point it at.

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
| ~22 MB for the embedder | the workbench | `oss model --fetch`, once, into `~/.oss-cli/models` |
| `gh` CLI, authenticated | harvesting GitHub | `gh auth login` |
| DEVONthink (macOS) | **optional** | search UI; the base works without it |
| Ollama | **optional** | local text generation only — never used for embedding |

**Embedding needs nothing running; the answering model is your choice.** The
workbench embeds text inside its own process, so there is no server to start,
no endpoint to configure and no embedding model to pick (see §6). Until you
fetch the model, search still works — it falls back to matching terms instead
of meaning rather than failing.

The model that *answers* questions is a name in config, not in code. Pick
whatever your machine and budget support — local through Ollama, or a cloud
model you paste into.

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

## 6. The embedder

The workbench embeds text itself, in the same process, using all-MiniLM-L6-v2
(quantised, ~22 MB, 384 dimensions). Fetch it once:

```bash
oss model --fetch
```

It lands in `~/.oss-cli/models` and is not something you choose or configure.
Search works before you fetch it, by shared terms rather than shared meaning, so
a missing model is a weaker answer and not an error.

Vectors from different embedders are not comparable, and the failure is silent:
mismatched vectors produce a plausible-looking similarity score rather than a
complaint. Each vector is therefore stored with the name of the embedder that
produced it, so vectors left over from an older setup are spotted and re-embedded
on the next sync:

```
Embedding model changed (all-minilm -> all-MiniLM-L6-v2-onnx) — re-embedding 'note.md'...
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
./kb token          # prompts, input hidden, writes the file and checks it
```

That is the whole thing. It creates the directory, writes mode 600 *before* the
secret goes in, strips stray whitespace, and asks GitHub whether the token
actually works — so a bad paste is caught now rather than at 09:15 tomorrow.

The token never appears on screen, in `ps`, or in your shell history. Doing it
by hand instead:

```bash
mkdir -p ~/.config/knowledge-creator
printf '%s' 'ghp_your_token_here' > ~/.config/knowledge-creator/gh-token
chmod 600 ~/.config/knowledge-creator/gh-token
```

**Tick no scopes at all.**

A classic token with **zero scopes selected** can read public information and use
the search API — which is the entire GitHub stage — and it **cannot write
anywhere**. All three repositories are public, so nothing here needs more.

That is not merely sufficient, it is the safest option available:

| Scope | What it actually grants |
|---|---|
| *(none)* | read public data. **Cannot post, comment, or push** ✅ |
| `public_repo` | read **and write** public repos — could comment on `apache/logging-log4j2` ⚠️ |
| `repo` | full control of every repository, public and private ⚠️ |

`public_repo` is commonly described as the "read-only" choice and is not one. A
token that cannot write is the only kind that cannot post by accident, which is
the same rule the upstream guard enforces — held in a second place, by a
credential that could not do it even if something tried.

If you later harvest a private repository, prefer a **fine-grained** token
(read-only permissions on selected repos) over classic `repo`.

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
