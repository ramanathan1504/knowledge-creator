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
# triage.sh v2 — READ-ONLY GitHub backlog triage with upstream/downstream map
#
# Generates a self-contained HTML report with sidebar navigation:
#   📊 Summary      — repo stats + API rate-limit gauge
#   🔗 Clusters     — PR/issue chains (fixes / depends-on / blocks / related)
#   ✅ Mergeable    — approved · no conflicts · CI green  → merge now
#   🔧 One fix away — stalled small PRs                  → nudge
#   ⭐ Top issues   — scored by reactions + labels       → pick next
#
# NOTHING is modified. All gh calls are read-only list/view.
#
# Usage:
#   ./triage.sh OWNER/REPO              report + AI enrichment (default)
#   ./triage.sh OWNER/REPO --no-ai      deterministic tables only, no tokens
#
# Env tunables (defaults shown):
#   STALE_DAYS=21   PR is "stalled" after this many idle days
#   SMALL=50        PR is "small" if +additions+deletions < this
#   TOP_ISSUES=20   how many top-scored issues to show
#   PR_LIMIT=200    max PRs to fetch
#   ISSUE_LIMIT=400 max issues to fetch
#   RATE_WARN=200   warn if fewer API calls remain
#
# Output: triage-YYYYMMDD-OWNER-REPO.html  (self-contained, opens in any browser)
#
set -euo pipefail

# ── args ──────────────────────────────────────────────────────────────────────
REPO="${1:-}"
[[ -z "$REPO" || "$REPO" == -* ]] && {
  echo "usage: $0 OWNER/REPO [--no-ai] [--dry-run]" >&2
  echo "" >&2
  echo "  --no-ai     skip Claude enrichment (saves tokens)" >&2
  echo "  --dry-run   reuse cached JSON from last real fetch (saves API quota)" >&2
  echo "" >&2
  echo "env tunables: STALE_DAYS SMALL TOP_ISSUES PR_LIMIT ISSUE_LIMIT RATE_WARN" >&2
  exit 2
}
AI=1; DRY_RUN=0
for flag in "${@:2}"; do
  case "$flag" in
    --no-ai)   AI=0 ;;
    --dry-run) DRY_RUN=1 ;;
    *) echo "error: unknown flag '$flag'" >&2; exit 2 ;;
  esac
done

STALE_DAYS="${STALE_DAYS:-21}"
SMALL="${SMALL:-50}"
TOP_ISSUES="${TOP_ISSUES:-20}"
PR_LIMIT="${PR_LIMIT:-200}"
ISSUE_LIMIT="${ISSUE_LIMIT:-400}"
RATE_WARN="${RATE_WARN:-200}"

# ── deps + auth ───────────────────────────────────────────────────────────────
for bin in gh jq; do
  command -v "$bin" >/dev/null || { echo "error: '$bin' not found on PATH" >&2; exit 3; }
done
gh auth status >/dev/null 2>&1 || { echo "error: run 'gh auth login' first" >&2; exit 3; }
[[ "$AI" == 1 ]] && ! command -v claude >/dev/null && \
  { echo "note: 'claude' not found — falling back to --no-ai" >&2; AI=0; }

# ── rate-limit check (skipped in dry-run) ────────────────────────────────────
if [[ "$DRY_RUN" == 0 ]]; then
  RL_JSON=$(gh api rate_limit 2>/dev/null \
    || echo '{"resources":{"core":{"remaining":9999,"limit":5000,"reset":0,"used":0}}}')
else
  RL_JSON='{"resources":{"core":{"remaining":9999,"limit":5000,"reset":0,"used":0}}}'
fi
RL_REM=$(printf '%s'  "$RL_JSON" | jq '.resources.core.remaining')
RL_LIM=$(printf '%s'  "$RL_JSON" | jq '.resources.core.limit')
RL_USED=$(printf '%s' "$RL_JSON" | jq '.resources.core.used')
RL_RST=$(printf '%s'  "$RL_JSON" | jq '.resources.core.reset')
RL_RST_FMT=$(date -r  "$RL_RST" '+%H:%M UTC' 2>/dev/null \
           || date -d "@${RL_RST}" '+%H:%M UTC' 2>/dev/null || echo "N/A")

if [[ "$DRY_RUN" == 0 ]]; then
  if [[ "$RL_REM" -lt "$RATE_WARN" ]]; then
    echo "⚠  WARNING: only $RL_REM/$RL_LIM API calls remaining (resets $RL_RST_FMT)" >&2
    echo "   Tip: --no-ai --dry-run to reuse cached data and save quota" >&2
  fi
  [[ "$RL_REM" -lt 5 ]] && { echo "error: rate limit exhausted — run with --dry-run to use cache" >&2; exit 4; }
