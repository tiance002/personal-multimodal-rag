"""FastAPI contract tests use an injected fake RuntimeService only."""
import asyncio
import json

import httpx
import pytest

from backend.weknora_port.http_api import AcceptedTurn, create_app
from backend.weknora_port.streaming.events import StreamEvent


PRINCIPAL = object()
SECRET = "simulated-secret-do-not-return"
ROLE_NAMES = ("embedding", "rerank", "cheap", "expensive", "vlm")


class FakeRuntime:
    def __init__(self):
        self.events = {}
        self.changed = asyncio.Event()
        self.accept_calls = []
        self.run_calls = []
        self.run_tasks = set()
        self.error_calls = []
        self.principals = []
        self.model_write = None
        self.upload_calls = []
        self.gate_run = False
        self.disconnect_after_initial = False
        self.run_started = asyncio.Event()
        self.release_run = asyncio.Event()
        self.fail_run = False
        self.accepted = {}

    def _record_principal(self, principal):
        assert principal is PRINCIPAL
        self.principals.append(principal)

    async def runtime_identity(self, principal):
        self._record_principal(principal)
        return {"implementation": "weknora-python-port", "api_version": "v1"}

    async def list_knowledge_bases(self, principal):
        self._record_principal(principal)
        return [{"id": "kb-1", "name": "Fixture", "cloud_allowed": False, "document_count": 0}]

    async def create_knowledge_base(self, principal, name):
        self._record_principal(principal)
        return {"id": "kb-new", "name": name, "cloud_allowed": False, "document_count": 0}

    async def list_documents(self, principal, knowledge_base_id):
        self._record_principal(principal)
        return [{"id": "doc-1", "knowledge_base_id": knowledge_base_id, "filename": "fixture.pdf", "status": "ready"}]

    async def upload_document(self, principal, knowledge_base_id, filename, content_type, source,
                              process_overrides=None):
        self._record_principal(principal)
        self.upload_calls.append((knowledge_base_id, filename, content_type, process_overrides))
        return {"id": "doc-upload", "knowledge_base_id": knowledge_base_id, "filename": filename, "status": "processing"}

    async def read_model_configuration(self, principal):
        self._record_principal(principal)
        return {"revision": 3, "roles": [
            {"role": role, "provider": "local", "model": role, "base_url": "http://localhost/v1",
             "api_key": SECRET if role == "cheap" else ""}
            for role in ROLE_NAMES
        ]}

    async def update_model_configuration(self, principal, revision, roles):
        self._record_principal(principal)
        self.model_write = (revision, roles)
        return {"revision": revision + 1, "roles": [
            {"role": row["role"], "provider": row["provider"], "model": row["model"],
             "base_url": row["base_url"], "api_key": row.get("api_key", "")}
            for row in roles
        ]}

    async def list_sessions(self, principal):
        self._record_principal(principal)
        return []

    async def create_session(self, principal, request):
        self._record_principal(principal)
        return {"id": "session-1", "title": request.title, "knowledge_base_scope": request.knowledge_base_scope,
                "document_scope": request.document_scope, "updated_at": "now"}

    async def list_messages(self, principal, session_id):
        self._record_principal(principal)
        return []

    async def accept_turn(self, request, principal):
        self._record_principal(principal)
        self.accept_calls.append((request.session_id, request))
        previous = self.accepted.get(request.request_id)
        if previous is not None:
            return AcceptedTurn(previous.run_id, previous.session_id, previous.user_message_id,
                                previous.assistant_message_id, previous.context,
                                is_new=False, replay_only=True)
        accepted = AcceptedTurn(request.request_id, request.session_id, "user-1", "assistant-1", context={"owner": "server"})
        self.accepted[request.request_id] = accepted
        self.events[(request.session_id, accepted.assistant_message_id)] = [StreamEvent(
            accepted.request_id, "agent_query", data={
                "assistant_message_id": accepted.assistant_message_id,
                "request_id": accepted.request_id,
                "session_id": request.session_id,
            })]
        self.changed.set()
        task = asyncio.create_task(self._drive(accepted, request, principal))
        self.run_tasks.add(task)
        task.add_done_callback(self.run_tasks.discard)
        return accepted

    async def _drive(self, accepted, request, principal):
        try:
            await self._run_turn(accepted, request, principal)
        except Exception:
            await self.publish_error(principal, accepted, "TURN_FAILED")

    async def _run_turn(self, accepted, request, principal):
        self._record_principal(principal)
        self.run_calls.append(accepted)
        self.run_started.set()
        if self.gate_run:
            await self.release_run.wait()
        if self.fail_run:
            raise RuntimeError(f"private provider detail {SECRET}")
        key = (accepted.session_id, accepted.assistant_message_id)
        self.events[key].extend([
            StreamEvent("answer-segment", "answer", "fixture answer", True,
                        data={"attempt_id": "attempt-1"}),
            StreamEvent(accepted.request_id, "complete", data={"final_content": "fixture answer"}),
        ])
        self.changed.set()

    async def publish_error(self, principal, accepted, code):
        self._record_principal(principal)
        self.error_calls.append((accepted.assistant_message_id, code))
        self.events[(accepted.session_id, accepted.assistant_message_id)].append(
            StreamEvent(accepted.request_id, "error", code, True,
                        data={"error_code": code, "assistant_message_id": accepted.assistant_message_id}))
        self.changed.set()

    async def validate_run(self, session_id, assistant_message_id, principal):
        self._record_principal(principal)
        key = (session_id, assistant_message_id)
        if key not in self.events:
            raise LookupError("ASSISTANT_MESSAGE_NOT_FOUND")
        event = self.events[key][0]
        previous = self.accepted[event.event_id]
        return AcceptedTurn(previous.run_id, previous.session_id, previous.user_message_id,
                            previous.assistant_message_id, previous.context,
                            is_new=False, replay_only=True)

    async def replay(self, accepted, principal):
        self._record_principal(principal)
        key = (accepted.session_id, accepted.assistant_message_id)
        offset = 0
        while True:
            rows = self.events[key]
            if offset < len(rows):
                next_rows = rows[offset:]
                offset = len(rows)
                for row in next_rows:
                    yield row
                    if row.type in ("complete", "error"):
                        return
                if self.disconnect_after_initial and offset >= 1:
                    return
                continue
            self.changed.clear()
            if len(self.events[key]) > offset:
                continue
            await self.changed.wait()

    async def read_source_chunk(self, principal, chunk_id):
        self._record_principal(principal)
        return {"chunk_id": chunk_id, "document_id": "doc-1", "filename": "fixture.pdf",
                "text": "fixture source", "source_url": ""}


