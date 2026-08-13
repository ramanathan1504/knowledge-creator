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
oss-harvest.py — pull every trace of my public OSS work on GitHub into the
DEVONthink knowledge base as Markdown.

Covers, for each issue / pull request I touched:
  * the title, body, state, labels, dates and links
  * MY comments, reviews and inline review-thread replies, called out
  * the full surrounding thread, so a comment is never stranded without context
  * for PRs: base/head, diffstat, commit list, and every review thread
    including ones GitHub has marked resolved or outdated
Plus my authored commits, de-duplicated across forks.

Scope
-----
Excluded server-side (not downloaded then filtered):
    -org:intemo-dev  -user:ramanathan1504
Everything else public is in scope, including one-off comments in repos I have
no other connection to.

Usage
-----
    ./oss-harvest.py --probe        # counts only, no writes, no detail fetch
    ./oss-harvest.py --full         # full window (2025-06-01 -> today)
    ./oss-harvest.py                # incremental: only items updated since
                                    # the last successful run
    ./oss-harvest.py --since 2026-07-01
    ./oss-harvest.py --limit 5      # cap items, for testing

Incremental runs are what the daily automation uses: the state file records
the last run, and GitHub's `updated:>=` qualifier means only genuinely changed
threads are re-fetched and rewritten.

Rate limits respected: Search API is 30 requests/minute (hence the sleeps),
GraphQL is 5000 points/hour. A full run is ~170 items and finishes well inside
both.
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------- config ---
# Whose activity to harvest. Asked of `gh` rather than hardcoded: this repository
# is public, and a hardcoded username means anyone who clones it silently
# harvests SOMEBODY ELSE'S history into their own archive -- which looks like the
# tool working, and is the most confusing possible failure.
#
# KB_GH_USER overrides, for harvesting an account other than the one `gh` is
# authenticated as.
def _default_user():
    env = os.environ.get("KB_GH_USER", "").strip()
    if env:
        return env
    try:
        r = subprocess.run(["gh", "api", "user", "--jq", ".login"],
                           capture_output=True, text=True, timeout=30)
        who = (r.stdout or "").strip()
        if who:
            return who
    except Exception:
        pass
    sys.exit("cannot tell whose activity to harvest: set KB_GH_USER, or run `gh auth login`")


USER = _default_user()

# Repositories to leave out -- your own forks and any private org whose work does
# not belong in a personal archive. KB_EXCLUDE replaces this wholesale; the
# default excludes only the harvesting user's own repos, since those are already
# yours and add noise rather than history.
EXCLUDE = os.environ.get("KB_EXCLUDE", f"-user:{USER}")
WINDOW_START = os.environ.get("KB_WINDOW_START", "2025-06-01")

HOME = Path.home()
from kbpaths import ARCHIVE as DEVON, SCRIPTS
PROJ = DEVON / "Projects"
OUT = PROJ / "oss-github"          # now holds only the cross-repo index

# Topic first, provenance second. A repository is about one thing, so this is a
# lookup rather than a classifier — and an unlisted repo lands in `oss-misc`
# instead of being guessed at, so a new repo shows up as an obvious gap rather
# than quietly polluting a real topic. Keep in sync with topic-refile.py.
REPO_TOPIC = {
    "apache/logging-log4j2":        "log4j",
    "apache/logging-log4j-samples": "log4j",
    "apache/logging-parent":        "log4j",
    "apache/logging-site":          "log4j",
    "apache/spark":                 "big-data",
    "aws/aws-lambda-java-libs":     "aws-infra",
    "canonical/cloud-init":         "aws-infra",
    "canonical/devpack-for-spring":     "spring",
    "canonical/devpack-for-spring-cli": "spring",
    "canonical/openssl-fips-java":  "security",
    "openssl/openssl":              "security",
    "elastic/elasticsearch":        "observability",
    "micrometer-metrics/tracing":   "observability",
    "opensearch-project/OpenSearch": "observability",
    "jreleaser/jreleaser":          "build-tooling",
    "spring-projects/spring-kafka": "kafka",
}


