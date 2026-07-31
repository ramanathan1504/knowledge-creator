#!/usr/bin/env python3
"""
coverage-gap.py — measure the knowledge base against the official documentation.

`knowledge-map.py` says which of MY notes touch a topic. `topic-digest.py` says
what they concluded. Neither can tell you what is MISSING, because both only
ever look at what is already there — a base with nothing on Log4j lookups will
happily report 100% of its Log4j notes as Log4j notes.

So this pass brings in an outside yardstick: the table of contents of the
official manual. Every section a technology documents is a thing worth knowing;
scoring each one against the notes turns "I have 278 Log4j notes" into "I have
covered 31 of 48 documented areas, and here are the 17 I have not".

Yardsticks, captured 2026-07-30:
  log4j       https://logging.apache.org/log4j/2.x/manual/
  spring-boot https://docs.spring.io/spring-boot/
  java        https://dev.java/learn/

Coverage is measured across the WHOLE base, not the topic folder. Spring Boot's
logging chapter is genuinely covered by notes filed under log4j — filing needs
one home per note, measurement does not.

  ./coverage-gap.py                 # dry run: the scorecard
  ./coverage-gap.py --apply         # write Reference/gaps/<tech>.md
  ./coverage-gap.py --apply log4j
"""

import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
from kbpaths import ARCHIVE as DEVON
OUT = DEVON / "Reference/gaps"
NOW = datetime.now(timezone.utc)

APPLY = "--apply" in sys.argv
ONLY = [a for a in sys.argv[1:] if not a.startswith("-")]

SCAN = ["Projects", "Reference", "Tooling", "Compliance"]

# Derived files are assembled FROM the notes. Counting them as coverage would
# let the base certify itself: the log4j digest mentions every log4j subtopic,
# so every gap would close the moment topic-digest.py ran.
SELF_OUTPUT = re.compile(
    r"^Reference/snippets/|^Reference/topics/|^Reference/gaps/|"
    r"^Reference/00-knowledge-map\.md$|^Reference/mindmap\.md$")

# A note "covers" a section only at this many mentions. One passing use of the
# word "lookup" is not knowledge of Lookups; the same floor knowledge-map.py
# needed when a single stray "thread" put 264 of 329 notes under concurrency.
MIN_HITS = 3
COVERED = 3          # documents at or above MIN_HITS
THIN = 1

# Two kinds of knowing, and conflating them flatters the result.
#
# The base holds a 767-turn conversation working through JVM internals and a
# migrated Obsidian study curriculum. Those are real notes and they count — but
# they are things READ. A note under `<topic>/oss-github/` is a public thread
# that was reviewed and merged: the same subject, USED. For someone whose
# question is "what do I actually know", the second is the stronger claim and
# deserves to be scored separately rather than averaged in.
APPLIED = re.compile(r"^Projects/[^/]+/oss-github/")

# ...but not every merged thread is evidence of anything. A Dependabot bump
# names a dozen libraries it knows nothing about: `JSON` scored ● applied on
# the strength of "Bump the maven-patch-updates group", which is a robot
# changing a version number. Same rule blog-gen.py already uses to score these
# at zero.
BORING = re.compile(
    r"\bbump\b|\bdependabot\b|update .* to v?\d|^chore|typo|^\[?docs?\]?:|"
    r"upgrade .* from .* to", re.I)

# ---------------------------------------------------------------------------
# The yardsticks. Chapter -> section -> regex.
#
# Patterns are deliberately narrow. `Filters` matched as a bare word scored
# every note that mentioned a Java stream filter or a GitHub search filter;
# what makes it a Log4j filter is the class names.
# ---------------------------------------------------------------------------

