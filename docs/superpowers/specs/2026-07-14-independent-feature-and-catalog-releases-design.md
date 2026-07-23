# Independent Feature and Catalog Releases Design

## Goal

Decouple Telepiplex image releases from Feature releases so a Feature update can be
discovered and installed through Telegram without moving the Telepiplex `latest`
image or restarting an unchanged Telepiplex container.

## Release identities

Telepiplex uses three independent identities:

- Telepiplex image: `telepiplex-v<semver>`, for example `telepiplex-v1.0.6`.
- Feature artifact: `<plugin-id>-v<semver>`, for example
  `search-v1.0.2`.
- Catalog revision: the commit at the head of the dedicated `catalog` branch.

The Feature version remains the immutable identity stored in each Feature's
`manifest.yaml`. Any source or artifact change requires a version bump. The
catalog revision is not a second Feature version and does not move the Telepiplex
image tag.

## Telepiplex release

Pushing `telepiplex-v<semver>` runs Telepiplex tests and builds only:

- `ghcr.io/<owner>/telepiplex:<semver>`
- `ghcr.io/<owner>/telepiplex:latest`

The Telepiplex workflow does not build Feature artifacts and does not create a
GitHub Release. Therefore a Telepiplex-only release cannot replace the GitHub
`releases/latest/download/catalog.yaml` compatibility endpoint.

Existing `platform-v*` tags and releases remain immutable historical records.
The workflow no longer accepts new `platform-v*` tags.

## Feature release

The Telepiplex release-infrastructure commit owns the Feature release workflow.
Feature release tags point to that Telepiplex commit and use one of these forms:

- `download-v<semver>`
- `search-v<semver>`
- `rename-v<semver>`
- `sync-v<semver>`

The workflow maps the tag to the fixed Feature source branch. A read-only build
job checks out that branch without persisted Git credentials and requires its
manifest version to equal the tag version. It builds exactly one `.tpx` and
verifies the embedded plugin ID, version, source branch, source commit, and
SHA-256. The write-scoped publication job consumes only the verified workflow
artifact and never executes the Feature build backend.

If the Feature Release already exists, the read-only job downloads its exact
`.tpx`, verifies the embedded source commit, and reuses those bytes without
resolving the moving Feature branch head. During initial migration, the first
run instead finds the same plugin version and source commit in the aggregate
catalog, downloads and verifies that prior immutable artifact, and reuses its
exact bytes. This bootstraps the four existing `1.0.1` Feature releases from
`platform-v1.0.5` without generating a second digest for an existing identity.

Each Feature GitHub Release contains:

- exactly one `<plugin-id>-<version>.tpx`;
- the complete resulting `catalog.yaml`;
- `catalog.yaml.sha256`.

Including the catalog assets preserves compatibility for deployed Telepiplex
configurations that still use
`https://github.com/countott/telepiplex/releases/latest/download/catalog.yaml`.

## Catalog branch

The `catalog` branch contains only the current catalog snapshot, its checksum,
and a short README. The preferred runtime URL is:

`https://raw.githubusercontent.com/countott/telepiplex/catalog/catalog.yaml`

After a Feature Release is successfully published, the workflow updates the
catalog branch. The updater:

1. preserves every unrelated plugin and version entry;
2. inserts or replaces only the released Feature version;
3. rejects a reused version when its digest, source branch, or source commit
   differs;
4. points the released entry to its immutable Feature Release asset;
5. writes deterministic YAML and `catalog.yaml.sha256` atomically.

Catalog publishing uses an optimistic merge/retry loop. Every attempt fetches
the fresh catalog head, merges only the current Feature entry while preserving
all unrelated entries, and performs a non-force push. A non-fast-forward push
is retried from the new head; authentication, network, and unknown probe or push
failures stop closed. This allows independent Feature tags to run concurrently
without a misleading workflow-level queue and without dropping catalog
entries.

The workflow creates the GitHub Release before moving the catalog branch, so
catalog readers never receive a URL to an unpublished asset. After a successful
catalog push it replaces the current Feature Release catalog assets with the
confirmed branch snapshot and also converges the latest Feature Release assets
for `releases/latest` compatibility. A catalog push failure leaves the new
Release available for a safe retry and does not corrupt the previous catalog.

## Telegram update flow

Telepiplex refreshes the catalog at startup, on the existing interval, and when the
operator opens `/plugin`. It compares the installed Feature semver with the
newest compatible stable catalog semver.

For a search-only update:

1. bump `search` from `1.0.1` to `1.0.2`;
2. push `search-v1.0.2` at the release-infrastructure Telepiplex commit;
3. publish `search-1.0.2.tpx` and update the catalog;
4. leave the Telepiplex `latest` image at `1.0.5`;
5. Telegram offers `search 1.0.1 -> 1.0.2` and updates only after the
   authorized user confirms.

Installed Features continue running when the catalog is unavailable. A failed
catalog refresh retains the last validated local cache.

## Initial migration

The existing `platform-v1.0.5` Release and Telepiplex image remain unchanged. After
the new workflows are pushed, publish these tags sequentially from the same
Telepiplex release-infrastructure commit:

- `download-v1.0.1`
- `search-v1.0.1`
- `rename-v1.0.1`
- `sync-v1.0.1`

Each release reuses the matching verified `platform-v1.0.5` artifact. The
first release bootstraps the catalog branch from the last valid aggregate
catalog; subsequent releases preserve and advance it. Since Feature versions
do not change during migration, Telegram does not report false updates.

## Verification

Automated tests cover tag parsing, tag-to-branch mapping, manifest-version
matching, immutable identity rejection, preservation of unrelated catalog
entries, deterministic checksum output, Telepiplex-only workflow behavior,
read/write job isolation, failure-closed probes, optimistic non-fast-forward
retry, catalog compatibility assets, and both default catalog URLs. Publication
verification checks all four GitHub Releases, catalog branch contents, SHA-256
values, source commits, and unchanged Telepiplex `latest` identity.
