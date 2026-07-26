# Search Scope Queries and Callback Keyboard Design

## Goal

Improve series release recall without broad synonym enumeration, while keeping
Prowlarr's media categories and the existing exact release gate. Also restore
the Host interaction invariant that a successfully handled Telegram callback
retires the keyboard from the message that was clicked.

## Approved search behavior

Prowlarr categories remain a coarse media filter:

- movies use the configured movie category;
- series use the configured TV category.

The release gate remains authoritative for exact title, year, media type, and
requested scope. Movie collections are not supported; movie search continues
to search one confirmed work only.

Series query variants come only from the verified contract:

- single episode: `Title S01E02`;
- whole season: `Title S02` and `Title Season 02`;
- one-season whole series: `Title S01`, `Title Season 01`, and
  `Title Complete`;
- multi-season whole series: `Title S01-S03` and `Title Complete`, where the
  range endpoints come from the first and last verified seasons.

There is no bare-title fallback and no generic release-name enumeration.
Existing bounded `(indexer, query)` concurrency, partial-failure handling,
deduplication, gating, and ranking apply to every variant.

## Textual season gate

The release gate recognizes `Season 2` and `Season 02`, including a title such
as `Complete Season 02`, as an explicit single-season pack for season 2. It
must accept that release only for a season-2 request and reject it for another
season. `Complete` does not turn an explicit one-season title into a
whole-series pack.

## Telegram callback keyboard invariant

After a Feature callback returns a valid action and that action is rendered
successfully:

- editing the clicked message replaces its old keyboard with the new stage's
  keyboard;
- editing it with no new keyboard explicitly removes the old keyboard;
- sending a new message or photo explicitly removes the keyboard from the
  clicked source message after the new message is sent successfully;
- a new stage control such as `取消` or `退出` remains visible because it is
  part of the newly rendered keyboard;
- a failed Feature request or invalid first action does not pre-emptively
  remove the old keyboard.

The Host owns this transport behavior so every Feature callback receives the
same interaction semantics.

## Verification

Automated coverage must prove:

- episode queries remain singular;
- season searches emit exactly the two approved variants;
- one-season and multi-season whole-series searches emit exactly their
  approved variants and no bare title;
- textual season releases pass only the matching season gate;
- callback actions that send a replacement message clear the clicked
  message's keyboard only after a successful send;
- callback edits without a new keyboard explicitly clear the old keyboard;
- edits with a new keyboard preserve that new keyboard.

