# Search Wikipedia Admission and media_metadata v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan. Subagent execution requires an explicit user request and is not enabled by this plan.

**Goal:** Preserve the user's search query, let Wikipedia search ranking provide recall, structurally admit useful work candidates, and publish only the minimal confirmed `media_metadata v2` contract.

**Architecture:** Search keeps rich provider evidence private while identifying a work. Wikipedia returns ranked pages; Search applies disambiguation, media-type, relation, and conflict gates to those results without constructing alias queries. At confirmation, a dedicated projector freezes the selected identity into v2, and every downstream payload uses that frozen value unchanged.

**Tech Stack:** Python 3.12, asyncio, MediaWiki API adapter, telepiplex Search Feature, shared SDK validation, unittest/pytest.

**Spec:** `docs/superpowers/specs/2026-08-25-search-message-segments-and-minimal-media-contract-design.md`

## Global Constraints

- Follow the Mac-only, no-Git, no-publish constraints from the first plan.
- Do not add aliases, punctuation variants, semantic suffixes, or rewritten seed queries. Apart from transport whitespace cleanup, send the user's text to Wikipedia unchanged.
- Do not make exact title equality the candidate admission gate. It may influence rank and unique auto-confirm only.
- Rich v1 fields remain private Search working data; they do not cross the Feature boundary after v2 is enabled.
- Apply TDD and use the same bundled Python runtime.

---

## Task 1: Make Wikipedia search ranking the recall source

**Files:**

- Modify: `features/search/src/telepiplex_search/adapters/wikipedia.py`
- Modify: `features/search/src/telepiplex_search/work_discovery.py`
- Test: `features/search/tests/test_wikipedia_adapter.py`
- Test: `features/search/tests/test_work_discovery.py`

**Step 1: Add failing query-preservation tests**

Add literal request-capture tests proving:

- `死神 千年血战` is sent as `gsrsearch=死神 千年血战`;
- no extra requests contain `死神：千年血战篇`, `篇`, colon substitutions, or concatenated text;
- `gsrlimit` is 10;
- Chinese Wikipedia is attempted first;
- English Wikipedia is attempted only when the Chinese result set yields zero structurally eligible works.

**Step 2: Verify RED**

```bash
cd features/search
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_wikipedia_adapter.py tests/test_work_discovery.py \
  -k 'query or spaced or language_fallback or limit'
```

**Step 3: Separate retrieval from structural admission**

Make the adapter return ranked page evidence for one requested language at a time. In `work_discovery.py`, add a pure admission function:

```python
def admit_ranked_work_candidates(
    pages: list[dict],
    *,
    requested_scope: dict,
) -> list[dict]: ...
```

Reject disambiguation/list/category pages, non-media entities, impossible media-type relations, and hard year conflicts. Preserve `search_rank`, `page_id`, canonical title, QID, and structural reason codes.

**Step 4: Remove exact-title seed gating**

Delete `exact_seed_qids` as a prerequisite for candidate traversal. Traverse only from admitted ranked works and their verified series/season/work relations. Keep exact title and normalized title equality as ranking signals.

**Step 5: Verify GREEN**

Run both test files in full. Add a mutation check test where the first ranked result is structurally wrong and the second is the correct work; the correct result must still be offered.

## Task 2: Introduce the shared minimal v2 validator and identity builder

**Files:**

- Create: `sdk/src/telepiplex_plugin_sdk/media_metadata_v2.py`
- Modify: `sdk/src/telepiplex_plugin_sdk/media_metadata.py`
- Modify: `sdk/src/telepiplex_plugin_sdk/__init__.py`
- Create: `tests/test_media_metadata_v2.py`

**Step 1: Add failing contract tests**

Use hand-written valid contracts for movie, whole series, season, and episode scopes. Assert the accepted public shape is exactly:

```python
{
    "schema_version": 2,
    "metadata_id": "...",
    "confirmed": True,
    "identity": {
        "primary_ref": {"provider": "wikidata", "id": "Q123"},
        "provider_refs": {"wikidata": "Q123", "tmdb_tv": "456"},
        "media_type": "series",
        "title_zh": "死神：千年血战篇",
        "title_original": "BLEACH 千年血戦篇",
        "year": 2022,
    },
    "scope": {"kind": "season", "season_number": 1},
    "placement": {"category_kind": "animated_series"},
}
```

Reject unknown provider-ref keys, blank primary ids, primary refs absent from `provider_refs`, invalid category kinds, impossible scope coordinates, unconfirmed contracts, and extra rich fields such as `countries`, `genres`, `summary`, `poster_url`, `cast`, `items`, `evidence`, and `inventory`.

