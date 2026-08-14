# File-first Rename Pipeline Design

## Status

The architecture and subtitle-retention policy in this specification were
approved in conversation on 2026-08-14 and implemented as `file-first-v1`.
The implementation is covered by focused provider, planner, executor,
processor, inventory, subtitle, and structured-AI-output tests.

## Goal

Make rename organize a selected storage tree by treating every media file as
an independent source of identity and as the smallest mutation unit. File and
subtitle names are primary evidence. Directory placement is weak context only
because the source tree is expected to be disorganized.

The pipeline must safely handle all of these layouts:

- videos and subtitles for the same work in different sibling directories;
- multiple unrelated works mixed in one directory;
- one work spread across many arbitrary directories;
- partially organized libraries containing canonical and non-canonical files;
- subtitle-only trees;
- anime episode markers such as `S1 - 01`;
- title variants such as `Veep` and `Veep (2012)`;
- AI output that is empty, invalid, or truncated.

## Decisions superseded by this specification

This specification supersedes the following older rename behaviors:

- a selected root's first-level child is a media-identity boundary;
- every file in one source directory must share one identity consensus;
- video presence prevents subtitle filenames from contributing identity;
- root or parent directory names may override usable filename evidence;
- mapping failure or target conflict moves a whole release to `/未整理`;
- unmatched videos or non-selected subtitles may be deleted;
- one unresolved subtitle blocks writes for every video in the source root;
- completing planned moves is sufficient evidence that the source directory
  may be deleted.

The bounded query-recovery rules in
`2026-08-13-file-tree-query-recovery-design.md` remain useful after they are
applied per file or provisional work group. Its whole-item conflict boundary
is replaced by the file-local conflict boundary defined here.

## Approaches considered

### 1. Patch the existing directory-batch pipeline

Add year normalization, `S1 - 01` parsing, DeepSeek JSON mode, and a same-path
move guard. This fixes the two observed incidents but still cannot associate
sibling directories or isolate mixed works and per-file failures.

### 2. Build file facts inside each first-level child

Replace identity consensus within a child while retaining first-level job
boundaries. This improves mixed folders but still cannot associate a subtitle
folder with a sibling video folder and continues to treat source layout as a
business boundary.

### 3. Scan the selected root once and derive groups from files

Create facts for every file in one immutable tree snapshot, resolve files
independently, then derive work and companion groups from verified evidence.
This is the selected approach. It makes the original layout an input rather
than an assumption and allows failures to remain local to one file.

## Non-goals

- rename does not download new subtitles or convert subtitle formats;
- rename does not select the active Plex subtitle stream;
- rename does not delete duplicate editions, samples, extras, or subtitles;
- rename does not invent metadata when search cannot verify a work;
- rename does not use directories as evidence that two incompatible files
  belong to one work;
- this change does not replace `media_metadata v1` or move external metadata
  verification out of search;
- this change does not publish, version, or release telepiplex from the Mac.

## Business invariants

1. The selected scan root is a traversal boundary, not an identity boundary.
2. A file is the smallest identity, planning, mutation, retry, and result unit.
3. A filename can establish work and item identity regardless of its parent.
4. Video and subtitle filenames are both first-class identity evidence.
5. Directories can fill a missing hint or break an otherwise equal tie. They
   cannot create a conflict, override usable filename evidence, or authorize a
   mutation.
6. Year is a qualifier separate from the normalized title key. `Veep` and
   `Veep (2012)` are compatible; two explicit, different years remain separate
   provisional candidates until metadata verification.
7. Grouping happens after file parsing. A group is derived state, never a
   reason to overwrite a file's stronger evidence.
8. Missing, ambiguous, rejected, or failed evidence keeps only the affected
   file in place.
9. No rename code path automatically deletes a media, subtitle, extra, or
   unknown file.
10. A storage operation whose effective source and target are identical is a
    successful no-op and must never issue copy or delete requests.
11. Directories may be removed only after a fresh storage listing proves they
    are empty. Listing uncertainty preserves the directory.
