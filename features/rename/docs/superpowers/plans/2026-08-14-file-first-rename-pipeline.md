# File-first Rename Pipeline Implementation Plan

> **For telepiplex maintainers:** Execute this plan in the private Mac
> workspace with the `executing-plans` and `test-driven-development` skills.
> The repository-local `AGENTS.md` overrides generic Git advice: do not run
> Git on the Mac. End every task with focused tests and an explicit changed-file
> checkpoint instead of a commit.

**Goal:** Replace rename's directory-batch identity and mutation boundary with
one immutable full-root snapshot, file-derived identity, per-file planning, and
safe per-file execution.

**Architecture:** A new file-first core converts provider tree nodes into
`FileFact` records, parses deterministic `ParsedFileEvidence`, derives
compatible provisional groups, and produces one `FileResolution` per media
file. Existing metadata confirmation and canonical naming remain authoritative,
but processor execution consumes only explicit file actions and never moves or
deletes a whole release as fallback. Storage providers retain an independent
same-path guard.

**Tech Stack:** Python 3.12, dataclasses, existing telepiplex plugin SDK,
provider capability proxy, pytest/unittest-compatible tests.

---

## Global constraints and interfaces

- Work only under `/Users/young/Documents/telepiplex`.
- Do not run Git, create worktrees, publish, tag, or connect this checkout to
  GitHub.
- Keep confirmed `media_metadata v1` and search confirmation as the external
  identity authority.
- Use pipeline version `file-first-v1` in snapshot and idempotency records.
- Preserve every media and subtitle source unless an explicit planned move has
  completed and the provider reports the source transition accurately.
- Do not use whole-root `/未整理` movement as an error path.
- Do not delete samples, extras, unmatched videos, subtitles, or unknown files.
- Treat `source == final target` as successful `no_op` in both planner and
  provider.
- Delete a directory only after a fresh complete listing proves it is empty;
  never delete the selected scan root.

The new core exposes these stable entry points:

```python
build_file_facts(nodes, *, root_path, provider, snapshot_id) -> list[FileFact]
parse_file_evidence(fact) -> ParsedFileEvidence
build_provisional_groups(evidence) -> list[ProvisionalWorkGroup]
plan_file_resolutions(facts, evidence, confirmed_groups, *, target_root)
    -> list[FileResolution]
execute_file_resolutions(storage, resolutions, *, selected_root, journal=None)
    -> FileExecutionSummary
```

Existing public processor functions remain callable while their internals are
migrated. This allows completed-download events and inventory orchestration to
share the new core without a competing legacy pipeline.

## Task 1: Enforce provider-level same-path safety

**Files:**

- Modify: `features/download/src/telepiplex_download/client.py`
- Create: `features/download/tests/test_client_move_safety.py`

**Step 1: Write the failing provider tests**

Create a minimal client stub that records `create_dir_recursive`, `copy_file`,
and `delete_single_file`. Assert both exact and normalized same-path moves are
reported as `no_op` without invoking any mutation:

```python
result = client.move_file_detailed(
    "/TV/Veep Season 07/Veep S07E01.mkv",
    "/TV/Veep Season 07",
)
assert result["state"] == "no_op"
assert result["target_path"].endswith("/Veep S07E01.mkv")
assert client.mutations == []
assert client.move_file(source, target_dir) is True
```

**Step 2: Verify the test fails**

Run:

```bash
cd /Users/young/Documents/telepiplex/features/download
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_client_move_safety.py
```

Expected: failure because `move_file_detailed` currently creates, copies, and
deletes even when the effective final path equals the source.

**Step 3: Implement normalized target calculation**

Add an internal POSIX normalizer and calculate `final_target` before directory
creation. Return:

```python
{
    "state": "no_op",
    "copied": False,
    "source_deleted": False,
    "source_path": source_path,
    "target_path": final_target,
}
```

when normalized source and final target match. Make `move_file` accept both
`moved` and `no_op` as success.

**Step 4: Run focused and download regressions**

Run the focused test, then the complete download Feature suite. Record exact
test counts and changed files. Do not commit.

## Task 2: Introduce immutable file facts and deterministic evidence

**Files:**

- Create: `features/rename/src/telepiplex_rename/file_facts.py`
- Modify: `features/rename/src/telepiplex_rename/media_naming.py`
- Modify: `features/rename/src/telepiplex_rename/content_probe.py`
- Create: `features/rename/tests/test_file_facts.py`
- Modify: `features/rename/tests/test_media_naming.py`

**Step 1: Write failing parser tests**

Cover:

- `Veep.S07E01.mkv` and `Veep (2012) S07E01.chs.srt` have the same
  `title_key`, while only the second has `year_hint=2012`;
- a subtitle and video each retain their own title evidence even when their
  parent folder disagrees;
