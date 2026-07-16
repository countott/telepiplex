# Media Candidate Photo Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend Core so media-search can safely render poster-backed candidate cards and carry the approved title-language fields through `media_metadata v1`.

**Architecture:** Keep Telegram transport and validation in Core. Feature responses remain declarative actions; Core validates HTTPS poster URLs and namespaced keyboards, renders media, and falls back to text without losing operation state. The SDK keeps schema version 1 and validates optional title-policy fields only when present.

**Tech Stack:** Python 3.12, python-telegram-bot, unittest, Telepiplex plugin SDK.

## Global Constraints

- Keep `media_metadata.schema_version == 1`.
- Existing `send_message` and `edit_message` behavior must remain unchanged.
- Only HTTPS poster URLs up to 2048 characters are accepted.
- A failed poster action must fall back once to a text message with the same keyboard.
- No media scoring or provider logic belongs in Core.

---

### Task 1: Validate Optional Canonical Title Fields

**Files:**
- Modify: `sdk/src/telepiplex_plugin_sdk/media_metadata.py`
- Test: `tests/test_core_media_metadata.py`

**Interfaces:**
- Consumes: existing `validate_media_metadata(value, require_confirmed=False)`.
- Produces: `_valid_title_policy(identity: dict) -> bool`; additive support for `official_english_title`, `romanized_original_title`, `canonical_search_title`, `original_language`, and `search_title_policy`.

- [ ] **Step 1: Write failing tests for official-English, Japanese-romaji, and invalid mixed policies**

```python
def test_v1_accepts_optional_official_english_title_policy(self):
    value = self._metadata()
    value["identity"].update({
        "official_english_title": "The Grand Budapest Hotel",
        "canonical_search_title": "The Grand Budapest Hotel",
        "search_title_policy": "official_english",
    })
    self.assertIsNotNone(validate_media_metadata(value))

def test_v1_accepts_japanese_romaji_as_english_compatibility_title(self):
    value = self._metadata()
    value["identity"].update({
        "english_title": "Shingeki no Kyojin",
        "original_language": "ja",
        "official_english_title": "Attack on Titan",
        "romanized_original_title": "Shingeki no Kyojin",
        "canonical_search_title": "Shingeki no Kyojin",
        "search_title_policy": "romanized_original",
    })
    self.assertIsNotNone(validate_media_metadata(value))

def test_v1_rejects_romaji_policy_with_english_translation_as_runtime_title(self):
    value = self._metadata()
    value["identity"].update({
        "english_title": "Attack on Titan",
        "original_language": "ja",
        "official_english_title": "Attack on Titan",
        "romanized_original_title": "Shingeki no Kyojin",
        "canonical_search_title": "Shingeki no Kyojin",
        "search_title_policy": "romanized_original",
    })
    self.assertIsNone(validate_media_metadata(value))
```

- [ ] **Step 2: Run the focused tests and verify the invalid case currently passes unexpectedly**

Run: `python3 -m unittest tests.test_core_media_metadata -v`

Expected: FAIL on the invalid policy assertion.

- [ ] **Step 3: Add strict optional-field validation**

```python
def _valid_title_policy(identity: dict) -> bool:
    policy = _text(identity.get("search_title_policy"))
    if not policy:
        return True
    canonical = _text(identity.get("canonical_search_title"))
    runtime_title = _text(identity.get("english_title"))
    if policy == "official_english":
        official = _text(identity.get("official_english_title"))
        return bool(official and canonical == official and runtime_title == official)
    if policy == "romanized_original":
        romanized = _text(identity.get("romanized_original_title"))
        return bool(
            _text(identity.get("original_language")).casefold() == "ja"
            and romanized
            and canonical == romanized
            and runtime_title == romanized
        )
    return False
```

Call `_valid_title_policy(identity)` from `validate_media_metadata` and reject invalid values.

- [ ] **Step 4: Run the focused tests**

Run: `python3 -m unittest tests.test_core_media_metadata -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sdk/src/telepiplex_plugin_sdk/media_metadata.py tests/test_core_media_metadata.py
git commit -m "feat(core): validate canonical media titles"
```

### Task 2: Render Safe Photo Response Actions

**Files:**
- Modify: `app/handlers/plugin_handler.py`
- Test: `tests/test_plugin_handler.py`

**Interfaces:**
- Consumes: Feature response actions and existing `_keyboard_markup`.
- Produces: safe `send_photo` and `edit_photo` action handling; `_photo_url(data) -> str | None | False`.

- [ ] **Step 1: Write failing action tests**