12. Downstream Plex work is emitted only for canonical media groups verified
    after execution.

## Scope and snapshots

There are two entry paths, but both produce the same snapshot contract:

- a completed download scans its `download_root` once;
- inventory scans the entire user-selected root once instead of converting
  each first-level child into a separate job.

The scan is recursive and paginated. A snapshot is rejected before metadata
or storage writes when pagination does not advance, a directory has no stable
identity, a cycle is found, or a provider declares the tree incomplete.

Every node in the snapshot is retained, including non-media files and
directories. Planning filters media nodes but cleanup decisions use the full
live tree. The pipeline does not infer that ignored nodes disappeared.

Each file receives a stable `source_id`:

1. provider `file_id` when available;
2. otherwise a hash of provider identity plus normalized absolute path.

Inventory job and mutation idempotency keys include pipeline version
`file-first-v1`, `source_id`, and the normalized final target. This prevents
old directory-batch terminal jobs from suppressing a new file-first scan.

## Data model

### `FileFact`

An immutable local description of one scanned file:

```text
source_id
provider
absolute_path
relative_path
basename
parent_parts
extension
size
sha1                 optional
media_kind           video | subtitle | other_media | non_media
snapshot_id
```

### `ParsedFileEvidence`

Deterministic evidence derived from the file itself:

```text
source_id
title_candidates[]
title_key
year_hint
season_number        optional
episode_number       optional
absolute_episode     optional
content_role         main | subtitle | sample | trailer | extra | unknown
subtitle_language    ISO 639-2/B code or unknown
subtitle_variant     simplified | traditional | bilingual | general | unknown
confidence           high | medium | low
evidence[]
directory_hints[]
```

`title_key` normalizes Unicode, case, separator differences, release noise,
and a trailing parenthesized year. The extracted year is retained separately.
The original filename and every derived value remain available for audit.

### `FileResolution`

The decision record for one file:

```text
source_id
status                resolved | ambiguous | unsupported | failed
work_identity         verified media_metadata identity or empty
item_identity         season, episode, role, and language facts
target_path           present only when resolved
action                no_op | rename_only | move_only | rename_and_move |
                      keep_original
reason_codes[]
```

### Derived groups

A `ProvisionalWorkGroup` deduplicates metadata lookup. It is formed from
compatible filename-derived title keys, not from folders. Two candidates are
compatible when their title keys match and their years match or at least one
year is absent. Two different explicit years are separate groups.

After search confirms metadata, files sharing the same external identity form
a `VerifiedWorkGroup`. Chinese and English aliases may converge only after
they resolve to the same verified external identity.

A `CompanionGroup` relates a video to sidecar subtitles with the same verified
work and item identity. It improves target naming and reporting but does not
turn execution back into an all-or-nothing directory batch.

## Evidence precedence

Evidence is applied deterministically in this order:

1. file basename title, year, season, episode, language, and role markers;
2. exact source hints already present in confirmed `media_metadata v1`;
3. another file's verified identity when both files independently expose the
   same item marker and their title evidence is compatible;
4. release title supplied by the download event;
5. parent directory segments, from nearest to farthest;
6. selected root name.

Levels 4-6 are weak evidence. They may fill a missing query or choose between
otherwise equal candidates. They cannot override levels 1-3, introduce an
identity conflict, or make an unsupported file safe to mutate.

When video and subtitle filenames disagree, both remain independent file
facts. The video does not erase subtitle evidence and the subtitle does not
veto the video. Search may resolve them into different verified work groups.

## Deterministic parsing

Episode parsing covers at least:

- `S01E01`, chained and ranged markers;
- `1x02`;
- Chinese season and episode markers;
- `S1 - 01` and `Season 1 - 01`;
- `E01`, `EP01`, `- 01`, and bracketed absolute numbers;
- a bare episode number only when another strong file-level fact supplies an
  unambiguous season.

A parent season directory may fill a missing season only after the filename
has independently provided title or episode evidence. A directory alone never
turns an opaque file into a resolved episode.