def make_client(runtime=None, resolver=None):
    runtime = runtime or FakeRuntime()
    resolver = resolver or (lambda request: PRINCIPAL)
    app = create_app(runtime, resolve_principal=resolver)
    return runtime, app, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                          headers={"Origin": "http://test"})


def parse_frames(text):
    frames = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        assert "event: message\n" in block
        data_line = next(line for line in block.splitlines() if line.startswith("data: "))
        frames.append(json.loads(data_line[6:]))
    return frames


def test_runtime_identity_and_read_envelopes_use_server_resolved_principal():
    async def exercise():
        runtime, app, client = make_client()
        async with client:
            identity = await client.get("/api/v1/runtime")
            bases = await client.get("/api/v1/knowledge-bases")
        assert identity.status_code == bases.status_code == 200
        assert identity.json()["data"] == {"implementation": "weknora-python-port", "api_version": "v1"}
        assert bases.json()["data"][0]["id"] == "kb-1"
        assert identity.json()["meta"]["request_id"]
        assert len(runtime.principals) == 2

    asyncio.run(exercise())


def test_upload_accepts_mineru_and_rejects_retired_ocr_adapters_and_page_selector():
    async def exercise():
        runtime, _, client = make_client()
        async with client:
            accepted = await client.post("/api/v1/knowledge-bases/kb-1/documents",
                data={"process_overrides": json.dumps({"ocr_adapter": "mineru",
                    "caption_mode": "explicit", "caption_model_id": "deepseek-config-id"})},
                files={"file": ("b3.pdf", b"synthetic-pdf-bytes", "application/pdf")})
            for adapter in ("tesseract", "vlm"):
                rejected_ocr = await client.post("/api/v1/knowledge-bases/kb-1/documents",
                    data={"process_overrides": json.dumps({"ocr_adapter": adapter})},
                    files={"file": ("retired.pdf", b"synthetic-pdf-bytes", "application/pdf")})
                assert rejected_ocr.status_code == 422
                assert rejected_ocr.json()["error"]["code"] == "OCR_ADAPTER_DISABLED_USE_MINERU"
            for name, media in (("b3.pdf", "application/pdf"), ("b3.txt", "text/plain")):
                rejected = await client.post("/api/v1/knowledge-bases/kb-1/documents",
                    data={"process_overrides": json.dumps({"image_pages": [9]})},
                    files={"file": (name, b"synthetic-bytes", media)})
                assert rejected.status_code == 422
                assert rejected.json()["error"]["code"] == "IMAGE_PAGES_UNSUPPORTED"
            for value in ("[9]", ""):
                direct_rejected = await client.post("/api/v1/knowledge-bases/kb-1/documents",
                    data={"image_pages": value},
                    files={"file": ("b3.pdf", b"synthetic-bytes", "application/pdf")})
                assert direct_rejected.status_code == 422
                assert direct_rejected.json()["error"]["code"] == "IMAGE_PAGES_UNSUPPORTED"
            config_rejected = await client.patch("/api/v1/knowledge-bases/kb-1/image-processing",
                json={"image_pages": [9]})
        assert accepted.status_code == 200
        assert runtime.upload_calls[0] == ("kb-1", "b3.pdf", "application/pdf", {
            "ocr_adapter": "mineru", "caption_mode": "explicit",
            "caption_model_id": "deepseek-config-id"})
        assert config_rejected.status_code == 422
        assert config_rejected.json()["error"]["code"] == "REQUEST_INVALID"
        assert len(runtime.upload_calls) == 1

    asyncio.run(exercise())


