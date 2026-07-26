# Main-as-Core Release and Legacy Archive Design

## Goal

Make `main` the only active Telepiplex Core/Host source line, guarantee that
the default `latest` image is produced from code already contained in `main`,
and retire the obsolete `feature/telepiplex-core` branch without losing its
unique history.

## Active source model

- `main` is the only Core/Host source branch.
- `app/`, `sdk/`, container files, release workflows, tools, and root tests
  describe the Core/Host runtime carried by `main`.
- `features/<plugin_id>` remain independently versioned Feature sources inside
  the same `main` monorepo.
- `catalog` remains the generated rolling Feature catalog branch.
- `feature/telepiplex-core` is not a valid development or release source.

Historical design records remain historical. Current README and workspace
instructions must state the active model without rewriting old decision
records.

## Core release gate

The existing `telepiplex-v<semver>` tag trigger remains the only Core image
publication trigger. A new validation step must:

1. fetch `refs/heads/main` into `refs/remotes/origin/main`;
2. resolve the tag event SHA and remote `main` to commit objects;
3. require the release commit to be an ancestor of remote `main`;
4. fail before tests or image publication when the commit is not contained in
   `main`.

Ancestor membership is intentional. It proves the release came from `main`
while still allowing an operator to publish an older known-good `main` commit.
The immutable version tag continues to identify the exact released commit.

## Default image and GitHub Latest

After the main-membership gate and the complete test suite pass, the release
workflow continues to publish both:

- `ghcr.io/<owner>/telepiplex:<version>`;
- `ghcr.io/<owner>/telepiplex:latest`.

The same workflow continues to create the `telepiplex-v<semver>` GitHub
Release with `--latest`. Ordinary pushes to `main` do not update either
default. This keeps `latest` on the newest successful formal Core release,
never on unvalidated branch work.

## Default deployment image

The repository's default `docker compose up -d` path must consume the formal
Core release from `ghcr.io/countott/telepiplex:latest` and request a fresh
pull. The Compose image and pull policy remain environment-overridable for
local development.

The existing `build.sh` contract remains unchanged: Mac or Unraid source
builds produce the local `telepiplex:latest` image. Running that extra local
build requires both `TELEPIPLEX_IMAGE=telepiplex:latest` and
`TELEPIPLEX_PULL_POLICY=never`; it does not change the default formal-release
path and cannot affect the qualified GHCR tag.

Feature publication keeps its existing tag behavior and does not gain a
separate `main` ancestry gate in this change.

## Legacy Core archive

Read-only GitHub inspection on 2026-07-26 found:

- legacy branch: `feature/telepiplex-core`;
- exact tip: `4393bebac52ff75a1b46cf1ef9d634a4b4299f9d`;
- comparison with `main`: diverged, with 95 legacy-only commits and 124
  main-only commits.

The branch must not be deleted without preserving that exact tip. The
recoverable archive sequence is:

1. create annotated tag
   `archive/feature-telepiplex-core-2026-07-26` at the exact legacy tip;
2. push the tag;
3. verify the remote tag peels to the expected commit;
4. verify the remote branch still has the expected tip;
5. delete the remote `feature/telepiplex-core` branch;
6. verify the branch is absent.

The archive tag does not match `telepiplex-v*` or any Feature release tag
pattern, so it cannot trigger a release workflow.

Mac-local development must not perform these Git operations. The repository
will contain a pinned Unraid runbook; the user performs the archival operation
from the authoritative Unraid Git workspace.

## Tests and verification

- Execute the Core source-membership shell step with a fake `git` command.
- Prove it succeeds only when `merge-base --is-ancestor` succeeds.
- Preserve existing tests that require versioned and `latest` image tags plus
  GitHub `--latest`.
- Run the root test suite and all five Feature test suites.
- Confirm `.git` and `.worktrees` remain absent and `.stfolder` remains
  present.
