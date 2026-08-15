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
#
# test_oss_harvest.py — what the harvester writes, and what reads it.
#
#   python3 -m unittest discover -p 'test_*.py'
#
# Standard library only, like everything else here. Nothing below touches the
# network: the module is imported with KB_GH_USER and KB_ARCHIVE preset so the
# `gh api user` call and archive lookup never run.
#
# Presetting KB_GH_USER is also what hid a real bug for months. The user was
# resolved at IMPORT time, so importing this module cost a `gh api user` call --
# and pr-review-file.py imports it purely to borrow slug() and CODEISH. With the
# wifi off, `oss memory file notes.md` -- a local write of a local file -- died
# with "cannot tell whose activity to harvest". Every test here set the variable
# first, so no test ever imported the module the way the real caller does.
# ImportCostsNothing below imports it the way the real caller does.
#
# The frontmatter tests are a CONTRACT, not an implementation detail. The
# workbench decides whether a note is your own work or material you merely
# collected by reading `my_role` and `source` out of these very lines. Rename a
# field here and retrieval silently reclassifies the whole corpus, with nothing
# to notice -- which is exactly the kind of cross-repository agreement that a
# test is for and a comment is not.

import importlib.util
import os
import unittest
from pathlib import Path

os.environ.setdefault("KB_GH_USER", "test-user")
os.environ.setdefault("KB_ARCHIVE", "/tmp/kb-archive-under-test")

_spec = importlib.util.spec_from_file_location(
    "oss_harvest", Path(__file__).with_name("oss-harvest.py")
)
harvest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harvest)


def node(author="someone-else", comments=(), reviews=(), threads=()):
    """One GraphQL issue node, in the shape fetch_item returns."""
    return {
        "__typename": "Issue",
        "number": 4129,
        "title": "Rollover leaves a zero-length file",
        "url": "https://example.invalid/i/4129",
        "state": "OPEN",
        "createdAt": "2026-08-01T00:00:00Z",
        "updatedAt": "2026-08-02T00:00:00Z",
        "author": {"login": author},
        "body": "Steps to reproduce.",
        "comments": {"nodes": [{"author": {"login": a}, "createdAt": "2026-08-01T01:00:00Z",
                                "body": "a comment"} for a in comments]},
        "reviews": {"nodes": [{"author": {"login": a}, "createdAt": "2026-08-01T02:00:00Z",
                               "state": "APPROVED", "body": "looks fine"} for a in reviews]},
        "reviewThreads": {"nodes": [
            {"path": "src/Main.java", "line": 1,
             "comments": {"nodes": [{"author": {"login": a}, "createdAt": "2026-08-01T03:00:00Z",
                                     "body": "inline"}]}} for a in threads]},
        "labels": {"nodes": []},
    }


def frontmatter(text):
    """The fields between the opening and closing --- markers."""
    lines = text.splitlines()
    assert lines[0] == "---", "a note must start with frontmatter"
    out = {}
    for line in lines[1:]:
        if line == "---":
            break
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


class RoleIsWhatYouDid(unittest.TestCase):
    """my_role describes participation, never how the thread was found."""

    def test_no_part_in_it_is_none(self):
        fm = frontmatter(harvest.render(node(), "owner", "name", "repo-scan"))
        self.assertEqual("none", fm["my_role"])

    def test_authoring_it_is_recorded(self):
        fm = frontmatter(harvest.render(node(author="test-user"), "owner", "name", "repo-scan"))
        self.assertIn("author", fm["my_role"])

    def test_commenting_is_recorded(self):
        fm = frontmatter(harvest.render(node(comments=["test-user"]), "owner", "name", "repo-scan"))
        self.assertIn("commenter", fm["my_role"])

    def test_reviewing_is_recorded(self):
        fm = frontmatter(harvest.render(node(reviews=["test-user"]), "owner", "name", "repo-scan"))
        self.assertIn("reviewer", fm["my_role"])

    def test_an_inline_reply_is_recorded(self):
        fm = frontmatter(harvest.render(node(threads=["test-user"]), "owner", "name", "repo-scan"))
        self.assertIn("inline-reviewer", fm["my_role"])

    def test_somebody_elses_comment_is_not_mine(self):
        fm = frontmatter(harvest.render(node(comments=["a-stranger"]), "owner", "name", "repo-scan"))
        self.assertEqual("none", fm["my_role"])


class SourceIsHowItWasFound(unittest.TestCase):
    """source and my_role answer different questions, and both are written."""

    def test_scanned(self):
        self.assertEqual("repo-scan", frontmatter(harvest.render(node(), "owner", "name", "repo-scan"))["source"])

    def test_involved(self):
        self.assertEqual("involved", frontmatter(harvest.render(node(), "owner", "name", "involved"))["source"])

    def test_defaults_to_involved(self):
        self.assertEqual("involved", frontmatter(harvest.render(node(), "owner", "name"))["source"])

    def test_only_none_and_repo_scan_together_mean_collected(self):
        # The exact pair the workbench demotes on. Both halves, or it stays yours.
        fm = frontmatter(harvest.render(node(), "owner", "name", "repo-scan"))
        self.assertEqual("none", fm["my_role"])
        self.assertEqual("repo-scan", fm["source"])

        mine = frontmatter(harvest.render(node(author="test-user"), "owner", "name", "repo-scan"))
        self.assertNotEqual("none", mine["my_role"], "authored threads must not be demoted")