LOG4J = {
    "API": {
        "Loggers":              r"logmanager\.getlogger|\blogger\s+log\b|getlogger\(",
        "Event Logger":         r"eventlogger|structuredmessage",
        "Simple Logger":        r"simplelogger",
        "Status Logger":        r"statuslogger|status logger|log4j2\.status",
        "Fluent API":           r"logbuilder|\batinfo\(|\batlevel\(|fluent api",
        "Fish tagging":         r"fish tag|\bfishtag",
        "Levels":               r"custom level|standardlevel|\blevel\.(trace|debug|info|warn|error|fatal)\b",
        "Markers":              r"\bmarkermanager|marker\.|\bmarkers?\b.{0,30}log4j",
        "Thread Context":       r"threadcontext|\bmdc\b|contextdata|contextmap|readonlystringmap",
        "Messages":             r"parameterizedmessage|objectmessage|mapmessage|messagefactory|reusablemessage",
        "Flow Tracing":         r"\bentry\(\)|\btraceentry|flow tracing|logger\.exit",
    },
    "Configuration": {
        "Architecture":         r"loggercontext|configurationfactory|log4j architecture|loggerconfig",
        "Configuration file":   r"log4j2\.xml|log4j2\.properties|log4j2\.json|xmlconfiguration|xinclude|composite configuration",
        "Configuration properties": r"log4j2\.[a-z]+property|log4j\.configurationfile|system propert.{0,20}log4j",
        "Programmatic configuration": r"configurationbuilder|configurator\.initialize|programmatic.{0,20}config",
    },
    "Appenders": {
        "File appenders":       r"fileappender|\bfileappender\b",
        "Rolling file appenders": r"rollingfile|triggeringpolicy|rolloverstrategy|sizebasedtriggering|compressionlevel",
        "Database appenders":   r"jdbcappender|jpaappender|nosqlappender|columnmapping",
        "Network Appenders":    r"socketappender|syslogappender|httpappender|smtpappender",
        "Message queue appenders": r"jmsappender|kafkaappender",
        "Delegating Appenders": r"asyncappender|failoverappender|routingappender|rewriteappender",
        "Console appender":     r"consoleappender",
    },
    "Layouts & filters": {
        "Pattern Layout":       r"patternlayout|patternconverter|conversion pattern|%d\{|%msg|%throwable",
        "JSON Template Layout": r"jsontemplatelayout|eventtemplate|jsontemplate",
        "Other layouts":        r"csvlayout|htmllayout|yamllayout|gelflayout|serializedlayout",
        "Lookups":              r"\blookup\b.{0,40}log4j|strlookup|contextmaplookup|\$\$?\{(env|sys|ctx|date|main):",
        "Filters":              r"regexfilter|thresholdfilter|levelmatchfilter|stringmatchfilter|burstfilter|markerfilter|abstractfilter",
        "Scripts":              r"scriptfilter|scriptappenderselector|\bscriptref\b",
    },
    "Extending & ops": {
        "Plugins":              r"@plugin\b|pluginprocessor|pluginbuilderattribute|plugin descriptor|log4j2plugins\.dat|@pluginfactory",
        "JMX":                  r"log4j.{0,20}jmx|jmx gui|mbean.{0,20}log4j",
        "Asynchronous loggers": r"asynclogger|disruptor|ringbuffer|asyncloggercontextselector",
        "Garbage-free logging": r"garbage-free|garbage free|reusablemessage|gc-free|nofreememory",
        "Performance & benchmarks": r"\bjmh\b|benchmark|throughput.{0,30}log|ops/s",
    },
    "Integration": {
        "Log4j Spring Boot Support": r"log4j-spring-boot|spring.{0,20}log4j|log4j2-spring\.xml",
        "Log4j Spring Cloud Config": r"spring cloud config.{0,30}log4j|log4j-spring-cloud",
        "JUL / Logback / SLF4J bridges": r"jul-to-log4j|log4j-to-jul|log4j-slf4j|log4j-jul|jcl-over|logback.{0,20}bridge",
        "Migrating from Log4j 1":  r"log4j 1\.x|log4j1|migrat.{0,20}log4j 1",
        "Migrating from Logback":  r"migrat.{0,20}logback|logback.{0,20}migrat",
        "GraalVM native images":   r"graalvm|native-image|graalvmprocessor|reflect-config",
        "Hibernate / Jakarta EE":  r"log4j.{0,30}(hibernate|jakarta)|log4j-jakarta|log4j-appserver",
        "Log4j IOStreams":         r"iostreams|loggeroutputstream|loggerprintstream",
        "Log4j Kotlin / Scala":    r"log4j-api-kotlin|log4j.{0,20}kotlin",
        "Log4j Tools":             r"log4j-tools|log4j-changelog|log4j-transform",
    },
    "Project": {
        "Security & CVEs":      r"cve-20\d\d|log4shell|jndilookup|vulnerab.{0,30}log4j",
        "Release & versioning": r"release candidate|\brc\d\b|changelog|milestone|version.{0,20}polic|cherry-pick",
        "Build & baseline":     r"\bbnd\b|baseline|api compatibility|spotless|revapi|japicmp",
        "Testing":              r"listappender|@loggercontextsource|log4j-core-test|logeventfactory",
    },
}

