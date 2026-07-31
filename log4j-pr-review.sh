#!/usr/bin/env bash
#
# log4j-pr-review — mechanical review harness for apache/logging-log4j2 PRs.
#
# Gathers the evidence a reviewer needs (feedback threads, diff, build, tests,
# spotless, source-tree pollution, changelog precedent) into one output dir.
#
# It does NOT judge the code. It produces facts you then read.
#
# Usage:  log4j-pr-review 4156
#         log4j-pr-review 4153 --full
#         log4j-pr-review 4156 --offline --keep
#
set -uo pipefail

# ---------------------------------------------------------------- defaults ---
REPO="apache/logging-log4j2"
PR=""
FORCE_FULL=0
NO_BUILD=0
KEEP=0
OFFLINE=""
OUT=""
WORKDIR="${LOG4J_REPO:-$HOME/apache/logging-log4j2}"

# Changes under these paths force a full reactor build: they can break any
# module, so a scoped build proves nothing.
FULL_TRIGGERS="log4j-plugin-processor log4j-plugins log4j-kit log4j-parent log4j-bom pom.xml .mvn"

usage() {
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    cat <<'EOF'

Options:
  --repo OWNER/NAME   default: apache/logging-log4j2
  --full              force a full reactor build
  --no-build          metadata + diff only, skip build/test/spotless
  --offline           pass -o to maven (fast; needs a warm ~/.m2)
  --keep              keep the worktree for manual poking
  --out DIR           output directory (default: ./log4j-review-<PR>)
  --repo-dir DIR      local clone to work in (default: $LOG4J_REPO or
                      ~/apache/logging-log4j2)
  -h, --help          this
EOF
}

# ------------------------------------------------------------------- args ---
while [ $# -gt 0 ]; do
    case "$1" in
        --repo)     REPO="$2"; shift 2 ;;
        --full)     FORCE_FULL=1; shift ;;
        --no-build) NO_BUILD=1; shift ;;
        --offline)  OFFLINE="-o"; shift ;;
        --keep)     KEEP=1; shift ;;
        --out)      OUT="$2"; shift 2 ;;
        --repo-dir) WORKDIR="$2"; shift 2 ;;
        -h|--help)  usage; exit 0 ;;
        -*)         echo "unknown option: $1" >&2; usage; exit 2 ;;
        *)          PR="$1"; shift ;;
    esac
done

[ -n "$PR" ] || { usage; exit 2; }
OUT="${OUT:-$PWD/log4j-review-$PR}"
# MUST be absolute: $WT is handed to `git -C "$WORKDIR" worktree add`, which
# resolves relative paths against the repo, not against $PWD. A relative --out
# would silently create the worktree inside the user's clone.
mkdir -p "$OUT" 2>/dev/null
OUT="$(cd "$OUT" && pwd)" || die "cannot resolve --out path"

# --------------------------------------------------------------- preflight ---
die()  { echo "ERROR: $*" >&2; exit 1; }
say()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; }

command -v gh      >/dev/null || die "gh CLI not found"
command -v git     >/dev/null || die "git not found"
command -v python3 >/dev/null || die "python3 not found (used to parse GraphQL)"
gh auth status >/dev/null 2>&1 || die "gh not authenticated — run: gh auth login"
[ -d "$WORKDIR/.git" ] || die "not a git clone: $WORKDIR (set --repo-dir or \$LOG4J_REPO)"
[ -x "$WORKDIR/mvnw" ] || die "no mvnw in $WORKDIR"

mkdir -p "$OUT"
BRANCH="pr-${PR}-review-$$"
WT="$OUT/worktree"

cleanup() {
    if [ "$KEEP" -eq 1 ]; then
        warn "worktree kept at $WT (branch $BRANCH)"
        return
    fi
    git -C "$WORKDIR" worktree remove --force "$WT" >/dev/null 2>&1
    git -C "$WORKDIR" worktree prune         >/dev/null 2>&1
    git -C "$WORKDIR" branch -D "$BRANCH"    >/dev/null 2>&1
}
trap cleanup EXIT

echo "PR      : $REPO#$PR"
echo "clone   : $WORKDIR"
echo "output  : $OUT"

# ------------------------------------------------------- 1. PR metadata -----
say "1/9  PR metadata"
gh pr view "$PR" --repo "$REPO" \
   --json title,body,author,state,createdAt,updatedAt,headRefName,baseRefName,\
additions,deletions,changedFiles,commits,mergeable \
   > "$OUT/01-metadata.json" 2>"$OUT/01-metadata.err" \
   || die "could not fetch PR (see $OUT/01-metadata.err)"