```python
async def test_feature_send_photo_action_preserves_namespaced_keyboard(self):
    route.client.request.return_value = {"actions": [{
        "kind": "send_photo",
        "text": "候选 1",
        "data": {
            "photo_url": "https://image.example/poster.jpg",
            "keyboard": [[{
                "text": "选择此项",
                "callback_data": "demo:select:1",
            }]],
        },
    }]}
    await plugin_command_dispatch(update, context, route)
    update.effective_message.reply_photo.assert_awaited_once()

async def test_feature_photo_failure_falls_back_to_text(self):
    update.effective_message.reply_photo.side_effect = RuntimeError("image")
    # dispatch the same valid send_photo response
    update.effective_message.reply_text.assert_awaited_once()

async def test_feature_rejects_non_https_photo(self):
    # return photo_url="http://image.example/poster.jpg"
    # assert the standard invalid Feature response is rendered
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python3 -m unittest tests.test_plugin_handler.PluginHandlerTest -v`

Expected: FAIL because `send_photo` is not in `_SAFE_ACTIONS`.

- [ ] **Step 3: Add URL validation and action rendering**

```python
from telegram import InputMediaPhoto

_SAFE_ACTIONS = {"send_message", "edit_message", "send_photo", "edit_photo"}

def _photo_url(data):
    if not isinstance(data, dict):
        return False
    value = str(data.get("photo_url") or "").strip()
    if not value.startswith("https://") or len(value) > 2048:
        return False
    return value
```

Allow `photo_url` alongside `keyboard` in action data. In `_render_actions`, truncate captions to 1024, call `reply_photo`/`edit_media`, and catch the media exception once to call `reply_text` with the same markup.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m unittest tests.test_plugin_handler.PluginHandlerTest -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/handlers/plugin_handler.py tests/test_plugin_handler.py
git commit -m "feat(core): render safe feature photo actions"
```

### Task 3: Render Poster-Backed Async Operations

**Files:**
- Modify: `app/handlers/plugin_handler.py`
- Modify: `app/handlers/interaction_handler.py`
- Test: `tests/test_interaction_handler.py`
- Test: `tests/test_plugin_handler.py`

**Interfaces:**
- Consumes: `OperationRecord.details["photo_url"]` and `OperationRecord.details["keyboard"]`.
- Produces: `render_operation` support for `edit_message_media` and `send_photo`, with text fallback; initial actions copy `photo_url` into operation details.

- [ ] **Step 1: Write failing operation rendering tests**

```python
async def test_render_operation_sends_candidate_photo(self):
    record.details = {"photo_url": "https://image.example/1.jpg"}
    context.application.bot.send_photo = AsyncMock(
        return_value=SimpleNamespace(message_id=55)
    )
    result = await render_operation(context.application, router, record)
    self.assertEqual(result, 55)

async def test_render_operation_photo_failure_falls_back_to_text(self):
    context.application.bot.send_photo.side_effect = RuntimeError("image")
    await render_operation(context.application, router, record)
    context.application.bot.send_message.assert_awaited_once()
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `python3 -m unittest tests.test_interaction_handler -v`

Expected: FAIL because `render_operation` ignores `photo_url`.

- [ ] **Step 3: Add media-aware operation rendering**

```python
photo_url = str(record.details.get("photo_url") or "").strip()
if photo_url.startswith("https://"):
    media = InputMediaPhoto(media=photo_url, caption=text[:1024])
    if record.message_id is not None:
        await application.bot.edit_message_media(..., media=media, reply_markup=markup)
    else:
        message = await application.bot.send_photo(
            chat_id=record.chat_id,
            photo=photo_url,
            caption=text[:1024],
            reply_markup=markup,
        )
```

On any media error, log the exception class and execute the existing text edit/send path. Extend `_with_rendered_keyboard` so the last action copies a validated `photo_url` into operation details.

- [ ] **Step 4: Run Core handler tests**

Run: `python3 -m unittest tests.test_interaction_handler tests.test_plugin_handler -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/handlers/plugin_handler.py app/handlers/interaction_handler.py tests/test_interaction_handler.py tests/test_plugin_handler.py
git commit -m "feat(core): render poster-backed operations"
```

### Task 4: Verify the Core Branch

**Files:**
- Verify only.

**Interfaces:**
- Produces: a Core branch that can be consumed by media-search without behavior regressions.

- [ ] **Step 1: Run targeted tests**

Run: `python3 -m unittest tests.test_core_media_metadata tests.test_plugin_handler tests.test_interaction_handler -v`

Expected: PASS.

- [ ] **Step 2: Run the full Core suite**

Run: `python3 -m unittest discover -s tests -t . -v`

Expected: PASS.

- [ ] **Step 3: Run syntax and whitespace checks**

Run: `python3 -m compileall -q app sdk/src tests`

Expected: exit 0.

Run: `git diff --check`

Expected: no output.
