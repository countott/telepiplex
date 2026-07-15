# Core GitHub Latest Release Design

## Goal

Every successful `core-v<semver>` publication must update both Core distribution
surfaces:

- `ghcr.io/<owner>/telepiplex-core:<semver>` and `:latest`;
- a same-tag GitHub Release marked as the repository Latest Release.

The first release under this contract is `core-v1.0.7`. It includes the pending
Core Feature-config migration fix and the release-workflow change. The existing
`core-v1.0.6` tag remains immutable.

## Release Contract

The Core workflow remains triggered only by an immutable `core-v<semver>` tag.
It validates and tests Core, builds and pushes the versioned and rolling GHCR
tags, and then creates a GitHub Release whose tag and title equal the Core tag.
The Release is explicitly marked Latest rather than relying on GitHub's date or
semantic-version inference.

The GitHub Release contains release notes identifying the Core image, source
commit, and supported platform. It does not contain `catalog.yaml`, Feature
packages, or other assets. The live Feature catalog remains owned by the
dedicated `catalog` branch and its raw GitHub URL.

Feature workflows continue creating their releases with `--latest=false`, so a
Feature publication cannot replace the latest Core Release.

## Failure Handling

The GitHub Release job runs only after Core validation and image publication
succeed. The workflow refuses to create a duplicate Release for an existing
Core tag. A Release failure leaves the already-published immutable image intact
and makes the workflow visibly fail; it never falls back to a Feature or
Platform Release as Latest.

The workflow receives `contents: write` only because creating the Core Release
requires it. Package publication remains covered by `packages: write`.

## Verification

Workflow contract tests require all of the following:

- `core-v*` remains the only Core tag trigger;
- the Core image publishes both `<semver>` and `latest`;
- the Release job depends on validation and image publication;
- the Release tag and title equal `core-v<semver>` and use an explicit Latest
  flag;
- the Release has no catalog or Feature assets;
- Feature Releases retain `--latest=false`.

After local tests pass, push `feature/telepiplex-core`, create and push
`core-v1.0.7`, then verify the GitHub Actions run, GHCR `1.0.7`/`latest` digest
identity, and GitHub `releases/latest` response.
