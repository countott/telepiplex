# Search 1.1.0 Resilience Follow-up Design

## Scope

This follow-up closes reliability gaps found during the Search 1.1.0
self-audit. Text and direct-link inputs continue to use the unified
multi-provider candidate pipeline. The work does not restore the legacy
candidate funnel or business-layer planning deadlines.

## Candidate completeness

Candidate discovery always attempts Wikipedia, Douban, and TVDB. A candidate
that binds confirmed facts from all three providers is `v1`. A candidate that
still has only one or two providers after one targeted supplement pass is
`v0`; it remains visible with its saved links, posters, unresolved providers,
and AI explanation.

Candidate validity is isolated. An incomplete candidate cannot prevent another
valid candidate from being shown. Zero provider facts returns `no_match` only
when all providers completed normally and the AI explicitly confirms no match.
If every provider failed, the result is `source_failure`. AI transport,
timeout, or schema failure is always `ai_candidate_failure`.

Selecting a frozen candidate performs exact reads of its saved URLs without a
new title search. Strict `media_metadata v1` is built after that exact-read
step. If the selected v0 candidate still cannot satisfy the downstream
metadata contract, the UI reports the missing fields and offers retry or exit.

## Numeric titles and Wikipedia

The ordinary numeric parser is retained. A user can explicitly mark a pure
numeric work title with ASCII or Chinese quotation marks, for example
`"1917"` or `“1917”`; the quotes are removed before provider lookup and the
number is not also treated as a year.

Wikipedia classification must not treat a numeric work title as its release
year. It recognizes television-animation signals and leaves media type empty
when both movie and series signals are present. HTTP 429 is reported as
`rate_limited`.

## Provider and AI resilience

TVDB movie, series-root, and episode-inventory work is isolated. A failure
while loading one series inventory preserves already returned movie and
series facts and records the failure. Discovery AI input is bounded by
deduplicating facts, limiting facts per provider, truncating free text, and
omitting full episode inventories. Inventory is loaded only for the candidate
that needs series scope verification.

## Telegram and errors

Every foreground planning failure maps to a stable Chinese message. Recoverable
source, AI, binding, fixed-link, metadata, and Prowlarr failures offer retry,
cancel, and exit controls.

Candidate photo captions are assembled within Telegram's 1024-character limit
without cutting HTML tags. The compact caption retains candidate number,
title, year, type, provider links, confidence, and a short reason.

## Metadata and queries

`media_metadata v1` retains the backward-compatible flat `external_ids` map
and adds role-aware `external_id_records` so multiple same-provider season
links cannot overwrite each other. Prowlarr titles remove provider
disambiguation suffixes such as `(2021 film)` before query generation.

## Verification

Regression coverage must include candidate-level isolation, v0 display,
all-source failure, AI failure, direct-link configuration failure, quoted
numeric titles, Wikipedia numeric-title classification, TVDB partial failure,
AI context bounds, HTML caption limits, multi-link external IDs, and query
suffix cleanup. Search and affected Host tests run before a throttled real
Wikipedia/Douban query matrix.

