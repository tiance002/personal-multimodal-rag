"""SIMULATED provider byte streams; no network, credentials or database."""
import json

import pytest

from backend.app.ports.ordinary_chat_stream import (
    ChatStreamAccumulator, MAX_EVENT_BYTES, MAX_STREAM_BYTES,
)


USAGE = {'prompt_tokens': 7, 'completion_tokens': 3, 'total_tokens': 10}


def packet(content=None, finish=None, usage=None, **overrides):
    value = {'id': 'response-1', 'model': 'model', 'choices': [
        {'index': 0, 'delta': {'content': content}, 'finish_reason': finish}], 'usage': usage}
    value.update(overrides)
    return value


def wire(*packets, done=True):
    result = b''.join(('data: ' + json.dumps(p, ensure_ascii=False) + '\n\n').encode() for p in packets)
    return result + (b'data: [DONE]\n\n' if done else b'')


def aggregate(raw, widths=(1, 2, 7, 13)):
    acc = ChatStreamAccumulator('model')
    offset = 0
    while offset < len(raw):
        width = widths[offset % len(widths)]
        acc.feed(raw[offset:offset + width])
        offset += width
    return acc, acc.finish()


def test_utf8_crlf_comments_multiline_and_exact_raw_wire():
    first = json.dumps(packet('引用🙂'), ensure_ascii=False)
    # Multi-line data has an actual JSON whitespace newline between fields.
    first = first.replace(', "model"', ',\ndata: "model"')
    raw = (': keepalive\r\nevent: message\r\nid: envelope-id\r\ndata: ' + first + '\r\n\r\n').encode()
    raw += wire(packet(' complete', 'stop', USAGE)).replace(b'\n', b'\r\n')
    acc, result = aggregate(raw)
    assert acc.raw_wire == raw
    assert result == {'id': 'response-1', 'model': 'model', 'choices': [
        {'index': 0, 'message': {'role': 'assistant', 'content': '引用🙂 complete'}, 'finish_reason': 'stop'}],
        'usage': USAGE}


@pytest.mark.parametrize('finish', ['stop', 'length'])
@pytest.mark.parametrize('content', ['', 'private candidate'])
def test_finish_and_usage_in_same_chunk_preserve_empty_or_truncated_content(finish, content):
    _, result = aggregate(wire(packet(content, finish, USAGE)))
    assert result['choices'][0]['finish_reason'] == finish
    assert result['choices'][0]['message']['content'] == content


def test_terminal_usage_only_chunk_and_intermediate_statistics():
    interim = {'prompt_tokens': 1, 'completion_tokens': 0, 'total_tokens': 1}
    raw = wire(packet('answer', usage=interim), packet(finish='stop'), packet(choices=[], usage=USAGE))
    _, result = aggregate(raw)
    assert result['usage'] == USAGE
    assert result['choices'][0]['message']['content'] == 'answer'


@pytest.mark.parametrize('raw', [
    wire(packet('answer', 'stop', USAGE), done=False),
    wire(packet('answer', usage=USAGE)),
    wire(packet('answer', 'stop')),
    wire(packet('answer', usage=USAGE), packet(finish='stop')),
    b'data: [DONE]\n\n',
    wire(packet(choices=[], usage=USAGE)),
    wire(packet('answer', 'stop', USAGE), packet('late')),
    wire(packet('answer', 'stop', USAGE), packet(finish='stop', usage=USAGE)),
    wire(packet('answer', 'stop', USAGE), packet(choices=[], usage={**USAGE, 'completion_tokens': 4, 'total_tokens': 11})),
    wire(packet('answer', 'stop', USAGE)) + wire(packet('late')),
    b'data: {"private":"sensitive"}\n\n',
    b'data: {"id":"x","id":"y"}\n\n',
    b'data: NaN\n\n',
    b'data: \xff\n\n',
    b'data: \xe4',
])
def test_incomplete_or_conflicting_stream_fails_without_payload(raw):
    acc = ChatStreamAccumulator('model')
    with pytest.raises(ValueError, match='^CHAT_STREAM_INVALID$'):
        acc.feed(raw)
        acc.finish()
    with pytest.raises(ValueError, match='^CHAT_STREAM_INVALID$'):
        acc.finish()


