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
aistudio-extract.py — turn the Google AI Studio archive into knowledge-base
Markdown: 111 chat exports, 66 plain-text pastes, and the loose documents and
screenshots that sit alongside them.

  ./aistudio-extract.py            # dry run: classification + counts
  ./aistudio-extract.py --apply
  ./aistudio-extract.py --apply --no-assets    # skip the 179 MB of PNGs

Decisions baked in
------------------
* Secrets are REDACTED, not dropped. Seven conversations contain AWS keys,
  GitHub tokens, a bearer token or `password=` strings. The surrounding
  troubleshooting is genuinely useful, so each match becomes
  `[REDACTED:aws-access-key]` and the discussion survives. Originals in Google
  Drive are never modified.
* Personal conversations go to `Personal/ai-studio/`, technical ones to
  `Projects/ai-studio/`, so a search for "MDC serialization" is not competing
  with a subscription refund thread.
* Model "thoughts" (`isThought`) are kept but folded into a <details> block.
  They are 943-in-3017 of the turns and often contain the real reasoning, but
  inline they drown the answer.
* Nothing is deleted or moved from Google Drive. This is a one-way read.
"""

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
from kbpaths import AISTUDIO as SRC
from kbio import read_text_resilient
from kbpaths import ARCHIVE as DEVON
PROJ = DEVON / "Projects"
SOURCE = "ai-studio"
PERS = DEVON / "Personal/ai-studio"
ASSETS = DEVON / "_assets/ai-studio"

# Topic first, provenance second: Projects/log4j/ai-studio/, not the reverse.
# The old layout put the harvester at the top of the tree, so Projects/ answered
# "where did this come from" — which nobody asks — and the topic, which was
# already computed right here, sat one level down where no browse could see it.
def tech_dir(topic: str, kind: str | None = None) -> Path:
    return PROJ / topic / SOURCE / kind if kind else PROJ / topic / SOURCE
NOW = datetime.now(timezone.utc)

APPLY = "--apply" in sys.argv
NO_ASSETS = "--no-assets" in sys.argv

# --------------------------------------------------------------- redaction --
REDACTIONS = [
    ("aws-access-key",  re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws-secret",      re.compile(r"(aws_secret_access_key\s*[=:]\s*)\S{20,}", re.I)),
    ("github-token",    re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}")),
    ("google-api-key",  re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("private-key",     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("slack-token",     re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    # `(?!\$\{)` skips template references -- ${DB_PASSWORD}, Log4j's
    # ${secure:sys:...} -- which are pointers to a secret, not the secret.
    ("password",        re.compile(r"(\bpass(?:word|wd)\s*[=:]\s*['\"]?)(?!\$\{)[^\s'\"]{8,}", re.I)),
    ("bearer-token",    re.compile(r"(\bBearer\s+)[A-Za-z0-9._\-]{25,}")),
    ("jdbc-credentials", re.compile(r"((?:jdbc:)?[a-z][a-z0-9+.\-]*://[^\s:/@]+:)[^\s@/]+(@)")),
]

redaction_counts = {}


def redact(text: str) -> str:
    if not text:
        return text
    for label, rx in REDACTIONS:
        def sub(m):
            redaction_counts[label] = redaction_counts.get(label, 0) + 1
            groups = [g for g in m.groups() if g is not None]
            if len(groups) >= 2:                      # keep the surrounding syntax
                return f"{groups[0]}[REDACTED:{label}]{groups[1]}"
            if len(groups) == 1:
                return f"{groups[0]}[REDACTED:{label}]"
            return f"[REDACTED:{label}]"
        text = rx.sub(sub, text)
    return text


# ---------------------------------------------------------- classification --
PERSONAL_TITLES = {
    "Applying Gradients to Figma Text", "Assistance With Refund Request",
    "Guidance On Job Application Questions", "Kaufland E-Commerce Job Openings",
    "Mirroring Phone With Broken Screen", "Profile Summary: Ramanathan Muthu",
    "Removing A Stripped Screw", "Requesting Pro-Rated Subscription Refund",
    "Requesting a College LinkedIn Page", "The Flaws of Modern Recruiting",
    "The Ultimate AI Roast", "Visa Document Request: Ramanathan Muthu",
    "OSS Contribution and Professional Growth", "Release Management And Personal Journey",
    "Adding A Google Meet Link",
}

# First match wins, so this runs MOST SPECIFIC first. Ordering matters more
# than it looks: with the generic `java` pattern earlier in the list it
# swallowed every Spring conversation (spring dropped to 1 of 111).
TOPICS = [
    ("log4j",         r"log4j|logback|jsontemplatelayout|patternlayout|mdc\b|slf4j"),
    ("spring",        r"\bspring\b|spring boot|\baop\b|servlet|@autowired|bean\b"),
    ("kafka",         r"kafka|confluent|splunk|debezium"),
    ("compliance",    r"soc 2|soc2|gdpr|audit"),
    ("security",      r"openssl|captcha|turnstile|hardening|\bcve\b|vulnerab"),
    ("observability", r"opentelemetry|tracing|metrics|prometheus"),
    ("ai-ml",         r"ollama|qwen|fine-tun|local ai|copilot|\bllm\b"),
    ("aws-infra",     r"\baws\b|\bec2\b|\beks\b|\brds\b|kubernetes|keda|zero-downtime"),
    ("databases",     r"\bsql\b|postgres|mysql|redis|jdbc|\bquery\b"),
    ("system-design", r"system design|architecture|rate limiting|scaling|tradeoff"),
    ("java",          r"\bjava\b|jvm|collection|functional interface|disruptor|generics|ffm|jni"),
    ("apache-process", r"\bapache\b|\basf\b|committer|dependabot|milestone|\brat\b|atr\b|release"),
    ("tooling",       r"intellij|datagrip|maven|gradle|git\b|github|ide\b"),
]


def classify(title: str, body: str):
    if title in PERSONAL_TITLES:
        return "personal", "personal"
    hay = (title + " " + body[:4000]).lower()
    for name, pat in TOPICS:
        if re.search(pat, hay):
            return "technical", name
    return "technical", "misc"


# ------------------------------------------------------------------ helpers --
def slug(text, n=70):
    s = re.sub(r"[^\w\s-]", "", (text or "").lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s[:n].rstrip("-") or "untitled"


CODE = re.compile(r"```(\w+)?\n(.*?)```", re.S)
CAMEL = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z0-9]+)+)\b")
TICKED = re.compile(r"`([\w.$#()]{3,40})`")
GH = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)")
HASHNUM = re.compile(r"(?:^|[\s(\[])#(\d{3,5})\b")


def keywords(title, text, topic):
    tags = {topic, "ai-studio", "gemini"}
    for m in CAMEL.finditer(title + " " + text):
        tags.add(m.group(1))
    for m in TICKED.finditer(text):
        tags.add(m.group(1).split(".")[-1].split("(")[0])
    for lang, _ in CODE.findall(text):
        if lang:
            tags.add(lang.lower())
    clean = [re.sub(r"[^\w.-]", "-", str(t)).strip("-") for t in tags]
    clean = [t for t in clean if 2 < len(t) <= 40]
    return sorted(set(clean), key=lambda t: (-text.count(t), t.lower()))[:26]


def gh_refs(text):
    refs = {f"{o}#{n}" for o, n in GH.findall(text)}
    refs |= {"#" + n for n in HASHNUM.findall(text)}
    return sorted(refs)[:30]


def header(title, topic, tags, refs, meta):
    return (
        "---\n"
        f"tags: [{', '.join(tags)}]\n"
        f"topic: {topic}\n"
        f"github: {' · '.join(refs) if refs else 'none identified'}\n"
        f"source: Google AI Studio/{title}\n"
        f"{meta}"
        f"extracted: {NOW.isoformat(timespec='seconds')}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"**Search Tags/Keywords:** {' '.join('#' + t for t in tags)}\n\n"
        f"**GitHub Context:** {' · '.join(refs) if refs else 'none identified'}\n\n"
        f"**Source:** Google AI Studio conversation `{title}`\n\n"
        "---\n\n"
    )


def chunks_of(d):
    cp = d.get("chunkedPrompt") if isinstance(d, dict) else None
    c = cp.get("chunks") if isinstance(cp, dict) else (cp if isinstance(cp, list) else None)
    return [x for x in (c or []) if isinstance(x, dict)]


# ---------------------------------------------------------------- rendering --
def render_chat(title, chunks):
    turns = [c for c in chunks if (c.get("text") or "").strip()]
    dates = [c.get("createTime", "")[:10] for c in chunks if c.get("createTime")]
    body_all = "\n".join(c.get("text") or "" for c in turns)
    kind, topic = classify(title, body_all)
    tags = keywords(title, body_all, topic)
    refs = gh_refs(body_all)

    n_imgs = sum(1 for c in chunks if c.get("driveImage") or c.get("inlineImage"))
    n_docs = sum(1 for c in chunks if c.get("driveDocument") or c.get("inlineFile"))
    meta = (f"date: {dates[0] if dates else 'unknown'}\n"
            f"turns: {len(turns)}\n"
            f"attachments: {n_imgs} image(s), {n_docs} document(s)\n")

    L = [header(title, topic, tags, refs, meta)]
    A = L.append

    A("## The Problem (What & Where)\n")
    first_user = next((c.get("text") for c in turns if c.get("role") == "user"), None)
    A(redact(first_user or "_(no opening question)_").strip() + "\n")

    A('\n## The "Why" (Review Discussions)\n')
    A(f"_Conversation of {len(turns)} turns"
      + (f", {dates[0]} to {dates[-1]}" if dates else "")
      + ". Topic classified as "
      + f"`{topic}`. Model reasoning is folded into collapsible blocks._\n")

    A("\n## The Solution (How)\n")
    qn = 0
    for c in turns:
        role = c.get("role")
        text = redact(c.get("text") or "").rstrip()
        stamp = (c.get("createTime") or "")[:19].replace("T", " ")
        if role == "user":
            qn += 1
            A(f"\n### Q{qn} — {stamp}\n")
            A("\n".join("> " + ln for ln in text.split("\n")) + "\n")
        elif c.get("isThought"):
            A("\n<details><summary>model reasoning</summary>\n")
            A(text + "\n")
            A("\n</details>\n")
        else:
            A(f"\n**Answer** — {stamp}\n")
            A(text + "\n")
        if c.get("driveImage") or c.get("inlineImage"):
            A("\n_[image attached — see `_assets/ai-studio/`]_\n")
    return "".join(L), kind, topic


def render_paste(path: Path):
    raw = path.read_text(encoding="utf-8", errors="replace")
    text = redact(raw)
    title = path.name
    _, topic = classify(title, text)
    tags = keywords(title, text, topic)
    refs = gh_refs(text)
    lang = ("java" if re.search(r"^\s*(package|import)\s+[\w.]+;", text, re.M)
            else "text")
    meta = f"kind: paste\nbytes: {len(raw)}\n"
    return (header(title, topic, tags, refs, meta)
            + "## The Problem (What & Where)\n\n"
            + f"Raw paste captured in AI Studio ({len(raw)} bytes). "
              "Usually a stack trace or a source dump attached to a nearby "
              "conversation.\n\n"
            + "## The Solution (How)\n\n"
            + f"```{lang}\n{text.strip()}\n```\n"), topic


def is_conversation(path, _probe=4096):
    """True if the file is an AI Studio conversation export.

    Sniffs the head for the two keys AI Studio always writes, rather than
    guessing from the filename. Cheap: one short read per candidate.
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            head = fh.read(_probe)
    except OSError:
        return False
    return '"runSettings"' in head or '"chunkedPrompt"' in head