SPRING_BOOT = {
    "Developing with Spring Boot": {
        "Build Systems":            r"spring-boot-starter|spring-boot-maven-plugin|spring-boot-gradle|dependencymanagement.{0,30}spring",
        "Structuring Your Code":    r"@springbootapplication.{0,60}package|component scan|structuring.{0,20}code",
        "Configuration Classes":    r"@configuration\b|@import\b|@enableautoconfiguration",
        "Auto-configuration":       r"autoconfigur|@conditionalonclass|@conditionalonmissingbean|spring\.factories|autoconfiguration\.imports",
        "Beans and Dependency Injection": r"@autowired|@bean\b|@component|@service\b|constructor injection|applicationcontext",
        "@SpringBootApplication":   r"@springbootapplication",
        "Running Your Application": r"bootrun|spring-boot:run|java -jar.{0,30}\.jar",
        "Developer Tools":          r"spring-boot-devtools|devtools|livereload",
        "Packaging for Production": r"executable jar|fat jar|repackage|layered jar",
    },
    "Core Features": {
        "SpringApplication":        r"springapplication\.run|springapplicationbuilder|applicationrunner|commandlinerunner",
        "Externalized Configuration": r"@configurationproperties|application\.(yml|yaml|properties)|@value\(|propertysource|relaxed binding",
        "Profiles":                 r"@profile\b|spring\.profiles|spring_profiles_active",
        "Logging":                  r"logging\.level|logback-spring|spring.{0,20}logging|logging\.pattern|logging\.file",
        "Internationalization":     r"messagesource|\bi18n\b|locale resolver",
        "Aspect-Oriented Programming": r"@aspect\b|pointcut|@around\b|@before\b|aopalliance|proxy.{0,20}(cglib|jdk)",
        "JSON":                     r"jackson|objectmapper|@jsonproperty|gson\b",
        "Task Execution and Scheduling": r"@scheduled\b|@async\b|taskexecutor|threadpooltaskexecutor",
        "Creating Your Own Auto-configuration": r"custom.{0,20}autoconfigur|@autoconfiguration\b|autoconfigure\.imports",
        "SSL":                      r"server\.ssl|ssl bundle|keystore.{0,20}spring",
    },
    "Web": {
        "Servlet Web Applications": r"@restcontroller|@requestmapping|@getmapping|dispatcherservlet|embedded tomcat",
        "Reactive Web Applications": r"webflux|\bmono<|\bflux<|reactive stream|routerfunction",
        "Graceful Shutdown":        r"graceful shutdown|server\.shutdown",
        "Spring Security":          r"spring security|securityfilterchain|@preauthorize|websecurityconfigur|authenticationmanager",
        "Spring Session":           r"spring session|@enableredishttpsession",
        "Spring for GraphQL":       r"graphql",
        "Spring HATEOAS":           r"hateoas|entitymodel|linkbuilder",
    },
    "Data": {
        "SQL Databases":            r"\bjpa\b|hibernate|@entity\b|jparepository|datasource\b|hikari|flyway|liquibase|@transactional",
        "NoSQL Technologies":       r"mongotemplate|spring data (mongo|redis|elasticsearch|cassandra)|redistemplate",
    },
    "IO & Messaging": {
        "Caching":                  r"@cacheable|@enablecaching|cachemanager|caffeine",
        "Validation":               r"@valid\b|@notnull\b|jakarta\.validation|hibernate validator",
        "Calling REST Services":    r"resttemplate|webclient\b|restclient\b|@httpexchange",
        "Apache Kafka Support":     r"@kafkalistener|kafkatemplate|spring-kafka",
        "JMS / AMQP / Pulsar":      r"@jmslistener|jmstemplate|rabbittemplate|@rabbitlistener|pulsar",
        "Spring Batch / Quartz":    r"spring batch|jobrepository|steplistener|quartz",
        "Sending Email":            r"javamailsender|spring.{0,20}mail",
        "WebSockets":               r"websocket|stomp\b|@messagemapping",
    },
    "Testing": {
        "Testing Spring Applications": r"@springboottest|@webmvctest|@datajpatest|mockmvc|@mockbean|@testconfiguration",
        "Testcontainers":           r"testcontainers|@servicecconnection|@servicelconnection|genericcontainer",
        "Test Slices":              r"test slice|@webmvctest|@jsontest|@restclienttest",
    },
    "Packaging & Native": {
        "Container Images":         r"buildpack|dockerfile|bootbuildimage|jib\b|container image",
        "GraalVM Native Images":    r"graalvm|native-image|nativehint|@registerreflectionforbinding",
        "Ahead-of-Time Processing": r"\baot\b.{0,30}(spring|process)|spring-aot",
    },
    "Production-ready": {
        "Actuator Endpoints":       r"actuator|/health\b|@endpoint\b|healthindicator|readinessstate",
        "Metrics":                  r"micrometer|meterregistry|@timed\b|prometheus.{0,20}(scrape|endpoint)",
        "Tracing / Observability":  r"opentelemetry|micrometer tracing|\bspan\b|traceid|zipkin|observability",
        "Auditing":                 r"auditevent|@enablejpaauditing|@createddate",
        "Monitoring over JMX/HTTP": r"jmx.{0,20}(endpoint|actuator)|management\.endpoints",
    },
}

