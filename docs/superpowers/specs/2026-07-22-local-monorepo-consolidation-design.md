# Local Monorepo Consolidation Design

## Objective

Convert the Mac development directory into a Git-independent, Syncthing-friendly
single source tree while preserving the GitHub-side release automation that will
run only after the user reviews and publishes the synchronized files from Unraid.

## Constraints

- All work in this change is local to `/Users/young/Documents/telepiplex`.
- Do not run Git commands or contact GitHub from the Mac.
- Do not create, inspect, edit, or depend on local Git metadata.
- Keep `.stfolder`; it is the Syncthing folder marker.
- Keep the local `.venv`, but exclude it from Syncthing.
- Keep GitHub Actions as source files. They are server-side release automation,
  not a Mac-to-GitHub publishing link.
- Preserve technical identities (`plugin_id`, tag prefixes, package names, and
  artifact names). User-facing module display-name changes are outside this task.
- Preserve tag-driven immutable releases. A plain `main` push does not create a
  versioned release; publishing still requires a matching version tag on Unraid.

## Chosen Layout

```text
telepiplex/
├── .github/workflows/          # GitHub-side release automation
├── app/                        # Telepiplex runtime
├── config/                     # Telepiplex configuration templates
├── examples/                   # Telepiplex Feature examples
├── features/
│   ├── search/
│   ├── download/
│   ├── sync/
│   └── rename/
├── sdk/                        # Feature SDK
├── tests/                      # Telepiplex tests
├── tools/                      # Build/catalog tooling
├── Dockerfile
└── requirements.txt
```

Telepiplex stays at the repository root because its Dockerfile, runtime imports, and
test paths already assume that layout. Feature projects keep their internal
standalone package layout under `features/<plugin_id>/`, so their manifests,
tests, and build inputs remain self-contained.

## Alternatives Considered

1. Keep the hidden `.worktrees/` directories and only remove their `.git`
   pointer files. Rejected because the project would remain hidden, confusing,
   and coupled to obsolete branch-shaped directories.
2. Put Telepiplex under `host/` next to `features/`. Rejected because it would require
   unnecessary Docker, import, test, and tooling path changes.
3. Keep each module in a separate top-level directory. Rejected because Telepiplex is
   the application root and the resulting layout would add a redundant wrapper.

## Source Migration

Copy only authored source and documentation from the five product worktrees.
Exclude `.git`, `.pytest_cache`, `__pycache__`, `build`, `dist`, `*.egg-info`,
`.DS_Store`, IDE state, and `.superpowers/sdd` review scratch data. The temporary
`backup-secret-redaction` worktree is not a product module and is removed with
the old worktree tree.

The current root `dist/` directory contains generated packages and old offline
backup/export material, so it is removed. Root `.pytest_cache` and `.DS_Store`
are also removed. `.venv` stays local and is excluded through `.stignore`.

## Release Source Model

Feature releases continue to use the existing tag families:
`download-v*`, `search-v*`, `rename-v*`, and `sync-v*`.
The release workflow resolves each `plugin_id` to `features/<plugin_id>` in the
single checked-out `main` tree instead of checking out a feature branch.

Feature manifests and generated catalog entries use `source.branch: main`.
`source.commit` remains the immutable GitHub commit used by Actions. This keeps
Telepiplex update verification compatible with the existing artifact identity model
without retaining branch-specific source locations.

## Syncthing Boundary

Create a root `.stignore` that excludes local Git/worktree data, virtual
environments, test caches, bytecode, build output, package metadata, IDE state,
and `.DS_Store`. This is a second safety layer; the prohibited `.git` and
`.worktrees` directories are also physically removed from the Mac project.

## Verification

Verification is filesystem- and Python-based only:

- assert the expected monorepo directories and key files exist;
- assert `.git`, `.worktrees`, worktree pointer files, caches, and build output
  no longer exist in the project source tree;
- assert no local SSH remote or GitHub publishing command remains outside
  historical documentation and GitHub Actions;
- run Telepiplex and all four Feature test suites from their new paths;
- parse manifests and workflow YAML;
- build the dependency-free echo Feature end to end, then build each product
  Feature from a temporary source copy without third-party dependency downloads
  and verify its wheel/SDK/manifest/`.tpx` structure.

No Git command, remote connection, publication, or release action is part of
this verification.
