# Search Evidence Clarification Guard Design

## Goal

Prevent Search from silently choosing a movie or series when the user supplied
an unconstrained title and verified source results still contain both media
types, even if the AI incorrectly returns `parsed`.

## Approved behavior

- AI remains responsible for typo repair and natural-language ambiguity.
- The intent prompt must make movie/series ambiguity a mandatory
  `needs_clarification` result unless the user explicitly supplied a media type,
  series scope, season, episode, year that uniquely resolves the work, or a
  stable direct identity.
- AI title hints remain unverified queries and always go back through the normal
  Wikipedia, Douban, and TVDB evidence chain.
- Source evidence is the deterministic safety net. When the bounded title
  candidates contain both a movie and a series and the request has no explicit
  media type, Search returns the existing movie/series clarification plan even
  if AI returned `parsed`.
- Explicit movie, series, season, episode, or direct-identity input bypasses the
  media-type clarification guard.
- Candidate clustering must never contain both known movie and series facts,
  including when an untyped fact links to each type through different stable
  IDs.
- AI candidate scoring is not called with an empty candidate list.
- No typo dictionary, Taiwanese-title conversion, Prowlarr behavior, evidence
  threshold, or candidate presentation limit changes in this patch.

## Data flow

The prompt asks AI to distinguish one resolved interpretation from multiple
plausible works and includes concrete examples. The parser keeps the existing
strict JSON contract.

After each source-backed candidate construction point, the planner checks only
the already bounded candidates that correspond to the current query or verified
AI title hints. If their known media types include both `movie` and `series`,
and the user did not explicitly constrain the type, it creates the existing
two-option clarification plan. A user choice restarts the normal evidence
pipeline.

The entity graph checks a new fact against the known media types of the entire
candidate cluster before considering pairwise stable-ID or title matches. This
blocks untyped transitive bridges without weakening valid same-type merges.

## Verification

Automated cases cover:

- `康斯坦汀`: AI incorrectly returns `parsed`, corrected source evidence contains
  a 2005 movie and a 2014 series, and Search must ask.
- `康斯坦丁`: exact source evidence contains both media types and Search must ask
  without invoking typo AI.
- `康斯坦汀（电影）`: the explicit type continues to the verified movie.
- `想见你`: a same-title movie and series remain separate and require
  clarification.
- `布达佩斯大饭店`: a single verified movie proceeds normally.
- an untyped cross-ID fact cannot bridge a movie and series cluster.
- an empty ranked set never reaches the AI scorecard.
- the focused real-title matrix is repeated to expose order-dependent or
  state-leak regressions.

Search release identity becomes `1.0.4`.
