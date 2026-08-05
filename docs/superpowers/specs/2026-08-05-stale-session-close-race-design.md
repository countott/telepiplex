# Stale Session Close Race Design

## Goal

Prevent an older Feature response from cancelling a newer recovery prompt when
both updates belong to the same telepiplex operation.

## Root cause

`handle_feature_result()` first submits `result["operation"]` to the
`InteractionCoordinator`. The coordinator correctly ignores stale revisions
and returns the newest stored record. Later, the handler processes
`session.state=close` and currently cancels any active `awaiting_input`
operation owned by the same Feature.

For an asynchronous search plan, the initial response can contain revision 1
in `running` state and `session.close`, while the background task has already
reported revision 2 in `awaiting_input` state. The session branch then cancels
revision 2 even though no exit callback occurred.

## Approved behavior

- Continue dropping the Feature free-text session whenever `session.close` is
  returned.
- If the result contains an operation snapshot for the same operation and that
  snapshot's revision is older than the active `awaiting_input` record, do not
  cancel the active operation.
- Do not render actions from that stale result over the newer operation. Render
  the coordinator's newest record so its status text and controls remain
  authoritative.
- Preserve the existing behavior for an explicit session-closing result that
  carries no operation: it releases the active waiting operation.
- Preserve current handling for current-revision results, terminal operations,
  other Features, and non-waiting operations.

## Implementation boundary

The change belongs in Core Host result handling. Search 1.5.1, callback
payloads, persisted operation schema, Feature protocol, and user-visible copy
do not change.

The Core Host version advances from `v3.4.11-host` to `v3.4.12-host` so the
immutable release containing the fix can be published and upgraded normally.

## Testing

Add a Host regression test that stores revision 2 as `awaiting_input`, then
passes a revision 1 `running` result with `session.close` through
`handle_feature_result()`. The active operation must remain at revision 2, the
revision 1 action must not be sent, and the revision 2 status must be rendered.

Keep the existing explicit-close test to prove that a close result without an
operation still cancels an active waiting operation.

Update the Core runtime version contract test to expect `v3.4.12-host`.

Run the targeted Host tests, the complete Core suite, and all five Feature
suites required by the local handoff contract.

## Delivery constraints

- Do not run Git or create Git metadata on the Mac.
- Do not publish from the Mac.
- Deliver through Syncthing after local verification.
