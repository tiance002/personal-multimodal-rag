"""Prompt-level protection for Global memory on the Cloud Quick route."""

from dataclasses import replace

from backend.app.application.context_window import ContextManager
from backend.app.application.memory import MemoryRetriever
from backend.app.application.quick_chain import QuickSettings
from backend.app.application.rule_router_v1 import RouterPolicy
from backend.app.domain.scope import Scope
from backend.tests.test_p5_router_generation import make_chain
from backend.tests.test_p55b_context_window import History, Store, window


class MemoryRepository:
    def recall_items(self, principal, scope):
        return [
            {
                "id": "global-pref",
                "kind": "preference",
                "fact_key": "global-language",
                "content": "GLOBAL-PRIVATE-MEMORY-93f4",
                "version": 1,
                "scope_type": "GLOBAL",
                "status": "active",
            },
            {
                "id": "scoped-pref",
                "kind": "preference",
                "fact_key": "scoped-style",
                "content": "SCOPED-AUTHORIZED-MEMORY-28a1",
                "version": 1,
                "scope_type": "SCOPED",
                "status": "active",
            },
        ]


def test_cloud_quick_dispatch_excludes_global_memory_but_keeps_scoped(monkeypatch):
    chain, _, dispatched, _ = make_chain(
        monkeypatch,
        policy=RouterPolicy(mode="CHEAP_ONLY"),
        contents=("A商品售价1000元",),
    )
    windows = {}
    for role, binding in chain.generation_roles.items():
        windows[role] = replace(window(), provider=binding.provider, model=binding.model)
    chain.context_manager = ContextManager(
        History([]),
        Store(),
        windows,
        memory_retriever=MemoryRetriever(MemoryRepository(), "owner-1"),
    )
    result = chain.invoke(
        "A 商品售价是多少？",
        Scope.from_ids(["kb"]),
        settings=QuickSettings(cloud_enabled=True),
        run_id="cloud-global-memory-regression",
        conversation_id="conversation-1",
        cloud_allowed_by_kb={"kb": True},
    )

    # The minimal fixture may fail answer validation after dispatch. The privacy
    # property is asserted against the captured Cloud transport envelope below.
    assert len(dispatched) == 1
    assert result.trace.model_calls == 1
    wire = str(dispatched[0])
    assert "SCOPED-AUTHORIZED-MEMORY-28a1" in wire
    assert "GLOBAL-PRIVATE-MEMORY-93f4" not in wire


def test_local_quick_memory_selection_keeps_global_and_scoped():
    prompt = "SYSTEM\nQuestion: local question\nEvidence: [E1]"
    retriever = MemoryRetriever(MemoryRepository(), "owner-1")
    result = retriever.quick_context(
        prompt,
        scope=Scope.from_ids(["kb"]),
        windows=[window()],
        output_tokens=100,
    )

    assert "GLOBAL-PRIVATE-MEMORY-93f4" in result
    assert "SCOPED-AUTHORIZED-MEMORY-28a1" in result