JAVA = {
    "Getting to Know the Language": {
        "Classes and Objects":      r"\bconstructor\b|instance (variable|method)|\bthis\.\w|static (method|field)",
        "Records":                  r"\brecord\s+[A-Z]\w*\s*\(|record class|compact constructor",
        "Numbers and Strings":      r"stringbuilder|string\.format|autoboxing|bigdecimal|string pool|\.intern\(",
        "Inheritance":              r"\bextends\b|@override\b|super\.|abstract class|polymorphism",
        "Interfaces":               r"\bimplements\b|default method|functional interface|\bsealed\b|interface \w+ \{",
        "Generics":                 r"\bgenerics?\b|type erasure|wildcard|\? extends |\? super |bounded type",
        "Lambda Expressions":       r"lambda|->\s*\{|method reference|::\w+|@functionalinterface",
        "Annotations":              r"@interface\b|annotation processor|retentionpolicy|@target\b|elementtype",
        "Packages & Modules":       r"module-info|\brequires\s+\w+;|\bexports\s+|jigsaw|jpms",
        "Pattern Matching":         r"pattern matching|instanceof \w+ \w+|switch.{0,20}case \w+ \w+ ->|sealed.{0,20}permits",
        "Exceptions":               r"try.{0,10}catch|finally\b|throws \w+exception|try-with-resources|suppressed exception",
        "Functional Style":         r"functional style|imperative.{0,20}functional|declarative.{0,20}refactor",
    },
    "Mastering the API": {
        "The Collections Framework": r"hashmap|arraylist|linkedlist|treemap|hashset|comparator\b|iterator\b|concurrentmodification",
        "The Stream API":           r"\.stream\(\)|collectors\.|\bflatmap\b|\.filter\(.{0,20}->|groupingby|reduce\(",
        "The Java I/O API":         r"inputstream|outputstream|bufferedreader|files\.(read|write|walk)|\bnio\b|bytebuffer",
        # `instant\b` matched the English word and scored 43 notes on phrases
        # like "instant feedback". Anchor to the actual API.
        "The Date Time API":        r"localdate|localdatetime|zoneddatetime|datetimeformatter|java\.time|instant\.(now|from|of)|duration\.of",
        "Regular Expressions":      r"pattern\.compile|matcher\(|regex|\\\\d\+|named group",
        "Reflection":               r"getdeclaredfield|getdeclaredmethod|class\.forname|setaccessible|\breflection\b",
        "Method Handles":           r"methodhandle|methodhandles\.lookup|invokedynamic|varhandle",
        "Virtual Threads":          r"virtual thread|thread\.ofvirtual|loom\b|structured concurrency",
        "Foreign Function & Memory": r"\bffm\b|memorysegment|foreign function|panama|\bjni\b|arena\b",
        "Security libraries":       r"messagedigest|keystore\b|securerandom|cipher\.|javax\.crypto",
    },
    "Concurrency": {
        "Threads & synchronization": r"\bsynchronized\b|\bvolatile\b|thread\.(start|join|sleep)|runnable\b|happens-before",
        "Executors & pools":        r"executorservice|threadpoolexecutor|executors\.new|forkjoinpool|scheduledexecutor",
        "Futures":                  r"completablefuture|\bfuture<|supplyasync|thenapply|thencombine",
        "Concurrent collections":   r"concurrenthashmap|copyonwritearraylist|blockingqueue|concurrentlinkedqueue",
        "Locks & atomics":          r"reentrantlock|atomicinteger|atomiclong|atomicreference|countdownlatch|semaphore|striped",
    },
    "Getting to Know the JVM": {
        "Garbage collection":       r"garbage collect|\bgc\b (pause|log|tuning)|\bg1gc\b|\bzgc\b|heap dump|-xmx",
        "JDK tools":                r"\bjcmd\b|\bjstack\b|\bjmap\b|\bjstat\b|\bjavap\b|\bjlink\b|\bjpackage\b",
        "JDK Flight Recorder":      r"flight recorder|\bjfr\b|jdk\.jfr",
        "Class loading":            r"classloader|class\.forname|serviceloader|\bspi\b|meta-inf/services",
        "JIT & performance":        r"\bjit\b|hotspot|escape analysis|inlining|\bjmh\b|benchmark",
    },
    "Tooling & practice": {
        "Build (Maven/Gradle)":     r"\bpom\.xml\b|<artifactid>|build\.gradle|gradle\b.{0,20}task|maven-compiler-plugin",
        "Testing":                  r"@test\b|junit|assertj|mockito|assertthat|@parameterizedtest|@beforeeach",
        "New language features":    r"\bjdk\s*(1[7-9]|2[0-9])\b|java\s*(1[7-9]|2[0-9])\b|preview feature|text block",
    },
}

