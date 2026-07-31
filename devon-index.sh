#!/usr/bin/env bash
#
# devon-index.sh — wire the capture folder into DEVONthink as an INDEXED
# folder, and keep that index current.
#
# Routine use is --sync. Indexing is a one-time act: once the folder is
# indexed, DEVONthink tracks it, and the only thing you need afterwards is a
# nudge to re-read files that changed on disk (a harvester run, a credential
# scrub). The --apply and --fresh modes are for first setup and rebuild.
#
#   ./devon-index.sh                    # report only (default, safe)
#   ./devon-index.sh --sync             # re-read changed files from disk
#   ./devon-index.sh --apply            # index the folder (first setup)
#   ./devon-index.sh --apply --purge-imported
#                                       # also move stray imported records
#                                       # to the database Trash
#   ./devon-index.sh --fresh --apply    # rebuild from the Markdown: archive
#                                       # the database, recreate, reindex
#
# Why indexed and not imported
# ----------------------------
# The original database held *imported* copies: every file lived twice, once
# on disk and once inside Files.noindex/ in the database package. A file
# written to the folder afterwards was invisible to DEVONthink until someone
# imported it by hand — which would silently break the "save devon" workflow.
#
# Indexed means DEVONthink references the files where they sit. New and
# changed files are picked up, the originals stay editable by any tool, and
# they remain plain Markdown in iCloud rather than being locked inside a
# database package. That is what a 5-year plain-text archive needs.
#
# Verified against DEVONthink 4.3.1's scripting dictionary:
#   index path "<posix path>" to <group>     (Contents/Resources/DEVONthink.sdef)
#
set -uo pipefail

# Paths come from the environment so this runs on any machine; the defaults
# keep an existing install working with nothing configured.
DB="${KB_DEVONTHINK_DB:-$HOME/Documents/Knowledge.dtBase2}"
FOLDER="${KB_ARCHIVE:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/Documents/Devon Capture}"
APPLY=0
PURGE=0
FRESH=0
SYNC=0

for a in "$@"; do
    case "$a" in
        --apply)           APPLY=1 ;;
        --purge-imported)  PURGE=1 ;;
        --fresh)           FRESH=1 ;;
        --sync)            SYNC=1 ;;
        # print the header block, whatever length it grows to
        -h|--help)         sed -n '2,/^set -/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $a" >&2; exit 2 ;;
    esac
done

say()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ -d /Applications/DEVONthink.app ] || die "DEVONthink not found in /Applications"
# --fresh recreates the database, so it is the one mode that may run without one
[ $FRESH -eq 1 ] || [ -e "$DB" ] || die "database not found: $DB"
[ -d "$FOLDER" ] || die "capture folder not found: $FOLDER"

if   [ $SYNC  -eq 1 ]; then MODE=SYNC
elif [ $APPLY -eq 1 ]; then MODE=APPLY
else                        MODE='REPORT ONLY'
fi
echo "database : $DB"
echo "folder   : $FOLDER"
echo "mode     : $MODE"
[ $PURGE -eq 1 ] && warn "will move pre-existing imported records to the database Trash"

# ------------------------------------------------------------------ sync ---
# The routine operation. DEVONthink notices most changes on its own, but not
# reliably for files rewritten underneath it by another tool -- synchronize
# forces the re-read. Safe: it only ever pulls disk state into the index.
if [ $SYNC -eq 1 ]; then
    say "Synchronizing the indexed group"
    RESULT=$(osascript <<OSA
tell application "DEVONthink"
    set theDB to open database "$DB"
    set n to 0
    repeat with r in (children of root of theDB)
        set isIdx to false
        try
            if (indexed of r) then set isIdx to true
        end try
        if isIdx then
            synchronize record r
            set n to n + 1
        end if
    end repeat
    if n is 0 then return "ERROR: no indexed group found -- run --apply first"
    return "synchronized " & n & " indexed group(s)"
end tell
OSA
    ) || die "synchronize failed — check DEVONthink"
    case "$RESULT" in ERROR:*) die "${RESULT#ERROR: }" ;; esac
    ok "$RESULT"
    exit 0
fi

# ----------------------------------------------------------- fresh start ---
# Rebuild the database from the Markdown, which is the actual archive -- the
# database only ever holds a derived index, so nothing of yours lives solely
# in it. Anything you did add inside DEVONthink itself (tags, annotations,
# manually filed records) is NOT on disk and does not survive. The old
# database is ARCHIVED, never deleted: you delete it yourself once happy.
if [ $FRESH -eq 1 ]; then
    say "Fresh start"
    if [ $APPLY -eq 0 ]; then
        echo "    would archive : $DB"
        echo "    would create  : $DB"
        echo "    would index   : $FOLDER"
        echo "    (add --apply to do it)"
        exit 0
    fi
    STAMP=$(date +%Y%m%d-%H%M%S)
    ARCHIVE="$HOME/Documents/_archived-$(basename "$DB" .dtBase2)-$STAMP.dtBase2"
    if [ -e "$DB" ]; then
        # close it first, or DEVONthink keeps writing to a moved package
        osascript -e "tell application \"System Events\" to (name of processes) contains \"DEVONthink\"" \
            | grep -q true && osascript -e 'tell application "DEVONthink" to quit' 2>/dev/null
        sleep 2
        mv "$DB" "$ARCHIVE" || die "could not archive the old database"
        ok "archived -> $ARCHIVE"
    else
        warn "no existing database at $DB"
    fi
    [ -e "$DB" ] && die "$DB still exists — move it aside first"

    RESULT=$(osascript <<OSA
tell application "DEVONthink"
    set theDB to create database "$DB"
    set rec to index path "$FOLDER" to (root of theDB)
    return "created " & (name of theDB) & ", indexed " & (name of rec)
end tell
OSA
    ) || die "create/index failed — check DEVONthink"
    ok "$RESULT"

    say "Verifying"
    osascript <<OSA
