# Runtime Recovery and Metadata Reliability Implementation Plan

> Mac-local implementation only. The telepiplex workspace must not use Git;
> publication remains a Syncthing-to-Unraid handoff.

## 1. Lock behavioral contracts with failing tests

- Add Search tests for metadata-probe scope inference, ambiguous scope,
  object/content-part AI responses, Japanese animation romanization, and
  partial evidence-source failure.
- Add download tests for 115 `10008` reattachment in running, retry, complete,
  and missing-match states.
- Add Rename tests for bare-episode probes, structured capability errors, and
  detailed fallback-move outcomes.
- Add Core tests for embedded Feature log severity and release-image revision
  propagation.
- Run each new test before production edits and record the expected failure.

## 2. Implement Search reliability

- Normalize OpenAI-compatible response content at the provider boundary.
- Feed `metadata_probe` into series scope resolution.
- Add a bounded Japanese animation romanization fallback.
- Distinguish evidence unavailable from evidence not found.
- Bump Search Feature patch identity and keep manifest/package versions equal.

## 3. Implement existing-task recovery

- Resolve an existing external 115 task after code `10008`.
- Reuse normal polling and downstream handoff for running, retry, and complete
  states.
- Emit a user-visible reattachment state without exposing magnets or tokens.
- Keep the existing stable failure when no external task matches.
- Bump download Feature patch identity and keep versions equal.

## 4. Implement Rename and cross-module error contracts

- Detect bare `Exx` episode packs without inventing a season.
- Preserve sanitized capability error envelopes through Rename operation
  reports.
- Use detailed storage outcomes for `/未整理` fallback.
- Bump Rename Feature patch identity and keep versions equal.

## 5. Implement runtime identity and severity

- Pass the release commit into Docker builds and export
  `TELEPIPLEX_COMMIT`.
- Map embedded Feature log prefixes to Host logger severity.
- Update the Host patch identity if required by the existing release contract.

## 6. Verify locally

- Run focused tests for every changed module.
- Run the complete Core suite and all five Feature suites with bytecode/cache
  disabled.
- Confirm `.git` and `.worktrees` are absent and `.stfolder` remains present.
- List all changed files and hand off to the user to wait for Syncthing
  `Up to Date / 最新`.