python3 - "$OUT/01-metadata.json" > "$OUT/01-metadata.md" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"# {d['title']}\n")
print(f"- author: @{d['author']['login']}   state: {d['state']}   mergeable: {d.get('mergeable')}")
print(f"- {d['headRefName']} -> {d['baseRefName']}")
print(f"- +{d['additions']} -{d['deletions']} across {d['changedFiles']} files")
print(f"- created {d['createdAt']}  updated {d['updatedAt']}\n")
print("## Commits (check authorship on ports!)\n")
for c in d["commits"]:
    who = ", ".join(f"{a['name']} <{a['email']}>" for a in c["authors"])
    print(f"- `{c['oid'][:10]}` **{who}** — {c['messageHeadline']}")
print("\n## Description\n")
print(d["body"] or "_(empty)_")
PY
ok "$OUT/01-metadata.md"
grep -E '^- (author|\+)' "$OUT/01-metadata.md" | sed 's/^/    /'

# ------------------------------- 2. feedback: comments + ALL threads --------
# REST /pulls/N/comments misses threads that were resolved or marked outdated.
# GraphQL is the only way to see everything, which matters when you are
# checking "did they address my earlier review".
say "2/9  Review feedback (incl. resolved + outdated threads)"
{
    echo "# Feedback on $REPO#$PR"
    echo
    echo "## Issue comments"
    echo
    gh api "repos/$REPO/issues/$PR/comments" --paginate \
       -q '.[] | "### @\(.user.login) — \(.created_at)\n\n\(.body)\n"' 2>/dev/null
    echo
    echo "## Review threads"
    echo
    gh api graphql -f query="
    { repository(owner:\"${REPO%%/*}\", name:\"${REPO##*/}\") {
        pullRequest(number: $PR) {
          reviewThreads(first:100){ nodes { isResolved isOutdated path line
            comments(first:50){ nodes { author{login} body } } } }
          reviews(first:100){ nodes { author{login} state body } } } } }" 2>/dev/null \
    | python3 -c '
import json,sys
try: d=json.load(sys.stdin)["data"]["repository"]["pullRequest"]
except Exception: print("_(none)_"); raise SystemExit
th=d["reviewThreads"]["nodes"]
if not th: print("_(no inline review threads)_\n")
for t in th:
    flags=[]
    if t["isResolved"]: flags.append("RESOLVED")
    if t["isOutdated"]: flags.append("OUTDATED")
    f=(" **["+", ".join(flags)+"]**") if flags else ""
    print(f"### `{t[\"path\"]}`:{t[\"line\"]}{f}\n")
    for c in t["comments"]["nodes"]:
        print(f"- **@{c[\"author\"][\"login\"]}**: {c[\"body\"]}\n")
print("## Formal reviews\n")
rv=[r for r in d["reviews"]["nodes"] if (r["body"] or "").strip() or r["state"]!="COMMENTED"]
if not rv: print("_(none)_")
for r in rv:
    print(f"- **@{r[\"author\"][\"login\"]}** {r[\"state\"]}: {(r[\"body\"] or \"\").strip()[:800]}")
'
} > "$OUT/02-feedback.md" 2>/dev/null
ok "$OUT/02-feedback.md"

# ------------------------------------------------- 3. diff + changed files --
say "3/9  Diff"
gh pr diff "$PR" --repo "$REPO" > "$OUT/03-diff.patch" 2>/dev/null
gh pr view "$PR" --repo "$REPO" --json files -q '.files[].path' > "$OUT/04-files.txt" 2>/dev/null
ok "$(wc -l < "$OUT/04-files.txt" | tr -d ' ') files changed -> $OUT/04-files.txt"

# ------------------------------------- 4. is this a port? changelog check ---
# House rule (verified against #4152/#4154/#4157/#4128): 2.x -> main ports do
# NOT get a changelog entry in main. The fix already has one on 2.x and the bug
# never shipped in a released 3.x. Entries in .3.x.x are for 3.x-only changes.
say "4/9  Port / changelog sanity"
TITLE=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["title"])' "$OUT/01-metadata.json")
IS_PORT=0
echo "$TITLE" | grep -qiE '\bport\b|\[main\]' && IS_PORT=1
# grep -c prints 0 and exits 1 on no match; don't add a second "0" with `|| echo 0`
CHANGELOG_ADDED=$(grep -c '^src/changelog/' "$OUT/04-files.txt" 2>/dev/null)
CHANGELOG_ADDED=${CHANGELOG_ADDED:-0}
{
    echo "# Port / changelog"
    echo
    echo "- title looks like a port: $([ $IS_PORT -eq 1 ] && echo YES || echo no)"
    echo "- changelog files touched: $CHANGELOG_ADDED"
    echo
    if [ "$IS_PORT" -eq 1 ] && [ "$CHANGELOG_ADDED" -gt 0 ]; then
        echo "> **CHECK**: this looks like a port *and* adds a changelog entry."
        echo "> Recent main ports added none (#4152, #4154, #4157, #4128)."
        echo "> An entry is only warranted if the port needed extra, user-visible"
        echo "> 3.x-only work beyond a faithful port — and then it should describe"
        echo "> *that*, not the ported fix."
        echo
    fi
    echo "## Recent main ports, for precedent"
    echo '```'
    git -C "$WORKDIR" log --oneline -25 origin/main 2>/dev/null \
      | grep -iE 'port|\[main\]' | head -10
    echo '```'
    echo
    echo "## Files each of those touched under src/changelog"
    for c in $(git -C "$WORKDIR" log --format=%h -40 origin/main 2>/dev/null \
               | head -40); do
        subj=$(git -C "$WORKDIR" log -1 --format=%s "$c")
        case "$subj" in
            *[Pp]ort*|*"[main]"*)
                n=$(git -C "$WORKDIR" show --stat --format="" "$c" \
                    | grep -c 'src/changelog' || true)
                echo "- \`$c\` changelog files: ${n:-0} — $subj" ;;
        esac
    done
} > "$OUT/05-changelog.md"
ok "$OUT/05-changelog.md"
[ "$IS_PORT" -eq 1 ] && [ "$CHANGELOG_ADDED" -gt 0 ] \
    && warn "port PR adds a changelog entry — verify against precedent"