def repo_dir(owner: str, repo: str) -> Path:
    topic = REPO_TOPIC.get(f"{owner}/{repo}", "oss-misc")
    return PROJ / topic / "oss-github" / f"{owner}__{repo}"
# Beside the script, not at a fixed path. oss-harvest-daily.sh --status reads the
# watermark from its own directory, so a hardcoded ~/claude-cli made the two
# disagree the moment the checkout moved -- and silently recreated that folder to
# hold the state of a checkout living somewhere else entirely.
STATE = SCRIPTS / ".oss-harvest-state.json"
LOG = SCRIPTS / ".oss-harvest.log"

SEARCH_SLEEP = 2.2          # 30 req/min ceiling on the Search API
NOW = datetime.now(timezone.utc)

# Forks that carry my upstream commits; collapse them onto the canonical repo
# so one commit does not become four notes.
FORK_CANON = {
    "AIxCyberChallenge/afc-logging-log4j2": "apache/logging-log4j2",
    "ashr123/logging-log4j2": "apache/logging-log4j2",
    "verbinna22/logging-log4j2": "apache/logging-log4j2",
}


def log(msg):
    line = f"{datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def gh(args, check=True):
    """Run gh and return stdout. gh handles auth and retries for us."""
    r = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        if check:
            raise RuntimeError(f"gh {' '.join(args[:3])}… failed: {r.stderr.strip()[:400]}")
        return ""
    return r.stdout


def gh_json(args, check=True):
    out = gh(args, check=check)
    return json.loads(out) if out.strip() else None