- `Honey and Clover S1 - 01.mkv` parses to season 1, episode 1;
- `Season 1 - 01`, `S01E01`, `1x01`, and existing Chinese markers remain
  supported;
- source ids prefer provider file ids and otherwise use a deterministic hash of
  provider plus normalized absolute path;
- non-media nodes remain facts but do not receive mutation evidence.

**Step 2: Verify failures before implementation**

Run only `test_file_facts.py` and the new media-naming cases. Confirm the new
module is missing and `S1 - 01` is not parsed.

**Step 3: Implement data records and normalizers**

Add frozen dataclasses:

```python
@dataclass(frozen=True)
class FileFact:
    source_id: str
    provider: str
    absolute_path: str
    relative_path: str
    basename: str
    parent_parts: tuple[str, ...]
    extension: str
    size: int
    sha1: str
    media_kind: str
    snapshot_id: str

@dataclass(frozen=True)
class ParsedFileEvidence:
    source_id: str
    title_candidates: tuple[str, ...]
    title_key: str
    year_hint: int | None
    season_number: int | None
    episode_number: int | None
    absolute_episode: int | None
    content_role: str
    subtitle_language: str
    subtitle_variant: str
    confidence: str
    evidence: tuple[str, ...]
    directory_hints: tuple[str, ...]
```

Keep basename evidence separate from `directory_hints`. Strip a trailing year
from `title_key` but retain it in `year_hint`. Add `S1 - 01` and
`Season 1 - 01` support to `parse_episode_marker` without weakening existing
patterns.

**Step 4: Make aggregate probes consume file evidence**

Keep `build_metadata_probe` backward compatible, but derive its file candidates
from `ParsedFileEvidence`. Do not use `video_paths or subtitle_paths`; include
both as independent candidates. A year mismatch is a conflict only when both
files state different explicit years.

**Step 5: Run focused and existing evidence suites**

Run `test_file_facts.py`, `test_media_naming.py`, `test_content_probe.py`, and
`test_query_recovery.py`. Record exact results.

## Task 3: Group compatible files independently of folders

**Files:**

- Create: `features/rename/src/telepiplex_rename/file_groups.py`
- Create: `features/rename/tests/test_file_groups.py`

**Step 1: Write failing grouping tests**

Assert:

- `Veep` and `Veep (2012)` form one provisional group;
- `Veep (2012)` and `Veep (2019)` remain separate;
- two works in one parent folder produce two groups;
- one work across sibling folders forms one group;
- files without a usable title remain ungrouped with a stable reason;
- subtitle-only evidence can create a group;
- group query keys are deterministic and independent of input order.

**Step 2: Implement compatibility grouping**

Add frozen `ProvisionalWorkGroup` records with `group_id`, `title_key`,
`year_hints`, `source_ids`, `query_candidates`, and `status`. Use connected
components for compatible title/year evidence: equal normalized title keys and
equal years or at least one missing year. Never merge two explicit different
years through a yearless bridge; partition explicit years first and attach a
yearless file only when that attachment is unambiguous.

**Step 3: Add verified-group projection**

Provide a pure function that accepts confirmed metadata keyed by provisional
group and returns verified external-identity groups. Only identical confirmed
external ids may merge aliases.

**Step 4: Run deterministic/order/scale tests**

Include 111, 1,000, and 10,000-fact local grouping cases and assert one query
candidate per compatible work. Run focused tests and record timing.

## Task 4: Replace subtitle selection with preservation and canonical naming

**Files:**

- Modify: `features/rename/src/telepiplex_rename/subtitles.py`
- Create: `features/rename/tests/test_subtitle_preservation.py`
- Modify: `features/rename/tests/test_subtitles.py`

**Step 1: Write preservation tests**

Assert simplified, traditional, bilingual, English, Japanese, Korean, French,
German, Spanish, Italian, Russian, Arabic, Thai, and Vietnamese markers map to
ISO 639-2/B suffixes. Assert `forced`, `sdh`, and `cc` never appear in target
names. Assert every recognized subtitle receives an operation, unknown
language gets `keep_original`, and no result contains a discard action.

Cover collision naming:

```text
Veep S07E01.chi.srt
Veep S07E01.variant-02.chi.srt
Veep S07E01.eng.ass
```

The lexicographically smallest `source_id` receives the canonical name and the
rest receive stable `variant-NN` names.

**Step 2: Verify legacy tests fail for the new contract**

Run new tests plus `test_subtitles.py`. Identify legacy assertions that require
traditional/other-language discard or whole-plan blocking.

**Step 3: Implement language facts and preserve-all planning**

Replace `_select_subtitles` with per-subtitle planning. Return explicit
`language_code`, `language_profile`, `subtitle_variant`, and `reason_codes`.
Keep the source extension. Unknown language returns `keep_original` and never
blocks other subtitle or video operations.

