# First-install Feature Catalog Design

**Date:** 2026-07-13

**Status:** approved by the confirmed OPS-TODO-02 recommendation

## Goal

Let a normal Telegram user discover and install released Features without opening ttyd, cloning branches, installing build dependencies, calculating SHA-256 values, or typing an exact version.

## User experience

- `/plugin` without arguments becomes the Feature overview instead of printing only syntax.
- The overview lists installed Features with version and state.
- For every catalog Feature that is not installed, Telepiplex selects the newest stable version compatible with the current Host API.
- Installable candidates receive an `安装 <plugin> <version>` button.
- Candidates whose required capabilities are not yet available remain visible, but their button is withheld and the prerequisite Feature or missing capability is explained.
- Clicking an install button is the explicit authorization point. Telepiplex never installs a Feature merely because it appears in the catalog.
- Existing `/plugin install name@version` and absolute `.tpx` paths remain supported as operator fallbacks.

## Catalog contract

The release catalog continues to pin every artifact URL and SHA-256. Each release entry additionally publishes the manifest-derived `provides` and `requires` capability lists. Telepiplex does not trust UI labels for activation; the existing `.tpx` verification and PluginManager install transaction remain authoritative.

Catalog discovery ignores prereleases, incompatible Host API ranges, malformed versions, invalid digests, and invalid capability metadata. If several compatible stable versions exist, the highest semantic version is selected.

## Dependency-aware choices

Telepiplex compares a candidate's `requires` list with the currently routed capabilities. For each missing capability it looks for another selected catalog candidate that provides it. This produces two groups:

- ready candidates with install buttons;
- blocked candidates with prerequisite Feature names or missing capability names.

There is no bulk install button. The user installs a ready provider first, then runs `/plugin` again to reveal newly ready consumers. This preserves the existing one-Feature transaction and makes every state change explicit.

## Failure and cache behavior

Opening `/plugin` attempts a remote refresh. If refresh fails but a previously validated catalog exists, the overview uses that cache and states no false success. If neither remote data nor a valid cache is available, Telepiplex returns a stable catalog-unavailable message plus the manual install syntax. Telepiplex and installed Features continue running.

Install callbacks are reserved under `host-plugin-install:` and are handled before dynamic Feature callbacks. They require `allowed_user`, validate the complete `name@version` reference, show progress, call `PluginManager.install`, and render only sanitized errors.

## Non-goals

- No silent or automatic installation.
- No bulk installation or implicit dependency installation.
- No Feature-specific configuration wizard in this iteration.
- No relaxation of digest, manifest, capability, health, or rollback checks.

## Verification

- Catalog tests cover newest-compatible selection, prerelease rejection, installed filtering, and dependency metadata.
- Generator tests prove `provides` and `requires` come from verified manifests.
- Manager tests cover cache fallback and current capability comparison.
- Telegram tests cover overview rendering, ready and blocked candidates, authorization, install success, and sanitized failure.
- Deployment tests keep command-line fallback and mark OPS-TODO-02 implemented.
