"""Bounded, private aggregation of one ordinary Chat Completions SSE response.

No network, provider admission, settlement or publication occurs here. Consumers
must still run their existing response/answer validators and settlement guards.
"""
import codecs
import json


MAX_STREAM_BYTES = 2_000_000
MAX_EVENT_BYTES = 131_072


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _constant(_value):
    raise ValueError()


class ChatStreamAccumulator:
    """feed bytes privately; finish requires a complete authoritative terminal.

    raw_wire is the exact received SSE byte sequence, bounded to 2 MB. It may
    contain generated text and must never be exposed as public diagnostics.
    """
    def __init__(self, expected_model: str):
        if type(expected_model) is not str or not expected_model.strip():
            raise ValueError('CHAT_STREAM_MODEL_INVALID')
        self.expected_model = expected_model
        self._raw = bytearray()
        self._decoder = codecs.getincrementaldecoder('utf-8')('strict')
        self._pending = ''
        self._event_bytes = 0
        self._data = []
        self._id = None
        self._content = []
        self._finish = None
        self._usage = None
        self._done = False
        self._failed = False
        self._finished = False

    @property
    def raw_wire(self) -> bytes:
        return bytes(self._raw)

    @property
    def complete(self) -> bool:
        """A complete DONE event was processed without failure; no EOF needed."""
        return self._done and not self._failed

    def _reject(self):
        self._failed = True
        raise ValueError('CHAT_STREAM_INVALID') from None

    def feed(self, chunk: bytes) -> None:
        if self._failed or self._finished or type(chunk) is not bytes:
            self._reject()
        if len(self._raw) + len(chunk) > MAX_STREAM_BYTES:
            self._reject()
        self._raw.extend(chunk)
        try:
            self._pending += self._decoder.decode(chunk)
        except UnicodeError:
            self._reject()
        lines = self._pending.split('\n')
        self._pending = lines.pop()
        for line in lines:
            self._line(line[:-1] if line.endswith('\r') else line)
        # Include the undecoded UTF-8 suffix in the current event bound.
        if self._event_bytes + len(self._pending.encode()) + len(self._decoder.getstate()[0]) > MAX_EVENT_BYTES:
            self._reject()

    def _line(self, line):
        self._event_bytes += len(line.encode()) + 2
        if self._event_bytes > MAX_EVENT_BYTES or '\r' in line:
            self._reject()
        if not line:
            self._dispatch()
            self._event_bytes = 0
            return
        if line.startswith(':'):
            return
        field, separator, value = line.partition(':')
        if separator and value.startswith(' '):
            value = value[1:]
        if field == 'data':
            self._data.append(value)
        elif field in {'event', 'id', 'retry'}:
            # SSE envelope metadata does not replace response JSON identity.
            return
        else:
            self._reject()

    def _dispatch(self):
        if not self._data:
            return
        data, self._data = '\n'.join(self._data), []
        if self._done:
            self._reject()
        if data == '[DONE]':
            if self._finish is None or self._usage is None:
                self._reject()
            self._done = True
            return
        try:
            chunk = json.loads(data, object_pairs_hook=_object, parse_constant=_constant)
        except (ValueError, TypeError, RecursionError):
            self._reject()
        if (type(chunk) is not dict or chunk.get('model') != self.expected_model
                or chunk.get('error') is not None
                or ('object' in chunk and chunk['object'] != 'chat.completion.chunk')):
            self._reject()
        identity = chunk.get('id')
        if type(identity) is not str or not identity.strip() or (self._id is not None and identity != self._id):
            self._reject()
        self._id = identity
        choices = chunk.get('choices')
        if type(choices) is not list or len(choices) > 1:
            self._reject()
        usage = self._parse_usage(chunk.get('usage'))
        if not choices:
            if self._finish is None or usage is None:
                self._reject()
        else:
            choice = choices[0]
            if type(choice) is not dict or type(choice.get('index')) is not int or choice['index'] != 0 or self._finish is not None:
                self._reject()
            delta = choice.get('delta')
            if type(delta) is not dict:
                self._reject()
            role = delta.get('role')
            if role is not None and role != 'assistant':
                self._reject()
            for field in ('content', 'reasoning_content', 'refusal'):
                value = delta.get(field)
                if value is not None and type(value) is not str:
                    self._reject()
                if field != 'content' and value:
                    self._reject()
            tools = delta.get('tool_calls')
            if tools is not None and (type(tools) is not list or tools):
                self._reject()
            if 'function_call' in delta and delta['function_call'] is not None:
                self._reject()
            if delta.get('content'):
                self._content.append(delta['content'])
            reason = choice.get('finish_reason')
            if reason is not None:
                if reason not in ('stop', 'length') or type(reason) is not str:
                    self._reject()
                self._finish = reason
        # Interim statistics cannot establish billed terminal usage.
        if self._finish is not None and usage is not None:
            if self._usage is not None and self._usage != usage:
                self._reject()
            self._usage = usage

    def _parse_usage(self, value):
        if value is None:
            return None
        fields = ('prompt_tokens', 'completion_tokens', 'total_tokens')
        if type(value) is not dict or any(type(value.get(k)) is not int or value[k] < 0 for k in fields):
            self._reject()
        if value['total_tokens'] != value['prompt_tokens'] + value['completion_tokens']:
            self._reject()
        return {k: value[k] for k in fields}

    def finish(self) -> dict:
        if self._failed or self._finished:
            self._reject()
        try:
            self._pending += self._decoder.decode(b'', final=True)
        except UnicodeError:
            self._reject()
        if self._pending:
            self._line(self._pending)
            self._pending = ''
        self._dispatch()
        if not self._done or self._finish is None or self._usage is None:
            self._reject()
        self._finished = True
        return {'id': self._id, 'model': self.expected_model,
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': ''.join(self._content)},
                             'finish_reason': self._finish}], 'usage': dict(self._usage)}