tell application "DEVONthink"
    set theDB to open database "$DB"
    set c to 0
    repeat with r in (children of root of theDB)
        try
            if (indexed of r) then set c to c + 1
        end try
    end repeat
    return "indexed top-level items: " & c
end tell
OSA
    echo
    echo "Old database archived at:"
    echo "  $ARCHIVE"
    echo "Delete it once you are satisfied. Note it holds the OLD full-text"
    echo "index, so any secret scrubbed from the Markdown since is still"
    echo "readable in there — rotating the credential is what actually counts."
    exit 0
fi

# --------------------------------------------------------------- report ---
say "Current database contents"
warn "this launches DEVONthink if it is not already running"

osascript <<OSA
tell application "DEVONthink"
    set theDB to open database "$DB"
    set out to "name: " & (name of theDB) & linefeed
    set out to out & "top-level items:" & linefeed
    repeat with r in (children of root of theDB)
        set k to "imported"
        try
            if (indexed of r) then set k to "INDEXED"
        end try
        set p to ""
        try
            set p to (path of r)
        end try
        set out to out & "  [" & k & "] " & (name of r) & " (" & (type of r as string) & ") " & p & linefeed
    end repeat
    return out
end tell
OSA

if [ $APPLY -eq 0 ]; then
    say "Report only — nothing changed"
    cat <<'EOF'
    Already showing an [INDEXED] group above? Nothing to do here —
    use --sync to pull in files that changed on disk.

    Otherwise re-run with --apply to index the folder.

    Equivalent manual route, if you would rather click:
      DEVONthink > File > Index Files and Folders…
      choose the "Devon Capture" folder, target the Knowledge database.
EOF
    exit 0
fi

# ---------------------------------------------------------------- purge ---
if [ $PURGE -eq 1 ]; then
    say "Moving pre-existing imported records to Trash"
    osascript <<OSA
tell application "DEVONthink"
    set theDB to open database "$DB"
    -- A database's own Inbox/Tags/Trash and its smart groups all report as
    -- "not indexed". They are not stale imports and must survive this flag.
    set keepIds to {}
    repeat with g in {incoming group of theDB, trash group of theDB, tags group of theDB}
        try
            copy (id of g) to end of keepIds
        end try
    end repeat
    set n to 0
    set kept to 0
    repeat with r in (children of root of theDB)
        set isIdx to false
        try
            set isIdx to (indexed of r)
        end try
        set isSpecial to false
        try
            if keepIds contains (id of r) then set isSpecial to true
        end try
        try
            if (type of r as string) contains "smart" then set isSpecial to true
        end try
        if isSpecial then
            set kept to kept + 1
        else if not isIdx then
            delete record r
            set n to n + 1
        end if
    end repeat
    return "moved " & n & " imported record(s) to Trash, kept " & kept & " special group(s)"
end tell
OSA
    warn "these are in the database Trash, not gone — verify before emptying"
fi

# ---------------------------------------------------------------- index ---
say "Indexing the capture folder"
RESULT=$(osascript <<OSA
tell application "DEVONthink"
    set theDB to open database "$DB"
    set rec to index path "$FOLDER" to (root of theDB)
    return "indexed: " & (name of rec)
end tell
OSA
)
RC=$?
if [ $RC -ne 0 ]; then
    die "indexing failed — is the folder already indexed? Check DEVONthink."
fi
ok "$RESULT"

say "Verifying"
osascript <<OSA
tell application "DEVONthink"
    set theDB to open database "$DB"
    set c to 0
    repeat with r in (children of root of theDB)
        try
            if (indexed of r) then set c to c + 1
        end try
    end repeat
    return "indexed top-level items: " & c
end tell
OSA

cat <<'EOF'

Done. From here on:

  * A file written into "Devon Capture" is picked up by DEVONthink.
    If it does not appear immediately, select the group and use
    File > Update Indexed Items (or the `synchronize` script command).

  * Keep the files as Markdown. DEVONthink Standard has no LLM to infer a
    document's topic, so the tags must be literally in the text — which is
    what the generated "Search Tags/Keywords" header line is for.

  * Useful without Pro: Tools > See Also & Classify works on text
    similarity alone, and Tools > Create Concordance builds a word index
    across the database. Neither needs the AI features you do not have.
EOF