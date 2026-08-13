#!/usr/bin/env bash
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
#
# oss-harvest-daily.sh — full knowledge refresh, once a day.
#
# Five collection stages, then embedding, then a DEVONthink index nudge:
#   1. GitHub          incremental, off the state-file watermark
#   2. Google AI Studio conversations, pastes, documents, images
#   3. Claude          newest claude.ai export + local Claude Code sessions
#   4. Knowledge map   coverage, mind map, snippet libraries — an index
#   4b. Topic digests  problem -> resolution, read out of the notes
#   4c. Coverage gaps  the base vs. the official manuals — what is MISSING
#   5. Blog drafts     scaffolds for newly-completed work, 2/day, no AI
#   6. Embed           oss sync --me — what is written becomes retrievable
#
# Stages are independent: a missing Google Drive mount or absent export is
# reported, not fatal. Keeps a rolling log.
#
#   ./oss-harvest-daily.sh              # run now, as the scheduler would
#   ./oss-harvest-daily.sh --install    # install the launchd job (09:15 daily)
#   ./oss-harvest-daily.sh --uninstall  # remove it
#   ./oss-harvest-daily.sh --catch-up   # run the GitHub stage a locked keychain skipped
#   ./oss-harvest-daily.sh --status     # is it loaded? when did it last run?
#
# launchd rather than cron: cron on macOS is deprecated, does not survive
# cleanly across reboots, and gets no Full Disk Access. launchd also runs a
# missed job after a wake-up, which matters for a daily job on a laptop.
#
set -uo pipefail

LABEL="com.ramanathan.oss-harvest"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
# Derived from this file, not assumed: a hardcoded ~/claude-cli would run a
# different checkout than the one you edited.
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
REPO="$(dirname "$SELF")"
HARVEST="$REPO/oss-harvest.py"
LOGDIR="$REPO/logs"
DEVON="${KB_ARCHIVE:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/Documents/Devon Capture}"
# Knowledge.dtBase2, not the old DevonCapture.dtBase2 — that one was archived
# when the folder was switched from imported to indexed. Pointing at the old
# path would make the nightly index refresh silently no-op.
DB="${KB_DEVONTHINK_DB:-$HOME/Documents/Knowledge.dtBase2}"

mkdir -p "$LOGDIR"

# 2,21 not 2,18: the header grew when --catch-up was added, and a usage() that
# stops short silently hides the newest flag -- which is the one most likely to
# be looked for.
usage() {
    # Anchored to the licence block, not to line numbers: a header at the top of
    # the file shifts the doc comment down, and a fixed range then prints the
    # licence as help text. Content-anchored survives that.
    sed -n '/limitations under the License\./,$p' "$0" \
      | sed -n '2,/^[^#]/p' | sed 's/^# \{0,1\}//' | sed '/^$/d;$d'
}

# ------------------------------------------------------------- install ----
install_job() {
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>            <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$SELF</string>
    </array>
    <!-- 09:15 daily. If the Mac is asleep, launchd runs it on wake. -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>   <integer>9</integer>
        <key>Minute</key> <integer>15</integer>
    </dict>
    <key>StandardOutPath</key>   <string>$LOGDIR/oss-harvest.out.log</string>
    <key>StandardErrorPath</key> <string>$LOGDIR/oss-harvest.err.log</string>
    <key>RunAtLoad</key>         <false/>
    <!-- gh lives in Homebrew; launchd's default PATH does not include it -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
PLIST_EOF

    launchctl unload "$PLIST" 2>/dev/null
    launchctl load  "$PLIST" || { echo "launchctl load failed" >&2; exit 1; }
    echo "installed: $PLIST"
    echo "runs daily at 09:15; logs in $LOGDIR"
    echo
    echo "NOTE: gh authenticates from the login keychain. If the job reports an"
    echo "auth error, run it once manually so macOS prompts for keychain access,"
    echo "or switch to a token in ~/.config/gh/hosts.yml."
}

