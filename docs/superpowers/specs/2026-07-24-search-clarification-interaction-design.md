# Search Clarification Interaction Design

## Goal

When a media query has several meaningful interpretations, Search must ask
the user to choose a verified dimension instead of collapsing the candidates
to an empty result or selecting one automatically.

## Decisions

- Raw provider result counts never trigger clarification.
- Movie and series facts are always separate entities, even when a provider
  reuses the same numeric identifier across entity types.
- A normal qualified multi-candidate result continues to use the existing
  candidate browser.
- AI `needs_clarification` is a valid structured outcome. It is not candidate
  evidence and cannot select a work, but its title hints may define bounded
  movie/series clarification options.
- If the user already specified movie or series, that explicit constraint
  resolves the corresponding AI ambiguity and the AI title hints go back
  through the normal source-validation chain.
- A clarification choice restarts planning inside the same Search operation.
  It locks only the chosen query dimension; it does not lower source,
  identity, title, year, or scope gates.
- At most six evidence-derived options are shown. This is a presentation cap,
  not a rejection threshold. A movie/series fallback has exactly two options.

## Planner Contract

`infer_search_hypotheses_with_ai()` returns normalized results for both
`parsed` and `needs_clarification`. A clarification result carries validated
title hints and its reason, and may also carry ordinary hypotheses for use
after an explicit user constraint.

`build_confirmable_search_plan()` may return either:

1. the existing ranked candidate plan; or
2. a plan with `status: needs_clarification` and a `clarification` object:
   `reason` plus bounded options containing `label` and `query`.

The planner creates clarification options only from stable dimensions:
media type and, when available, a source-backed year. An option is a new
targeted query, not a selectable media identity.

## Service Interaction

The service stores clarification state in the existing request-scoped
`plans` map and reports operation stage `clarification`.

The Telegram keyboard contains one row per clarification option and one
`退出` row. Clicking an option:

1. releases the old clarification plan;
2. keeps the same operation ID;
3. changes the operation back to `planning`;
4. edits the existing message to show the selected refinement;
5. runs the normal planner and source gates again.

Expired or foreign callbacks retain the existing closed behavior.

## Failure Handling

- Provider timeout or insufficient independent evidence remains visible after
  a clarified retry.
- An invalid AI response remains unavailable rather than becoming a choice.
- Empty or malformed clarification options fail closed.
- Non-interactive metadata capability never returns a clarification as a
  resolved identity.

## Verification

Tests must prove:

- equal TVDB numeric IDs do not merge movie and series facts;
- `needs_clarification` survives AI response validation;
- explicit movie/series input consumes AI hints instead of asking again;
- unresolved AI ambiguity returns bounded movie/series options;
- the service renders those options and restarts within the same operation;
- ordinary multi-candidate browsing and all existing source gates still work.

