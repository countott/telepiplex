"""Size the complete inline handoff frames before cloud file cleanup."""
import json
from telepiplex_plugin_sdk.diagnostics import outbound_diagnostic_context

RPC_FRAME_LIMIT = 1_048_576
# Covers the later Host diagnostic context and 43-byte per-Feature startup token.
# Metadata, release, operation identity, and all tree nodes are measured in full.
HOST_ENVELOPE_RESERVE = 16_384


class TreeCapacityError(RuntimeError):
    pass


def check_completion_capacity(payload, *, token='', frame_limit=RPC_FRAME_LIMIT):
    # Handoff adds its durable revision immediately before publication. Measure
    # that field up front as a signed SQLite INTEGER's maximum decimal width.
    payload = {**payload, 'operation_revision': payload.get('operation_revision', 9_223_372_036_854_775_807)}
    request_id = 'f' * 32
    diagnostics = outbound_diagnostic_context(request_id=request_id)
    envelope = {'type': 'request', 'id': request_id, 'token': str(token),
                'deadline_at': 9999999999.999999, 'diagnostics': diagnostics}
    frames = [
        {**envelope, 'method': 'event.publish', 'params': {'event_type': 'download.completed', 'payload': payload},
         'idempotency_key': f"{payload.get('job_id', '')}:completed"},
        {**envelope, 'token': 'f' * 43, 'method': 'event.deliver',
         'params': {'event_id': request_id, 'event_type': 'download.completed', 'payload': payload},
         'idempotency_key': request_id},
        {'type': 'response', 'id': request_id, 'ok': True, 'result': {'value': payload['file_tree']}},
    ]
    try:
        sizes = [len((json.dumps(frame, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')) for frame in frames]
    except (TypeError, ValueError) as exc:
        raise TreeCapacityError('download completion cannot be encoded as JSON') from exc
    ceiling = min(RPC_FRAME_LIMIT, int(frame_limit))
    if max(sizes) + HOST_ENVELOPE_RESERVE > ceiling:
        raise TreeCapacityError(f'download completion exceeds inline RPC capacity ({ceiling} bytes including {HOST_ENVELOPE_RESERVE} bytes envelope reserve)')
