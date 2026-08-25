# Operation Message Segments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan. Subagent execution requires an explicit user request and is not enabled by this plan.

**Goal:** Replace the single mutable operation message cursor with durable, owner-scoped message segments so each stage edits exactly one Telegram message and cross-Feature handoffs create a new segment.

**Architecture:** `InteractionCoordinator` remains the durable authority. A new segment model records ownership, presentation kind, revision, callback generation, delivery state, and Telegram identity. One per-operation renderer serializes create/edit/seal work. Feature reports declare their logical segment; the Host validates ownership and performs compare-and-swap persistence before accepting later edits or callbacks.

**Tech Stack:** Python 3.12, asyncio, SQLite, python-telegram-bot, unittest/pytest, telepiplex plugin SDK.

**Spec:** `docs/superpowers/specs/2026-08-25-search-message-segments-and-minimal-media-contract-design.md`

## Global Constraints

- Work only in `/Users/young/Documents/telepiplex`; do not run Git or create `.git`/`.worktrees`.
- Use `apply_patch` for source and test edits.
- Apply TDD for every behavior change: add one failing test, run it and confirm the intended failure, add the minimum implementation, rerun it, then refactor.
- Preserve lowercase `telepiplex` in user-facing copy and keep existing technical identifiers unchanged.
- Do not publish. Version surfaces are updated only by the final integration task in the third plan.
- Run Python with:

  ```bash
  PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
  ```

---

## Task 1: Define and persist the segment state machine

**Files:**

- Create: `app/runtime/operation_segments.py`
- Modify: `app/runtime/interaction_coordinator.py`
- Test: `tests/test_interaction_coordinator.py`

**Step 1: Add failing model and schema tests**

Add tests that prove these observable contracts:

- a report from owner `search` opens `search.identity` as a `photo` segment;
- the segment survives coordinator restart with its Telegram `chat_id` and `message_id`;
- a report from a new owner cannot reuse the old owner's segment;
- a same-owner role or presentation-kind change is rejected instead of editing incompatible Telegram media or implicitly creating another message;
- legacy `operations.message_id/message_kind` is imported once as an open, read-only `legacy` segment and is never a callback target.

The expected persisted projection is literal and must include:

```python
{
    "owner_plugin_id": "search",
    "role": "identity",
    "presentation_kind": "photo",
    "state": "creating",
    "business_revision": 1,
    "rendered_revision": 0,
    "generation": 1,
    "callback_generation": 1,
}
```

**Step 2: Verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_interaction_coordinator.py -k 'segment or legacy_message_cursor'
```

Expected: failures because the segment type, table, and coordinator methods do not exist.

**Step 3: Add the minimum segment model**

Create immutable value types and validation in `operation_segments.py`:

```python
@dataclass(frozen=True)
class OperationMessageSegment:
    segment_id: str
    operation_id: str
    sequence: int
    owner_plugin_id: str
    role: str
    generation: int
    presentation_kind: str
    state: str
    business_revision: int
    rendered_revision: int
    callback_generation: int
    projection_hash: str
    chat_id: int | None
    message_id: int | None
    delivery_state: str

def validate_segment_declaration(value: object) -> tuple[str, str]: ...
def projection_hash(projection: dict) -> str: ...
```

Restrict roles to `identity`, `search`, `download`, `rename`, and `legacy`; restrict presentation kinds to `text` and `photo`.

**Step 4: Add the durable table and coordinator API**

Add `operation_message_segments` and `operations.active_segment_id`. Keep old columns read-only for migration compatibility. Implement transaction-scoped methods:

```python
def accept_segment_report(self, plugin_id: str, report: dict) -> tuple[OperationRecord, OperationMessageSegment]: ...
def get_active_segment(self, operation_id: str) -> OperationMessageSegment | None: ...
def get_segment(self, segment_id: str) -> OperationMessageSegment | None: ...
def bind_segment_message(self, segment_id: str, *, owner_plugin_id: str, generation: int, chat_id: int, message_id: int) -> OperationMessageSegment | None: ...
def mark_segment_delivery(self, segment_id: str, *, revision: int, state: str) -> OperationMessageSegment | None: ...
def seal_segment(self, plugin_id: str, operation_id: str, role: str) -> OperationMessageSegment: ...
```

`accept_segment_report` must be idempotent for the same `(owner, role, kind, business_revision, projection_hash)`, reject decreasing revisions, and reject role/kind conflicts while an active segment exists.

**Step 5: Verify GREEN and refactor**

Run the Task 1 command, then the entire coordinator file:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_interaction_coordinator.py
```

Keep SQL row conversion in one helper and state-transition validation in `operation_segments.py`.

## Task 2: Make callback validity segment-scoped

**Files:**

- Modify: `app/runtime/interaction_coordinator.py`
- Modify: `app/handlers/interaction_handler.py`
- Test: `tests/test_interaction_coordinator.py`
- Test: `tests/test_interaction_handler.py`

**Step 1: Add failing callback tests**

Cover:

- only the active open segment's `(message_id, callback_generation)` is accepted;
- the first click atomically advances the callback generation and changes the same message to busy state;
- a second click from the old keyboard is rejected before the Feature RPC;
- callbacks on sealed, superseded, or cross-owner segments are rejected;
- legacy callbacks keep their existing terminal response but cannot restart work.

