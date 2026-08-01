#!/usr/bin/env python3
"""
knowledge-map.py — mine the whole knowledge base and build the derived layer:

  Reference/00-knowledge-map.md          what I have actually covered, by topic
  Reference/snippets/<topic>.md          runnable snippets pulled from every
                                         fenced code block, deduplicated
  Reference/mindmap.md                   mermaid mind map of topic -> subtopic

Sources: every note under Projects/, Personal/, Reference/, Tooling/ and
Compliance/, whatever folder it sits in.

Why this exists
---------------
The extractors give one note per conversation or per pull request. That answers
"what happened in thread X". It does not answer "show me the JPA locking snippet
I worked out last year". This pass builds the cross-cutting view.

Topics are MULTI-LABEL here, unlike the folder placement: a Log4j conversation
that also teaches Java generics counts toward both. Filing needs one home per
document; a coverage map does not.

Note that this is an INDEX, not a digest. It says which notes touch a topic and
how heavily. For what those notes actually concluded, see `topic-digest.py`,
which reads the notes rather than counting matches in them.

  ./knowledge-map.py            # dry run: counts
  ./knowledge-map.py --apply
"""

import hashlib
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
from kbpaths import ARCHIVE as DEVON
REF = DEVON / "Reference"
SNIP = REF / "snippets"
NOW = datetime.now(timezone.utc)
APPLY = "--apply" in sys.argv

SCAN = ["Projects", "Personal", "Reference", "Tooling", "Compliance"]

# Multi-label. A document can and should match several of these.
TOPICS = {
    "log4j": r"log4j|logback|slf4j|jsontemplatelayout|patternlayout|\bmdc\b|appender|layout\b",
    "java": r"\bjava\b|\bjvm\b|collection|stream api|generics|functional interface|lambda|record\b",
    "java-concurrency": r"concurren|thread|executor|completablefuture|synchroniz|volatile|disruptor|deadlock",
    "spring": r"\bspring\b|spring boot|@autowired|@bean|@component|\baop\b|servlet|@transactional",
    "spring-data": r"\bjpa\b|hibernate|repository|@entity|@query|lock\s*modetype|pessimistic",
    "kafka": r"kafka|confluent|debezium|producer|consumer group|partition",
    "databases": r"postgres|mysql|\brds\b|redis|\bsql\b|jdbc|index scan|replica",
    "aws-infra": r"\baws\b|\bec2\b|\beks\b|kubernetes|kops|\bk8s\b|terraform|autoscal",
    "build-tooling": r"\bmaven\b|gradle|\bbnd\b|baseline|annotation processor|spotless",
    "testing": r"junit|assertj|mockito|@test|surefire|integration test",
    "apache-process": r"\basf\b|apache way|committer|\bpmc\b|release vote|dependabot",
    "security": r"openssl|\bcve\b|vulnerab|hardening|\bfips\b|captcha",
    "observability": r"opentelemetry|tracing|metrics|prometheus|span\b",
    "system-design": r"system design|scalab|tradeoff|rate limit|architecture|throughput",
    "compliance": r"soc\s*2|gdpr|audit|policy",
    "ai-ml": r"\bllm\b|ollama|qwen|fine-tun|embedding|prompt",
}

# Subtopics for the mind map: topic -> {label: pattern}
SUB = {
    "log4j": {
        "plugins & annotation processor": r"pluginprocessor|@plugin|pluginbuilderattribute|plugin descriptor",
        "layouts": r"patternlayout|jsontemplatelayout|layout\b",
        "filters": r"filter\b|stringmatchfilter|regexfilter|levelmatchfilter",
        "appenders": r"appender|rollingfile|async",
        "configuration": r"xmlconfiguration|configurationscheduler|log4j2\.xml|xinclude",
        "MDC / context": r"\bmdc\b|threadcontext|contextdata",
        "release & porting": r"port\b|2\.x|cherry-pick|changelog|milestone",
    },
    "java": {
        "collections": r"collection|hashmap|arraylist|treemap|\bset\b",
        "streams & functional": r"stream|lambda|functional interface|optional",
        "generics": r"generic|type erasure|wildcard",
        "records & modern": r"record\b|sealed|pattern matching|switch expression",
        "JNI / FFM": r"\bjni\b|\bffm\b|foreign function|panama",
    },
    "spring": {
        "core & DI": r"@autowired|@bean|@component|dependency injection|applicationcontext",
        "AOP": r"\baop\b|@aspect|pointcut|proxy",
        "web & servlet": r"servlet|@restcontroller|@requestmapping|dispatcher",
        "data & JPA": r"\bjpa\b|hibernate|@entity|repository|@transactional",
        "boot": r"spring boot|autoconfigur|application\.yml|starter",
    },
}