# ------------------------ 5. upstream 2.x comparison hints (ports only) -----
say "5/9  2.x comparison hints"
{
    echo "# 2.x comparison"
    echo
    echo "Most review findings on a port come from diffing against what actually"
    echo "landed on 2.x. Look for: things 2.x deliberately KEPT (deprecated"
    echo "aliases, constants) that the port silently drops, and behaviour the"
    echo "port changes without saying so."
    echo
    git -C "$WORKDIR" fetch origin 2.x --quiet 2>/dev/null
    while IFS= read -r f; do
        case "$f" in *.java) ;; *) continue ;; esac
        base=$(basename "$f")
        hits=$(git -C "$WORKDIR" ls-tree -r --name-only origin/2.x \
               | grep -F "/$base" | head -3)
        [ -n "$hits" ] || continue
        echo "## \`$f\`"
        echo
        echo "2.x counterpart(s):"
        echo "$hits" | sed 's/^/  - /'
        echo
        echo "  git show origin/2.x:$(echo "$hits" | head -1) | less"
        echo
    done < "$OUT/04-files.txt"
} > "$OUT/06-2x-comparison.md"
ok "$OUT/06-2x-comparison.md"

if [ "$NO_BUILD" -eq 1 ]; then
    say "skipping build (--no-build)"
    printf '\n\033[1;32mDone.\033[0m Output in %s\n' "$OUT"
    exit 0
fi

# --------------------------------------------------------- 6. worktree -----
say "6/9  Checking out PR into a worktree"
git -C "$WORKDIR" fetch origin "pull/$PR/head:$BRANCH" --force --quiet \
    || die "could not fetch PR head"
git -C "$WORKDIR" worktree add --quiet "$WT" "$BRANCH" \
    || die "could not create worktree"
# in a linked worktree .git is a *file* pointing at the real gitdir, not a dir
[ -e "$WT/.git" ] || die "worktree missing at $WT — refusing to continue"
ok "worktree at $WT"

# --------------------------------------------- 7. decide build scope -------
MODULES=""
NEED_FULL=$FORCE_FULL
while IFS= read -r f; do
    top="${f%%/*}"
    for t in $FULL_TRIGGERS; do
        [ "$top" = "$t" ] && NEED_FULL=1
    done
    case "$top" in
        log4j-*) case " $MODULES " in *" $top "*) ;; *) MODULES="$MODULES $top" ;; esac ;;
    esac
done < "$OUT/04-files.txt"

# a change in log4j-X is only really exercised by log4j-X-test
EXTRA=""
for m in $MODULES; do
    case "$m" in *-test) continue ;; esac
    [ -d "$WORKDIR/$m-test" ] && EXTRA="$EXTRA $m-test"
done
MODULES="$MODULES$EXTRA"
MODULES=$(echo "$MODULES" | tr ' ' '\n' | grep -v '^$' | sort -u | paste -sd, -)

MVN_COMMON="$OFFLINE -Dbnd.baseline.skip=true -Denforcer.skip=true \
-Drat.skip=true -Dmaven.javadoc.skip=true -Dspotbugs.skip=true -Dcyclonedx.skip=true"

if [ "$NEED_FULL" -eq 1 ]; then
    SCOPE="FULL REACTOR"
    BUILD_ARGS=""
else
    SCOPE="scoped: $MODULES"
    BUILD_ARGS="-pl $MODULES -am"
fi

