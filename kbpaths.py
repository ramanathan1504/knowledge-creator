#!/usr/bin/env python3
"""
kbpaths.py — one place to say where your data is.

Every script used to carry its own copy of the same absolute paths, including
one containing a specific person's email address:

    SRC = HOME / "Library/CloudStorage/GoogleDrive-someone@gmail.com/My Drive/..."

So nobody else could run the harvesters without editing source, and the whole
toolchain assumed macOS with iCloud Drive.

The design now: you point at ONE folder holding the material you want
collected. Local disk, iCloud, Google Drive, Dropbox, a network mount -- it
does not matter, because it is just a directory.

    export KB_SOURCES=~/my-knowledge-sources

Inside it, sources are found by convention, and any name that is not
recognised is simply left alone:

    <KB_SOURCES>/ai-studio/       Google AI Studio conversations
    <KB_SOURCES>/claude/          claude.ai export (conversations.json)
    <KB_SOURCES>/obsidian/        an Obsidian vault, if you are migrating one

Everything ELSE keeps its own conventional home, because those tools already
decide for themselves and there is nothing to configure:

    the archive     KB_ARCHIVE, else the conventional location
    DEVONthink      its own database, wherever DEVONthink keeps it
    Ollama          its own endpoint
    self-analyse    its own ~/.self-analyse

If KB_SOURCES is unset, the old locations are auto-detected so an existing
install keeps working untouched. The Google Drive folder is found by globbing
rather than by a configured address, so it works for whichever account is
signed in without anyone publishing their own.
"""

import os
from pathlib import Path

HOME = Path.home()

# Derived, never assumed to be ~/claude-cli: the daily job used to hardcode that
# and would silently run a different checkout than the one you edited.
SCRIPTS = Path(__file__).resolve().parent


def _env(name):
    v = os.environ.get(name, "").strip()
    return Path(v).expanduser() if v else None


# ------------------------------------------------------- the one input root --
SOURCES = _env("KB_SOURCES")


def _source(convention_name, *legacy_candidates):
    """
    Resolve one input source.

    Under KB_SOURCES the layout is a convention, so a folder is found by name.
    Without it, fall back to wherever that source historically lived, which is
    what keeps an existing install running with no configuration at all.
    """
    if SOURCES:
        by_convention = SOURCES / convention_name
        if by_convention.is_dir():
            return by_convention
        # Named folder absent: still prefer the root over a stale legacy path,
        # so a deliberate KB_SOURCES is never silently ignored.
        return by_convention
    for candidate in legacy_candidates:
        if candidate and candidate.is_dir():
            return candidate
    return legacy_candidates[0] if legacy_candidates else HOME / convention_name


def _google_drive_aistudio():
    """Google Drive names its folder after the signed-in account, so glob for it."""
    cloud = HOME / "Library/CloudStorage"
    if cloud.is_dir():
        for drive in sorted(cloud.glob("GoogleDrive-*")):
            candidate = drive / "My Drive/Google AI Studio"
            if candidate.is_dir():
                return candidate
    return None


AISTUDIO = _source("ai-studio", _google_drive_aistudio(), HOME / "Google AI Studio")
CLAUDE_EXPORT = _source("claude", HOME / "Downloads")
OBSIDIAN_VAULT = _source(
    "obsidian", HOME / "Library/Mobile Documents/com~apple~CloudDocs/ObsidianVault"
)


# ------------------------------------------------------------------ archive --
# Output, not input, so it stays separate from KB_SOURCES: you generally do not
# want generated notes written back into the folder you are collecting from.
_ICLOUD_ARCHIVE = HOME / "Library/Mobile Documents/com~apple~CloudDocs/Documents/Devon Capture"

ARCHIVE = _env("KB_ARCHIVE") or next(
    (p for p in (_ICLOUD_ARCHIVE, HOME / "knowledge-base") if p.is_dir()),
    _ICLOUD_ARCHIVE,
)

PROJECTS = ARCHIVE / "Projects"
REFERENCE = ARCHIVE / "Reference"
PERSONAL = ARCHIVE / "Personal"
BLOG = ARCHIVE / "Blog"
ASSETS = ARCHIVE / "_assets"

# Optional and macOS-only. The archive is fully usable without DEVONthink, so
# scripts check for it rather than requiring it.
DEVONTHINK_DB = _env("KB_DEVONTHINK_DB") or (HOME / "Documents/Knowledge.dtBase2")


def describe():
    rows = [
        ("sources root", SOURCES or Path("(auto-detect)"), "KB_SOURCES"),
        ("  ai studio", AISTUDIO, ""),
        ("  claude export", CLAUDE_EXPORT, ""),
        ("  obsidian", OBSIDIAN_VAULT, ""),
        ("archive (output)", ARCHIVE, "KB_ARCHIVE"),
        ("devonthink db", DEVONTHINK_DB, "KB_DEVONTHINK_DB"),
    ]
    out = []
    for label, path, var in rows:
        mark = "ok" if Path(path).exists() else "--"
        suffix = f"   (override: {var})" if var else ""
        out.append(f"  [{mark}] {label:17} {path}{suffix}")
    return "\n".join(out)


if __name__ == "__main__":
    print("knowledge-base paths\n")
    print(describe())
    print("\n  [ok] = found on disk, [--] = absent (that source is skipped)")