CODE = re.compile(r"```(\w+)?\n(.*?)```", re.S)
NOISE = re.compile(r"^\s*(\$|#|>)\s")

# A document only "covers" a topic if it mentions it substantially. Without
# this floor, one passing use of the word "thread" put 264 of 329 documents
# under java-concurrency, which is not a map, it is a fog.
MIN_HITS = 4

# Snippets are classified on the CODE ITSELF, not on the topics of the note
# they happen to sit in. Classifying by document meant one snippet landed in
# every topic its note touched and 2,779 real snippets inflated to 19,485.
SNIPPET_SIG = {
    "spring-data":      r"JpaRepository|@Entity\b|@Query\b|EntityManager|LockModeType|@Transactional",
    "spring":           r"@Autowired|@Bean\b|@RestController|@Service\b|@Component|springframework|@SpringBootApplication",
    "log4j":            r"org\.apache\.logging\.log4j|LogManager|Appender|PatternLayout|log4j2?\.(xml|properties)|@PluginBuilderAttribute",
    "kafka":            r"KafkaProducer|KafkaConsumer|@KafkaListener|bootstrap\.servers|ConsumerRecord",
    "java-concurrency": r"ExecutorService|CompletableFuture|\bsynchronized\b|Atomic\w+|CountDownLatch|ReentrantLock|Executors\.",
    "testing":          r"@Test\b|assertThat|assertEquals|Mockito|@BeforeEach|Assertions\.",
    "build-tooling":    r"<dependency>|<plugin>|<artifactId>|implementation\s+['\"]|pluginManagement|spotless",
    "databases":        r"\bSELECT\s|\bINSERT\s+INTO\b|CREATE\s+TABLE|\bjdbc:|EXPLAIN\s+ANALYZE",
    "aws-infra":        r"\bkubectl\b|apiVersion:|eksctl|\baws\s+\w+\s|terraform\b|kops\b",
    "java":             r"\bpublic\s+(class|interface|record|enum)\b|import\s+java\.|@Override|void\s+\w+\(",
}
MAX_PER_TOPIC = 250          # keep the snippet files openable


# This generator writes into Reference/, then scans Reference/ on the next run
# — so it was reading its OWN output as source material. A leaked credential in
# a snippet library got copied forward into the regenerated library every time,
# meaning redacting the true source did nothing. Skip anything we produce.
SELF_OUTPUT = re.compile(
    r"^Reference/snippets/|^Reference/00-knowledge-map\.md$|^Reference/mindmap\.md$"
    r"|^Reference/topics/")          # topic-digest.py output — derived, not source


def docs():
    for top in SCAN:
        d = DEVON / top
        if d.is_dir():
            for p in d.rglob("*.md"):
                if SELF_OUTPUT.search(str(p.relative_to(DEVON))):
                    continue
                yield p


def labels(text):
    low = text.lower()
    return {t for t, pat in TOPICS.items() if re.search(pat, low)}


def title_of(p, text):
    for ln in text.splitlines():
        if ln.startswith("# "):
            return ln[2:].strip()
    return p.stem