**Step 2: Verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_media_metadata_v2.py
```

**Step 3: Implement strict v2 validation**

Expose:

```python
def build_media_metadata_v2_id(contract_without_id: dict) -> str: ...
def validate_media_metadata_v2(value: object, *, require_confirmed: bool = True) -> dict | None: ...
def validate_media_metadata_v2_detailed(value: object, *, require_confirmed: bool = True) -> tuple[dict | None, dict | None]: ...
def attach_media_metadata_v2(metadata: dict | None, value: dict) -> dict: ...
def extract_confirmed_media_metadata_v2(metadata: dict | None) -> dict | None: ...
```

Build `metadata_id` from the canonical JSON of identity, scope, and placement; exclude volatile provider display text. Return deep copies so callers cannot mutate the frozen contract through shared references.

**Step 4: Keep v1 API explicitly legacy**

Do not silently reinterpret current `validate_media_metadata` as v2. Re-export the new explicit v2 names while keeping v1 validators for durable-job migration in Rename.

**Step 5: Verify GREEN**

Run `tests/test_media_metadata_v2.py` and existing SDK metadata tests.

## Task 3: Project the confirmed Search identity to v2

**Files:**

- Create: `features/search/src/telepiplex_search/media_metadata_v2.py`
- Modify: `features/search/src/telepiplex_search/search_plan.py`
- Modify: `features/search/src/telepiplex_search/direct_plan.py`
- Test: `features/search/tests/test_media_metadata_v2.py`
- Test: `features/search/tests/test_search_plan.py`

**Step 1: Add failing projection tests**

Prove that a rich internal candidate projects to the minimal exact v2 shape and that:

- `primary_ref` is one verified stable identity;
- all included `provider_refs` were verified for the same work;
- provider ids missing verification are omitted;
- titles, year, scope, and `category_kind` are sufficient for downstream naming;
- no v1 evidence, poster, genres, countries, items, or inventory escapes;
- confirmation returns a new value and does not mutate the internal candidate.

**Step 2: Verify RED**

Run the two Search test files filtered to `v2` and `projection`.

**Step 3: Implement the projector**

Use a single public function:

```python
def project_confirmed_media_metadata_v2(candidate: dict, *, requested_scope: dict) -> dict: ...
```

Select `primary_ref` by evidence quality, not a hard-coded “always Wikidata” assumption. If more than one provider id is preserved, verify that each maps to the frozen work before adding it to `provider_refs`.

**Step 4: Update the confirmation boundary**

Keep candidate cards and provider enrichment on the private rich model. Change the final confirmation path so the durable resolution and public result contain v2. Any presentation text needed after confirmation must be computed before projection or from v2 fields.

**Step 5: Verify GREEN**

Run both files in full plus `features/search/tests/test_metadata_resolutions.py`.

## Task 4: Make Search use the approved two-segment topology

**Files:**

- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/identity_presentation.py`
- Modify: `features/search/src/telepiplex_search/candidate_preview.py`
- Test: `features/search/tests/test_feature_service.py`
- Test: `features/search/tests/test_identity_presentation.py`

**Step 1: Add failing report-sequence tests**

Capture the real reports sent to the Host and assert:

- identity progress, candidates, callback-busy confirmation, and final identity all declare `{"role": "identity", "presentation_kind": "photo"}`;
- Prowlarr progress/results/selection declare `{"role": "search", "presentation_kind": "text"}`;
- candidate confirmation emits one new revision, not a second Telegram action message;
- Prowlarr starts only after the identity segment is sealed;
- no report omits the segment declaration when it renders UI.

**Step 2: Verify RED**

Run `test_feature_service.py` filtered to `segment`, `identity`, `prowlarr`, and `confirmation`.

**Step 3: Declare segments on every UI report**

Centralize report construction helpers in `service.py` so callers cannot accidentally choose a different kind for the same role. `operation.milestone` should request sealing only; it must not send an independent final Telegram message.

**Step 4: Eliminate the second candidate report caused by poster enrichment**

Remove `_start_candidate_poster_enrichment`. Await `_supplement_candidate_posters` inside the candidate preparation budget before accepting the candidate revision. On timeout, use the Host's deterministic placeholder and keep the single accepted revision; do not issue a no-op refresh later.

**Step 5: Verify GREEN**

Run both files in full. Confirm a fixture with no poster still creates one identity segment.

## Task 5: Remove `naming_metadata` and hand off v2 unchanged

**Files:**

- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/live_pipeline_audit.py`
- Test: `features/search/tests/test_feature_service.py`
- Test: `features/search/tests/test_live_pipeline_audit.py`
- Test: `tests/test_operation_pipeline_e2e.py`

**Step 1: Add failing boundary tests**

Assert the resolved result, Prowlarr selection state, `download.submit` payload, and handoff receipt all contain byte-for-byte equivalent v2 values after JSON normalization. Assert `naming_metadata` is absent.

**Step 2: Verify RED**

Run the three files filtered to `media_metadata_v2`, `download_submit`, and `naming_metadata`.

**Step 3: Update outbound payload builders**

Read all naming input from v2 identity/scope/placement. Pass a deep copy of the frozen v2 contract and do not enrich it with release, torrent, or Prowlarr result data.

**Step 4: Update the live audit**

Keep rich candidate checks private, then audit the projected v2 public boundary separately. A successful live audit must demonstrate query preservation, candidate admission, v2 confirmation, and exact downstream propagation.

**Step 5: Verify GREEN and checkpoint**

Run all Search tests:

```bash
cd features/search
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests
```

Record exact files/results and confirm no version bump or publish has occurred.
