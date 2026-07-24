# Search locale, Prowlarr error, and typo recovery design

## Goal

Make Search prefer verified mainland-Chinese titles without converting Taiwanese
translations, use source-language artwork, preserve actionable Prowlarr failures,
remove stale controls after terminal failures, and retry typo recovery through AI
when lexical matches all fail the evidence gate.

## Approved behavior

- Never convert a Taiwanese translation into simplified characters and treat it as
  a mainland-Chinese title.
- A verified Chinese title typed by the user wins. Otherwise use an explicit
  Douban Chinese title. Untagged Wikipedia Chinese titles are not canonical
  mainland-Chinese fallbacks.
- Artwork must match the work's original language. Language-neutral artwork is an
  allowed fallback; artwork in another known language is not.
- Prowlarr search defaults to 200 seconds. Errors retain a stable kind, HTTP
  status where available, retryability, and safe original message.
- A running or terminal Prowlarr result may retain the candidate photo, but
  replaces candidate-selection details so the old search and exit buttons are
  removed. A running request exposes only the operation cancel control; a
  terminal failure exposes no controls.
- Typo repair remains an AI responsibility. It runs after lexical candidates
  exist but all fail qualification, then re-runs the normal source evidence
  providers with the AI hypotheses.
- Search source changes after released `1.0.1` use release identity `1.0.2`.

## Data flow

`EvidenceFact` retains explicit Chinese-title and poster-language provenance.
`resolve_title_policy()` accepts the request's preferred Chinese title and only
uses it when it is present in verified candidate aliases. Poster selection uses
candidate original-language evidence and refuses a known mismatched artwork
language.

The deterministic planner performs its normal evidence pass and lexical match.
If that set produces no qualified ranked score, it performs one bounded AI
hypothesis pass and reuses the same Wikipedia, Douban, and TVDB providers. The
AI output never becomes metadata directly.

The Prowlarr adapter raises `ProwlarrRequestError` with structured fields. The
service logs the safe structured error, sends its safe message to the operation
status, retains only the optional photo detail, and releases the plan.

## Verification

Regression tests cover title provenance, no Taiwanese-title conversion,
source-language artwork, 200-second configuration, structured timeout and HTTP
errors, cleared terminal controls, and the post-qualification AI recovery path.
The complete Search test suite and the root technical-identity test must pass.