**Step 2: Verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_interaction_coordinator.py tests/test_interaction_handler.py \
  -k 'callback and (segment or generation or double)'
```

**Step 3: Implement atomic callback claiming**

Add:

```python
def claim_segment_callback(
    self,
    plugin_id: str,
    operation_id: str,
    *,
    message_id: int,
    segment_generation: int,
    callback_generation: int,
) -> OperationMessageSegment | None: ...
```

The SQL update must include the old generation, `state='open'`, active segment id, and owner in its `WHERE` clause. Return `None` when the compare-and-swap loses.

**Step 4: Route the callback gate through the claim**

Update `operation_gate` and the callback handler so busy rendering is queued before invoking the Feature method. Encode `callback_generation` in new callback data while retaining a parser for current callback payloads during migration.

**Step 5: Verify GREEN**

Run both test files without `-k`.

## Task 3: Replace independent report/milestone delivery with one serialized renderer

**Files:**

- Modify: `app/handlers/interaction_handler.py`
- Modify: `app/handlers/plugin_handler.py`
- Modify: `app/runtime/poster_grid.py`
- Test: `tests/test_interaction_handler.py`
- Test: `tests/test_plugin_handler.py`
- Test: `tests/test_operation_pipeline_e2e.py`
- Test: `tests/test_pressure_operation_pipeline.py`

**Step 1: Add failing create/edit convergence tests**

Name the breaks explicitly:

- two reports arriving while the initial photo send is in flight produce one Telegram message, then edit it to the latest projection;
- an edit failure never falls back to `send_message` or `send_photo` for the same segment;
- a delivery whose message id cannot be durably bound becomes `delivery_uncertain` and is not blindly resent;
- sealing is serialized after the latest accepted render;
- Feature action rendering cannot overwrite the operation's active segment cursor;
- a Search-to-Download handoff creates one new `download` segment.

Update the existing coalescing test so its literal expectation is one send plus an edit to the latest revision, not “first and latest” as two sends.

**Step 2: Verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_interaction_handler.py tests/test_plugin_handler.py \
  tests/test_operation_pipeline_e2e.py tests/test_pressure_operation_pipeline.py \
  -k 'segment or coalesc or duplicate or handoff or delivery_uncertain'
```

**Step 3: Refactor `OperationReportSink` into the single writer**

Keep one renderer task and one lock per `operation_id`. Its queue entry contains `segment_id`, accepted `revision`, and the complete latest projection. The renderer must:

1. re-read the segment before I/O;
2. create only when no message is bound and delivery state permits creation;
3. bind the returned message id with compare-and-swap;
4. re-read the latest revision after binding;
5. edit the same message if a newer projection arrived during send;
6. never create another message after an edit error;
7. seal only after the latest revision is rendered or explicitly marked uncertain.

Fold `OperationMilestoneSink` delivery into this queue. It may continue accepting milestone intents, but it must not own a second Telegram write path.

**Step 4: Provide deterministic photo initialization**

Use `poster_grid.py` to build a local placeholder for a new `photo` segment when remote artwork is unavailable. This preserves the segment's media type from its first send and removes text-to-photo fallback sends.

**Step 5: Remove the cross-owner cursor write**

Delete the unconditional `coordinator.set_message_id(...)` path after `_render_actions`. Operation-bound actions must be projected into the declared segment renderer; standalone commands may retain their non-operation response behavior.

**Step 6: Verify GREEN**

Run all four test files. Then run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_runtime_broker.py tests/test_bot_runtime_startup.py
```

## Task 4: Expose the Host protocol in SDK and runtime broker

**Files:**

- Modify: `sdk/src/telepiplex_plugin_sdk/host_client.py`
- Modify: `app/runtime/runtime_broker.py`
- Modify: `app/runtime/interaction_coordinator.py`
- Test: `tests/test_runtime_broker.py`
- Test: `tests/test_operation_pipeline_e2e.py`

**Step 1: Add failing protocol tests**

Require `operation.report` to accept:

```python
"segment": {"role": "identity", "presentation_kind": "photo"}
```

Require `operation.seal` to seal a named role and add an authenticated read-only `operation.get` response containing owner, status, active segment, and latest handoff state. It must not expose Telegram tokens or unrelated user data.

**Step 2: Verify RED**

Run the two test files filtered to `operation_report`, `operation_seal`, and `operation_get`.

**Step 3: Implement SDK and broker methods**

Use these public signatures:

```python
async def report_operation(self, report: dict, *, segment: dict | None = None): ...
async def seal_operation_segment(self, operation_id: str, role: str): ...
async def get_operation_snapshot(self, operation_id: str): ...
```

The broker authenticates the calling Feature, passes reports to `accept_segment_report`, and restricts snapshots to the current owner or a recorded handoff participant.

**Step 4: Verify GREEN and regression**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_runtime_broker.py tests/test_operation_pipeline_e2e.py \
  tests/test_interaction_coordinator.py tests/test_interaction_handler.py
```

## Task 5: Local checkpoint

Record the exact modified files and actual test outputs. Do not change version numbers yet. Confirm:

```bash
test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

This checkpoint is complete only when the Host accepts old silent reports during migration, new interactive reports require a segment declaration, and no test path can produce two live Telegram messages for one segment.