def test_message_post_starts_once_and_get_replays_the_same_run_without_generation():
    async def exercise():
        runtime, app, client = make_client()
        body = {"content": "question", "request_id": "request-1", "mode": "quick",
                "knowledge_base_scope": ["kb-1"], "document_scope": ["doc-1"]}
        async with client:
            response = await client.post("/api/v1/sessions/session-1/messages", json=body)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            frames = parse_frames(response.text)
            resumed = await client.get("/api/v1/sessions/continue-stream/session-1",
                                       params={"message_id": "assistant-1"})
        assert [event["response_type"] for event in frames] == ["agent_query", "answer", "complete"]
        assert frames[0]["id"] == "request-1"
        assert frames[0]["data"] == {"assistant_message_id": "assistant-1", "request_id": "request-1", "session_id": "session-1"}
        assert frames[1]["data"]["event_id"] == "answer-segment"
        assert frames[1]["done"] is True and frames[2]["response_type"] == "complete"
        assert [item["response_type"] for item in parse_frames(resumed.text)] == ["agent_query", "answer", "complete"]
        assert len(runtime.accept_calls) == len(runtime.run_calls) == 1
        assert len(runtime.events[("session-1", "assistant-1")]) == 3

    asyncio.run(exercise())


def test_duplicate_request_id_replays_existing_run_without_starting_a_second_worker():
    async def exercise():
        runtime, _, client = make_client()
        body = {"content": "question", "request_id": "request-idempotent", "mode": "quick",
                "knowledge_base_scope": ["kb-1"], "document_scope": []}
        async with client:
            first = await client.post("/api/v1/sessions/session-1/messages", json=body)
            second = await client.post("/api/v1/sessions/session-1/messages", json=body)
        assert first.status_code == second.status_code == 200
        assert [row["response_type"] for row in parse_frames(first.text)] == ["agent_query", "answer", "complete"]
        assert [row["response_type"] for row in parse_frames(second.text)] == ["agent_query", "answer", "complete"]
        assert len(runtime.accept_calls) == 2
        assert len(runtime.run_calls) == 1
        assert sum(event.type == "agent_query" for event in runtime.events[("session-1", "assistant-1")]) == 1

    asyncio.run(exercise())


def test_stream_disconnect_does_not_cancel_the_separate_background_turn():
    async def exercise():
        runtime = FakeRuntime()
        runtime.gate_run = True
        runtime.disconnect_after_initial = True
        app = create_app(runtime, resolve_principal=lambda request: PRINCIPAL)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                   headers={"Origin": "http://test"})
        async with client:
            response = await client.post("/api/v1/sessions/session-1/messages", json={
                "content": "question", "request_id": "request-disconnect", "mode": "quick",
                "knowledge_base_scope": ["kb-1"], "document_scope": [],
            })
        assert response.status_code == 200
        assert [event["response_type"] for event in parse_frames(response.text)] == ["agent_query"]
        await asyncio.wait_for(runtime.run_started.wait(), timeout=1)
        tasks = runtime.run_tasks
        assert len(tasks) == 1 and not next(iter(tasks)).done()
        runtime.release_run.set()
        await asyncio.wait_for(asyncio.gather(*tuple(tasks)), timeout=1)
        assert len(runtime.run_calls) == 1

    asyncio.run(exercise())


def test_background_failure_emits_safe_error_for_assistant_message_only():
    async def exercise():
        runtime = FakeRuntime()
        runtime.fail_run = True
        _, _, client = make_client(runtime)
        async with client:
            response = await client.post("/api/v1/sessions/session-1/messages", json={
                "content": "question", "request_id": "request-fail", "mode": "quick",
                "knowledge_base_scope": ["kb-1"], "document_scope": [],
            })
        frames = parse_frames(response.text)
        assert frames[-1]["response_type"] == "error"
        assert frames[-1]["content"] == "TURN_FAILED"
        assert SECRET not in response.text
        assert runtime.error_calls == [("assistant-1", "TURN_FAILED")]
        assert runtime.run_calls[0].assistant_message_id != "user-message"

    asyncio.run(exercise())