@pytest.mark.parametrize('replacement', [
    {'model': 'other'}, {'model': None}, {'id': ''}, {'id': 7}, {'choices': {}},
    {'choices': [{'index': True, 'delta': {}, 'finish_reason': 'stop'}]},
    {'choices': [{'index': 1, 'delta': {}, 'finish_reason': 'stop'}]},
    {'choices': [{}, {}]},
    {'choices': [{'index': 0, 'delta': None, 'finish_reason': 'stop'}]},
    {'choices': [{'index': 0, 'delta': {'content': 3}, 'finish_reason': 'stop'}]},
    {'choices': [{'index': 0, 'delta': {'role': 'user'}, 'finish_reason': 'stop'}]},
    {'choices': [{'index': 0, 'delta': {'reasoning_content': 'hidden'}, 'finish_reason': 'stop'}]},
    {'choices': [{'index': 0, 'delta': {'refusal': 'refused'}, 'finish_reason': 'stop'}]},
    {'choices': [{'index': 0, 'delta': {'tool_calls': [{'id': 'tool'}]}, 'finish_reason': 'stop'}]},
    {'choices': [{'index': 0, 'delta': {'function_call': {}}, 'finish_reason': 'stop'}]},
    {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]},
    {'usage': {**USAGE, 'completion_tokens': True}},
    {'usage': {**USAGE, 'prompt_tokens': -1}},
    {'usage': {**USAGE, 'total_tokens': 11}},
    {'usage': {'completion_tokens': 3}},
])
def test_invalid_provider_types_and_disallowed_output_are_rejected(replacement):
    value = packet('private', 'stop', USAGE)
    value.update(replacement)
    with pytest.raises(ValueError, match='^CHAT_STREAM_INVALID$'):
        aggregate(wire(value))


@pytest.mark.parametrize('replacement', [{'id': 'response-2'}, {'model': 'other'}])
def test_identity_cannot_change_on_usage_only_chunk(replacement):
    with pytest.raises(ValueError, match='^CHAT_STREAM_INVALID$'):
        aggregate(wire(packet('answer', 'stop'), packet(choices=[], usage=USAGE, **replacement)))


def test_event_and_total_byte_limits_bound_comments_and_unterminated_data():
    acc = ChatStreamAccumulator('model')
    with pytest.raises(ValueError):
        acc.feed(b'data: ' + b'a' * MAX_EVENT_BYTES)
    acc = ChatStreamAccumulator('model')
    heartbeat = b':' + b'x' * 998 + b'\n\n'
    count = 0
    while count + len(heartbeat) <= MAX_STREAM_BYTES:
        acc.feed(heartbeat)
        count += len(heartbeat)
    before = len(acc.raw_wire)
    with pytest.raises(ValueError):
        acc.feed(heartbeat)
    assert len(acc.raw_wire) == before <= MAX_STREAM_BYTES


def test_input_and_finished_state_are_strict_and_do_not_republish():
    with pytest.raises(ValueError):
        ChatStreamAccumulator('')
    acc, result = aggregate(wire(packet('answer', 'stop', USAGE)))
    result['usage']['total_tokens'] = 0
    with pytest.raises(ValueError):
        acc.feed(b'')
    with pytest.raises(ValueError):
        acc.finish()
    with pytest.raises(ValueError):
        ChatStreamAccumulator('model').feed('not bytes')


def test_done_at_eof_does_not_require_a_trailing_blank_line():
    _, result = aggregate(wire(packet('answer', 'stop', USAGE)).rstrip(b'\n'))
    assert result['usage'] == USAGE


def test_complete_requires_dispatched_done_and_is_revoked_on_failure():
    acc = ChatStreamAccumulator('model')
    acc.feed(wire(packet('answer', 'stop', USAGE), done=False))
    assert not acc.complete
    acc.feed(b'data: [DO')
    assert not acc.complete
    acc.feed(b'NE]\n')
    assert not acc.complete
    acc.feed(b'\n')
    assert acc.complete
    assert acc.finish()['usage'] == USAGE
    assert acc.complete
    with pytest.raises(ValueError):
        acc.feed(b'late')
    assert not acc.complete
