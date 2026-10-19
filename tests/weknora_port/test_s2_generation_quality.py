import pytest
from scripts.s2_generation_quality import paired_messages, pair_is_complete


def test_pairs_preserve_user_history_and_only_add_system_guidance():
    original = [{'role': 'system', 'content': 'Rules\n\n## Source handling protocol\nProtocol'},
                {'role': 'user', 'content': 'Original source & raw figures'},
                {'role': 'assistant', 'content': 'History'},
                {'role': 'user', 'content': 'Question'}]
    a, b = paired_messages(original, 'Cover requested parts.')
    assert a == original and b[1:] == original[1:]
    assert b[0]['content'].replace('\n\nCover requested parts.', '', 1) == original[0]['content']
    assert 'Cover requested parts.' not in original[0]['content']


@pytest.mark.parametrize('messages', [[], [{'role': 'user', 'content': 'x'}],
    [{'role': 'system', 'content': [{'type': 'image_url'}]}],
    [{'role': 'system', 'content': 'Cover requested parts.'}]])
def test_unfrozen_or_already_modified_inputs_are_rejected(messages):
    with pytest.raises(ValueError):
        paired_messages(messages, 'Cover requested parts.')


@pytest.mark.parametrize('entries,arms', [([], {}),
    ([{'request_id': 'p/A', 'status': 'SETTLED'}], {'A': {}}),
    ([{'request_id': 'p/A', 'status': 'SETTLED'}, {'request_id': 'p/B', 'status': 'UNKNOWN'}], {'A': {}, 'B': {}}),
    ([{'request_id': 'p/A', 'status': 'SETTLED'}, {'request_id': 'other/B', 'status': 'SETTLED'}], {'A': {}, 'B': {}})])
def test_unexecuted_partial_and_unknown_pairs_are_not_complete(entries, arms):
    assert not pair_is_complete(entries, arms, 'p')


def test_both_settled_distinct_arms_are_required():
    assert pair_is_complete([{'request_id': 'p/A', 'status': 'SETTLED'},
                             {'request_id': 'p/B', 'status': 'SETTLED'}], {'A': {}, 'B': {}}, 'p')