case "${1:-}" in
    --install)   install_job; exit 0 ;;
    --catch-up)
        # Run ONLY the stage a locked keychain caused us to skip, and only if it
        # was actually skipped. Re-running the whole harvest to recover one
        # stage would redo four sources that already succeeded, and a catch-up
        # that is expensive is one nobody runs.
        marker="$REPO/logs/.github-stage-pending"
        if [ ! -f "$marker" ]; then
            echo "nothing pending — the GitHub stage last ran successfully"
            exit 0
        fi
        if ! gh auth status >/dev/null 2>&1; then
            echo "gh still not authenticated — unlock the screen, then run this again" >&2
            exit 1
        fi
        echo "catching up the GitHub stage (pending since $(cat "$marker"))"
        if python3 "$HARVEST"; then
            rm -f "$marker"
            echo "✓ caught up"
            exit 0
        fi
        echo "catch-up failed; the marker is kept so it can be retried" >&2
        exit 1
        ;;
    --uninstall) launchctl unload "$PLIST" 2>/dev/null; rm -f "$PLIST"
                 echo "removed $LABEL"; exit 0 ;;
                 # NOT `launchctl list | grep -q`: grep -q exits on the first
                 # match and closes the pipe, launchctl dies of SIGPIPE, and
                 # `set -o pipefail` then reports the whole pipeline as failed
                 # — so an installed job was reported "loaded: no". Query the
                 # label directly instead; it is also exact rather than a
                 # substring match.
    --status)    if launchctl list "$LABEL" >/dev/null 2>&1; then
                     echo "loaded: yes"
                 else
                     echo "loaded: no"
                 fi
                 [ -f "$REPO/.oss-harvest-state.json" ] && \
                     python3 -c "import json,sys;d=json.load(open('$REPO/.oss-harvest-state.json'));print('last run:',d.get('last_run'));[print('  ',r) for r in d.get('runs',[])[-5:]]"
                 exit 0 ;;
    -h|--help)   usage; exit 0 ;;
    "")          ;;
    *)           echo "unknown option: $1" >&2; usage; exit 2 ;;
esac

# ----------------------------------------------------------- the daily run --
echo "───────────────────────────────────────────────"
echo "knowledge refresh  $(date '+%Y-%m-%d %H:%M:%S')"

command -v gh >/dev/null || { echo "gh not on PATH: $PATH" >&2; exit 1; }

# gh keeps its token in the macOS keyring. This job is scheduled for 09:15 and
# launchd runs it on wake if the Mac was asleep, which can land BEFORE the
# keychain is unlocked -- so `gh auth status` fails on a perfectly healthy
# machine. Retry briefly to let the keychain catch up.
# A token file, if one exists, removes the keychain from the picture entirely:
# `gh` prefers GH_TOKEN from the environment and never consults the keyring when
# it is set. That is the real fix for a scheduled job -- waiting below is only
# the fallback for a machine that has not been given one.
#
# Deliberately a file with 0600 permissions and NOT the plist's
# EnvironmentVariables: a launchd plist is world-readable by default, is copied
# into backups, and shows up in `launchctl print`. Neither is encrypted at rest
# the way the keychain is, which is exactly why this is opt-in and why the token
# it wants is a READ-ONLY one -- this job only ever reads.
GH_TOKEN_FILE="${KB_GH_TOKEN_FILE:-$HOME/.config/knowledge-creator/gh-token}"
if [ -z "${GH_TOKEN:-}" ] && [ -f "$GH_TOKEN_FILE" ]; then
    perms=$(stat -f "%OLp" "$GH_TOKEN_FILE" 2>/dev/null || echo "?")
    if [ "$perms" != "600" ]; then
        echo "  ! $GH_TOKEN_FILE is mode $perms — should be 600. Refusing to read it." >&2
        echo "    chmod 600 \"$GH_TOKEN_FILE\"" >&2
    else
        GH_TOKEN="$(tr -d ' \t\r\n' < "$GH_TOKEN_FILE")"
        if [ -n "$GH_TOKEN" ]; then
            export GH_TOKEN
            echo "  using the token file — no keychain needed"
        fi
    fi