def main():
    coverage = defaultdict(list)      # topic -> [(title, relpath, hits)]
    sub_hits = defaultdict(Counter)   # topic -> subtopic -> count
    snippets = defaultdict(list)      # topic -> [(lang, code, title, relpath)]
    seen_code = set()
    total = 0

    for p in docs():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        total += 1
        rel = p.relative_to(DEVON)
        title = title_of(p, text)
        low = text.lower()
        tops = set()
        for t, pat in TOPICS.items():
            n = len(re.findall(pat, low))
            if n >= MIN_HITS:
                tops.add(t)
                coverage[t].append((title, str(rel), n))
        for t, subs in SUB.items():
            if t in tops:
                for label, pat in subs.items():
                    n = len(re.findall(pat, low))
                    if n:
                        sub_hits[t][label] += n

        for lang, code in CODE.findall(text):
            code = code.strip("\n")
            if len(code) < 60 or code.count("\n") < 2:
                continue                              # one-liners are not snippets
            if sum(1 for ln in code.splitlines() if NOISE.match(ln)) > len(code.splitlines()) * 0.6:
                continue                              # shell transcript, not code
            h = hashlib.sha1(re.sub(r"\s+", " ", code).encode()).hexdigest()
            if h in seen_code:
                continue
            seen_code.add(h)
            # score against the snippet body; keep the best two topics only
            scored = sorted(
                ((len(re.findall(sig, code)), t) for t, sig in SNIPPET_SIG.items()),
                reverse=True)
            for score, t in scored[:2]:
                if score:
                    snippets[t].append((score, lang or "text", code, title, str(rel)))

    print(f"scanned {total} documents\n")
    print(f"coverage (multi-label, >= {MIN_HITS} mentions):")
    for t, items in sorted(coverage.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(items):4d}  {t}")
    print(f"\nunique code blocks: {len(seen_code)}"
          f"   placements: {sum(len(v) for v in snippets.values())}")
    for t, v in sorted(snippets.items(), key=lambda kv: -len(kv[1])):
        cap = f"  (capped to {MAX_PER_TOPIC})" if len(v) > MAX_PER_TOPIC else ""
        print(f"  {len(v):4d}  {t}{cap}")

    if not APPLY:
        print("\nDry run. Re-run with --apply.")
        return

    REF.mkdir(parents=True, exist_ok=True)
    SNIP.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------- coverage map --
    all_tags = sorted(coverage)
    L = ["---",
         f"tags: [knowledge-map, index, coverage, {', '.join(all_tags)}]",
         f"generated: {NOW.isoformat(timespec='seconds')}",
         "---", "",
         "# Knowledge map — what I have actually covered", "",
         "**Search Tags/Keywords:** #knowledge-map #index #coverage "
         + " ".join("#" + t for t in all_tags), "",
         "**GitHub Context:** derived from harvested apache/logging-log4j2, "
         "jreleaser/jreleaser and 15 other repositories, plus 111 AI Studio "
         "conversations.", "",
         f"Generated by `knowledge-creator/knowledge-map.py` from {total} documents. "
         "Regenerated on demand — do not hand-edit.", "", "---", ""]

    for t, items in sorted(coverage.items(), key=lambda kv: -len(kv[1])):
        items.sort(key=lambda x: -x[2])
        L += [f"## {t}  ({len(items)} documents)", ""]
        if sub_hits.get(t):
            L.append("**Subtopics by weight:** " + " · ".join(
                f"{k} ({v})" for k, v in sub_hits[t].most_common()))
            L.append("")
        for title, rel, hits in items[:40]:
            L.append(f"- [{title}]({rel.replace(' ', '%20')}) · {hits} hits")
        if len(items) > 40:
            L.append(f"- _… and {len(items)-40} more_")
        L.append("")
    (REF / "00-knowledge-map.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    # -------------------------------------------------------------- mind map --
    M = ["---", "tags: [mindmap, knowledge-map, mermaid, java, spring, log4j]",
         f"generated: {NOW.isoformat(timespec='seconds')}", "---", "",
         "# Mind map", "",
         "**Search Tags/Keywords:** #mindmap #java #spring #log4j #kafka "
         "#databases #aws #testing #security", "", "```mermaid", "mindmap",
         "  root((knowledge base))"]
    for t, items in sorted(coverage.items(), key=lambda kv: -len(kv[1])):
        M.append(f"    {t} [{len(items)}]")
        for label, n in sub_hits.get(t, Counter()).most_common(6):
            M.append(f"      {re.sub(r'[^a-zA-Z0-9 /&-]', '', label)} {n}")
    M += ["```", ""]
    (REF / "mindmap.md").write_text("\n".join(M) + "\n", encoding="utf-8")

    # -------------------------------------------------------------- snippets --
    for t, items in snippets.items():
        # strongest signal first, then longest — the best examples float up
        items.sort(key=lambda x: (-x[0], -len(x[2])))
        kept, dropped = items[:MAX_PER_TOPIC], max(0, len(items) - MAX_PER_TOPIC)
        S = ["---",
             f"tags: [snippets, {t}, code, reference]",
             f"generated: {NOW.isoformat(timespec='seconds')}",
             "---", "",
             f"# {t} — snippet library  ({len(kept)} of {len(items)})", "",
             f"**Search Tags/Keywords:** #snippets #{t} #code #reference "
             + " ".join(sorted({"#" + (l or 'text') for _, l, _, _, _ in kept})), "",
             "**GitHub Context:** extracted from harvested PR discussions and "
             "AI Studio conversations; each snippet links back to its source note.",
             "",
             f"_Ranked by signal strength then length. "
             + (f"{dropped} weaker snippets omitted — raise MAX_PER_TOPIC in "
                "`knowledge-map.py` to include them._" if dropped
                else "Nothing omitted._"),
             "", "---", ""]
        for i, (score, lang, code, title, rel) in enumerate(kept, 1):
            S += [f"## {i}. {title}", "",
                  f"_from [{rel}]({rel.replace(' ', '%20')})_  ·  signal {score}", "",
                  f"```{lang}", code, "```", ""]
        (SNIP / f"{t}.md").write_text("\n".join(S) + "\n", encoding="utf-8")

    print(f"\nwrote:")
    print(f"  {REF}/00-knowledge-map.md")
    print(f"  {REF}/mindmap.md")
    print(f"  {SNIP}/*.md  ({len(snippets)} topic files)")


if __name__ == "__main__":
    main()
