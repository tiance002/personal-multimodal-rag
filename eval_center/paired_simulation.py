"""Explicit offline runtime fixtures for the paired public-cohort CLI."""
from __future__ import annotations

import json
import hashlib
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from backend.app.adapters.models.deepseek import SimulatedDeepSeekTransport
from backend.app.adapters.models.factory import ProviderFactory
from backend.app.adapters.models.v6_text_capacity import (
    DEEPSEEK_RENDERER_IDENTITY,
    DeepseekV41RecipeRenderer,
    DeepseekV41TextCapacityCounter,
)
from backend.app.application.deepseek_gate_types import DEEPSEEK_GATE_TYPES
from backend.app.application.quick_chain import LangChainQuickChain
from backend.app.application.rule_router_v1 import (
    GenerationRole,
    V6GenerationBinding,
)
from backend.app.application.validation_usd_budget import (
    OwnerActivityTokenGrant,
    POLICY,
    V6OwnerActivityGate,
)
from backend.app.domain.model_registry import ModelRegistry
from backend.app.ports.run_lifecycle import current_execution
from backend.app.domain.models import EvidenceSnapshot


class _SimulatedCounter(DeepseekV41TextCapacityCounter):
    execution_kind = "SIMULATED"


class _SimulatedRenderer:
    identity = DEEPSEEK_RENDERER_IDENTITY

    def __call__(self, body):
        return "SIMULATED framing:" + body["messages"][0]["content"]


class _SimulatedUsageGuard:
    def reserve(self, **kwargs):
        return {"reserved": True, **kwargs}

    def settle(self, reservation, usage, *, sent):
        return None


class _CheapGateway:
    provider_name = "siliconflow"
    chat_model = "XingChenAGI/Xing4.0-29B"
    execution_kind = "SIMULATED"
    provider_kind = "cloud"

    def __init__(self, calls=None):
        self.usage_guard = _SimulatedUsageGuard()
        self.calls = calls if calls is not None else []

    def generate(self, messages, *, timeout_seconds, max_tokens):
        self.calls.append({"provider": "siliconflow", "execution_kind": "SIMULATED"})
        return SimpleNamespace(
            model_requested=self.chat_model,
            model_reported=self.chat_model,
            finish_reason="stop",
            text="SIMULATED answer [E1]",
        )


class _SimulatedEvidence:
    """Public fixed evidence fixture; the real QuickChain still validates/finalizes."""

    def __init__(self):
        self.hardening = SimpleNamespace(
            check_candidate=lambda *args: None,
            observe_evidence_answer=lambda *args: None,
        )
        self.commit_check = SimpleNamespace(check=lambda *args, **kwargs: None)

    def bundle(self, plan, retrieval):
        return SimpleNamespace(
            plan=plan,
            retrieval=retrieval,
            selected=(SimpleNamespace(chunk=SimpleNamespace(
                chunk_id="SIMULATED-public-fixture", content="SIMULATED public evaluation evidence",
                parent_id=None, document_id="SIMULATED-public-doc")),),
            decision=SimpleNamespace(accepted=True, missing_targets=(), reason=None),
            context="SIMULATED public evaluation evidence.",
            labels=(),
            snapshots=(),
        )

    def with_context(self, bundle, run_id, citations):
        label = "E1"
        snap = EvidenceSnapshot(
            label=label, version_id="SIMULATED-v1", chunk_id="SIMULATED-public-fixture",
            quote="SIMULATED public evaluation evidence",
            quote_sha256=hashlib.sha256(b"SIMULATED public evaluation evidence").hexdigest(),
            locator={"kind": "text", "start": 0, "end": 36},
        )
        return SimpleNamespace(**{
            **bundle.__dict__, "context": "SIMULATED public evaluation evidence.",
            "labels": (label,), "snapshots": (snap,),
        })

    def validate_answer(self, *args):
        return None

    def finalize_answer(self, run_id, answer, snapshots, chunks, plan, error=None):
        return SimpleNamespace(answer=answer, rejection=None, fallback=None, error=None)


class _SimulatedKnowledgeGateway:
    """Deterministic retrieval fixture supplied only to offline simulations."""

    def __init__(self):
        from backend.app.application.knowledge_gateway import QueryPlan
        from backend.app.domain.text_normalization import normalize_query

        self._query_plan = QueryPlan(
            "SIMULATED public question", SimpleNamespace(targets=(), kind="simple"),
            normalize_query("SIMULATED public question"),
        )
        self.evidence = _SimulatedEvidence()

    def plan(self, question):
        from backend.app.application.knowledge_gateway import QueryPlan
        from backend.app.domain.text_normalization import normalize_query

        return QueryPlan(question, self._query_plan.evidence_plan, normalize_query(question))

    def retrieve_question(self, scope, question, **kwargs):
        from backend.app.application.retrieval import RetrievalResult
        plan = self.plan(question)
        return plan, RetrievalResult(plan.retrieval_plan, [], ("SIMULATED",))