def test_model_configuration_never_returns_write_only_keys_and_accepts_only_five_roles():
    async def exercise():
        runtime, _, client = make_client()
        async with client:
            read = await client.get("/api/v1/settings/model-configuration")
            roles = [{"role": role, "provider": "local", "model": role,
                      "base_url": "http://localhost/v1", "api_key": SECRET if role == "cheap" else ""}
                     for role in ROLE_NAMES]
            update = await client.put("/api/v1/settings/model-configuration",
                                      json={"revision": "3", "roles": roles})
            invalid = await client.put("/api/v1/settings/model-configuration",
                                       json={"revision": "3", "roles": roles, "owner_id": "attacker"})
        assert read.status_code == update.status_code == 200
        assert read.json()["data"]["revision"] == "3"
        assert read.json()["data"]["roles"][2]["key_configured"] is True
        assert "api_key" not in read.text and SECRET not in read.text
        assert "api_key" not in update.text and SECRET not in update.text
        assert runtime.model_write[0] == 3
        assert runtime.model_write[1][2]["api_key"] == SECRET
        assert invalid.status_code == 422 and SECRET not in invalid.text
        assert invalid.json()["error"]["code"] == "REQUEST_INVALID"

    asyncio.run(exercise())


def test_smart_mode_is_explicitly_unavailable_and_scope_identity_is_not_client_controlled():
    async def exercise():
        runtime, _, client = make_client()
        async with client:
            smart = await client.post("/api/v1/sessions/session-1/messages", json={
                "content": "question", "request_id": "request-smart", "mode": "smart",
                "knowledge_base_scope": ["kb-1"], "document_scope": [],
            })
            forged = await client.post("/api/v1/sessions/session-1/messages", json={
                "content": "question", "request_id": "request-forged", "mode": "quick",
                "knowledge_base_scope": ["kb-1"], "document_scope": [], "owner_id": "attacker",
            })
        assert smart.status_code == 501 and smart.json()["error"]["code"] == "SMART_MODE_UNAVAILABLE"
        assert forged.status_code == 422
        assert runtime.accept_calls == runtime.run_calls == []
        assert runtime.principals == []

    asyncio.run(exercise())


def test_missing_server_principal_fails_closed():
    async def exercise():
        runtime, _, client = make_client(resolver=lambda request: None)
        async with client:
            response = await client.get("/api/v1/knowledge-bases")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "TRUSTED_PRINCIPAL_REQUIRED"
        assert runtime.principals == []

    asyncio.run(exercise())


def test_exception_messages_are_not_promoted_to_public_error_codes():
    async def exercise():
        runtime, _, client = make_client()

        async def unsafe_failure(principal):
            raise ValueError("PRIVATE_API_KEY_SHOULD_NOT_LEAK")

        runtime.list_knowledge_bases = unsafe_failure
        async with client:
            response = await client.get("/api/v1/knowledge-bases")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "REQUEST_INVALID"
        assert "PRIVATE_API_KEY_SHOULD_NOT_LEAK" not in response.text

    asyncio.run(exercise())


def test_known_model_rebinding_error_code_remains_actionable():
    async def exercise():
        runtime, _, client = make_client()

        async def reject_rebinding(principal, revision, roles):
            raise ValueError("MODEL_CREDENTIAL_REBINDING_REQUIRED")

        runtime.update_model_configuration = reject_rebinding
        roles = [{"role": role, "provider": "other", "model": role,
                  "base_url": "http://localhost/v1", "api_key": ""}
                 for role in ROLE_NAMES]
        async with client:
            response = await client.put("/api/v1/settings/model-configuration",
                                        json={"revision": "3", "roles": roles})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "MODEL_CREDENTIAL_REBINDING_REQUIRED"

    asyncio.run(exercise())


def test_cross_site_origin_is_rejected_before_any_mutation_but_allowlisted_shadow_origin_is_supported():
    async def exercise():
        runtime, _, client = make_client()
        async with client:
            denied = await client.post("/api/v1/knowledge-bases", headers={"Origin": "https://evil.example"},
                                       json={"name": "must not create"})
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"
        assert runtime.principals == []

        allowed_runtime = FakeRuntime()
        app = create_app(allowed_runtime, resolve_principal=lambda request: PRINCIPAL,
                         allowed_origins=("https://shadow.example",))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Origin": "https://shadow.example"}) as allowed_client:
            accepted = await allowed_client.post("/api/v1/knowledge-bases", json={"name": "shadow"})
        assert accepted.status_code == 200
        assert accepted.headers["access-control-allow-origin"] == "https://shadow.example"
        assert allowed_runtime.principals == [PRINCIPAL]

    asyncio.run(exercise())