fi

# ── setup ─────────────────────────────────────────────────────────────────────
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
NOW="$(date +%s)"
DATE_LABEL="$(date -u '+%Y-%m-%d %H:%M UTC')"
DATE_TAG="$(date +%Y%m%d)"
REPO_SLUG="${REPO//\//-}"
OUT="triage-${DATE_TAG}-${REPO_SLUG}.html"
CACHE_DIR=".triage-cache/${REPO_SLUG}"


# ── fetch (read-only) or load from cache (--dry-run) ─────────────────────────
if [[ "$DRY_RUN" == 1 ]]; then
  # Dry-run: reuse cached JSON from last real fetch — zero API calls
  [[ ! -f "$CACHE_DIR/prs.json" || ! -f "$CACHE_DIR/issues.json" ]] && {
    echo "error: no cache found at $CACHE_DIR/" >&2
    echo "  Run without --dry-run first to populate the cache." >&2
    exit 5
  }
  cp "$CACHE_DIR/prs.json"    "$WORK/prs.json"
  cp "$CACHE_DIR/issues.json" "$WORK/issues.json"
  echo "Dry-run: loaded data from cache $CACHE_DIR/  (0 API calls used)" >&2
else
  echo "Fetching $REPO  ($RL_REM API calls remaining)…" >&2
  gh pr list --repo "$REPO" --state open --limit "$PR_LIMIT" \
    --json number,title,url,author,updatedAt,reviewDecision,mergeable,additions,deletions,isDraft,labels,reviews,statusCheckRollup,body \
    > "$WORK/prs.json" \
    || { echo "error: gh pr list failed for $REPO" >&2; exit 6; }

  gh issue list --repo "$REPO" --state open --limit "$ISSUE_LIMIT" \
    --json number,title,url,updatedAt,labels,comments,assignees,reactionGroups,body \
    > "$WORK/issues.json" \
    || { echo "error: gh issue list failed for $REPO" >&2; exit 6; }

  # Validate JSON is actually an array (catch auth/repo errors early)
  jq -e 'if type=="array" then . else error("expected array, got \(type)") end' \
    "$WORK/prs.json" > /dev/null \
    || { echo "error: prs.json is not valid — check repo name and auth" >&2; exit 6; }
  jq -e 'if type=="array" then . else error("expected array, got \(type)") end' \
    "$WORK/issues.json" > /dev/null \
    || { echo "error: issues.json is not valid — check repo name and auth" >&2; exit 6; }

  # Save to cache for future --dry-run use
  mkdir -p "$CACHE_DIR"
  cp "$WORK/prs.json"    "$CACHE_DIR/prs.json"
  cp "$WORK/issues.json" "$CACHE_DIR/issues.json"
  echo "  Cached to $CACHE_DIR/ for --dry-run reuse" >&2
fi

PR_COUNT=$(jq length "$WORK/prs.json")
ISSUE_COUNT=$(jq length "$WORK/issues.json")
echo "  $PR_COUNT PRs · $ISSUE_COUNT issues" >&2