YARDSTICKS = {
    "log4j": (LOG4J, "Apache Log4j 2.x manual",
              "https://logging.apache.org/log4j/2.x/manual/"),
    "spring-boot": (SPRING_BOOT, "Spring Boot reference documentation",
                    "https://docs.spring.io/spring-boot/"),
    "java": (JAVA, "The Java tutorials (dev.java)", "https://dev.java/learn/"),
}


def docs():
    for top in SCAN:
        d = DEVON / top
        if not d.is_dir():
            continue
        for p in d.rglob("*.md"):
            rel = str(p.relative_to(DEVON))
            if SELF_OUTPUT.search(rel):
                continue
            yield p, rel


def load():
    out = []
    for p, rel in docs():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        title = next((ln[2:].strip() for ln in text.splitlines()
                      if ln.startswith("# ")), p.stem)
        shipped = bool(APPLIED.search(rel)) and not BORING.search(title)
        out.append((rel, text.lower(), shipped))
    return out


def score(corpus, tech):
    chapters, _, _ = YARDSTICKS[tech]
    result = {}
    for chapter, sections in chapters.items():
        rows = []
        for name, pat in sections.items():
            rx = re.compile(pat)
            hits = []
            for rel, text, shipped in corpus:
                n = len(rx.findall(text))
                if n >= MIN_HITS:
                    hits.append((n, rel, shipped))
            hits.sort(reverse=True)
            total = sum(h[0] for h in hits)
            applied = [h for h in hits if h[2]]
            if len(hits) >= COVERED:
                status = "applied" if applied else "studied"
            elif len(hits) >= THIN:
                status = "thin"
            else:
                status = "gap"
            # Lead with the shipped thread when there is one — it is the
            # stronger evidence and the more useful link.
            best = (applied + hits)[:3]
            rows.append((name, len(hits), len(applied), total, status, best))
        result[chapter] = rows
    return result


MARK = {"applied": "●", "studied": "◑", "thin": "◐", "gap": "○"}


