import json
import pytest
from telepiplex_download.transport_capacity import (
    check_completion_capacity, TreeCapacityError, RPC_FRAME_LIMIT,
)


def payload(text):
    return {'job_id': 'job', 'file_tree': [{'path': '/下载/' + text, 'name': text}],
            'release': {'title': '片源' * 100}, 'media_metadata': {'evidence': {'title': text}}}


def test_long_chinese_paths_are_measured_as_utf8_in_full_message():
    value = payload('中' * 116000)
    assert len(json.dumps(value, ensure_ascii=False)) < RPC_FRAME_LIMIT
    with pytest.raises(TreeCapacityError):
        check_completion_capacity(value)


def test_full_event_envelope_not_just_tree_must_fit():
    value = {'job_id': 'j' * 1000, 'file_tree': [], 'release': {'title': 'a' * (RPC_FRAME_LIMIT - 17000)}}
    assert len(json.dumps(value).encode()) < RPC_FRAME_LIMIT
    with pytest.raises(TreeCapacityError):
        check_completion_capacity(value)


def test_safe_side_of_capacity_boundary_and_first_rejected_byte():
    # Find the byte boundary using complete candidate events, then independently
    # exercise a multibyte name at that boundary (not character-count sizing).
    low, high = 0, RPC_FRAME_LIMIT
    while low < high:
        middle = (low + high + 1) // 2
        try:
            check_completion_capacity({'job_id': 'j', 'file_tree': [], 'release': {'title': 'a' * middle}})
        except TreeCapacityError:
            high = middle - 1
        else:
            low = middle
    check_completion_capacity({'job_id': 'j', 'file_tree': [], 'release': {'title': 'a' * low}})
    with pytest.raises(TreeCapacityError):
        check_completion_capacity({'job_id': 'j', 'file_tree': [], 'release': {'title': 'a' * low + '中'}})


@pytest.mark.parametrize('bad', [float('nan'), object()])
def test_unencodable_metadata_cannot_pass_capacity_check(bad):
    with pytest.raises(TreeCapacityError):
        check_completion_capacity({'job_id': 'j', 'file_tree': [], 'release': {'title': bad}})
