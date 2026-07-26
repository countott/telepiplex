# Search Verified Clarification Identity Design

## Goal

Prevent an unconstrained title such as `想见你` from becoming a synthesized
query such as `想见你 想見你 (2022)（电视剧）`, and prevent an explicitly
requested series from sending a movie candidate to AI scoring.

## Approved behavior

- AI title hints remain retrieval hints. They never become the displayed title,
  clarification label, year, or selected work identity without source evidence.
- A generic AI-only movie/series clarification keeps the normalized user title.
  Selecting an option reruns the ordinary evidence chain with only that media
  type added.
- When source evidence has both movie and series interpretations, each option is
  built from one source-backed candidate. Its label follows the user's Chinese
  writing system when the candidates are related through verified titles, and
  includes the candidate's media type and year. This changes presentation only;
  source titles remain unchanged in evidence.
- A source-backed option carries the candidate's strongest stable identity,
  preferring TVDB, then Douban, then Wikipedia. The refined search remains
  locked to that identity while all normal source and contract checks rerun.
- Simplified and traditional Chinese source values are never converted. A
  simplified query displays `想见你`, while a traditional query displays
  `想見你`; regional translations are not mechanically converted. Related source
  candidates may be recognized through a shared verified title family such as
  `Someday or One Day` and `Someday or One Day: The Movie`.
- Explicit media type and year are deterministic hard gates before program
  scoring or AI scoring.
- Only candidates that remain selectable after deterministic thresholds may be
  sent to the AI scorecard or counted as a successful typo recovery.
- AI title hints containing a hallucinated year or media-type suffix are
  rejected. Format-control characters are removed before validation.

## Data flow

The planner first classifies the user query and collects source evidence.
Source-backed ambiguity produces candidate-specific options. The Search service
stores each option's stable identity and passes it back to the default planner
when the user chooses it. The planner then filters every source round to that
identity before constructing media metadata.

If only AI identifies ambiguity, the planner asks about movie versus series
using the original user title. It does not expose or promote the AI's corrected
title hint. After the user chooses a type, AI typo recovery may still supply
retrieval hints, but those hints must pass source verification.

Candidate qualification rejects a mismatched explicit year or media type.
Thresholding removes excluded candidates immediately, so empty recovery sets
cannot reach the AI scorecard.

## Verification

Automated tests cover:

- `想见你` with a 2019 series and a 2022 extension movie whose Chinese titles
  use different scripts and whose English titles share a verified base;
- choosing the 2019 series option preserves the stable identity and does not
  turn it into a 2022 query;
- `想见你 2022（电视剧）` cannot send the 2022 movie to AI scoring;
- AI clarification containing `想见你 想見你 (2022)` cannot become a callback
  query;
- typo correction still works after an explicit movie choice;
- normal same-title, exact-title, direct-link, multi-candidate, and candidate
  scoring behavior remains intact.

Search remains at the pending local release identity `1.0.6`.