**Step 4: Update superseded tests and run the subtitle suite**

Replace only tests that encode behavior superseded by the approved spec. Keep
all canonical naming and conflict checks that remain valid.

## Task 5: Add structured DeepSeek final-content handling

**Files:**

- Modify: `features/rename/src/telepiplex_rename/ai.py`
- Create: `features/rename/tests/test_ai_structured_output.py`
- Modify: `features/rename/tests/test_ai.py`

**Step 1: Write failing request/response tests**

Assert a DeepSeek model request includes:

```python
"response_format": {"type": "json_object"}
"thinking": {"type": "enabled"}
```

Assert `reasoning_content` is ignored, final `content` alone is parsed, and a
reasoning-only response is `ai_output_unavailable`. For `N=44`, assert the
first budget is at least 16,384. Assert empty, invalid, and `length` responses
retry exactly once; `length` doubles the bounded budget up to 32,768.

**Step 2: Implement typed completion extraction**

Create a local response record containing `content`, `finish_reason`, usage,
`reasoning_tokens`, content length, and parse status. Do not persist or log
reasoning text. Provider detection uses explicit provider id or model prefix,
not endpoint hostname.

**Step 3: Implement bounded request and retry policy**

Use:

```python
min(32768, max(16384, 4096 + 256 * unresolved_file_count))
```

for DeepSeek mapping. Retry once with a compact JSON-only repair prompt.
Anthropic-style endpoints retain prompt-only JSON enforcement and do not
receive OpenAI-specific fields.

**Step 4: Run AI tests**

Run structured-output tests and the complete existing rename AI suite. Verify
logs contain only counts/status, never reasoning text.

## Task 6: Plan one immutable resolution per file

**Files:**

- Create: `features/rename/src/telepiplex_rename/file_plan.py`
- Create: `features/rename/tests/test_file_plan.py`
- Modify: `features/rename/src/telepiplex_rename/tvdb_rename.py`

**Step 1: Write failing resolution tests**

Cover actions `no_op`, `rename_only`, `move_only`, `rename_and_move`, and
`keep_original`. Assert:

- existing target with the same provider identity is `no_op`;
- existing target with another identity creates a conflict only for that file;
- matching hashes across distinct identities are reported but never authorize
  deletion;
- two files claiming one final target remain individually preserved;
- non-media facts never enter mutation planning;
- unknown subtitle language stays original while a video resolves.

**Step 2: Implement `FileResolution` and target preflight**

Create a frozen record with source identity/path, status, work/item identity,
target path, action, and reason codes. Normalize POSIX paths before comparing.
Derive action strictly from parent and basename differences.

**Step 3: Adapt canonical naming helpers**

Reuse existing sanitization and confirmed TVDB item naming without allowing a
job-wide `allowed_targets` list to erase unrelated files. Expose pure helpers
that accept one file plus one verified item.

**Step 4: Run planner and TVDB tests**

Update only assertions that encoded whole-release conflicts or deletion.

## Task 7: Execute resolutions safely and independently

**Files:**

- Create: `features/rename/src/telepiplex_rename/file_executor.py`
- Modify: `features/rename/src/telepiplex_rename/operations.py`
- Create: `features/rename/tests/test_file_executor.py`
- Modify: `features/rename/tests/test_operations.py`

**Step 1: Write failing execution tests**

Use a stateful fake provider and assert:

- Veep Season 07 uses rename-only calls and never copy/delete;
- after a rename reaches the final path, execution stops;
- one operation failure records the observed source/current path and unrelated
  files continue;
- kept/conflicting files invoke no mutations;
- replay after each journal transition is idempotent;
- cleanup lists bottom-up, removes only freshly verified empty directories,
  and never deletes the selected root.

**Step 2: Add durable per-file journal transitions**

Extend the journal with JSON-serializable transitions keyed by pipeline
version, source id, normalized final target, stage, and observed path. Preserve
existing rollback behavior for legacy callers.

**Step 3: Implement the action executor**

Before every provider call, re-read source/target state and recompute the
current path. Use rename for same-parent basename changes. Use
`move_file_detailed` only when the normalized current parent differs from the
target parent. Accept provider `no_op` as success. Never call a file deletion
method directly.

**Step 4: Implement verified empty-directory cleanup**

Require a fully consumed fresh listing and zero children. Listing errors,
pagination uncertainty, and any child preserve the directory.

**Step 5: Run executor and operation regressions**

Record actual tests and ensure no assertions depend on mutation order across
independent files.

## Task 8: Migrate processor behavior from batch fallback to file resolutions

**Files:**

- Modify: `features/rename/src/telepiplex_rename/processor.py`
- Modify: `features/rename/tests/test_feature_processor.py`
- Create: `features/rename/tests/test_file_first_processor.py`