## Metadata resolution

Deterministic file parsing happens for the entire snapshot before external
lookups. Compatible provisional groups share a bounded metadata query, so 65
files for one work do not cause 65 search requests.

Search remains the sole authority that validates external metadata and emits
confirmed `media_metadata v1`. Confirmation is attached to the verified work
group and then to each compatible file. It is never applied to every file in
the source directory by position.

When search requires user confirmation, the inventory session pauses only the
affected provisional group. Already resolved groups and unrelated ambiguous
files retain their file-level state and are not moved.

## Subtitle policy

Every scanned subtitle remains a first-class `FileFact` and is preserved.
Traditional Chinese, English, another known language, an unknown language,
and a second copy of the same language are not discard reasons.

When work and item identity are resolved, subtitle targets use the canonical
video stem and a Plex-compatible ISO 639-2/B language code. telepiplex uses
the three-letter form consistently with its existing `.chi` target contract:

```text
Veep S07E01.chi.srt
Veep S07E01.eng.ass
```

The canonical target name retains only the language suffix and the source
file's real format extension. Source markers such as `forced`, `sdh`, and `cc`
are removed. Simplified and traditional Chinese remain separately represented
in `FileResolution` even when both use Plex's general Chinese language code in
the filename. The collision-only `variant-NN` discriminator below is the sole
additional suffix allowed.

For multiple subtitles that would otherwise have the same target tuple of
episode, language, and extension, the lexicographically smallest
`source_id` receives the canonical name. Additional copies receive a stable
`variant-NN` discriminator before the language component, for example:

```text
Veep S07E01.variant-02.chi.srt
```

The variant is deterministic for one immutable snapshot. Because Plex only
documents the canonical language-code form, the result records
`warning:subtitle_variant_detection_not_guaranteed` for additional variants.
Preservation takes priority over silently discarding a duplicate.

If title, episode, or language cannot be resolved safely, the subtitle uses
`keep_original`. Unknown language never blocks a resolved video or another
resolved subtitle.

## AI structured-output contract

AI is a bounded long-tail mapper for unresolved file evidence. It cannot
confirm a media identity, authorize deletion, override confirmed metadata, or
change a file whose deterministic evidence is already sufficient.

All rename JSON tasks use a structured request path:

- the prompt explicitly requests JSON and includes the exact response shape;
- OpenAI-compatible endpoints receive
  `response_format={"type":"json_object"}`;
- DeepSeek models receive `thinking={"type":"enabled"}` so difficult file
  mappings can benefit from reasoning before the final JSON answer;
- Anthropic-style `messages` endpoints retain prompt-enforced JSON and do not
  receive unsupported OpenAI request fields;
- only final `content` is parsed;
- `reasoning_content` is neither parsed, persisted, nor logged;
- `finish_reason`, token usage, reasoning-token count when supplied by the
  provider, content length, and parse status are logged;
- `finish_reason=length`, empty content, or invalid JSON is not accepted.

A model is treated as DeepSeek when its configured model identifier starts
with `deepseek-` or an explicit provider identifier is `deepseek`. Endpoint
hostnames alone do not select provider-specific payload fields.

DeepSeek's `max_tokens` covers both reasoning and the final answer, so the
episode-mapping budget must not remain at the current fixed value of 4,096.
For `N` unresolved files the default first-attempt budget is:

```text
min(32768, max(16384, 4096 + 256 * N))
```

The formula is bounded and configurable, and AI still receives only the
affected group's compact facts. Deterministic cases do not call AI.

An empty or invalid response gets one retry with the same bounded facts and a
compact JSON-only repair prompt. When `finish_reason=length`, the retry doubles
the first budget up to 32,768 in addition to compacting the prompt. A response
that contains `reasoning_content` but no final `content` is incomplete and is
never treated as a mapping. A second failure returns a typed
`ai_output_unavailable` result and leaves only the affected files in place.
There is no whole-root AI fallback and no unbounded retry.

