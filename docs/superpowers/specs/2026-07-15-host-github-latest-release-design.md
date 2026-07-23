# Telepiplex GitHub Latest Release Design

This design supersedes the Telepiplex Release and Latest Platform compatibility
sections of `2026-07-14-independent-feature-and-catalog-releases-design.md`.
Feature identity, immutable artifacts, and optimistic catalog publication from
that design remain unchanged.

## Goal

Every successful `telepiplex-v<semver>` publication must update both Telepiplex distribution
surfaces:

- `ghcr.io/<owner>/telepiplex:<semver>` and `:latest`;
- a same-tag GitHub Release marked as the repository Latest Release.

The first release under this contract is `telepiplex-v1.0.7`. It includes the pending
Telepiplex Feature-config migration fix and the release-workflow change. The existing
`telepiplex-v1.0.6` tag remains immutable.

## Release Contract

The Telepiplex workflow remains triggered only by an immutable `telepiplex-v<semver>` tag.
It validates and tests Telepiplex, builds and pushes the versioned and rolling GHCR
tags, and then creates a GitHub Release whose tag and title equal the Telepiplex tag.
The Release is explicitly marked Latest rather than relying on GitHub's date or
semantic-version inference.

The GitHub Release contains release notes identifying the Telepiplex image, source
commit, and supported platform. It does not contain `catalog.yaml`, Feature
packages, or other assets. The live Feature catalog remains owned by the
dedicated `catalog` branch and its raw GitHub URL.

Feature workflows continue creating their releases with `--latest=false`, so a
Feature publication cannot replace the latest Telepiplex Release.

## Failure Handling

The GitHub Release job runs only after Telepiplex validation and image publication
succeed. The workflow refuses to create a duplicate Release for an existing
Telepiplex tag. A Release failure leaves the already-published immutable image intact
and makes the workflow visibly fail; it never falls back to a Feature or
Platform Release as Latest.

The workflow receives `contents: write` only because creating the Telepiplex Release
requires it. Package publication remains covered by `packages: write`.

## Verification

Workflow contract tests require all of the following:

- `telepiplex-v*` remains the only Telepiplex tag trigger;
- the Telepiplex image publishes both `<semver>` and `latest`;
- the Release job depends on validation and image publication;
- the Release tag and title equal `telepiplex-v<semver>` and use an explicit Latest
  flag;
- the Release has no catalog or Feature assets;
- Feature Releases retain `--latest=false`.

After local tests pass, push `main`, create and push
`telepiplex-v1.0.7`, then verify the GitHub Actions run, GHCR `1.0.7`/`latest` digest
identity, and GitHub `releases/latest` response.