**Step 1: Write end-to-end processor failures**

Cover:

- restored Veep Season 07 video and subtitle sibling folders;
- two unrelated works mixed in one folder;
- one unresolved third file stays at its original path;
- Honey and Clover `S1 - 01` resolves without AI;
- one target conflict does not move a source root;
- extra videos, samples, subtitles, and non-media files are never deleted.

**Step 2: Add the file-first processor entry point**

Implement `process_file_first_media(event, confirmed_metadata_by_group=...)`.
It builds facts and evidence from one supplied snapshot, groups compatible
files, associates only confirmed metadata, plans resolutions, executes them,
and returns file-level counters and outcomes.

**Step 3: Route existing public processors through the new entry point**

Keep `process_tvdb_episode` and `process_generic_media` signatures for callers,
but remove these legacy branches:

- `_move_unmatched_to_unorganized` deletion;
- whole-source `/未整理` movement on mapping failure;
- whole-source movement on target conflict;
- unconditional cleanup of the source root.

Use `keep_original` outcomes and stable reason codes instead.

**Step 4: Replace superseded regression assertions**

Tests that intentionally required release movement, unmatched-file deletion,
subtitle discard, or partial batch claims must assert preservation and
file-local outcomes. Retain cancellation and rollback checks.

**Step 5: Run the processor suite**

Run focused file-first tests, `test_feature_processor.py`, then all rename tests.

## Task 9: Scan one complete selected root and report file-level inventory

**Files:**

- Modify: `features/rename/src/telepiplex_rename/service.py`
- Modify: `features/rename/src/telepiplex_rename/models.py`
- Modify: `features/rename/tests/test_inventory_jobs.py`
- Modify: `features/rename/tests/test_feature_processor.py`
- Create: `features/rename/tests/test_file_first_inventory.py`

**Step 1: Write failing inventory tests**

Assert one selected-root snapshot can relate sibling video/subtitle directories
and can split mixed works. Assert first-level children do not become identity
jobs. Reject a snapshot before metadata calls/writes when pagination does not
advance, a directory lacks stable identity, a cycle occurs, or the provider
reports incompleteness.

**Step 2: Implement the snapshot contract**

Make recursive traversal paginated and retain every directory and file node.
Attach `snapshot_id`, `snapshot_complete`, and `provider`. Completed-download
events use `download_root`; inventory uses the entire selected root.

**Step 3: Version inventory idempotency**

Include `file-first-v1`, `source_id`, and normalized final target in mutation
keys. Ensure terminal directory-batch jobs cannot suppress a new scan.

**Step 4: Replace first-level child orchestration**

Create one scan job and derive provisional/verified work groups after parsing.
Pause only groups awaiting search confirmation. Report:

```text
media_files_total
organized_files
canonical_no_ops
kept_unresolved
target_conflicts
failed_files
verified_work_groups
```

with per-file paths and stable reasons.

**Step 5: Run inventory, resume, cancellation, and replay suites**

Verify inventory state remains resumable and unrelated group results survive a
pending confirmation.

## Task 10: Verify downstream handoff and remove obsolete behavior

**Files:**

- Modify: `features/rename/src/telepiplex_rename/service.py`
- Modify: relevant rename integration tests under `features/rename/tests/`
- Modify: `features/rename/docs/superpowers/specs/2026-08-14-file-first-rename-pipeline-design.md`

**Step 1: Write handoff tests**

After execution, re-read canonical targets and emit one `media.organized` event
per verified external identity. Include only verified final paths. A subtitle-
only verified group may emit; unresolved/failed paths appear only as warnings.

**Step 2: Audit forbidden behavior**

Search processor/service/subtitle code for:

```text
delete_single_file
move_unmatched
未整理
discard
video_paths or subtitle_paths
```

Any remaining occurrence must be a verified empty-directory cleanup,
non-mutating wording, or an explicitly unrelated code path. Add a test for each
necessary exception.

**Step 3: Mark the design implemented**

Update the design status only after acceptance tests pass. Document any
deliberately deferred non-goal without weakening the ten acceptance criteria.

**Step 4: Run full local verification**

First run the rename and download suites, then the repository-local complete
test sequence from `AGENTS.md`:

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests

for module in download search rename sync caption; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider tests
  )
done

test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

Do not claim any suite that was not actually run. If a pre-existing unrelated
failure occurs, isolate and report it with evidence.

**Step 5: Prepare the local handoff**

List every added, modified, deleted, or renamed file and the purpose of each.
Report actual commands and results. Do not publish. Remind the user to wait for
Syncthing `Up to Date / 最新` before inspecting and releasing from
`/mnt/user/archives/life hacker/telepiplex` on Unraid.
