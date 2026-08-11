# Contributing

Contributions are welcome. This page is short on purpose — everything below is a
consequence of two rules.

## 1. Fork, then open a pull request

`main` is protected: it takes no direct pushes, no force-pushes, and cannot be
deleted. There is no write access to hand out, so the path is the same for
everyone including people who have been here a while:

```bash
gh repo fork ramanathan1504/knowledge-creator --clone
git switch -c what-it-does
# … change things …
gh pr create
```

Branch from `main`, name the branch after what it does, and open the pull
request against `main`.

Conversations on a pull request must be resolved before it merges. That is not
ceremony: an unresolved thread is usually a question nobody answered, and merging
past it is how the answer gets lost.

## 2. Say what you verified, not what you intended

The single most useful line in a pull request is what you ran and what it
printed. "Should fix the parsing" and "reproduced the crash on the attached
input, and it no longer occurs" cost the same to write and are worth very
different amounts to review.

If you could not verify something, say that too. A stated gap is reviewable; a
silent one is discovered later, by someone else.

## Running it

No build step — it is Python and shell.

```bash
./kb doctor          # is the archive reachable, and did the last sync work?
./kb --help          # every verb
```

Every path is an environment override (`KB_ARCHIVE`, `KB_DEVONTHINK_DB`,
`KB_SOURCES`, `KB_GH_USER`), so nothing here is tied to one machine or one
person. If you find something that still is, that is a bug worth reporting.

Scripts dry-run by default and write only with `--apply`. Keep it that way: this
writes into somebody's personal archive, and a script that writes by default is
one that eventually writes something nobody asked for.

## Licence

Apache 2.0. New source files carry the standard header; the build does not add
it for you, and a file without one will be asked about in review.

By opening a pull request you agree that your contribution is licensed under the
same terms.

## Reporting something insecure

Do not open a public issue for a vulnerability. Use GitHub's **Report a
vulnerability** button under the Security tab, which is private until there is a
fix worth publishing.

Dependency alerts and secret scanning are enabled on this repository, so an
automated pull request from Dependabot is expected traffic rather than a
compromise.