AI output must reference exact `source_id` or relative paths in its input.
Every proposed work, season, episode, and target is independently validated
against confirmed metadata and deterministic source evidence. Subtitle
language is locked to deterministic filename parsing; AI cannot supply or
override it.

## Planning

Planning creates one `FileResolution` per media file. Resolved files receive a
fully normalized final target before any writes begin. Other files receive
`keep_original` with stable reason codes.

Target preflight runs per file across the whole snapshot:

- two planned files may not silently claim the same final path;
- an existing target with the same provider file identity is `no_op`;
- an existing target with a different identity is `target_conflict`, and only
  that source file remains in place;
- matching hashes on two different file identities are reported as duplicates
  but do not authorize source deletion;
- non-media files never enter mutation planning.

Movie folders containing multiple videos no longer select one main video and
delete the rest. Each video is resolved independently. Multiple editions that
would claim one canonical name become file-local target conflicts until a
separate edition-naming contract resolves them.

## Execution and storage safety

Execution handles four explicit operation forms:

1. `no_op`: effective source and final paths are identical;
2. `rename_only`: the parent directory is already correct;
3. `move_only`: the basename is already correct;
4. `rename_and_move`: both components differ.

The executor normalizes source, target directory, and final target before each
provider call. It recalculates the current path after rename or move. If the
current path becomes the final path, execution stops successfully without a
copy or delete request.

The 115 storage provider independently rejects a same-path move as a no-op.
This defense remains mandatory even when the rename planner has already made
the same decision.

Every file operation writes a durable journal transition before and after a
provider mutation. Replay reads current provider state and resumes from the
last verified transition. A failure stops that file, records its observed
location, and continues with unrelated files. The result never claims that a
failed file reached its target.

Source-directory cleanup is bottom-up and optional. A directory is deleted
only when a fresh, fully paginated listing returns zero children. Any listing
error, non-media child, unresolved file, or retained duplicate preserves the
directory. The selected root is never deleted by rename.

## Result and downstream contract

Inventory progress and the final summary report file outcomes rather than
first-level child outcomes:

```text
media_files_total
organized_files
canonical_no_ops
kept_unresolved
target_conflicts
failed_files
verified_work_groups
```

Each unresolved or failed file includes its path and stable reason code. The
summary must distinguish safe preservation from an organization failure.

After execution, rename re-reads successful canonical paths and groups them by
verified external identity. It publishes one `media.organized` event per
verified work group that has a canonical video present or a successfully
placed subtitle-only result. Event items include only verified final paths.
Kept or failed source files are reported as warnings and are not presented to
Plex as organized.

The end-to-end success boundary remains:

```text
file evidence -> search confirmation -> file resolution -> safe mutation
-> verified canonical paths -> media.organized -> Plex enqueue
```

## Migration boundaries

The implementation replaces directory-batch orchestration rather than adding
a second competing path. The following current responsibilities are split:

- tree traversal produces one snapshot for the selected root;
- filename parsing produces `ParsedFileEvidence` without storage writes;
- grouping and metadata resolution consume parsed evidence;
- planning produces immutable `FileResolution` records;
- execution consumes only planned resolutions;
- directory cleanup consumes only a refreshed post-execution tree.

Existing `media_metadata v1`, target naming helpers, target sanitization,
storage proxy journaling, search capability calls, and Plex handoff contracts
are reused where their boundaries remain valid.

Tests that currently require whole-release movement, unmatched-file deletion,
subtitle discard, or partial batch success are replaced because they encode
superseded behavior. Compatibility is retained for completed-download events
that already provide a single download-root file tree.

## Failure handling

| Failure | Required result |
|---|---|
| incomplete storage snapshot | no metadata calls or writes |
| one file has conflicting filename evidence | keep that file only |
| multiple files identify different works | create separate groups |
| directory disagrees with filename | keep filename evidence |
| metadata unresolved or rejected | keep affected files |
| user confirmation pending | pause affected group only |
| AI empty, invalid, or truncated twice | keep affected files |
| subtitle language unknown | keep subtitle; continue videos |
| target occupied by another file | keep conflicting source file |
| source already equals target | no-op; never copy or delete |
| one provider operation fails | stop that file; continue unrelated files |
| cleanup listing fails | preserve directory |
| Plex event publication fails | preserve completed file results and report handoff failure |