# ── jq helpers ────────────────────────────────────────────────────────────────
read -r -d '' HELPERS <<'JQ' || true
def checkstate:
  (.statusCheckRollup // []) as $c
  | if ($c|length)==0 then "none"
    elif ($c | map(.conclusion // .state // "") | any(. == "FAILURE" or . == "ERROR")) then "failing"
    else "passing" end;
def ageDays($now): (($now - (.updatedAt|fromdateiso8601)) / 86400) | floor;
def blocker:
  if .mergeable=="CONFLICTING"              then "needs rebase"
  elif .reviewDecision=="CHANGES_REQUESTED" then "changes requested"
  elif (checkstate=="failing")              then "CI red"
  else                                           "awaiting review" end;
def reactions:
  # Handle both old API (.users.totalCount) and new API (.users as array or .reactors.totalCount)
  ([(.reactionGroups//[])[]
    | select(.content|IN("THUMBS_UP","HEART","HOORAY","ROCKET","LAUGH"))
    | ((.reactors // .users) // 0)                   # reactors = newer gh cli, users = older
    | if   type=="object" then (.totalCount // 0)    # {totalCount: N}
      elif type=="array"  then length                # [{login:…}, …]
      elif type=="number" then .
      else 0 end
  ] | add // 0);
def commentcount:
  # .comments is an integer in older gh cli, array of objects in newer versions
  (.comments | if type=="number" then . else (. // [] | length) end);
def labelnames: [(.labels//[])[].name | ascii_downcase];
def labelboost:
  labelnames as $l
  | (if ($l|any(test("security|data.?loss|critical|regression|blocker"))) then 12 else 0 end)
  + (if ($l|any(test("bug|p1|p2|priority")))                              then  5 else 0 end);
JQ

# ── bucket filters ────────────────────────────────────────────────────────────
# 1. Mergeable now: approved, no conflicts, CI green
jq --argjson now "$NOW" "$HELPERS"'
  map(select(.isDraft==false and .reviewDecision=="APPROVED"
             and .mergeable=="MERGEABLE" and (checkstate!="failing")))
  | sort_by(.updatedAt)' "$WORK/prs.json" > "$WORK/b1.json"

# 2. One small fix away: stalled + small + blocked by one thing
jq --argjson now "$NOW" --argjson stale "$STALE_DAYS" --argjson small "$SMALL" "$HELPERS"'
  map(select(.isDraft==false
        and ((.additions + .deletions) < $small)
        and (ageDays($now) >= $stale)
        and (.mergeable=="CONFLICTING"
             or .reviewDecision=="CHANGES_REQUESTED"
             or (checkstate=="failing")
             or ((.reviews|length)>0 and .reviewDecision!="APPROVED"))))
  | sort_by(.additions + .deletions)' "$WORK/prs.json" > "$WORK/b2.json"

# 3. High-priority issues: scored by engagement + label severity
jq --argjson top "$TOP_ISSUES" "$HELPERS"'
  map(. + {score: ((reactions*2) + commentcount + labelboost),
           react: reactions,
           ccount: commentcount,
           labs: (labelnames | join(", "))})
  | sort_by(-.score) | .[0:$top]' "$WORK/issues.json" > "$WORK/b3.json" \
  || { echo "error: failed to score issues — check issues.json shape" >&2
       echo "  Tip: inspect with: jq '.[0] | {comments,reactionGroups}' .triage-cache/${REPO_SLUG}/issues.json" >&2
       exit 6; }

B1_COUNT=$(jq length "$WORK/b1.json")
B2_COUNT=$(jq length "$WORK/b2.json")
B3_COUNT=$(jq length "$WORK/b3.json")
echo "  Buckets: ✅ mergeable=$B1_COUNT  🔧 one-fix-away=$B2_COUNT  ⭐ top-issues=$B3_COUNT" >&2

# ── relationship graph ────────────────────────────────────────────────────────
echo "  Building relationship graph…" >&2

# Extract cross-references from PR/issue body text
cat > "$WORK/rel.jq" <<'JQ'
def fp: "(?i)(?:fix(?:es|ed)?|clos(?:es|ed)|resolv(?:es|ed))\\s+#([0-9]+)";
def dp: "(?i)(?:depends?\\s+on|blocked\\s+by)\\s+#([0-9]+)";
def bp: "(?i)blocks?\\s+#([0-9]+)";
def rp: "(?i)(?:related\\s+to|see\\s+also|part\\s+of|implements?|addresses?)\\s+#([0-9]+)";
[
  (.[0][] | . as $i | (.body//"") as $b |
    ($b|scan(fp) | {from:$i.number,to:(.[0]|tonumber),rel:"fixes",     ftype:"pr",   ftitle:$i.title,furl:$i.url}),
    ($b|scan(dp) | {from:$i.number,to:(.[0]|tonumber),rel:"depends_on",ftype:"pr",   ftitle:$i.title,furl:$i.url}),
    ($b|scan(bp) | {from:$i.number,to:(.[0]|tonumber),rel:"blocks",    ftype:"pr",   ftitle:$i.title,furl:$i.url}),
    ($b|scan(rp) | {from:$i.number,to:(.[0]|tonumber),rel:"related",   ftype:"pr",   ftitle:$i.title,furl:$i.url})
  ),
  (.[1][] | . as $i | (.body//"") as $b |
    ($b|scan(dp) | {from:$i.number,to:(.[0]|tonumber),rel:"depends_on",ftype:"issue",ftitle:$i.title,furl:$i.url}),
    ($b|scan(bp) | {from:$i.number,to:(.[0]|tonumber),rel:"blocks",    ftype:"issue",ftitle:$i.title,furl:$i.url}),
    ($b|scan(rp) | {from:$i.number,to:(.[0]|tonumber),rel:"related",   ftype:"issue",ftitle:$i.title,furl:$i.url})
  )
] | unique_by([.from,.to,.rel])
JQ

jq -s -f "$WORK/rel.jq" "$WORK/prs.json" "$WORK/issues.json" > "$WORK/edges.json"

# Incoming index: to_number → [{from, rel, ftype, ftitle, furl}]
jq 'group_by(.to) |
    map({key:(.[0].to|tostring),
         value:map({from:.from,rel:.rel,ftype:.ftype,ftitle:.ftitle,furl:.furl})}) |
    from_entries' "$WORK/edges.json" > "$WORK/incoming.json"

EDGE_COUNT=$(jq length "$WORK/edges.json")
CL_COUNT=$(jq '[.[].from] | unique | length' "$WORK/edges.json")
echo "  Cross-reference edges: $EDGE_COUNT  ($CL_COUNT items with outgoing refs)" >&2

# Enrich each bucket: add .out_refs (what it references) and .in_refs (who references it)
cat > "$WORK/enrich.jq" <<'JQ'
. as $items |
($edges | group_by(.from) | map({key:(.[0].from|tostring),value:.}) | from_entries) as $out |
$items | map(. as $item | . + {
  out_refs: ($out[($item.number|tostring)] // []),
  in_refs:  ($incoming[($item.number|tostring)] // [])
})
JQ

for bucket in b1 b2 b3; do
  jq -f "$WORK/enrich.jq" \
     --argjson edges    "$(cat "$WORK/edges.json")" \
     --argjson incoming "$(cat "$WORK/incoming.json")" \
     "$WORK/${bucket}.json" > "$WORK/${bucket}_e.json"
done

# ── markdown → HTML ──────────────────────────────────────────────────────────
# The model is asked for GFM; the report is HTML. Without this the pipe tables
# land on the page as raw "| a | b |" text. Handles the subset the prompts ask
# for: pipe tables, links, inline code, bold. Escapes first so model output can
# never inject markup.
md_to_html() {
  sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' \
      -e 's/`\([^`]*\)`/<code>\1<\/code>/g' \
      -e 's/\[\([^][]*\)\](\([^() ]*\))/<a href="\2" target="_blank">\1<\/a>/g' \
      -e 's/\*\*\([^*]*\)\*\*/<strong>\1<\/strong>/g' \
  | awk '
      function trim(s){ sub(/^[ \t]+/,"",s); sub(/[ \t]+$/,"",s); return s }
      function flush(){ if(intable){ print "</tbody></table></div>"; intable=0 } }
      /^[ \t]*\|/ {
        line=$0
        sub(/^[ \t]*\|/,"",line); sub(/\|[ \t]*$/,"",line)
        # separator row (|---|:--:|) carries no content
        if (line ~ /^[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)*$/) next
        n=split(line,cell,"|")
        if(!intable){
          print "<div class=\"ai-wrap\"><table class=\"ai-table\"><thead><tr>"
          for(i=1;i<=n;i++) print "<th>" trim(cell[i]) "</th>"
          print "</tr></thead><tbody>"
          intable=1
          next
        }
        print "<tr>"
        for(i=1;i<=n;i++) print "<td>" trim(cell[i]) "</td>"
        print "</tr>"
        next
      }
      /^[ \t]*$/ { flush(); next }
      { flush(); print "<p class=\"ai-note\">" $0 "</p>" }
      END{ flush() }
    '
}

# ── Claude enrichment helper (read-only) ─────────────────────────────────────
RO_TOOLS='Read,Bash(gh pr view:*),Bash(gh pr diff:*),Bash(gh issue view:*)'
enrich_ai() {
  local file="$1" prompt="$2"
  { claude -p "READ-ONLY triage for ${REPO}. Do NOT run any command that modifies the repo.
Output GitHub-Flavored Markdown only: a single table, no preamble and no trailing commentary.
Keep every cell under 240 characters — this renders as a table cell, not a paragraph.
${prompt}
JSON input:
$(cat "$file")" \
      --allowedTools "$RO_TOOLS" --output-format text --max-turns 6 2>/dev/null \
      || echo "AI enrichment unavailable — deterministic tables above are unaffected."
  } | md_to_html
}

# ── rate-limit bar color ──────────────────────────────────────────────────────
RL_PCT=$(( (RL_USED * 100) / (RL_LIM > 0 ? RL_LIM : 1) ))
if   [[ $RL_PCT -lt 50 ]]; then RL_COLOR="#22c55e"
elif [[ $RL_PCT -lt 80 ]]; then RL_COLOR="#f59e0b"
else                             RL_COLOR="#ef4444"; fi

# ── HTML report assembly ─────────────────────────────────────────────────────
# Assembled into a scratch file first, then moved into place (see mv below) so a
# failure part-way through never leaves a half-written report behind.
echo "Writing ${OUT}…" >&2
{

# ── header + embedded CSS ────────────────────────────────────────────────────
cat <<'CSS'
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
CSS
echo "<title>Triage: ${REPO} — ${DATE_TAG}</title>"
cat <<'CSS'
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f6f8fa;color:#24292e;font-size:14px}
a{color:#0366d6;text-decoration:none}a:hover{text-decoration:underline}
#sidebar{position:fixed;top:0;left:0;width:230px;height:100vh;background:#1a1e27;color:#c9d1d9;padding:16px;overflow-y:auto;z-index:100}
.sb-repo{font-size:13px;font-weight:700;word-break:break-all;margin-bottom:4px}
.sb-repo a{color:#58a6ff}
.sb-meta{font-size:11px;color:#8b949e;line-height:1.7;margin-bottom:12px}
.sb-hr{border:none;border-top:1px solid #30363d;margin:10px 0}
.sb-sec{font-size:10px;text-transform:uppercase;color:#8b949e;letter-spacing:.08em;padding:6px 8px 2px;font-weight:600}
.nav-list{list-style:none}
.nav-list li{margin:1px 0}
.nav-list a{display:block;padding:5px 8px;color:#c9d1d9;border-radius:4px;font-size:13px}
.nav-list a:hover{background:#21262d;color:#f0f6fc}
.sb-n{float:right;background:#21262d;border-radius:10px;padding:1px 7px;font-size:11px;color:#8b949e}
#content{margin-left:246px;padding:28px 36px;max-width:1080px}
h1{font-size:20px;font-weight:600;margin-bottom:6px}
h2{font-size:17px;font-weight:600;margin:36px 0 8px;padding-bottom:8px;border-bottom:2px solid #e1e4e8}
h3{font-size:14px;font-weight:600;margin:20px 0 6px;color:#444}
.hint{color:#666;font-size:12px;margin-bottom:12px;line-height:1.7}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e1e4e8;border-radius:6px;overflow:hidden;margin-bottom:16px;font-size:13px}
th{background:#f6f8fa;padding:8px 12px;text-align:left;font-weight:600;font-size:12px;border-bottom:1px solid #e1e4e8;white-space:nowrap}
td{padding:7px 12px;border-top:1px solid #f0f0f0;vertical-align:top;line-height:1.6}
tr:hover td{background:#fafbfc}
.b{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:500;margin:1px 2px;white-space:nowrap}
.g{background:#e6ffed;color:#1a7f37;border:1px solid #a7f3d0}
.r{background:#ffeef0;color:#b91c1c;border:1px solid #fca5a5}
.y{background:#fffbeb;color:#92400e;border:1px solid #fde68a}
.u{background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe}
.s{background:#f6f8fa;color:#555;border:1px solid #e1e4e8}
.p{background:#f3e8ff;color:#6d28d9;border:1px solid #ddd6fe}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin:16px 0 24px}
.stat{background:#fff;border:1px solid #e1e4e8;border-radius:8px;padding:14px;text-align:center}
.stat-n{font-size:26px;font-weight:700}
.stat-l{font-size:11px;color:#666;margin-top:3px}
.cluster{background:#fff;border:1px solid #e1e4e8;border-left:4px solid #0366d6;border-radius:6px;padding:14px 16px;margin-bottom:12px}
.ch{font-weight:600;font-size:13px;margin-bottom:8px}
.rr{display:flex;align-items:center;gap:8px;padding:4px 0;font-size:12px;color:#444;border-top:1px solid #f6f8fa}
.warn{background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:10px 14px;margin-bottom:16px;color:#92400e;font-size:12px}
.ai{background:#fbfcfd;border:1px solid #e1e4e8;border-left:4px solid #7c3aed;border-radius:6px;padding:14px 16px;margin:8px 0 20px;font-size:13px;line-height:1.65}
.ai-wrap{overflow-x:auto;margin:4px 0}
.ai-table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e1e4e8;border-radius:6px;overflow:hidden;font-size:13px;margin:0}
.ai-table th{background:#f3e8ff;color:#5b21b6;padding:7px 12px;text-align:left;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid #e9d5ff;white-space:nowrap}
.ai-table td{padding:9px 12px;border-top:1px solid #f0f0f0;vertical-align:top;line-height:1.65}
.ai-table tr:hover td{background:#faf9fe}
.ai-table td:first-child{white-space:nowrap;font-weight:600}
.ai-table td:last-child{min-width:22em}
.ai-table code{background:#f6f8fa;border:1px solid #e9ecef;border-radius:4px;padding:1px 5px;font-family:'SFMono-Regular',Consolas,monospace;font-size:12px;word-break:break-word}
.ai-note{color:#57606a;font-size:12.5px;margin:0 0 10px;line-height:1.65}
.ai-note:last-child{margin-bottom:0}
.rl-bar{height:8px;background:#e1e4e8;border-radius:4px;overflow:hidden;margin-top:8px}
.rl-fill{height:100%;border-radius:4px}
footer{margin-top:48px;padding:16px 0;border-top:1px solid #e1e4e8;font-size:11px;color:#666;text-align:center}
</style></head><body>
CSS

# ── sidebar navigation ────────────────────────────────────────────────────────
cat <<SIDEBAR
<nav id="sidebar">
  <div class="sb-repo"><a href="https://github.com/${REPO}" target="_blank">${REPO}</a></div>
  <div class="sb-meta">${DATE_LABEL}<br>${PR_COUNT} PRs &middot; ${ISSUE_COUNT} issues</div>
  <hr class="sb-hr">
  <div class="sb-sec">Sections</div>
  <ul class="nav-list">
    <li><a href="#summary">📊 Summary</a></li>
    <li><a href="#clusters">🔗 Clusters <span class="sb-n">${CL_COUNT}</span></a></li>
    <hr class="sb-hr" style="margin:4px 0">
    <li><a href="#b1">✅ Mergeable Now <span class="sb-n">${B1_COUNT}</span></a></li>
    <li><a href="#b2">🔧 One Fix Away <span class="sb-n">${B2_COUNT}</span></a></li>
    <li><a href="#b3">⭐ Top Issues <span class="sb-n">${B3_COUNT}</span></a></li>
    <hr class="sb-hr" style="margin:4px 0">
    <li><a href="#ratelimit">📈 Rate Limit</a></li>
  </ul>
  <div style="position:absolute;bottom:16px;left:0;right:0;text-align:center;font-size:10px;color:#444">read-only &middot; no changes made</div>
</nav>
<main id="content">
SIDEBAR

# ── summary ──────────────────────────────────────────────────────────────────
cat <<SUMMARY
<section id="summary">
<h1>Triage: ${REPO}</h1>
<p style="color:#666;font-size:12px;margin-bottom:16px">${DATE_LABEL} &middot; read-only &middot; no repository changes made</p>
SUMMARY
[[ "$RL_REM" -lt "$RATE_WARN" ]] && \
  echo "<div class=\"warn\">⚠ Rate limit low: <strong>$RL_REM / $RL_LIM</strong> API calls remaining. Resets $RL_RST_FMT. Use --no-ai to conserve quota.</div>"
cat <<SUMMARY
<div class="stat-grid">
  <div class="stat"><div class="stat-n" style="color:#0366d6">${PR_COUNT}</div><div class="stat-l">Open PRs</div></div>
  <div class="stat"><div class="stat-n" style="color:#0366d6">${ISSUE_COUNT}</div><div class="stat-l">Open Issues</div></div>
  <div class="stat"><div class="stat-n" style="color:#1a7f37">${B1_COUNT}</div><div class="stat-l">Mergeable Now</div></div>
  <div class="stat"><div class="stat-n" style="color:#d97706">${B2_COUNT}</div><div class="stat-l">One Fix Away</div></div>
  <div class="stat"><div class="stat-n" style="color:#7c3aed">${B3_COUNT}</div><div class="stat-l">Top Issues</div></div>
  <div class="stat"><div class="stat-n" style="color:#0366d6">${EDGE_COUNT}</div><div class="stat-l">Cross-refs</div></div>
</div>
<p class="hint">Thresholds: stalled &ge; ${STALE_DAYS}d idle &middot; small &lt; ${SMALL} lines &middot; top ${TOP_ISSUES} issues &middot; PR fetch &le; ${PR_LIMIT} &middot; issue fetch &le; ${ISSUE_LIMIT}</p>
</section>
SUMMARY

# ── clusters: upstream/downstream map ────────────────────────────────────────
cat <<CLUST
<section id="clusters">
<h2>🔗 Clusters — Upstream / Downstream Relationship Map</h2>
<p class="hint">
  Items whose body text references other PRs or issues via keyword patterns.<br>
  <span class="b g">⬇ fixes / closes / resolves</span> — this item resolves the linked issue (it is downstream of it) &nbsp;
  <span class="b r">⬆ depends on / blocked by</span> — must wait for linked item to merge first (upstream dep) &nbsp;
  <span class="b y">⚡ blocks</span> — this item blocks the linked one from proceeding &nbsp;
  <span class="b u">↔ related / implements / see also</span> — loosely connected<br>
  <span class="b s">← incoming</span> — another item references this one
</p>
CLUST

if [[ "$EDGE_COUNT" == "0" ]]; then
  echo '<p style="color:#666;padding:12px 0"><em>No cross-references found in PR/issue bodies. Patterns checked: fixes/closes/resolves #N, depends on #N, blocked by #N, blocks #N, related to #N, implements #N, addresses #N, see also #N.</em></p>'
else
  jq -r --arg repo "$REPO" '
    group_by(.from) | .[] |
    . as $grp | $grp[0] as $f |
    "<div class=\"cluster\">" +
    "<div class=\"ch\">" +
      "<span class=\"b " + (if $f.ftype=="pr" then "p" else "u" end) + "\">" +
      $f.ftype + " #" + ($f.from|tostring) + "</span> " +
      "<a href=\"" + $f.furl + "\" target=\"_blank\">" + ($f.ftitle|@html) + "</a>" +
    "</div>" +
    ($grp | map(
      "<div class=\"rr\">" +
        "<span class=\"b " +
        (if .rel=="fixes"        then "g\">⬇ fixes / closes #"
         elif .rel=="depends_on" then "r\">⬆ depends on #"
         elif .rel=="blocks"     then "y\">⚡ blocks #"
         else                         "u\">↔ related to #" end) +
        (.to|tostring) + "</span>" +
        " <a href=\"https://github.com/" + $repo + "/issues/" +
        (.to|tostring) + "\" target=\"_blank\">#" + (.to|tostring) + "</a>" +
      "</div>"
    ) | join("")) +
    "</div>"
  ' "$WORK/edges.json"
fi
echo '</section>'

# ── bucket 1: mergeable now ───────────────────────────────────────────────────
cat <<B1HDR
<section id="b1">
<h2>✅ Mergeable Now — approved &middot; no conflicts &middot; CI green (${B1_COUNT})</h2>
<p class="hint">Quick wins: review once more and merge.</p>
B1HDR

if [[ "$B1_COUNT" == "0" ]]; then
  echo '<p style="color:#666"><em>No PRs currently meet all merge criteria.</em></p>'
else
  echo '<table><thead><tr><th>#</th><th>Size</th><th>Idle</th><th>Author</th><th>Title</th><th>Relationships</th></tr></thead><tbody>'
  jq -r --argjson now "$NOW" "$HELPERS"'
    .[] |
    "<tr id=\"pr-\(.number)\">" +
    "<td><a href=\"\(.url)\" target=\"_blank\">#\(.number)</a></td>" +
    "<td><span class=\"b g\">+\(.additions)/-\(.deletions)</span></td>" +
    "<td>\(ageDays($now))d</td>" +
    "<td>\(.author.login//"")</td>" +
    "<td>\(.title|@html)</td>" +
    "<td>" +
      ((.out_refs//[]) | map(
        "<span class=\"b " +
        (if .rel=="fixes"        then "g\">⬇ #"
         elif .rel=="depends_on" then "r\">⬆ #"
         elif .rel=="blocks"     then "y\">⚡ #"
         else                         "u\">↔ #" end) +
        (.to|tostring) + "</span>"
      ) | join(" ")) +
      ((.in_refs//[]) | map(
        "<span class=\"b s\">&larr; " + .ftype + " #" + (.from|tostring) + "</span>"
      ) | join(" ")) +
    "</td></tr>"
  ' "$WORK/b1_e.json"
  echo '</tbody></table>'
fi

if [[ "$AI" == 1 && "$B1_COUNT" != "0" ]]; then
  echo '<h3>🤖 AI Assessment</h3><div class="ai">'
  enrich_ai "$WORK/b1_e.json" \
    "For each PR give a one-line 'anything to double-check before merge?' note. Return a GFM table: #, title, note."
  echo '</div>'
fi
echo '</section>'

# ── bucket 2: one fix away ────────────────────────────────────────────────────
cat <<B2HDR
<section id="b2">
<h2>🔧 One Fix Away — stalled &amp; small, needs a nudge (${B2_COUNT})</h2>
<p class="hint">Near-close PRs blocked on rebase / one reviewer comment / red CI.</p>
B2HDR

if [[ "$B2_COUNT" == "0" ]]; then
  echo '<p style="color:#666"><em>No stalled small PRs found with current thresholds.</em></p>'
else
  echo '<table><thead><tr><th>#</th><th>Size</th><th>Idle</th><th>Blocker</th><th>Title</th><th>Relationships</th></tr></thead><tbody>'
  jq -r --argjson now "$NOW" "$HELPERS"'
    .[] |
    "<tr id=\"pr-\(.number)\">" +
    "<td><a href=\"\(.url)\" target=\"_blank\">#\(.number)</a></td>" +
    "<td>+\(.additions)/-\(.deletions)</td>" +
    "<td>\(ageDays($now))d</td>" +
    "<td><span class=\"b " +
      (if blocker=="CI red" then "r" elif blocker=="needs rebase" then "y" else "s" end) +
      "\">\(blocker)</span></td>" +
    "<td>\(.title|@html)</td>" +
    "<td>" +
      ((.out_refs//[]) | map(
        "<span class=\"b " +
        (if .rel=="fixes"        then "g\">⬇ #"
         elif .rel=="depends_on" then "r\">⬆ #"
         elif .rel=="blocks"     then "y\">⚡ #"
         else                         "u\">↔ #" end) +
        (.to|tostring) + "</span>"
      ) | join(" ")) +
      ((.in_refs//[]) | map(
        "<span class=\"b s\">&larr; " + .ftype + " #" + (.from|tostring) + "</span>"
      ) | join(" ")) +
    "</td></tr>"
  ' "$WORK/b2_e.json"
  echo '</tbody></table>'
fi

if [[ "$AI" == 1 && "$B2_COUNT" != "0" ]]; then
  echo '<h3>🤖 AI Assessment</h3><div class="ai">'
  enrich_ai "$WORK/b2_e.json" \
    "For each PR state in one line exactly what single change unblocks it. Return a GFM table: #, title, what-is-left."
  echo '</div>'
fi
echo '</section>'

# ── bucket 3: top issues ──────────────────────────────────────────────────────
cat <<B3HDR
<section id="b3">
<h2>⭐ High-Priority Issues — worth picking up next (${B3_COUNT})</h2>
<p class="hint">Score = (👍 reactions &times; 2) + 💬 comments + label boost (+12 security/critical/blocker, +5 bug/p1/p2).</p>
B3HDR

if [[ "$B3_COUNT" == "0" ]]; then
  echo '<p style="color:#666"><em>No open issues found.</em></p>'
else
  echo '<table><thead><tr><th>Score</th><th>#</th><th>👍</th><th>💬</th><th>Labels</th><th>Title</th><th>Relationships</th></tr></thead><tbody>'
  jq -r '
    .[] |
    "<tr id=\"issue-\(.number)\">" +
    "<td><strong>\(.score)</strong></td>" +
    "<td><a href=\"\(.url)\" target=\"_blank\">#\(.number)</a></td>" +
    "<td>\(.react)</td>" +
    "<td>\(.ccount)</td>" +
    "<td>" + (if .labs!="" then "<span class=\"b s\">\(.labs|@html)</span>" else "" end) + "</td>" +
    "<td>\(.title|@html)</td>" +
    "<td>" +
      ((.out_refs//[]) | map(
        "<span class=\"b " +
        (if .rel=="depends_on" then "r\">⬆ #"
         elif .rel=="blocks"   then "y\">⚡ #"
         else                       "u\">↔ #" end) +
        (.to|tostring) + "</span>"
      ) | join(" ")) +
      ((.in_refs//[]) | map(
        "<span class=\"b s\">&larr; " + .ftype + " #" + (.from|tostring) + "</span>"
      ) | join(" ")) +
    "</td></tr>"
  ' "$WORK/b3_e.json"
  echo '</tbody></table>'
fi

if [[ "$AI" == 1 && "$B3_COUNT" != "0" ]]; then
  echo '<h3>🤖 AI Assessment</h3><div class="ai">'
  enrich_ai "$WORK/b3_e.json" \
    "Pick the 8 you'd act on first. For each: one-line why-now + scope (small/medium/large). Return a GFM table: #, title, why-now, scope."
  echo '</div>'
fi
echo '</section>'

# ── rate limit ────────────────────────────────────────────────────────────────
cat <<RATELIMIT
<section id="ratelimit">
<h2>📈 GitHub API Rate Limit</h2>
<table>
<thead><tr><th>Metric</th><th>Value</th></tr></thead>
<tbody>
<tr><td>Remaining</td><td><strong>${RL_REM}</strong> / ${RL_LIM}</td></tr>
<tr><td>Used this hour</td><td>${RL_USED} calls (${RL_PCT}%)</td></tr>
<tr><td>Resets at</td><td>${RL_RST_FMT}</td></tr>
<tr><td>Calls used by this run</td><td>~3 (pr list + issue list + rate_limit check)</td></tr>
</tbody>
</table>
<div class="rl-bar"><div class="rl-fill" style="width:${RL_PCT}%;background:${RL_COLOR}"></div></div>
<p class="hint" style="margin-top:8px">
  Authenticated users: 5,000 req/hr &nbsp;&middot;&nbsp;
  AI enrichment adds ~1 call per viewed PR/issue &nbsp;&middot;&nbsp;
  <a href="https://docs.github.com/en/rest/rate-limit" target="_blank">GitHub rate limit docs &nearr;</a>
</p>
</section>
RATELIMIT

# ── footer ────────────────────────────────────────────────────────────────────
cat <<FOOTER
<footer>
  triage.sh v2 &middot; read-only &middot; no repository changes made &middot;
  <a href="https://github.com/${REPO}" target="_blank">${REPO}</a> &middot;
  ${DATE_LABEL}
</footer>
</main></body></html>
FOOTER

} > "$WORK/report.html"

# Only publish once the whole document is written — a mid-way failure (bad jq,
# gh hiccup) must never leave a truncated report at "$OUT".
mv "$WORK/report.html" "$OUT"

echo "Wrote ${OUT}" >&2