say "7/9  Build ($SCOPE)"
[ "$NEED_FULL" -eq 1 ] && warn "full build forced: change touches build-wide code"
( cd "$WT" && ./mvnw $MVN_COMMON $BUILD_ARGS -DskipTests -Dspotless.skip=true install ) \
    > "$OUT/07-build.log" 2>&1
BUILD_RC=$?
if [ $BUILD_RC -eq 0 ]; then ok "BUILD SUCCESS"; else warn "BUILD FAILED (rc=$BUILD_RC)"; fi
grep -E '^\[INFO\] (BUILD|Apache Log4j).*(SUCCESS|FAILURE|SKIPPED)' "$OUT/07-build.log" \
    | tail -40 > "$OUT/07-build-summary.txt"
grep -E '^\[ERROR\]' "$OUT/07-build.log" | head -30 >> "$OUT/07-build-summary.txt"

# ------------------------------------------------- 8. tests + spotless -----
say "8/9  Tests + spotless"
( cd "$WT" && ./mvnw $MVN_COMMON ${BUILD_ARGS:+-pl $MODULES} -Dspotless.skip=true test ) \
    > "$OUT/08-test.log" 2>&1
TEST_RC=$?
# per-module rollups are the "Tests run:" lines WITHOUT "-- in <class>"
{
    echo "# failures / errors (if any)"
    grep -E 'Tests run:.*(Failures: [1-9]|Errors: [1-9])' "$OUT/08-test.log" || echo "(none)"
    echo
    echo "# totals"
    grep -E 'Tests run:' "$OUT/08-test.log" | grep -v -- '-- in' \
      | awk -F'[:,]' '{t+=$2; f+=$4; e+=$6; s+=$8}
                      END{printf "run=%d failures=%d errors=%d skipped=%d\n",t,f,e,s}'
} > "$OUT/08-test-summary.txt"
if [ $TEST_RC -eq 0 ]; then ok "tests passed"; else warn "tests FAILED (rc=$TEST_RC)"; fi
grep -E 'Tests run:.*(Failures: [1-9]|Errors: [1-9])' "$OUT/08-test.log" | head

( cd "$WT" && ./mvnw $OFFLINE -q spotless:check ) > "$OUT/09-spotless.log" 2>&1
SPOT_RC=$?
if [ $SPOT_RC -eq 0 ]; then ok "spotless clean"; else warn "spotless VIOLATIONS — see $OUT/09-spotless.log"; fi

# --------------------------- 9. did the tests dirty the source tree? -------
# Annotation-processor and codegen tests love to write into the module dir.
# A clean tree here is a real, checkable property.
say "9/9  Source-tree pollution check"
POLLUTION=$(git -C "$WT" status --porcelain 2>/dev/null)
if [ ! -e "$WT/.git" ]; then
    POLLUTION="(worktree gone — pollution check did NOT run)"
    warn "$POLLUTION"
elif [ -z "$POLLUTION" ]; then
    ok "tree clean after tests"
else
    warn "tests left files behind:"
    echo "$POLLUTION" | sed 's/^/      /'
fi
echo "$POLLUTION" > "$OUT/10-pollution.txt"

# --------------------------------------------------------------- summary ---
{
    echo "# Review evidence — $REPO#$PR"
    echo
    echo "\`$TITLE\`"
    echo
    echo "| check | result |"
    echo "|---|---|"
    echo "| build ($SCOPE) | $([ $BUILD_RC -eq 0 ] && echo 'PASS' || echo '**FAIL**') |"
    echo "| tests | $([ $TEST_RC -eq 0 ] && echo 'PASS' || echo '**FAIL**') |"
    echo "| spotless | $([ $SPOT_RC -eq 0 ] && echo 'clean' || echo '**violations**') |"
    echo "| source tree clean after tests | $([ -z "$POLLUTION" ] && echo 'yes' || echo '**no — see 10-pollution.txt**') |"
    echo "| port? | $([ $IS_PORT -eq 1 ] && echo yes || echo no) |"
    echo "| changelog files touched | $CHANGELOG_ADDED |"
    echo
    echo "## Test totals"
    echo '```'
    cat "$OUT/08-test-summary.txt" 2>/dev/null
    echo '```'
    echo
    echo "## Now read, in order"
    echo
    echo "1. \`02-feedback.md\` — what was actually asked for. Tick each item off."
    echo "2. \`06-2x-comparison.md\` — for ports, diff against 2.x. Highest-yield step."
    echo "3. \`03-diff.patch\` — separate *required 3.x adaptation* from *extra work*."
    echo "4. \`05-changelog.md\` — ports should not add an entry."
    echo "5. \`07-build-summary.txt\`, \`10-pollution.txt\` — the mechanical facts."
} > "$OUT/00-SUMMARY.md"

say "Done"
cat "$OUT/00-SUMMARY.md"
printf '\nAll output: %s\n' "$OUT"