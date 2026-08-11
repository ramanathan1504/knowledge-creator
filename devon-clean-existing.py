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
devon-clean-existing.py — clean and reorganise the material ALREADY sitting in
the DEVONthink capture folder.

This is deliberately separate from devon-migrate.py (which brings in the
Obsidian vault and claude-cli).  Run this one FIRST: it settles the existing
Notes/ and Snippets/ into the by-purpose structure so the migration lands on
top of a tidy tree rather than beside a messy one.

  ./devon-clean-existing.py            # dry run (default)
  ./devon-clean-existing.py --apply    # do it

What it does
------------
1. Converts .rtf / .html clipboard captures to Markdown.  Every one of these
   is plain Java/YAML code wrapped in styling noise; RTF is a poor format for
   a 5-year plain-text archive and DEVONthink indexes Markdown far better.
2. Renames to descriptive, searchable names (and fixes "parallel procees").
3. Prepends the same search header devon-migrate.py uses, so both bodies of
   material are consistent.
4. Quarantines rather than deletes: the junk file, the exact duplicate, and
   every original .rtf/.html go to _Quarantine/ for you to review and remove.

IMPORTANT — the DEVONthink database
-----------------------------------
All 10 of these files are currently *imported* into DevonCapture.dtBase2,
i.e. the database holds its own copies under Files.noindex/.  After this
script runs, those DB copies are stale duplicates pointing at names that no
longer exist on disk.  Fix that with devon-index.sh, which removes the stale
items and re-indexes the folder.  Run this script first, then that one.
"""

import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

HOME = Path.home()
from kbpaths import ARCHIVE as DEVON, SCRIPTS
QUAR = DEVON / "_Quarantine"
TODAY = date.today().isoformat()
APPLY = "--apply" in sys.argv

# src (relative to capture folder) -> (dest, language fence, description)
PLAN = {
    "Notes/Log git jira.rtf": (
        "Projects/automation/github-jira-monitor-run-log.md", "text",
        "Terminal output of scripts/github_jira_monitor.py core workflow — "
        "gathers GitHub data, groups by owner/repo/issue, creates JIRA objects."),

    "Snippets/Java/NamedInstantPatternTest.rtf": (
        "Projects/log4j/snippets/named-instant-pattern-test.md", "java",
        "JUnit test for log4j NamedInstantPattern — legacy vs modern formatter "
        "equality, with a whole-hour-offset assumption for "
        "ISO8601_OFFSET_DATE_TIME_HH."),

    "Snippets/Java/github event payload.rtf": (
        "Projects/automation/github-event-payload-fetch.md", "java",
        "Fetches and filters GitHub events via githubApi.fetchGithubEvents — "
        "pagination, repo extraction, payload shape."),

    "Snippets/Java/executor.rtf": (
        "Projects/intemo-bot/document-type-executor.md", "java",
        "ScriptResponseDTO processing dispatch by document type (CKL, VINV) "
        "using Callable tasks — checkListFlow / vendorInvoiceFlow."),

    "Snippets/Java/lockrow.html": (
        "Projects/intemo-bot/jpa-pessimistic-row-lock.md", "java",
        "BotEntryRepository with @Lock(PESSIMISTIC_WRITE) — row-level locking "
        "via findByIdWithLock to prevent concurrent pickup."),

    "Snippets/Java/multi thread.html": (
        "Projects/intemo-bot/bot-scheduler-multithreading.md", "java",
        "BotSchedulerApplication multithreading — shutdown hook, "
        "BotEntryService lifecycle, graceful executor teardown."),

    "Snippets/Java/parallel procees.html": (   # note: original name misspelled
        "Projects/intemo-bot/parallel-doctype-processing.md", "java",
        "processAllDocTypesInParallel — fixed thread pool sized to docTypes, "
        "@Transactional boundary, executor.shutdown()."),

    "Snippets/Java/sql log.html": (
        "Reference/databases/hibernate-sql-logging-config.md", "yaml",
        "Hibernate SQL logging configuration — show-sql, format_sql and the "
        "binder/extractor trace descriptors."),
}

# quarantined outright, with the reason recorded
JUNK = {
    "Snippets/Text/snippet_2025-07-21_08-38-17.md":
        "Junk capture: 42 bytes, body is a single '/' character.",
    "Snippets/Text/snippet_2025-07-21_08-27-22.md":
        "Exact duplicate of Snippets/Java/lockrow.html "
        "(BotEntryRepository pessimistic lock), captured via Automator.",
}

FRAMEWORKS = [
    "log4j", "junit", "spring", "spring-boot", "hibernate", "jpa", "jdbc",
    "kafka", "github", "jira", "java", "sql", "postgres", "concurrency",
    "executor", "multithreading", "transactional", "yaml", "python",
]
STOP = {"The", "This", "That", "When", "With", "From", "String", "System"}


# The pre-existing capture folder is not clean either: `Notes/Log git jira.rtf`
# is a terminal log of github_jira_monitor.py that contains a GitHub classic
# PAT twice. Redact on conversion, same rules as aistudio-extract.py, so the
# knowledge base never gains a credential it did not already have.
REDACTIONS = [
    ("aws-access-key",   re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token",     re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}")),
    ("google-api-key",   re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack-token",      re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private-key",      re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    # `(?!\$\{)` skips template references -- ${DB_PASSWORD}, Log4j's
    # ${secure:sys:...} -- which are pointers to a secret, not the secret.
    ("password",         re.compile(r"(\bpass(?:word|wd)\s*[=:]\s*['\"]?)(?!\$\{)[^\s'\"]{8,}", re.I)),
    ("bearer-token",     re.compile(r"(\bBearer\s+)[A-Za-z0-9._\-]{25,}")),
    ("jdbc-credentials", re.compile(r"((?:jdbc:)?[a-z][a-z0-9+.\-]*://[^\s:/@]+:)[^\s@/]+(@)")),
]
redaction_counts = {}


def redact(text: str) -> str:
    for label, rx in REDACTIONS:
        def sub(m):
            redaction_counts[label] = redaction_counts.get(label, 0) + 1
            g = [x for x in m.groups() if x is not None]
            if len(g) >= 2:
                return f"{g[0]}[REDACTED:{label}]{g[1]}"
            if len(g) == 1:
                return f"{g[0]}[REDACTED:{label}]"
            return f"[REDACTED:{label}]"
        text = rx.sub(sub, text)
    return text


def to_text(path: Path) -> str:
    """RTF/HTML -> plain text via macOS textutil; Markdown passes through."""
    if path.suffix.lower() in (".rtf", ".html", ".htm"):
        out = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", str(path)],
            capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError(f"textutil failed on {path}: {out.stderr.strip()}")
        return out.stdout
    return path.read_text(encoding="utf-8", errors="replace")


def tags_for(body: str, dest: str, desc: str) -> list:
    tags = set()
    for cls in re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z0-9]+)+)\b", body + " " + desc):
        if cls not in STOP:
            tags.add(cls)
    for ident in re.findall(r"\b([a-z][a-zA-Z0-9]{4,30})\s*\(", body):
        tags.add(ident)
    low = (body + " " + desc).lower()
    for fw in FRAMEWORKS:
        if re.search(rf"\b{re.escape(fw)}\b", low):
            tags.add(fw)
    for part in Path(dest).parent.parts:
        tags.add(part.lower().replace(" ", "-"))
    return sorted(tags, key=lambda t: (-body.count(t), t.lower()))[:22]


def header(src_rel: str, dest_rel: str, body: str, desc: str) -> str:
    tags = tags_for(body, dest_rel, desc)
    tag_line = " ".join("#" + re.sub(r"[^\w.-]", "", t) for t in tags if t)
    gh = sorted(set(re.findall(r"(?:^|[\s(\[])#(\d{3,5})\b", body)))
    gh_line = " · ".join("#" + n for n in gh) if gh else "none identified"
    return (
        "---\n"
        f"tags: [{', '.join(tags)}]\n"
        f"github: {gh_line}\n"
        f"source: Devon Capture/{src_rel}\n"
        f"cleaned: {TODAY}\n"
        "---\n\n"
        f"**Search Tags/Keywords:** {tag_line}\n\n"
        f"**GitHub Context:** {gh_line}\n\n"
        f"**Intent:** {desc}\n\n"
        f"**Source:** `Devon Capture/{src_rel}` (converted {TODAY})\n\n"
        "---\n\n"
    )


def main():
    if not DEVON.exists():
        sys.exit(f"capture folder not found: {DEVON}")

    # Only the folders this script governs. The capture folder now also holds
    # everything the harvesters generated; scanning all of it would report
    # hundreds of "unrecognised" files that are none of this script's business.
    on_disk = set()
    for sub in ("Notes", "Snippets"):
        d = DEVON / sub
        if d.is_dir():
            on_disk |= {
                str(p.relative_to(DEVON)) for p in d.rglob("*")
                if p.is_file() and p.name != ".DS_Store"
            }
    known = set(PLAN) | set(JUNK)
    unknown = sorted(on_disk - known)

    print(f"{'APPLY' if APPLY else 'DRY RUN'}\n")
    print(f"convert + move : {len(PLAN)}")
    print(f"quarantine     : {len(JUNK)}")
    print(f"unrecognised   : {len(unknown)}\n")

    for src, (dest, lang, desc) in PLAN.items():
        exists = "" if (DEVON / src).exists() else "   [MISSING]"
        print(f"  {src}{exists}\n    -> {dest}   ```{lang}```")
    print()
    for src, why in JUNK.items():
        exists = "" if (DEVON / src).exists() else "   [MISSING]"
        print(f"  QUARANTINE {src}{exists}\n    reason: {why}")
    if unknown:
        print("\n  untouched (not in plan):")
        for u in unknown:
            print(f"    {u}")

    if not APPLY:
        print("\nNothing changed. Re-run with --apply.")
        return

    QUAR.mkdir(exist_ok=True)
    (QUAR / "originals").mkdir(exist_ok=True)
    manifest, undo, n = [], [], 0

    for src, (dest, lang, desc) in PLAN.items():
        s = DEVON / src
        if not s.exists():
            continue
        d = DEVON / dest
        if d.exists():
            print(f"  SKIP (exists): {dest}")
            continue
        d.parent.mkdir(parents=True, exist_ok=True)

        body = redact(to_text(s).replace("\r\n", "\n").replace("\r", "\n").strip("\n"))
        if s.suffix.lower() == ".md":
            content = header(src, dest, body, desc) + body + "\n"
        else:
            content = header(src, dest, body, desc) + f"```{lang}\n{body}\n```\n"
        d.write_text(content, encoding="utf-8")

        keep = QUAR / "originals" / Path(src).name
        shutil.move(str(s), str(keep))
        undo.append(f"mv {sh(keep)} {sh(s)} && rm -f {sh(d)}")
        manifest.append((src, dest, "converted"))
        n += 1

    for src, why in JUNK.items():
        s = DEVON / src
        if not s.exists():
            continue
        dst = QUAR / Path(src).name
        shutil.move(str(s), str(dst))
        (QUAR / "REASONS.txt").open("a", encoding="utf-8").write(f"{src}\n    {why}\n")
        undo.append(f"mv {sh(dst)} {sh(s)}")
        manifest.append((src, f"_Quarantine/{Path(src).name}", "quarantined"))
        n += 1

    # drop now-empty capture subfolders (Notes/, Snippets/Java, Snippets/Text)
    for d in sorted(DEVON.rglob("*"), key=lambda p: -len(p.parts)):
        if d.is_dir() and "_Quarantine" not in d.parts:
            try:
                next(d.iterdir())
            except StopIteration:
                d.rmdir()
                print(f"  removed empty dir: {d.relative_to(DEVON)}")

    (DEVON / "_cleanup-manifest.tsv").write_text(
        "original\tnew\taction\n" + "\n".join("\t".join(r) for r in manifest) + "\n",
        encoding="utf-8")

    undo_path = SCRIPTS / "devon-clean-existing-undo.sh"
    undo_path.write_text(
        "#!/usr/bin/env bash\n# Reverses devon-clean-existing.py\n"
        "set -euo pipefail\n" + "\n".join(reversed(undo)) + "\n", encoding="utf-8")
    undo_path.chmod(0o755)

    if redaction_counts:
        print("\nREDACTED (credentials neutralised on conversion):")
        for k, v in sorted(redaction_counts.items()):
            print(f"    {v:4d}x  {k}")
    print(f"\nProcessed {n} files.")
    print(f"Manifest  : {DEVON}/_cleanup-manifest.tsv")
    print(f"Quarantine: {QUAR}   (review, then delete when happy)")
    print(f"Undo      : {undo_path}")
    print("\nNEXT: the 11 imported copies in DevonCapture.dtBase2 are now stale.")
    print("      Run devon-index.sh to clear them and index the folder instead.")


def sh(p):
    return "'" + str(p).replace("'", "'\\''") + "'"


if __name__ == "__main__":
    main()