class ExclusionCannotFightTheQualifier(unittest.TestCase):
    """A named repository is an instruction, not a preference.

    EXCLUDE is `-user:<me>`, there to keep your own repositories out of an archive
    of contributions to other people's. Combined with `repo:<mine>` it contradicts
    itself, and GitHub answers a contradiction by dropping the `repo:` filter and
    returning a thousand unrelated threads -- which looks like a productive scan
    and is entirely the wrong material.
    """

    def setUp(self):
        self.seen = []
        self._real = harvest.gh_json
        harvest.gh_json = lambda args, check=True: self.seen.append(args) or {"items": []}
        self._sleep = harvest.time.sleep
        harvest.time.sleep = lambda _s: None

    def tearDown(self):
        harvest.gh_json = self._real
        harvest.time.sleep = self._sleep

    def _query(self):
        for a in self.seen:
            for i, part in enumerate(a):
                if part == "-f" and a[i + 1].startswith("q="):
                    return a[i + 1]
        raise AssertionError("no search query was issued")

    def test_involvement_search_keeps_the_exclusion(self):
        harvest.search_issues("involves:test-user", "2026-01-01")
        self.assertIn("-user:test-user", self._query())

    def test_a_named_repository_drops_it(self):
        harvest.search_issues("repo:test-user/oss-cli", "2026-01-01", exclude=False)
        q = self._query()
        self.assertIn("repo:test-user/oss-cli", q)
        self.assertNotIn("-user:test-user", q, "the exclusion would cancel the repo filter")


class Slugs(unittest.TestCase):
    def test_a_title_becomes_a_filename(self):
        self.assertEqual("rollover-leaves-a-zero-length-file",
                         harvest.slug("Rollover leaves a zero-length file"))

    def test_punctuation_and_case_are_dropped(self):
        self.assertNotIn(" ", harvest.slug("NPE in Foo.bar(): why?!"))
        self.assertEqual(harvest.slug("Upper CASE"), harvest.slug("upper case"))

    def test_it_is_bounded(self):
        self.assertLessEqual(len(harvest.slug("word " * 200)), 60)

    def test_nothing_is_survivable(self):
        self.assertIsInstance(harvest.slug(""), str)


class ImportCostsNothing(unittest.TestCase):
    """Importing this module must not spend the network.

    It is imported by pr-review-file.py for its naming conventions alone. When
    the user lookup ran at import time, that import shelled out to `gh api user`
    -- so filing a note, which needs no GitHub at all, failed on a machine with
    no connection. A module that cannot be imported offline cannot be borrowed
    from offline.
    """

    def _import_fresh(self, env):
        """Import a second, independent copy under a given environment."""
        import subprocess
        import sys

        script = (
            "import importlib.util, sys;"
            "spec = importlib.util.spec_from_file_location('fresh', %r);"
            "m = importlib.util.module_from_spec(spec);"
            "sys.modules['fresh'] = m;"
            "spec.loader.exec_module(m);"
            "print(m.slug('Rollover Compression Fails'))"
        ) % str(Path(__file__).with_name("oss-harvest.py"))
        return subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, cwd=str(Path(__file__).parent), env=env,
        )

    def test_imports_with_no_user_resolvable(self):
        # No KB_GH_USER, and a `gh` on PATH that fails the way an offline one does.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "gh"
            fake.write_text("#!/bin/sh\nexit 1\n")
            fake.chmod(0o755)

            env = dict(os.environ)
            env.pop("KB_GH_USER", None)
            env["PATH"] = f"{tmp}:{env['PATH']}"
            env.setdefault("KB_ARCHIVE", "/tmp/kb-archive-under-test")

            r = self._import_fresh(env)

            self.assertEqual(r.returncode, 0, f"import failed offline:\n{r.stderr}")
            self.assertNotIn("cannot tell whose activity to harvest", r.stderr)
            self.assertEqual(r.stdout.strip(), "rollover-compression-fails")

    def test_the_user_is_still_demanded_when_actually_harvesting(self):
        # Lazy must not mean optional. Anything that genuinely needs to know whose
        # history this is still refuses rather than harvesting somebody else's.
        #
        # `gh` is sabotaged rather than trusted to fail: this machine has a working
        # one, and the first version of this test PASSED THROUGH TO THE REAL API and
        # then failed the assertion. A test whose result depends on whether the
        # developer happens to be logged in is testing the developer.
        import tempfile

        harvest._USER = None
        real_path = os.environ["PATH"]
        real_user = os.environ.pop("KB_GH_USER", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fake = Path(tmp) / "gh"
                fake.write_text("#!/bin/sh\nexit 1\n")
                fake.chmod(0o755)
                os.environ["PATH"] = f"{tmp}:{real_path}"

                with self.assertRaises(SystemExit):
                    harvest.gh_user()
        finally:
            os.environ["PATH"] = real_path
            os.environ["KB_GH_USER"] = real_user or "test-user"
            harvest._USER = None


if __name__ == "__main__":
    unittest.main()
