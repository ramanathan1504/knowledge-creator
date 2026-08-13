# AI-OPTIONAL.md

**How this knowledge base works with no AI, with a local model, and with Claude.**

Companion to `README.md`, which is the operating manual. This one is Q&A, no
code. It exists to answer one question: *how much of this still works when the
AI is switched off?*

Short answer: **all of the storing, and most of the finding.**

---

## 0. The fact everything else follows from

**Q. Does any part of this pipeline use an LLM?**

No. Not one line. I checked every script for API calls, SDK imports, and
network use. The only outbound call in the whole repo is `gh` talking to
GitHub in `oss-harvest.py`.

Everything in `Reference/` — the knowledge map, the topic digests, the snippet
libraries, the gap scorecards, the mind map — is produced by deterministic
Python: regex counts, heading extraction, table-of-contents scoring, keyword
matching. The word "llm" appears in the scripts only inside topic-classifier
patterns, because you have notes *about* AI. No AI was used to file them.

**Why this matters:** the base is not an AI product with a text fallback. It
is a plain-Markdown archive with an AI accelerator bolted on the side. Pull
the accelerator off and you still have 556 notes, 4.5M words, 221 MB, fully
indexed and readable, that will open in any editor in 2035.

---

## 1. Without any AI at all

**Q. What can I actually do?**

Everything except *asking questions in a sentence*. Concretely:

| Need | Tool | AI? |
|---|---|---|
| "What did I conclude about X?" | `Reference/topics/<topic>.md` | no |
| "Which notes touch X, how heavily?" | `Reference/00-knowledge-map.md` | no |
| "The snippet I worked out once" | `Reference/snippets/<topic>.md` | no |
| "What have I *not* covered?" | `Reference/gaps/<tech>.md` | no |
| "Find this exact string" | grep / ripgrep | no |
| "Find this vaguely" | DEVONthink full-text search | no |
| "Notes similar to this one" | DEVONthink *See Also* | no |
| "What should I work on next?" | `oss pick` — in the core | no |

**Q. What is the right order to search in?**

This is the part worth memorising, because it is also the discipline in §4.

1. **The digest first.** `Reference/topics/<topic>.md` is the only file that
   states *conclusions* rather than listing links. It is grouped by subtopic
   as problem → what resolved it. Usually it answers outright and tells you
   which note holds the detail.
2. **Then the map.** `00-knowledge-map.md` when you need breadth, not answers
   — it counts matches and hands you every link.
3. **Then grep.** For an exact identifier, error string, or class name, grep
   beats everything, including AI. It is faster and it cannot be wrong.
4. **Then DEVONthink.** For the fuzzy case where you don't recall the words.

Quote the digest to orient; cite the underlying note as evidence. The digest
is derived — never cite it as the source.

**Q. What does DEVONthink give me without the Pro AI features?**

More than people assume. Full-text search across everything. *See Also &
Classify*, which works on text similarity alone and needs no LLM. *Create
Concordance*, a word index over the database. That is a real semantic-ish
search layer for zero dollars and zero tokens.

The catch, and the reason the harvesters write a `Search Tags/Keywords` line
into every note: DEVONthink Standard cannot infer what a document is about.
The tags have to be *literally in the text*. They are. That is not decoration,
it is the no-AI search layer doing its job.

**Q. So what genuinely breaks without AI?**

Three things, honestly:

- **Natural-language questions.** "Why did the JDBC appender fail?" is not a
  grep. You have to translate it into terms yourself.
- **Synthesis across many notes.** Reading 12 notes and producing one answer.
  The digests pre-compute this per topic, but only per topic, and only for the
  structure the harvesters emit.
- **New writing.** Blog drafts, PR review prose, summarising a thread you have
  not read.

Everything else is intact.

---

## 2. With a local model

**Q. Can a local model do what Claude does here?**

For *using* the knowledge — largely yes. For *reasoning hard about code* — no,
not at the size you can run.

Split the job in two, because they have very different answers:

- **Retrieval** ("find me the right 5 notes"): a local model matches cloud AI
  almost exactly. This is the high-value part and it is well within reach.
- **Generation** ("now reason about them and write the fix"): this is where
  model size shows, and where a 4B model and Claude are not comparable.

The good news is that retrieval is the part you use most, and the part that
makes the base feel smart.

**Q. What is my actual hardware ceiling?**

Apple M2, 8 cores, **8 GB RAM**. That is the binding constraint, and it is
tighter than the model list suggests. Of what you already have pulled:

| Model | Size | Verdict on 8 GB |
|---|---|---|
| `qwen3:14b` | 9.3 GB | exceeds RAM — will swap, unusable |
| `qwen2.5-coder:7b` | 4.7 GB | fits, but little headroom |
| `qwen3:8b` | 5.2 GB | borderline, pressure with anything else open |
| `qwen3:4b` | 2.5 GB | **the practical working model** |
| `qwen3-log4j` | 1.2 GB | your custom one — fast, narrow |