fi

# 40 seconds was not enough, and the logs say so: this failed with github(auth)
# on consecutive days, every one of them a run that started at exactly 09:15.
# The machine is asleep at that hour, launchd runs the job on wake, and the
# login keychain is not readable until the screen is actually unlocked -- which
# is minutes later, not seconds. Three tries twenty seconds apart could only
# ever have covered a wake that happened to coincide with an unlock.
#
# So wait for the keychain on the timescale a person actually unlocks a laptop.
# Polling a locked keychain is free; the job is a background agent with nothing
# waiting on it, and finishing forty minutes late beats not harvesting at all.
GH_WAIT_SECONDS="${KB_GH_WAIT_SECONDS:-1800}"
GH_OK=0
waited=0
while :; do
    if gh auth status >/dev/null 2>&1; then GH_OK=1; break; fi
    [ "$waited" -ge "$GH_WAIT_SECONDS" ] && break
    # Announce the wait once, so a log read months later says what it was doing
    # rather than showing an unexplained gap between two timestamps.
    [ "$waited" -eq 0 ] && echo "  gh not authenticated yet — waiting up to $((GH_WAIT_SECONDS / 60))m for the keychain (unlock the screen)" >&2
    sleep 30
    waited=$((waited + 30))
done
[ "$GH_OK" -eq 1 ] && [ "$waited" -gt 0 ] && echo "  gh became available after $((waited / 60))m ${waited}s of waiting" >&2

# Not being able to reach GitHub must NOT abort the run. Six of the seven
# stages below never touch it, and this check used to `exit 1` before any of
# them ran -- so one locked keychain froze the AI Studio harvest, the knowledge
# map, the digests, the gap reports and the blog drafts too. That contradicted
# the stage-level tolerance documented right underneath it.
# A marker, so a missed keychain window is recoverable rather than simply lost
# until tomorrow. --catch-up reads this and runs the one stage that was skipped.
CATCHUP_MARKER="$REPO/logs/.github-stage-pending"
if [ $GH_OK -eq 0 ]; then
    echo "  ! gh still not authenticated after $((GH_WAIT_SECONDS / 60))m." >&2
    echo "    Skipping the GitHub stage; every other source still runs." >&2
    echo "    Recover without waiting for tomorrow:  ./oss-harvest-daily.sh --catch-up" >&2
    mkdir -p "$REPO/logs" && date -u +%Y-%m-%dT%H:%M:%SZ > "$CATCHUP_MARKER"
else
    rm -f "$CATCHUP_MARKER" 2>/dev/null || true
fi

# Every source, then the derived layer. Harvesting only GitHub left the AI
# Studio and Claude material frozen at whenever it was last run by hand, and
# the knowledge map stale the moment anything new landed.
#
# Each stage is non-fatal on its own: Google Drive may not be mounted, an
# export may be absent. One missing source must not stop the rest, so failures
# are recorded and reported at the end rather than aborting the run.
FAILED=""