class _SimulatedLifecycle:
    def __init__(self):
        self.streams = SimpleNamespace(renew_live_run=lambda *args: True)
        self.repository = SimpleNamespace(
            claim_attempt=lambda **kwargs: None,
            finish_attempt=lambda *args: None,
        )


@contextmanager
def _execution_context(run_id):
    claim = SimpleNamespace(run_id=run_id, owner="SIMULATED-owner")
    token = current_execution.set((claim, _SimulatedLifecycle(), __import__("threading").Event(), "SIMULATED-session"))
    try:
        yield
    finally:
        current_execution.reset(token)


def build_simulated_container(*, ledger_dir: Path | None = None):
    """Build project QuickChain with explicit, fully offline role bindings."""
    if ledger_dir is None:
        raise ValueError("SIMULATED_LEDGER_DIRECTORY_REQUIRED")
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = ledger_dir / "simulated-v1-ledger.json"
    if not ledger_path.exists():
        rows = []
        for index in range(53):
            rows.append({
                "attempt_id": f"sim-legacy-{index}",
                "status": "ok",
                "validation_tokens": {
                    "state": "settled",
                    "input": 1_168 if index < 9 else 1_167,
                    "output": 70 if index < 52 else 69,
                    "reserved_input": 1_168 if index < 9 else 1_167,
                    "reserved_output": 70 if index < 52 else 69,
                },
            })
        ledger_path.write_text(json.dumps({
            "schema_version": 1, "provider": "deepseek",
            "calls_allowed_in_this_task": False, "authorized_limit": 53,
            "consumed_attempts": 53, "reserved_attempts": 0,
            "attempts": rows, "validation_policy": dict(POLICY),
        }), encoding="utf-8")
    grant = OwnerActivityTokenGrant.simulated(baseline_used_tokens=65_569)
    gate = V6OwnerActivityGate(ledger_path, grant=grant)
    gate.initialize_grant()
    counter = _SimulatedCounter(_SimulatedRenderer())
    guard = _SimulatedUsageGuard()
    factory = ProviderFactory(ModelRegistry.frozen_defaults(), enabled_roles=("chat_expensive",))

    observed_calls = []
    def simulated_send(request, timeout):
        json.loads(request.data.decode("utf-8"))
        observed_calls.append({"provider": "deepseek", "execution_kind": "SIMULATED"})
        return {
            "model": "deepseek-flash",
            "choices": [{"message": {"content": "SIMULATED answer [E1]"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 42, "completion_tokens": 6, "total_tokens": 48},
        }

    deepseek = factory.build_v6_gateway(
        activity_gate=gate, gate_types=DEEPSEEK_GATE_TYPES, api_key="SIMULATED-ONLY",
        transport=SimulatedDeepSeekTransport(simulated_send),
    )
    v6_binding = V6GenerationBinding(grant, gate, counter)
    roles = {
        "chat_cheap": GenerationRole("chat_cheap", _CheapGateway(observed_calls), simulated=True,
                                     usage_guard=_SimulatedUsageGuard()),
        "chat_expensive": GenerationRole("chat_expensive", deepseek, simulated=True,
                                         usage_guard=guard, v6_binding=v6_binding),
    }
    chain = LangChainQuickChain(
        _SimulatedKnowledgeGateway(), generation_roles=roles,
        input_token_estimator=lambda prompt: (max(1, len(prompt) // 4), "SIMULATED_CHAR_ESTIMATOR"),
    )
    container = SimpleNamespace(quick_chain=chain, store=object(), execution_context=_execution_context)
    container.simulated_gate = gate
    container.simulated_counter = counter
    container.simulated_provider_calls = observed_calls
    return container


def build_simulated_runtime(*, state_path: Path, run_id: str | None = None, dataset_names=None):
    """Supply labelled offline dependencies to the production cohort scheduler."""
    from backend.app.domain.scope import Scope
    from eval_center.paired_state import DATASETS
    from eval_center.public_runner import PairedRuntime
    from eval_center.paired_state import FULL_QA_DATASETS
    names=tuple(dataset_names) if dataset_names is not None else DATASETS
    if set(names) not in (set(DATASETS),set(FULL_QA_DATASETS)):
        raise ValueError('simulated_dataset_contract_invalid')
    identity = hashlib.sha256((run_id or "default-simulation").encode("utf-8")).hexdigest()[:20]
    container = build_simulated_container(ledger_dir=state_path.parent / "simulation_runtime" / identity)
    scope = Scope.from_ids(["p7-public-simulation"])
    return PairedRuntime(
        containers={name: container for name in names},
        scopes={name: scope for name in names},
        cloud_allowed_by_kb={"p7-public-simulation": True},
        dispatch_guard=lambda: None, execution_kind="SIMULATED",
        database_queries=lambda: 0,
        provider_observations=lambda: {
            "execution_kind": "SIMULATED", "real_provider_calls": 0,
            "cheap_fixture_calls": sum(row["provider"] == "siliconflow" for row in container.simulated_provider_calls),
            "deepseek_fixture_calls": sum(row["provider"] == "deepseek" for row in container.simulated_provider_calls),
        },
    )


__all__ = ["build_simulated_container", "build_simulated_runtime"]
