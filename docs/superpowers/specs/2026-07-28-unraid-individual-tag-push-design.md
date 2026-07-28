# Unraid Individual Release Tag Push Design

## Goal

Ensure every telepiplex Host and Feature release tag pushed by the Unraid
`telepiplex Publish` script generates its own GitHub `push` event and can
therefore trigger the existing tag-filtered GitHub Actions workflows.

## Problem

The publisher currently collects every missing Host and Feature tag and sends
all refs through one `git push --atomic`. A normal release can contain the Host
tag plus four Feature tags. GitHub does not create tag `push` events when more
than three tags are pushed at once, so the repository can receive all tags
without starting any release workflow.

## Considered approaches

1. **Push each tag separately — selected.** Every command contains one tag,
   stays below GitHub's event limit, and maps one command to one release
   workflow. If a later push fails, a rerun reads the already-published remote
   tags and resumes with the remaining versions.
2. **Push batches of at most three tags.** This stays within the documented
   limit but retains unnecessary coupling between otherwise independent
   releases and makes partial recovery less obvious.
3. **Add `workflow_dispatch` release recovery.** This is useful as a separate
   future capability, but it would require refactoring workflow inputs,
   checkout refs, and release commit identity. It does not fix the publisher's
   normal tag event behavior.

## Selected behavior

The script continues to:

- validate the Host version and every Feature manifest/project version;
- push `main` before any release tag;
- create missing annotated tags at the pushed `main` HEAD;
- skip versions whose matching tag already exists remotely;
- verify every newly pushed tag on the remote.

The tag publication step changes to:

1. iterate over `TAG_REFS` in its existing deterministic order;
2. run one `git push origin refs/tags/<tag>` per entry;
3. stop immediately if any push fails;
4. retain the existing post-push remote verification loop.

The script no longer promises cross-tag atomicity. Each tag already represents
an independent immutable release, and rerunning the script is the recovery
path for a partial sequence.

## Error handling

`set -Eeuo pipefail` keeps the current fail-fast behavior. A failed tag push
ends the run before later tags are sent. Tags already accepted by GitHub remain
valid. On the next run, the remote tag scan excludes those versions from
`PENDING_TAGS`; locally created but not yet remote tags are accepted only when
they still point to the current HEAD.

## Tests

The fake-Git publisher test will reproduce a Host plus four-Feature release and
assert that:

- five separate tag push commands are emitted in deterministic order;
- every tag push contains exactly one `refs/tags/...` ref;
- no `push --atomic` command is emitted;
- existing tests still prove already-published Feature versions do not receive
  new tags and a single missing Feature version is published correctly.

Focused publisher tests, shell syntax validation, the root test suite, all five
Feature test suites, and the Mac workspace boundary checks must pass before
Syncthing handoff.