These are chat models — the generation half. Treat `qwen3:4b` as the daily
driver and `qwen3:8b` as the "close everything else first" option. `qwen3:14b`
is not a realistic choice on this machine. Nothing here does the embedding;
that is covered next.

**Q. Which of these does the embedding?**

None. The core does, inside its own process. It ships a small ONNX embedder
(all-MiniLM-L6-v2, quantised, about 22 MB) that you fetch once with `oss model
--fetch`; it lands in `~/.oss-cli/models`. There is no Ollama model to pull for
this, no server to keep running, and nothing to choose. Ollama is used here only
for chat and generation.

That embedder matters more than the chat model. The base is ~6M tokens. No model
— local or cloud, today or in five years — puts that in a context window.
*Every* setup must retrieve first and read second, so retrieval quality sets the
ceiling: a bigger chat model cannot recover text the embedder never read.

It embeds all 556 notes in minutes, runs entirely on CPU, costs nothing per
query, and gives you the one thing grep cannot: finding a note that means what
you asked without containing the words you used. Skip the fetch and search still
answers, by shared terms rather than shared meaning — weaker, not broken.

Reasonable expectation: semantic search over the whole base, answers cited
back to real notes, on a machine with no network. That is genuinely close to
what you get from the cloud for the *using-my-notes* case.

**Q. Where will a local model visibly fall short?**

- **Long multi-step reasoning.** Following a bug across five files, holding
  the whole chain, staying correct at the end.
- **Instruction-following under pressure.** Small models drift from format
  and length constraints, especially in long sessions.
- **Confident wrongness.** A 4B model will summarise a note it retrieved badly
  with exactly the same confidence as one it retrieved well. Citations are not
  optional — you need every claim traceable to a file you can open.
- **Tool use / agentic loops.** The multi-step edit-run-test cycle is where
  the gap is widest. This is not a near-term local win.

**Q. Can local ever feel like claude-cli in the terminal?**

For the knowledge half, yes, and it's a realistic goal: ask in a sentence, get
an answer with citations, no network, no cost. That is achievable now with
what is already installed.

For the *coding-agent* half — reading a repo, planning, editing, running
tests, reacting to failures — no, not at 8 GB. Be clear-eyed about which half
you are trying to replace. The knowledge half is the one that pays off, and
it is the one you can have.

**Q. What would I set up, in order?**

1. Fetch the built-in embedder once (`oss model --fetch`), embed all 556 notes,
   store the vectors locally.
2. Wire a query path: question → embed → top-N notes → feed to `qwen3:4b`
   with a hard rule to cite file paths and refuse when the notes don't cover it.
3. Keep it a thin terminal command, so it sits beside grep rather than
   replacing it.
4. Re-embed when the harvesters run. Same cadence as `devon-index.sh --sync`.

No new infrastructure. The embedder ships inside the core, and Ollama — needed
only for step 2, the generation half — is already installed with the models
pulled.

---

## 3. With Claude (claude-cli)

**Q. What does the cloud model actually add?**

- Reading 20 notes and synthesising one grounded answer.
- Multi-step work: find the leak, fix the regex, scrub the files, verify.
- Writing that has to be *good* — blog drafts, PR review prose.
- Noticing what you did not ask about. The credential leak surfaced from a
  question about something else entirely.

**Q. What does it not add?**

- It does not know your notes unless it reads them, and it reads them through
  the same grep and the same digests you would use. **The retrieval layer is
  shared.** Claude is fast because `Reference/` already exists, not instead of it.
- It cannot beat grep at exact-string lookup.
- It is not the archive. The Markdown is the archive.

**Q. What is the actual risk?**

Not wrong answers — you can check those. The risk is **atrophy**: reaching for
the model before reaching for what you already know, until you stop knowing
what you know. A base of 4.5M words of your own hard-won experience is only an
asset if you can still navigate it yourself.

---

## 4. The habit

> Surf your own experience first. Prompt second.

For any question about something you have worked on before:

1. **Digest.** `Reference/topics/<topic>.md`. Ten seconds.
2. **Grep.** If you know the string. Faster than any model.
3. **DEVONthink.** If you know the shape but not the words.
4. **Then AI** — local or cloud — for the questions the first three could not
   answer: synthesis, natural language, multi-step work.

Steps 1–3 need no AI, no network, and no tokens. They cover more than you'd
expect. Step 4 gets better when you arrive already knowing what is in the base
— you ask a sharper question and you can tell immediately when the answer is
wrong.

The base was built to make you less dependent on the model, not more. Nothing
in it requires AI to stay useful. That is the design, and it is worth keeping.

---

## One-line summary

| | Store & organise | Find exact | Find fuzzy | Ask in a sentence | Reason & build |
|---|---|---|---|---|---|
| **No AI** | full | full | good | — | — |
| **Local** | full | full | full | good | limited |
| **Claude** | full | full | full | full | full |

The first row is the floor, and the floor is high. Everything above it is an
accelerator you can lose without losing the archive.