No failure in this table moves an entire root to `/未整理` or deletes an
unresolved file.

## Testing strategy

### File evidence tests

- `Veep` and `Veep (2012)` produce compatible title keys and a separate year
  hint;
- video and subtitle names both contribute independent work evidence;
- a wrong directory cannot override a usable filename;
- two works in one folder produce two provisional groups;
- one work across sibling folders converges after metadata verification;
- `S1 - 01` and the existing marker formats produce correct item identities.

### Subtitle tests

- subtitle-only trees resolve without a video;
- videos and subtitles in sibling directories form companion groups;
- simplified, traditional, bilingual, English, and other known-language
  subtitles are preserved and receive language-suffixed names;
- source markers such as `forced`, `sdh`, and `cc` are not copied into target
  names;
- same-language duplicates receive deterministic variant names;
- an unknown subtitle remains in place without blocking video writes;
- no subtitle plan contains a discard operation.

### AI tests

- DeepSeek structured requests set JSON mode and explicitly enable thinking;
- parsers ignore `reasoning_content` and consume only final `content`;
- a 44-file mapping receives at least a 16,384-token output budget;
- empty content, invalid JSON, and `finish_reason=length` retry once;
- a second failure returns `ai_output_unavailable` without storage writes;
- AI cannot reference an absent file or override confirmed metadata.

### Storage and execution tests

- the restored Veep Season 07 layout performs rename-only operations;
- planner and provider both reject same-path move copy/delete calls;
- one failed file does not stop unrelated work groups;
- target conflicts preserve the source and do not move its parent;
- unmatched videos, subtitles, samples, extras, and non-media files are never
  deleted;
- cleanup removes only freshly verified empty directories;
- replay after each journal transition is idempotent.

### Integration tests

- a selected root containing sibling Veep video and subtitle directories
  resolves, renames, and emits one verified work event;
- Honey and Clover `S1 - 01` files resolve deterministically without AI;
- an AI-only long-tail group survives one empty DeepSeek response and succeeds
  on the single retry;
- two unrelated works mixed in one source folder organize independently;
- an unresolved third file stays at its original path;
- the full chain reaches `media.organized` and Plex enqueue only with verified
  canonical paths.

### Scale and regression tests

- snapshots with 111, 1,000, and 10,000 files remain locally bounded for
  search and AI context;
- provisional metadata queries are deduplicated by compatible title and year;
- current movie, series, inventory, cancellation, replay, and downstream event
  suites are updated to assert file-level results;
- rename and download Feature suites pass independently before the complete
  telepiplex local suite is run.

## Acceptance criteria

The implementation is complete only when:

1. Veep video and subtitle siblings are handled as one verified work without
   treating `Veep` and `Veep (2012)` as conflicting identities.
2. Veep Season 07 performs no same-directory copy or delete request.
3. Honey and Clover `S1 - 01` maps without AI dependency.
4. DeepSeek structured mapping keeps thinking enabled, parses only final
   content, provides a bounded reasoning-plus-answer budget, and safely retries
   one empty or truncated result.
5. Mixed source folders can produce multiple independently successful work
   groups.
6. Every unresolved, duplicate, conflicting, or failed file remains at a
   verified path.
7. No rename code path automatically deletes a media or subtitle file.
8. All recognized subtitle variants are preserved and receive collision-safe
   target names.
9. A failure in one file does not move or block its parent directory or an
   unrelated file.
10. Only verified canonical final paths enter the Plex handoff.

## Local delivery boundary

Implementation and validation occur only in the private Mac workspace. No Git
command, branch, tag, pull request, or publication action is part of this
design. After implementation and local verification, changed files and actual
test results are reported, then the user waits for Syncthing to show
`Up to Date / 最新` before performing any Unraid Git or release action.