def render(tech, scored, corpus_n):
    _, doc_name, doc_url = YARDSTICKS[tech]
    flat = [r for rows in scored.values() for r in rows]
    n = len(flat)
    app = sum(1 for r in flat if r[4] == "applied")
    stud = sum(1 for r in flat if r[4] == "studied")
    thin = sum(1 for r in flat if r[4] == "thin")
    gap = n - app - stud - thin
    pct = round(100 * (app + stud) / n) if n else 0
    pct_app = round(100 * app / n) if n else 0

    L = ["---",
         f"tags: [gap-analysis, coverage, {tech}, curriculum, roadmap]",
         f"topic: {tech}",
         f"generated: {NOW.isoformat(timespec='seconds')}",
         "---", "",
         f"# {tech} — covered vs. the official manual", "",
         f"**Search Tags/Keywords:** #gap-analysis #coverage #{tech} #roadmap "
         "#curriculum #whattolearnnext", "",
         f"**GitHub Context:** measured across {corpus_n} notes in the base "
         f"against the section list of the {doc_name}.", "",
         f"Yardstick: [{doc_name}]({doc_url}). Generated by "
         "`~/claude-cli/coverage-gap.py` — do not hand-edit.", "",
         f"## {pct}% touched · {pct_app}% applied", "",
         f"Of {n} documented areas: **● {app} applied** — worked in a public "
         f"thread · **◑ {stud} studied** — notes but no shipped work · "
         f"**◐ {thin} thin** — one or two notes · **○ {gap} nothing**.", "",
         f"A section counts once {COVERED} or more notes mention it at least "
         f"{MIN_HITS} times; **applied** additionally requires at least one of "
         "those notes to be a GitHub thread under `<topic>/oss-github/`. The "
         "two are worth separating: the base holds a 767-turn walk through JVM "
         "internals and a migrated study curriculum, which is real knowledge "
         "but not the same claim as having shipped it.", "",
         "Measured across the whole base, not one folder — Spring Boot's "
         "logging chapter is covered by notes filed under `log4j`, and that "
         "counts.", "", "---", ""]

    for chapter, rows in scored.items():
        a = sum(1 for r in rows if r[4] == "applied")
        s = sum(1 for r in rows if r[4] == "studied")
        L += [f"## {chapter}  ({a} applied, {s} studied of {len(rows)})", "",
              "| | area | notes | shipped | mentions | strongest note |",
              "|---|---|---:|---:|---:|---|"]
        for name, ndocs, napp, total, status, top in sorted(
                rows, key=lambda r: (-r[2], -r[1], r[0])):
            best = ""
            if top:
                rel = top[0][1]
                best = f"[{Path(rel).stem[:40]}](../../{rel.replace(' ', '%20')})"
            L.append(f"| {MARK[status]} | {name} | {ndocs} | {napp or ''} | "
                     f"{total} | {best} |")
        L.append("")

    holes = [(chapter, name, ndocs, status) for chapter, rows in scored.items()
             for name, ndocs, _, _, status, _ in rows
             if status in ("gap", "thin", "studied")]
    if holes:
        L += ["---", "", "## What to cover next", "",
              "Emptiest first. A ○ is untouched; a ◐ has one or two notes to "
              "build on; a ◑ you have read about but never shipped, which is "
              "the cheapest gap to close — pick up an issue in it.", ""]
        rank = {"gap": 0, "thin": 1, "studied": 2}
        for chapter, name, ndocs, status in sorted(
                holes, key=lambda h: (rank[h[3]], h[2], h[0])):
            L.append(f"- {MARK[status]} **{name}** — _{chapter}_"
                     + (f" · {ndocs} note{'s' if ndocs > 1 else ''}, none shipped"
                        if ndocs else ""))
        L.append("")

    return "\n".join(L) + "\n"


def main():
    corpus = load()
    if not corpus:
        sys.exit(f"no notes found under {DEVON}")
    techs = [t for t in YARDSTICKS if not ONLY or t in ONLY]
    if ONLY:
        for t in set(ONLY) - set(YARDSTICKS):
            print(f"  !! no yardstick for '{t}' "
                  f"(have: {', '.join(YARDSTICKS)})\n")

    print(f"{'APPLY' if APPLY else 'DRY RUN'}   measured over {len(corpus)} notes\n")
    built = {}
    for t in techs:
        scored = score(corpus, t)
        built[t] = scored
        flat = [r for rows in scored.values() for r in rows]
        n = len(flat)
        app = sum(1 for r in flat if r[4] == "applied")
        stud = sum(1 for r in flat if r[4] == "studied")
        thin = sum(1 for r in flat if r[4] == "thin")
        print(f"  {t:12s} {app}/{n} applied ({round(100*app/n)}%)  ·  "
              f"{stud} studied only  ·  {thin} thin  ·  "
              f"{n-app-stud-thin} untouched")
        for chapter, rows in scored.items():
            a = sum(1 for r in rows if r[4] == "applied")
            miss = [x[0] for x in rows if x[4] == "gap"]
            print(f"       {chapter:32s} {a}/{len(rows)} applied"
                  + (f"   nothing: {', '.join(miss[:4])}" if miss else ""))
        print()

    if not APPLY:
        print("Nothing written. Re-run with --apply.")
        return

    OUT.mkdir(parents=True, exist_ok=True)
    for t, scored in built.items():
        (OUT / f"{t}.md").write_text(render(t, scored, len(corpus)),
                                     encoding="utf-8")
    print(f"wrote {len(built)} gap reports to {OUT}")


if __name__ == "__main__":
    main()