# --------------------------------------------------------------------- main --
def main():
    if not SRC.exists():
        sys.exit(f"source not found: {SRC}")

    images = sorted(p for p in SRC.iterdir()
                    if p.suffix.lower() in (".png", ".jpeg", ".jpg"))
    docs = sorted(p for p in SRC.iterdir()
                  if p.suffix.lower() in (".pdf", ".rtf", ".md", ".docx", ".txt", ".json", ".zip"))

    # A conversation is identified by what is INSIDE it, not by its filename.
    # The old test was `"." not in p.name`, meaning "extensionless => chat". That
    # silently dropped every conversation whose TITLE held a period -- version
    # numbers ("Initiating Release 2.26.1 Process"), abbreviations ("Apache POI
    # vs. AI Conversion"), a trailing full stop ("Log4j Modules Explained.").
    # Those also missed the `docs` suffix list, so they fell through every branch
    # and were never reported. Release conversations always carry a version in
    # the title, so the loss was systematic, not random.
    handled = {p for p in images} | {p for p in docs}
    convs, pastes, unknown = [], [], []
    for p in sorted(SRC.iterdir()):
        if not p.is_file() or p in handled:
            continue
        if p.name.startswith("Paste"):
            pastes.append(p)
        elif is_conversation(p):
            convs.append(p)
        else:
            unknown.append(p)

    print(f"{'APPLY' if APPLY else 'DRY RUN'}\n")
    print(f"  conversations : {len(convs)}")
    print(f"  pastes        : {len(pastes)}")
    print(f"  documents     : {len(docs)}")
    print(f"  images        : {len(images)}"
          f" ({sum(p.stat().st_size for p in images)/1e6:.0f} MB)"
          f"{'  [SKIPPED --no-assets]' if NO_ASSETS else ''}")
    # Never drop a file without saying so.
    if unknown:
        print(f"  unclassified  : {len(unknown)}  (not harvested)")
        for p in unknown:
            print(f"      {p.name}")

    plan, topics, personal = [], {}, []
    for p in convs:
        try:
            d = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            print(f"  !! unreadable: {p.name} ({str(e)[:50]})")
            continue
        cs = chunks_of(d)
        body = "\n".join(c.get("text") or "" for c in cs)
        kind, topic = classify(p.name, body)
        topics[topic] = topics.get(topic, 0) + 1
        (personal if kind == "personal" else plan).append((p, kind, topic))

    print(f"\n  technical : {len(plan)}")
    print(f"  personal  : {len(personal)}")
    print("\n  topic split:")
    for t, n in sorted(topics.items(), key=lambda kv: -kv[1]):
        print(f"    {n:4d}  {t}")
    if personal:
        print("\n  -> Personal/ai-studio/:")
        for p, _, _ in personal:
            print(f"      {p.name}")

    if not APPLY:
        print("\nNothing written. Re-run with --apply.")
        return

    PERS.mkdir(parents=True, exist_ok=True)

    written = 0
    for p, kind, topic in plan + personal:
        try:
            d = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            md, kind2, topic2 = render_chat(p.name, chunks_of(d))
            root = PERS if kind2 == "personal" else tech_dir(topic2)
            root.mkdir(parents=True, exist_ok=True)
            (root / f"{slug(p.name)}.md").write_text(md, encoding="utf-8")
            written += 1
        except Exception as e:
            print(f"  FAILED {p.name}: {str(e)[:90]}")

    # Pastes and documents used to be filed by KIND, which threw away the topic
    # this function had just computed. 60 of 75 of them are log4j; they sat in
    # a `pastes/` bucket where no topic search would ever reach them.
    for p in pastes:
        try:
            md, topic = render_paste(p)
            root = tech_dir(topic, "pastes")
            root.mkdir(parents=True, exist_ok=True)
            (root / f"{slug(p.name)}.md").write_text(md, encoding="utf-8")
            written += 1
        except Exception as e:
            print(f"  FAILED {p.name}: {str(e)[:90]}")

    for p in docs:
        # Guarded like the two loops above it, which this one was not. A single
        # unreadable file -- a Google Drive read that timed out while the daemon
        # materialised it -- raised straight out of main() and ended the whole
        # AI Studio stage, discarding every document already converted.
        try:
            if p.suffix.lower() in (".rtf", ".docx"):
                out = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(p)],
                                     capture_output=True, text=True)
                if out.returncode == 0:
                    body = redact(out.stdout)
                    topic = classify(p.stem, body)[1]
                    tags = keywords(p.stem, body, topic)
                    root = tech_dir(topic, "documents")
                    root.mkdir(parents=True, exist_ok=True)
                    (root / f"{slug(p.stem)}.md").write_text(
                        header(p.stem, topic, tags, gh_refs(body),
                               f"kind: converted-{p.suffix.lstrip('.')}\n")
                        + body, encoding="utf-8")
                    written += 1
                    continue
            if p.suffix.lower() == ".md":
                body = redact(read_text_resilient(p))
                topic = classify(p.stem, body)[1]
                root = tech_dir(topic, "documents")
                root.mkdir(parents=True, exist_ok=True)
                # Write the REDACTED body. This used to compute `body` and then
                # shutil.copy2 the original over the top, so the scrub was discarded
                # and the raw file landed in the archive.
                (root / p.name).write_text(body, encoding="utf-8")
                continue
            # Attachments. Text-shaped ones still go through the scrubber: a .txt
            # attachment carried a live password and an auth token into the archive,
            # because "not a note" was treated as "nothing to redact".
            adocs = ASSETS / "documents"
            adocs.mkdir(parents=True, exist_ok=True)
            if p.suffix.lower() in (".txt", ".json"):
                (adocs / p.name).write_text(
                    redact(read_text_resilient(p)), encoding="utf-8")
                continue
            # PDFs and zips are binary; copying is all that is possible here.
            shutil.copy2(p, adocs / p.name)
        except Exception as e:
            print(f"  FAILED {p.name}: {str(e)[:90]}")


    if not NO_ASSETS and images:
        ASSETS.mkdir(parents=True, exist_ok=True)
        for p in images:
            t = ASSETS / p.name
            if not t.exists():
                shutil.copy2(p, t)
        print(f"  copied {len(images)} images -> {ASSETS}")

    print(f"\nwrote {written} notes")
    if redaction_counts:
        print("\nREDACTED:")
        for k, v in sorted(redaction_counts.items()):
            print(f"    {v:4d}x  {k}")


if __name__ == "__main__":
    main()
