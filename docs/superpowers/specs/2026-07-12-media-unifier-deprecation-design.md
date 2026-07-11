# Media Unifier Deprecation and Plex Management Replacement

**Date:** 2026-07-12

## Decision

`feature/media-unifier` is deprecated. Its supported replacement is
`feature/plex-management`, registered as the post-renaming Plex pipeline module.

The stable runtime order is:

```text
app.modules.renaming
app.modules.plex_management
```

`feature/plex-management` remains an independent, reusable feature branch and
worktree even though its module is also merged into `main`.

## Main-branch changes

- Mark `feature/media-unifier` as deprecated in `README.md`.
- Point readers to `feature/plex-management` as its replacement.
- State that Plex management runs after successful `renaming.*` completion.
- Add or tighten a regression test that locks the default module order to
  `renaming` followed immediately by `plex_management`.

No runtime compatibility shim or alias named `media-unifier` will be added.

## Legacy-branch changes

- Add a concise deprecation notice on the current `feature/media-unifier`
  branch.
- The notice identifies `feature/plex-management` as the replacement and does
  not remove historical source content.
- Rename the local branch to `archive/deprecated-media-unifier` after the
  deprecation commit succeeds.

## Branch and publication boundaries

- Preserve `feature/plex-management` and its existing worktree unchanged.
- Do not delete or rename `feature/plex-management`.
- Do not push any branch or delete any remote reference in this change.
- The existing remote `origin/feature/media-unifier` remains untouched until a
  separate publication request explicitly authorizes remote mutation.

## Verification

- Run the focused README/module-order tests on `main`.
- Verify `DEFAULT_ENABLED_MODULES` places `app.modules.plex_management`
  immediately after `app.modules.renaming`.
- Verify the archived branch contains the deprecation notice and retains its
  historical files.
- Verify both `main` and `feature/plex-management` worktrees are clean after the
  operation.
