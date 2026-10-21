import asyncio
import time

from scripts.m4_tenant332_api import BearerTokenGuard, M4EvaluationController, TARGET_KBS


def _request_headers(authorization=None, query_token=False):
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("ascii")))
    return headers


def _invoke(headers, *, kind="http", query_token=False, token="t" * 40):
    messages = []
    called = []

    async def app(scope, receive, send):
        called.append(True)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {"type": kind, "headers": headers, "method": "GET", "path": "/api/v1/sessions/continue-stream/x",
             "query_string": b"token=valid" if query_token else b"", "asgi": {"version": "3.0"}}
    wrapped = BearerTokenGuard(app, token, time.time() + 60)
    asyncio.run(wrapped(scope, receive, send))
    return messages, called


def test_missing_or_invalid_token_rejected_on_business_and_resume_routes():
    for headers in ([], [(b"authorization", b"Bearer wrong")],
                    [(b"authorization", b"Bearer " + b"t" * 40),
                     (b"authorization", b"Bearer wrong")]):
        messages, called = _invoke(headers)
        assert messages[0]["status"] == 401
        assert not called
        assert b"t" * 20 not in messages[1]["body"]


def test_query_parameter_does_not_authenticate():
    messages, called = _invoke([], query_token=True)
    assert messages[0]["status"] == 401
    assert not called


def test_correct_token_is_accepted_and_expired_token_is_rejected():
    token = "t" * 40
    headers = [(b"authorization", ("Bearer " + token).encode("ascii"))]
    wrapped = BearerTokenGuard(None, token, time.time() + 60)
    assert wrapped.valid(headers)
    assert not wrapped.valid(headers, now=time.time() + 61)
    messages, called = _invoke(headers, token=token)
    assert messages[0]["status"] == 204
    assert called


def test_websocket_is_closed_without_exposing_business_routes():
    messages, called = _invoke([], kind="websocket")
    assert messages == [{"type": "websocket.close", "code": 4401}]
    assert not called


def test_m4_controller_uses_normal_api_without_sending_gold_or_identity():
    class RecordingClient:
        def __init__(self):
            self.calls = []

        def create_session(self, **kwargs):
            self.calls.append(("create_session", kwargs))
            return {"id": "session-1"}

        def ask(self, session_id, **kwargs):
            self.calls.append(("ask", session_id, kwargs))
            return ["event: done"]

        def messages(self, session_id):
            self.calls.append(("messages", session_id))
            return [{"role": "assistant", "content": "answer"}]

    client = RecordingClient()
    case = {"case_id": "frozen-1", "tenant_id": 332,
            "knowledge_base_id": sorted(TARGET_KBS)[0], "document_scope": [],
            "question": "frozen question", "gold": {"answer": "never send"},
            "answer_points": ["never send"], "subject_id": "not-a-request-field"}

    result = M4EvaluationController(client).run_case(case)

    assert [call[0] for call in client.calls] == ["create_session", "ask", "messages"]
    assert client.calls[0][1] == {
        "knowledge_base_scope": [case["knowledge_base_id"]],
        "document_scope": [], "title": "M4 evaluation frozen-1"}
    assert client.calls[1][2]["question"] == "frozen question"
    assert client.calls[1][2]["knowledge_base_scope"] == [case["knowledge_base_id"]]
    assert set(client.calls[1][2]) == {
        "question", "request_id", "knowledge_base_scope", "document_scope"}
    assert "gold" not in result and "answer_points" not in result
    assert result["session_id"] == "session-1"
    assert result["history"][0]["content"] == "answer"


def test_m4_controller_rejects_non_frozen_tenant_or_kb_before_api_calls():
    class NoCalls:
        def create_session(self, **kwargs):
            raise AssertionError("must reject before API")

    case = {"tenant_id": 1, "knowledge_base_id": sorted(TARGET_KBS)[0], "question": "q"}
    try:
        M4EvaluationController(NoCalls()).run_case(case)
    except ValueError as exc:
        assert str(exc) == "M4_CASE_SCOPE_INVALID"
    else:
        raise AssertionError("expected scope rejection")