# 1. GitHub — incremental. No --full: the state file carries the watermark, so
#    only threads that actually changed are re-fetched and rewritten.
#
#    KB_HARVEST_REPOS additionally pulls every thread in the repositories it
#    names, not only the ones I am attached to. That is the material you need to
#    work on something you have not touched yet: the core stores an issue's body
#    and a count of its comments, never their text. Unset means nobody's
#    repository is harvested by default, which is the right default -- naming
#    them is a decision with a rate-limit cost.
#
#      export KB_HARVEST_REPOS="owner/name owner/other"
echo; echo "[1/5] GitHub"
if [ $GH_OK -eq 1 ]; then
    REPO_ARGS=()
    for nwo in ${KB_HARVEST_REPOS:-}; do
        REPO_ARGS+=(--repo "$nwo")
    done
    [ ${#REPO_ARGS[@]} -gt 0 ] && echo "  also scanning: ${KB_HARVEST_REPOS}"
    python3 "$HARVEST" "${REPO_ARGS[@]}" || FAILED="$FAILED github"
else
    echo "  skipped — gh not authenticated"
    FAILED="$FAILED github(auth)"
fi

# 2. Google AI Studio — cheap to redo; image copying skips files already there.
echo; echo "[2/5] Google AI Studio"
# Ask the resolver where the source is, instead of hardcoding an account-specific
# Google Drive path. Set KB_SOURCES to override.
AISTUDIO_DIR="$(python3 -c "import sys;sys.path.insert(0,'$REPO');import kbpaths;print(kbpaths.AISTUDIO)" 2>/dev/null)"
if [ -n "$AISTUDIO_DIR" ] && [ -d "$AISTUDIO_DIR" ]; then
    python3 "$REPO/aistudio-extract.py" --apply >/dev/null 2>&1 \
        && echo "  ok" || FAILED="$FAILED ai-studio"
else
    echo "  Google Drive not mounted — skipped"
fi

# 3. Claude — newest export in Downloads, plus local Claude Code sessions.
echo; echo "[3/5] Claude"
python3 "$REPO/claude-harvest.py" --apply 2>&1 | tail -3 \
    || FAILED="$FAILED claude"

# 3b. Warn when the claude.ai export has gone stale. The export is a manual
#     action on claude.ai with no API, so this cannot be automated away — but
#     silently harvesting a months-old snapshot while reporting success is
#     worse than being nagged. Threshold 21 days.
python3 - <<'PYEOF'
import json, pathlib, datetime, subprocess
home = pathlib.Path.home()
best = None
for p in home.glob("Downloads/**/conversations.json"):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        latest = max((c.get("created_at", "") for c in d), default="")
        if latest and (best is None or latest > best[0]):
            best = (latest, p)
    except Exception:
        continue
if best is None:
    print("  !! no claude.ai export found in ~/Downloads")
else:
    latest, path = best
    day = datetime.date.fromisoformat(latest[:10])
    age = (datetime.date.today() - day).days
    print(f"  export covers to {day} ({age} days ago)")
    if age > 21:
        msg = (f"claude.ai export is {age} days stale (to {day}). "
               "Settings > Privacy > Export data, unzip into ~/Downloads.")
        print(f"  !! {msg}")
        subprocess.run(["osascript", "-e",
            f'display notification "{msg}" with title "Knowledge base" '
            f'subtitle "claude.ai export stale"'], capture_output=True)
PYEOF

# 4. Derived layer — coverage map, mind map, snippet libraries. Must run LAST:
#    it reads whatever the three harvesters just wrote.
echo; echo "[4/5] Knowledge map"
python3 "$REPO/knowledge-map.py" --apply 2>&1 | tail -2 \
    || FAILED="$FAILED knowledge-map"

# 4b. Topic digests. Separate from the map on purpose: the map counts matches,
#     this reads the notes. Runs after the map so both see the same harvest.
echo; echo "[4b/5] Topic digests"
python3 "$REPO/topic-digest.py" --apply 2>&1 | tail -3 \
    || FAILED="$FAILED topic-digest"

# 4c. Gap analysis against the official manuals. The only stage that can say
#     what is MISSING — the other two only ever look at what is already here.
echo; echo "[4c/5] Coverage gaps"
python3 "$REPO/coverage-gap.py" --apply 2>&1 | grep -E "applied|wrote" \
    || FAILED="$FAILED coverage-gap"

# 5. Blog drafts. Deterministic only — no --ai in the unattended run, because
#    prose generation costs tokens and the voice should be a choice, not a
#    side effect of a cron job. Capped at 2/day: that keeps up with new merges
#    and slowly works through the backlog without dumping 100 drafts at once.
echo; echo "[5/5] Blog drafts"
python3 "$REPO/blog-gen.py" --top 2 --apply 2>&1 | tail -4 \
    || FAILED="$FAILED blog-gen"

# 6. Embed what the day wrote.
#
# Harvesting and vectorising are two steps joined by a path: these stages write
# Markdown into the archive, and `oss sync --me` reads the configured note
# folders and embeds them. Without this the day's work is on disk and invisible
# -- searchable by filename, absent from every answer -- which looks exactly
# like a harvest that did not run. The whole point of collecting a discussion is
# that the next question can find it.
#
# Incremental: a note is re-embedded only when its content changed or the
# embedder that produced its vectors is not the one running now.
echo; echo "[6/6] Embedding the archive"
if command -v oss >/dev/null 2>&1; then
    oss sync --me 2>&1 | tail -3 || FAILED="$FAILED embed"
else
    echo "  skipped — 'oss' is not on PATH; run 'oss sync --me' to make today searchable"
    FAILED="$FAILED embed(no-oss)"
fi

[ -n "$FAILED" ] && echo && echo "STAGES FAILED:$FAILED" >&2

# Nudge DEVONthink to pick up new files.
#
# This is the ONLY stage that does not heal itself. GitHub carries a watermark,
# the AI Studio and Claude harvesters re-read their sources in full, and the
# derived layer is regenerated from scratch -- so a skipped run is caught up by
# the next one. The index sync is a one-shot AppleScript: skip it and the search
# index simply stays behind until a human notices.
#
# The old rule was "only if DEVONthink is already running", on the grounds that
# waking it from a background job is rude. But a scheduled run almost always
# fires when the app is closed, so the polite path meant the index was
# permanently stale -- 315 of 566 notes had changed on disk before this was
# spotted. Politeness that never yields is just breakage.
#
# So: sync opportunistically whenever the app is up, and record how long the
# index has been dirty when it is not. Past the threshold, staleness outweighs
# politeness and the sync launches DEVONthink itself.
DIRTY_MARKER="$REPO/.devon-index-dirty"
DIRTY_MAX_HOURS=24

devon_dirty_hours() {
    [ -f "$DIRTY_MARKER" ] || { echo 0; return; }
    local then now
    then=$(cat "$DIRTY_MARKER" 2>/dev/null || echo 0)
    now=$(date +%s)
    echo $(( (now - then) / 3600 ))
}

DIRTY_HOURS=$(devon_dirty_hours)
FORCE_DEVON=0
if [ "$DIRTY_HOURS" -ge "$DIRTY_MAX_HOURS" ]; then
    FORCE_DEVON=1
    echo "DEVONthink index dirty for ${DIRTY_HOURS}h — syncing even though the app is closed"
fi

if pgrep -x DEVONthink >/dev/null 2>&1 || [ $FORCE_DEVON -eq 1 ]; then
    # stdout too, not just stderr: the AppleScript's last expression evaluates
    # to `true` and would print a bare "true" line into every daily log
    if osascript >/dev/null 2>&1 <<OSA
tell application "DEVONthink"
    set theDB to open database "$DB"
    repeat with r in (children of root of theDB)
        try
            if (indexed of r) then synchronize record r
        end try
    end repeat
end tell
OSA
    then
        echo "DEVONthink index refreshed"
        rm -f "$DIRTY_MARKER"          # caught up; stop counting
    else
        echo "DEVONthink sync failed — leaving the index marked dirty" >&2
        [ -f "$DIRTY_MARKER" ] || date +%s > "$DIRTY_MARKER"
        FAILED="$FAILED devon-sync"
    fi
else
    # Record WHEN it first went dirty, not each time, so the age keeps growing
    # until a sync actually succeeds.
    [ -f "$DIRTY_MARKER" ] || date +%s > "$DIRTY_MARKER"
    echo "DEVONthink not running — index marked dirty (${DIRTY_HOURS}h), will force after ${DIRTY_MAX_HOURS}h"
fi

# keep the logs from growing without bound
for f in "$LOGDIR"/oss-harvest.*.log; do
    [ -f "$f" ] || continue
    if [ "$(wc -c < "$f")" -gt 2000000 ]; then
        tail -c 500000 "$f" > "$f.tmp" && mv "$f.tmp" "$f"
        echo "truncated $f"
    fi
done

echo "done $(date '+%H:%M:%S')"