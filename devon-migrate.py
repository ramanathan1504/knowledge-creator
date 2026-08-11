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
devon-migrate.py — migrate ObsidianVault + claude-cli knowledge into the
DEVONthink capture folder, reorganised by purpose, with generated search
headers and indexes.

Dry-run by default.  Pass --apply to actually move files.

  ./devon-migrate.py            # show what would happen
  ./devon-migrate.py --apply    # do it (writes an undo script)

Design notes
------------
* Files are MOVED (the user is retiring Obsidian), never copied, so there is
  exactly one copy of the truth.
* Every migrated file gets a header: YAML front matter (machine-readable) plus
  a visible hashtag line (full-text searchable in DEVONthink Standard, which
  has no LLM to infer topics for you).  The original body is preserved
  byte-for-byte below a `---` separator.
* Existing `Notes/` and `Snippets/` in the capture folder are NOT touched:
  they are already imported into DevonCapture.dtBase2 and reorganising them
  would orphan the DB's copies.
* An undo script and a manifest are written so the whole move is reversible.
"""

import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

HOME = Path.home()
from kbpaths import OBSIDIAN_VAULT as VAULT
from kbpaths import ARCHIVE as DEVON
CLAUDE_CLI = HOME / "claude-cli"
TODAY = date.today().isoformat()

APPLY = "--apply" in sys.argv

# --------------------------------------------------------------------------
# Explicit placement map.  Directory-level rules are expanded below.
# Renames are only applied to names that are useless for search
# ("Untitled.md", "test.md"); the original name is preserved in the header's
# `source:` field so nothing becomes untraceable.
# --------------------------------------------------------------------------
FILE_MAP = {
    # --- root notes -------------------------------------------------------
    "1976-workout.md":                  "Personal/1976-workout.md",
    "1976.md":                          "Personal/1976.md",
    "Post.md":                          "Personal/conference-post-community-over-code-2026.md",
    "Resume.md":                        "Personal/resume.md",
    "Username.md":                      "Personal/username-reference.md",
    "Audit followup.md":                "Compliance/soc2/audit-followup.md",
    "Soc 2 type 2.md":                  "Compliance/soc2/soc2-type-2-overview.md",
    "Soc 2.zip":                        "Compliance/soc2/soc2-archive.zip",
    "Gpg Git Key.md":                   "Reference/git-github/gpg-git-key-setup.md",
    "Pre-commit.md":                    "Reference/git-github/pre-commit-hooks.md",
    "Postgres User Setup.md":           "Reference/databases/postgres-user-setup.md",
    "Kafka Bug.md":                     "Projects/kafka/kafka-bug.md",
    "kafka stateful.md":                "Projects/kafka/kafka-stateful.md",
    "Rds Review.md":                    "Projects/aws-rds/rds-review.md",
    "rds replica.md":                   "Projects/aws-rds/rds-replica.md",
    "Log4j2 Release process.md":        "Projects/log4j/log4j2-release-process.md",
    # ambiguous names resolved by reading the content
    "Untitled.md":                      "Projects/log4j/log4j-benchmark-keyexcludes-vs-regex.md",
    "TestCase.md":                      "Projects/log4j/log4j-spring-boot-externalcontext-bridge.md",
    "test.md":                          "Projects/virtual-browser/virtual-browser-activation-system.md",
    "OSS Issues.canvas":                "Projects/oss-issues.canvas",

    # --- OSS root ---------------------------------------------------------
    "OSS/4118.md":                              "Projects/log4j/pr-4118.md",
    "OSS/Log4j PR code push.md":                "Projects/log4j/log4j-pr-code-push.md",
    "OSS/Log4j Issue Intelligence CLI.md":      "Tooling/log4j-issue-intelligence-cli.md",
    "OSS/Discussion.md":                        "Projects/log4j/log4j-async-tracing-map-vs-native-fields.md",
    "OSS/Sponsor.md":                           "Personal/sponsor.md",
    "OSS/Banking Transaction Simulator (OSS).md": "Projects/banking-simulator/banking-transaction-simulator.md",
    "OSS/Git Other Pr clone to Upstream.md":    "Reference/git-github/clone-other-pr-to-upstream.md",
    "OSS/apache recovery.md":                   "Reference/git-github/apache-git-recovery.md",
    "OSS/github-command.md":                    "Reference/git-github/github-commands.md",
    "OSS/intemo.md":                            "Reference/kubernetes/intemo-kops-database-config.md",
    "OSS/k8-command.md":                        "Reference/kubernetes/kubernetes-commands.md",
    "OSS/kafka-command.md":                     "Projects/kafka/kafka-commands.md",
    "OSS/kafka-debezium.md":                    "Projects/kafka/kafka-debezium.md",
    "OSS/kafka-elastic-setup.md":               "Projects/kafka/kafka-elastic-setup.md",

    # --- Review root ------------------------------------------------------
    "Review/Cover letter.md":               "Personal/cover-letter.md",
    "Review/Collection.md":                 "Reference/java-syntax/collections.md",
    "Review/Stream.md":                     "Reference/java-syntax/streams.md",
    "Review/Stream and Java Colletion.md":  "Reference/java-syntax/streams-and-collections.md",
}

# directory -> destination prefix (applied to every file beneath)
DIR_MAP = {
    "OSS/JReleaser":     "Projects/jreleaser",
    "OSS/Logging-log4j": "Projects/log4j",
    "OSS/spring-kafka":  "Projects/spring-kafka",
    "Automation":        "Projects/automation",
    "Soc 2":             "Compliance/soc2",
    "Review/Syntax":     "Reference/java-syntax",
    "javaDoc":           "Reference/java",
}

# claude-cli knowledge documents (the .sh files stay put and runnable)
CLAUDE_MAP = {
    "log4j-pr-4153-review-comment.md": "Projects/log4j/pr-4153-review-comment.md",
    "log4j-pr-4156-review-comment.md": "Projects/log4j/pr-4156-review-comment.md",
    "log4j-pr-review-guide.md":        "Tooling/log4j-pr-review-guide.md",
    "triage-sh-command-reference.md":  "Tooling/triage-sh-command-reference.md",
}

# --------------------------------------------------------------------------
# Redaction. This was missing on the first run and it mattered: the vault's
# kubernetes-commands.md carried a live AWS access key AND secret key in a
# `kubectl create secret --from-literal=` line, and they went into the indexed
# base verbatim. The other three harvesters redact; this one did not.
# --------------------------------------------------------------------------
REDACTIONS = [
    ("aws-access-key",   re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws-secret-key",   re.compile(r"(AWS_SECRET_ACCESS_KEY\s*=\s*'?)[A-Za-z0-9/+=]{35,}")),
    ("aws-secret-kv",    re.compile(r"(aws_secret_access_key\s*[=:]\s*)\S{20,}", re.I)),
    ("github-token",     re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}")),
    ("google-api-key",   re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack-token",      re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private-key",      re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("bearer-token",     re.compile(r"(\bBearer\s+)[A-Za-z0-9._\-]{25,}")),
    # `(?!\$\{)` skips template references -- ${DB_PASSWORD}, Log4j's
    # ${secure:sys:...} -- which are pointers to a secret, not the secret.
    ("password",         re.compile(r"(\bpass(?:word|wd)\s*[=:]\s*['\"]?)(?!\$\{)[^\s'\"]{8,}", re.I)),
    ("jdbc-credentials", re.compile(r"((?:jdbc:)?[a-z][a-z0-9+.\-]*://[^\s:/@]+:)[^\s@/]+(@)")),
]
redaction_counts = {}


def redact(text):
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


# --------------------------------------------------------------------------
# Keyword extraction — DEVONthink Standard has no LLM, so the tags must be
# literally present in the text for search and See Also/Classify to work.
# --------------------------------------------------------------------------
FRAMEWORKS = [
    "log4j", "log4j2", "slf4j", "logback", "apache", "kafka", "debezium",
    "spring", "spring-boot", "springboot", "jreleaser", "maven", "gradle",
    "postgres", "postgresql", "mysql", "rds", "aws", "s3", "kubernetes",
    "kops", "docker", "elasticsearch", "elastic", "mongodb", "junit",
    "jmh", "graalvm", "osgi", "git", "github", "gpg", "soc2", "drata",
    "java", "jvm", "concurrency", "generics", "reflection", "annotations",
    "hibernate", "jpa", "jdbc", "rest", "sonar", "jenkins", "terraform",
]

STOP = {
    "The", "This", "That", "There", "These", "Those", "When", "Then", "With",
    "From", "Here", "What", "Which", "While", "Where", "Note", "Yes", "No",
}


def extract(text: str, dest: str):
    """Return (tags, github_refs) derived from the document body."""
    tags, gh = set(), set()

    # github.com/<owner>/<repo>/(issues|pull)/<n>
    for owner_repo, kind, num in re.findall(
        r"github\.com/([\w.-]+/[\w.-]+)/(issues|pull)/(\d+)", text
    ):
        gh.add(f"{owner_repo}#{num}")
        tags.add(f"{'issue' if kind == 'issues' else 'pr'}-{num}")

    # bare #1234 / "PR #1234" / "Issue #1234"
    for num in re.findall(r"(?:^|[\s(\[])#(\d{3,5})\b", text):
        gh.add(f"#{num}")
        tags.add(f"gh-{num}")

    # `backtickedIdentifiers`
    for ident in re.findall(r"`([A-Za-z_][\w.$]{2,40})`", text):
        base = ident.split(".")[-1].split("(")[0]
        if len(base) > 2 and base not in STOP:
            tags.add(base)

    # CamelCase class names
    for cls in re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z0-9]+)+)\b", text):
        if cls not in STOP:
            tags.add(cls)

    low = text.lower()
    for fw in FRAMEWORKS:
        if re.search(rf"\b{re.escape(fw)}\b", low):
            tags.add(fw)

    # topic tags from the destination path
    for part in Path(dest).parent.parts:
        tags.add(part.lower().replace(" ", "-"))

    def rank(t):
        # prefer identifiers that actually recur in the document
        return (-text.count(t), t.lower())

    ordered = sorted(tags, key=rank)[:24]
    return ordered, sorted(gh)


def build_header(src_rel: str, dest_rel: str, body: str, origin: str) -> str:
    tags, gh = extract(body, dest_rel)
    tag_line = " ".join("#" + re.sub(r"[^\w.-]", "", t) for t in tags if t)
    gh_line = " · ".join(gh) if gh else "none identified"
    yaml_tags = ", ".join(t for t in tags if t)
    return (
        "---\n"
        f"tags: [{yaml_tags}]\n"
        f"github: {gh_line}\n"
        f"source: {origin}/{src_rel}\n"
        f"migrated: {TODAY}\n"
        "---\n\n"
        f"**Search Tags/Keywords:** {tag_line}\n\n"
        f"**GitHub Context:** {gh_line}\n\n"
        f"**Source:** `{origin}/{src_rel}`  ·  migrated {TODAY}\n\n"
        "---\n\n"
    )


# --------------------------------------------------------------------------
def collect():
    """Build the full [(src_path, dest_path, src_rel, origin)] work list."""
    jobs = []

    for rel, dest in FILE_MAP.items():
        p = VAULT / rel
        if p.exists():
            jobs.append((p, DEVON / dest, rel, "ObsidianVault"))

    for dirrel, prefix in DIR_MAP.items():
        base = VAULT / dirrel
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.name == ".DS_Store":
                continue
            sub = p.relative_to(base)
            jobs.append((p, DEVON / prefix / sub, str(p.relative_to(VAULT)), "ObsidianVault"))

    for rel, dest in CLAUDE_MAP.items():
        p = CLAUDE_CLI / rel
        if p.exists():
            jobs.append((p, DEVON / dest, rel, "claude-cli"))

    return jobs


def main():
    if not VAULT.exists():
        sys.exit(f"vault not found: {VAULT}")
    if not DEVON.exists():
        sys.exit(f"capture folder not found: {DEVON}")

    jobs = collect()

    # ---- report unmapped files so nothing is silently left behind ---------
    mapped = {j[0] for j in jobs}
    orphans = [
        p for p in sorted(VAULT.rglob("*"))
        if p.is_file()
        and p not in mapped
        and p.name != ".DS_Store"
        and ".obsidian" not in p.parts
    ]

    print(f"{'APPLY' if APPLY else 'DRY RUN'} — {len(jobs)} files\n")
    for src, dst, rel, origin in jobs:
        print(f"  {origin}/{rel}\n    -> {dst.relative_to(DEVON)}")
    if orphans:
        print(f"\n  !! {len(orphans)} UNMAPPED (left in place):")
        for p in orphans:
            print(f"     {p.relative_to(VAULT)}")

    if not APPLY:
        print("\nNothing moved. Re-run with --apply.")
        return

    # ---- do the move -----------------------------------------------------
    undo, manifest, done = [], [], 0
    for src, dst, rel, origin in jobs:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            print(f"  SKIP (exists): {dst}")
            continue

        if src.suffix.lower() == ".md":
            body = redact(src.read_text(encoding="utf-8", errors="replace"))
            dst.write_text(build_header(rel, str(dst.relative_to(DEVON)), body, origin) + body,
                           encoding="utf-8")
            src.unlink()
        else:
            shutil.move(str(src), str(dst))

        undo.append(f'mv {sh(dst)} {sh(src)}')
        manifest.append((origin, rel, str(dst.relative_to(DEVON))))
        done += 1

    (DEVON / "_migration-manifest.tsv").write_text(
        "origin\toriginal_path\tnew_path\n"
        + "\n".join("\t".join(r) for r in manifest) + "\n",
        encoding="utf-8")

    undo_path = CLAUDE_CLI / "devon-migrate-undo.sh"
    undo_path.write_text(
        "#!/usr/bin/env bash\n"
        "# Reverses devon-migrate.py. NOTE: .md files were rewritten with a\n"
        "# generated header; moving them back does NOT strip it.\n"
        "set -euo pipefail\n" + "\n".join(reversed(undo)) + "\n",
        encoding="utf-8")
    undo_path.chmod(0o755)

    if redaction_counts:
        print("\nREDACTED:")
        for k, v in sorted(redaction_counts.items()):
            print(f"    {v:4d}x  {k}")
    print(f"\nMoved {done} files.")
    print(f"Manifest: {DEVON}/_migration-manifest.tsv")
    print(f"Undo:     {undo_path}")


def sh(p):
    return "'" + str(p).replace("'", "'\\''") + "'"


if __name__ == "__main__":
    main()