def graphql(query, **variables):
    args = ["api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        # -F does type inference (ints stay ints); -f would stringify
        args += ["-F", f"{k}={v}"]
    data = gh_json(args)
    if data and "errors" in data:
        raise RuntimeError(f"GraphQL: {data['errors']}")
    return data["data"] if data else None


# ------------------------------------------------------------- discovery ---
def search_issues(qualifier, since, exclude=True):
    """Page through the Search API, returning (owner, repo, number, updated).

    `exclude` off for a qualifier that already names its repository. EXCLUDE is
    `-user:<me>` by default, which keeps my own repositories out of an archive of
    contributions to other people's -- correct for "everything I am involved in",
    and a contradiction for "everything in this repository" when the repository
    is mine. GitHub does not reject the contradiction: it drops the `repo:`
    filter and returns a thousand threads from everywhere, which looks like a
    wildly productive scan and is entirely the wrong material.
    """
    q = f"{qualifier} updated:{since}..{NOW:%Y-%m-%d}"
    if exclude:
        q = f"{q} {EXCLUDE}"
    found, page = [], 1
    while page <= 10:                      # Search API caps at 1000 results
        data = gh_json(["api", "-X", "GET", "search/issues",
                        "-f", f"q={q}", "-f", "per_page=100", "-f", f"page={page}"],
                       check=False)
        time.sleep(SEARCH_SLEEP)
        if not data or not data.get("items"):
            break
        for it in data["items"]:
            m = re.search(r"/repos/([^/]+)/([^/]+)$", it["repository_url"])
            if m:
                found.append((m.group(1), m.group(2), it["number"], it["updated_at"]))
        if len(data["items"]) < 100:
            break
        page += 1
    return found


def discover_repos(repos, since):
    """Every thread in a repository, whether or not I am attached to it.

    `discover` answers "what have I touched", which is the right question for a
    record of your own work and the wrong one for working on something new: an
    issue you are about to pick up is, by definition, one you have never
    commented on. Its discussion is then the one thing not on disk -- the core
    stores an issue's body and a count of its comments, never their text.

    Scoped to named repositories and to the same watermark as everything else,
    because "every thread in every repository" is not a thing anyone wants.
    """
    seen = {}
    for nwo in repos:
        hits = search_issues(f"repo:{nwo}", since, exclude=False)
        log(f"  repo:{nwo:37s} {len(hits):4d}")
        for owner, repo, num, updated in hits:
            seen[(owner, repo, num)] = updated
    return seen


def discover(since):
    """Union of every way I can be attached to a thread."""
    qualifiers = [
        f"involves:{USER}",              # author, assignee, mentions, commenter
        f"reviewed-by:{USER} type:pr",   # reviews are NOT covered by involves
        f"review-requested:{USER} type:pr",
        f"commenter:{USER}",
        f"mentions:{USER}",
    ]
    seen = {}
    for qual in qualifiers:
        hits = search_issues(qual, since)
        log(f"  {qual:42s} {len(hits):4d}")
        for owner, repo, num, updated in hits:
            seen[(owner, repo, num)] = updated
    return seen


# ---------------------------------------------------------------- detail ---
ITEM_Q = """
query($owner:String!,$name:String!,$number:Int!,
      $cCur:String,$rCur:String,$tCur:String){
 repository(owner:$owner,name:$name){
  issueOrPullRequest(number:$number){
   __typename
   ... on Issue {
     number title body url state createdAt updatedAt closedAt
     author{login} labels(first:25){nodes{name}}
     comments(first:100,after:$cCur){
       pageInfo{hasNextPage endCursor}
       nodes{author{login} createdAt body url}}
   }
   ... on PullRequest {
     number title body url state merged mergedAt createdAt updatedAt closedAt
     author{login} baseRefName headRefName additions deletions changedFiles
     labels(first:25){nodes{name}}
     comments(first:100,after:$cCur){
       pageInfo{hasNextPage endCursor}
       nodes{author{login} createdAt body url}}
     reviews(first:100,after:$rCur){
       pageInfo{hasNextPage endCursor}
       nodes{author{login} state submittedAt body url}}
     reviewThreads(first:100,after:$tCur){
       pageInfo{hasNextPage endCursor}
       nodes{isResolved isOutdated path line
         comments(first:50){nodes{author{login} createdAt body url}}}}
     commits(first:100){nodes{commit{oid messageHeadline}}}
   }
  }
 }
}"""


def fetch_item(owner, repo, number):
    """Fetch one thread, paginating comments / reviews / review threads fully.

    'Every inch' means no silent truncation: GitHub returns at most 100 per
    page and log4j threads routinely exceed that.
    """
    node, c_cur, r_cur, t_cur = None, None, None, None
    while True:
        d = graphql(ITEM_Q, owner=owner, name=repo, number=number,
                    **{k: v for k, v in
                       (("cCur", c_cur), ("rCur", r_cur), ("tCur", t_cur)) if v})
        cur = d["repository"]["issueOrPullRequest"] if d["repository"] else None
        if cur is None:
            return None
        if node is None:
            node = cur
        else:
            for key in ("comments", "reviews", "reviewThreads"):
                if key in cur and key in node:
                    node[key]["nodes"] += cur[key]["nodes"]
                    node[key]["pageInfo"] = cur[key]["pageInfo"]

        more = False
        for key, var in (("comments", "cCur"), ("reviews", "rCur"),
                         ("reviewThreads", "tCur")):
            pi = (node.get(key) or {}).get("pageInfo") or {}
            if pi.get("hasNextPage"):
                more = True
                if var == "cCur":
                    c_cur = pi["endCursor"]
                elif var == "rCur":
                    r_cur = pi["endCursor"]
                else:
                    t_cur = pi["endCursor"]
            else:
                if var == "cCur":
                    c_cur = None
                elif var == "rCur":
                    r_cur = None
                else:
                    t_cur = None
        if not more:
            return node


# --------------------------------------------------------------- commits ---
def fetch_commits(since):
    q = f"author:{USER} author-date:>={since} {EXCLUDE}"
    out, page = {}, 1
    while page <= 10:
        d = gh_json(["api", "-X", "GET", "search/commits",
                     "-f", f"q={q}", "-f", "per_page=100", "-f", f"page={page}"],
                    check=False)
        time.sleep(SEARCH_SLEEP)
        if not d or not d.get("items"):
            break
        for it in d["items"]:
            sha = it["sha"]
            full = it["repository"]["full_name"]
            canon = FORK_CANON.get(full, full)
            # keep the canonical repo if the same commit shows up via a fork
            if sha not in out or full == canon:
                out[sha] = {
                    "sha": sha, "repo": canon, "seen_in": full,
                    "message": it["commit"]["message"],
                    "date": it["commit"]["author"]["date"],
                    "url": it["html_url"].replace(full, canon) if canon != full else it["html_url"],
                }
        if len(d["items"]) < 100:
            break
        page += 1
    return out


# --------------------------------------------------------------- render ---
def slug(text, n=60):
    s = re.sub(r"[^\w\s-]", "", (text or "").lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return (s[:n].rstrip("-")) or "untitled"


CODEISH = re.compile(r"[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+|`([\w.$#()]{3,40})`")


def keywords(node, owner, repo):
    tags = {owner.lower(), repo.lower(), "oss", "github"}
    text = (node.get("title") or "") + " " + (node.get("body") or "")
    for c in (node.get("comments") or {}).get("nodes", []):
        text += " " + (c.get("body") or "")
    for m in CODEISH.finditer(text):
        tags.add((m.group(1) or m.group(0)).split(".")[-1].split("(")[0])
    for lb in (node.get("labels") or {}).get("nodes", []):
        tags.add(lb["name"])
    kind = "pr" if node["__typename"] == "PullRequest" else "issue"
    tags.add(f"{kind}-{node['number']}")
    clean = []
    for t in tags:
        t = re.sub(r"[^\w.-]", "-", str(t)).strip("-")
        if 2 < len(t) <= 40:
            clean.append(t)
    return sorted(set(clean), key=lambda t: (-text.count(t), t.lower()))[:26]


def fmt_body(text, indent=""):
    if not text or not text.strip():
        return indent + "_(no body)_"
    return "\n".join(indent + ln for ln in text.replace("\r\n", "\n").rstrip().split("\n"))


def render(node, owner, repo, source="involved"):
    is_pr = node["__typename"] == "PullRequest"
    kind = "PR" if is_pr else "Issue"
    num = node["number"]
    nwo = f"{owner}/{repo}"
    tags = keywords(node, owner, repo)
    author = (node.get("author") or {}).get("login", "ghost")

    mine_comments = [c for c in (node.get("comments") or {}).get("nodes", [])
                     if (c.get("author") or {}).get("login") == USER]
    mine_reviews = [r for r in (node.get("reviews") or {}).get("nodes", [])
                    if (r.get("author") or {}).get("login") == USER]
    mine_threads = []
    for t in (node.get("reviewThreads") or {}).get("nodes", []):
        if any((c.get("author") or {}).get("login") == USER
               for c in t["comments"]["nodes"]):
            mine_threads.append(t)

    role = []
    if author == USER:
        role.append("author")
    if mine_reviews:
        role.append("reviewer")
    if mine_comments:
        role.append("commenter")
    if mine_threads:
        role.append("inline-reviewer")
    # "participant" was a safe default while every harvested thread was one I had
    # touched. Repository-wide harvesting breaks that: most of what it collects I
    # have no part in, and labelling it "participant" would file somebody else's
    # conversation as my own work. Retrieval leans on this -- what I decided and
    # what I merely read are different kinds of evidence for the next question.
    role = ", ".join(role) or "none"

    L = []
    A = L.append
    A("---")
    A(f"tags: [{', '.join(tags)}]")
    A(f"github: {nwo}#{num}")
    A(f"url: {node['url']}")
    A(f"kind: {kind.lower()}")
    A(f"my_role: {role}")
    # How this note came to exist, kept separate from my_role because they answer
    # different questions: one is why it was fetched, the other is what I did in
    # it. A thread found by a repository scan that I turn out to have commented
    # on is still my work, and reads as such.
    A(f"source: {source}")
    A(f"state: {node.get('state')}" + (" (merged)" if node.get("merged") else ""))
    A(f"created: {node.get('createdAt')}")
    A(f"updated: {node.get('updatedAt')}")
    A(f"harvested: {NOW.isoformat(timespec='seconds')}")
    A("---")
    A("")
    A(f"# {nwo} {kind} #{num} — {node.get('title')}")
    A("")
    A("**Search Tags/Keywords:** " + " ".join("#" + t for t in tags))
    A("")
    A(f"**GitHub Context:** [{nwo}#{num}]({node['url']}) · opened by @{author} · "
      f"state `{node.get('state')}`" + (" · **merged**" if node.get("merged") else "") +
      f" · my role: **{role}**")
    if is_pr:
        A("")
        A(f"**Diff:** `{node.get('baseRefName')}` ← `{node.get('headRefName')}` · "
          f"+{node.get('additions')} −{node.get('deletions')} across "
          f"{node.get('changedFiles')} files")
    labels = [l["name"] for l in (node.get("labels") or {}).get("nodes", [])]
    if labels:
        A("")
        A("**Labels:** " + ", ".join(f"`{l}`" for l in labels))
    A("")
    A("---")
    A("")

    A("## The Problem (What & Where)")
    A("")
    A(fmt_body(node.get("body")))
    A("")

    A('## The "Why" (Review Discussions)')
    A("")
    if not (mine_comments or mine_reviews or mine_threads):
        A("_No comment or review of mine on this thread — captured because I am "
          "referenced, assigned, or requested as a reviewer._")
        A("")

    if mine_reviews:
        A("### My reviews")
        A("")
        for r in mine_reviews:
            A(f"**{r.get('state')}** — {r.get('submittedAt')} · [link]({r.get('url')})")
            A("")
            A(fmt_body(r.get("body"), "> "))
            A("")

    if mine_threads:
        A("### My inline review threads")
        A("")
        for t in mine_threads:
            flags = []
            if t.get("isResolved"):
                flags.append("RESOLVED")
            if t.get("isOutdated"):
                flags.append("OUTDATED")
            f = f"  _[{', '.join(flags)}]_" if flags else ""
            A(f"#### `{t.get('path')}`:{t.get('line')}{f}")
            A("")
            for c in t["comments"]["nodes"]:
                who = (c.get("author") or {}).get("login", "ghost")
                mark = " **(me)**" if who == USER else ""
                A(f"- **@{who}**{mark} — {c.get('createdAt')}")
                A("")
                A(fmt_body(c.get("body"), "  > "))
                A("")

    if mine_comments:
        A("### My comments")
        A("")
        for c in mine_comments:
            A(f"**{c.get('createdAt')}** · [link]({c.get('url')})")
            A("")
            A(fmt_body(c.get("body"), "> "))
            A("")

    A("## The Solution (How)")
    A("")
    if is_pr:
        commits = (node.get("commits") or {}).get("nodes", [])
        if commits:
            A("### Commits")
            A("")
            for c in commits:
                A(f"- `{c['commit']['oid'][:10]}` {c['commit']['messageHeadline']}")
            A("")
    A("### Full thread (all participants, for context)")
    A("")
    everyone = sorted((node.get("comments") or {}).get("nodes", []),
                      key=lambda c: c.get("createdAt") or "")
    if not everyone:
        A("_No comments._")
    for c in everyone:
        who = (c.get("author") or {}).get("login", "ghost")
        mark = " **(me)**" if who == USER else ""
        A(f"**@{who}**{mark} — {c.get('createdAt')}")
        A("")
        A(fmt_body(c.get("body"), "> "))
        A("")

    other_threads = [t for t in (node.get("reviewThreads") or {}).get("nodes", [])
                     if t not in mine_threads]
    if other_threads:
        A("### Other review threads")
        A("")
        for t in other_threads:
            flags = []
            if t.get("isResolved"):
                flags.append("RESOLVED")
            if t.get("isOutdated"):
                flags.append("OUTDATED")
            f = f"  _[{', '.join(flags)}]_" if flags else ""
            A(f"#### `{t.get('path')}`:{t.get('line')}{f}")
            A("")
            for c in t["comments"]["nodes"]:
                who = (c.get("author") or {}).get("login", "ghost")
                A(f"- **@{who}** — {c.get('createdAt')}")
                A("")
                A(fmt_body(c.get("body"), "  > "))
                A("")

    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ main ---
def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except json.JSONDecodeError:
            log("state file corrupt, treating as first run")
    return {}


def main():
    argv = sys.argv[1:]
    probe = "--probe" in argv
    full = "--full" in argv
    limit = None
    since = None
    repos = []
    for i, a in enumerate(argv):
        if a == "--limit":
            limit = int(argv[i + 1])
        if a == "--since":
            since = argv[i + 1]
        # Repeatable: --repo owner/name --repo owner/other. Named explicitly
        # rather than discovered, because harvesting a repository you merely
        # watch is a decision with a cost, not a default.
        if a == "--repo":
            repos.append(argv[i + 1])

    state = load_state()
    if since is None:
        since = WINDOW_START if full or not state.get("last_run") \
            else state["last_run"][:10]

    LOG.parent.mkdir(parents=True, exist_ok=True)
    log(f"=== harvest since {since} "
        f"({'probe' if probe else 'full' if full else 'incremental'}) ===")

    items = discover(since)
    involved = set(items)
    log(f"discovered {len(items)} distinct issues/PRs")

    if repos:
        scanned = discover_repos(repos, since)
        # setdefault, so a thread found both ways keeps the earlier watermark and
        # stays in `involved`. One thread is one note; harvesting it twice under
        # two provenances would put the same conversation in the corpus twice and
        # let a duplicate outrank a better match.
        for key, updated in scanned.items():
            items.setdefault(key, updated)
        # "not already found" rather than "not mine": the involvement search
        # excludes my own repositories by default, so plenty of what lands here
        # is my own work. What each note actually was is decided per note, from
        # who wrote what in it, not from how it was discovered.
        log(f"repository scan added {len(items) - len(involved)} thread(s) not "
            f"already found, across {len(repos)} repo(s)")

    commits = fetch_commits(since)
    log(f"discovered {len(commits)} commits "
        f"({len({c['repo'] for c in commits.values()})} repos after fork-dedup)")

    if probe:
        by_repo = {}
        for (o, r, _n) in items:
            by_repo[f"{o}/{r}"] = by_repo.get(f"{o}/{r}", 0) + 1
        log("probe only — nothing written")
        for k, v in sorted(by_repo.items(), key=lambda kv: -kv[1]):
            log(f"    {v:4d}  {k}")
        return

    OUT.mkdir(parents=True, exist_ok=True)
    written, failed = 0, []
    todo = list(items)[:limit] if limit else list(items)

    for i, (owner, repo, number) in enumerate(todo, 1):
        try:
            node = fetch_item(owner, repo, number)
            if node is None:
                failed.append((owner, repo, number, "not visible"))
                continue
            d = repo_dir(owner, repo)
            d.mkdir(parents=True, exist_ok=True)
            kind = "pr" if node["__typename"] == "PullRequest" else "issue"
            path = d / f"{kind}-{number:05d}-{slug(node.get('title'))}.md"
            source = "involved" if (owner, repo, number) in involved else "repo-scan"
            path.write_text(render(node, owner, repo, source), encoding="utf-8")
            written += 1
            if i % 10 == 0 or i == len(todo):
                log(f"  {i}/{len(todo)} … {owner}/{repo}#{number}")
        except Exception as e:                       # keep going; report at end
            failed.append((owner, repo, number, str(e)[:150]))

    # commits: one rollup per repo, cheap to regenerate
    by_repo = {}
    for c in commits.values():
        by_repo.setdefault(c["repo"], []).append(c)
    for repo, cs in by_repo.items():
        owner, name = repo.split("/", 1)
        d = repo_dir(owner, name)
        d.mkdir(parents=True, exist_ok=True)
        cs.sort(key=lambda c: c["date"], reverse=True)
        lines = [
            "---",
            f"tags: [{owner}, {name}, commits, oss, github]",
            f"github: {repo}",
            f"harvested: {NOW.isoformat(timespec='seconds')}",
            "---",
            "",
            f"# {repo} — my commits since {since}",
            "",
            f"**Search Tags/Keywords:** #{owner} #{name} #commits #oss "
            + " ".join("#" + c["sha"][:8] for c in cs[:20]),
            "",
            f"**GitHub Context:** {repo} · {len(cs)} commits authored by @{USER}",
            "",
            "---",
            "",
        ]
        for c in cs:
            head = c["message"].split("\n")[0]
            lines.append(f"### `{c['sha'][:10]}` — {c['date'][:10]}")
            lines.append("")
            lines.append(f"[{head}]({c['url']})")
            lines.append("")
            rest = "\n".join(c["message"].split("\n")[1:]).strip()
            if rest:
                lines.append(fmt_body(rest, "> "))
                lines.append("")
            if c["seen_in"] != c["repo"]:
                lines.append(f"_(also present in fork `{c['seen_in']}`)_")
                lines.append("")
        (d / "commits.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    write_index(OUT)

    state["last_run"] = NOW.isoformat(timespec="seconds")
    state.setdefault("runs", []).append(
        {"at": state["last_run"], "since": since, "items": written,
         "commits": len(commits), "failed": len(failed)})
    state["runs"] = state["runs"][-60:]
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")

    log(f"wrote {written} item notes, {len(by_repo)} commit rollups")
    if failed:
        log(f"{len(failed)} FAILED:")
        for f in failed[:20]:
            log(f"    {f[0]}/{f[1]}#{f[2]}: {f[3]}")


def read_text_resilient(path, retries=4, fallback=""):
    """Read a file that lives in iCloud, where a plain read can simply fail.

    `OSError: [Errno 11] Resource deadlock avoided` is what iCloud returns while a
    file is being materialised or is held by another process -- DEVONthink
    indexing the same folder is the usual other process. It is transient, and it
    is not a corrupt file or a bug in the caller.

    It killed a whole run once. write_index() reads every note purely to pull a
    title out of its first heading, and one unreadable file raised out of the
    entire GitHub stage -- AFTER all the fetching was done, discarding the work
    and leaving no index at all. A title lookup must never be able to do that.

    So: retry briefly, then give up on THAT FILE and return the fallback. Losing
    one note's title is a cosmetic loss; losing the index is not.
    """
    delay = 0.25
    for attempt in range(retries):
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            # EDEADLK(11) and EBUSY(16) are the iCloud contention pair. Anything
            # else -- a real permission problem, a vanished file -- should not be
            # retried into a silent fallback.
            if e.errno not in (11, 16) or attempt == retries - 1:
                if e.errno in (11, 16):
                    print(f"    ! unreadable after {retries} tries ({path.name}): {e}", flush=True)
                    return fallback
                raise
            time.sleep(delay)
            delay *= 2
    return fallback


def write_index(root: Path):
    """Regenerated every run; safe to overwrite.

    The repo folders live under their topic now, so this is the one place that
    still shows GitHub activity as a single list — grouped by topic, because
    that is the axis the rest of the base is filed on. Links reach across from
    Projects/oss-github/ with `../`.
    """
    repos = sorted(d for d in PROJ.glob("*/oss-github/*") if d.is_dir())
    lines = [
        "---",
        "tags: [oss, github, index, log4j, apache, jreleaser]",
        f"harvested: {NOW.isoformat(timespec='seconds')}",
        "---",
        "",
        "# OSS GitHub activity — index",
        "",
        "**Search Tags/Keywords:** #oss #github #index #apache #log4j #jreleaser "
        "#pullrequest #issue #codereview",
        "",
        f"Auto-generated by `knowledge-creator/oss-harvest.py`. Regenerated on every "
        f"run — do not hand-edit; put your own notes in `Projects/<topic>/`.",
        "",
        "---",
        "",
    ]
    total = 0
    by_topic = {}
    for d in repos:
        by_topic.setdefault(d.parent.parent.name, []).append(d)

    for topic in sorted(by_topic):
        lines.append(f"# {topic}")
        lines.append("")
        for d in by_topic[topic]:
            rel = f"../{topic}/oss-github/{d.name}"
            notes = sorted(p for p in d.glob("*.md") if p.name != "commits.md")
            total += len(notes)
            lines.append(f"## {d.name.replace('__', '/')}  ({len(notes)})")
            lines.append("")
            if (d / "commits.md").exists():
                lines.append(f"- [commits]({rel}/commits.md)")
            for p in notes:
                title = ""
                for ln in read_text_resilient(p).splitlines():
                    if ln.startswith("# "):
                        title = ln[2:]
                        break
                lines.append(f"- [{title or p.stem}]({rel}/{p.name})")
            lines.append("")
    lines.insert(8, f"**{total} threads across {len(repos)} repositories, "
                    f"{len(by_topic)} topics.**\n")
    root.mkdir(parents=True, exist_ok=True)
    (root / "00-INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
