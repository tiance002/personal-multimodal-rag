"""Real isolated runs for the public benchmark dataset adapters."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
import uuid
import copy
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from decimal import Decimal
from datetime import timedelta
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from backend.app.adapters.models.usage import capture_usage
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.knowledge_gateway import EvidenceService, KnowledgeGateway
from backend.app.application.quick_chain import LangChainQuickChain, QuickSettings
from backend.app.application.rule_router_v1 import RouterPolicy
from backend.app.application.retrieval import HybridRetriever, RetrievalItem
from backend.app.bootstrap import build_container
from backend.app.config import Settings
from backend.app.domain.scope import Scope
from backend.app.domain.evidence import EvidenceResolver
from backend.app.workers.ingestion import run_once
from eval_center.query_embedding_cache import EmbeddingCacheIdentity, RunScopedQueryEmbeddingCache
from eval_center.isolated_index import create_isolated_database, read_index_snapshot
from eval_center.metrics import aggregate_metrics, ranking_metrics
from eval_center.public_data import PublicCase, PublicDataset, SOURCE_URLS, fold_chunk_ranking, load_public_dataset
from eval_center.runtime import committed_code_sha, effective_configuration, model_identities
from eval_center.verification import ExperimentInvalidError


RETRIEVAL_MODES = ("keyword", "vector", "hybrid")
PAIRED_ANSWER_OUTPUT_TOKEN_CAP = 512


@dataclass(frozen=True)
class PairedRuntime:
    """Explicit composition-root injection; no keys, DBs or grants are created.

    REAL dispatch_guard owns current account/egress/monthly-budget/single-writer
    checks. capacity_probe must return the counters' actual typed estimates.
    index_bindings contains independently observed expected/actual identities
    consumed by the existing read-only index verifier. All I/O is synchronous;
    the owner composition must bound DB/telemetry I/O by the supplied deadline.
    """

    containers: dict[str, Any]
    scopes: dict[str, Scope]
    cloud_allowed_by_kb: dict[str, bool]
    dispatch_guard: Any
    execution_kind: str
    index_bindings: dict[str, dict[str, Any]] = field(default_factory=dict)
    capacity_probe: Any = None
    source_id_for_chunk: Any = None
    database_queries: Any = None
    langfuse_client: Any = None
    index_manifest_sha256: str | None = None
    provider_observations: Any = None
    configure_deadline: Any = None
    composition_identity_sha256: str | None = None
    settlement_observer: Any = None
    source_annotations_for_chunk: Any = None
    execution_contract: Any = None
    shared_retrieval_binding: Any = None


@dataclass(frozen=True)
class PairedProviderCallBudget:
    """Frozen owner input to worst-case planning; native billing stays authoritative."""
    request_id: str
    qid: str
    arm: str
    purpose: str
    input_token_bound: int
    output_token_bound: int
    safety_tokens: int = 0


@dataclass(frozen=True)
class PairedDatasetComposition:
    """Owner-supplied typed inputs to the normal backend bootstrap, not a ready flag."""
    settings: Settings
    scope: Scope
    v6_binding: Any
    xing_binding: Any
    execution_context: Any
    model_usage_guards: dict[str, Any]
    embedding_provider: Any
    embedding_admission: Any = None
    deepseek_calls: tuple[PairedProviderCallBudget, ...] = ()
    free_model_bindings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PairedRuntimeComposition:
    datasets: dict[str, PairedDatasetComposition]
    cloud_allowed_by_kb: dict[str, bool]
    dispatch_guard: Any
    execution_kind: str = "REAL"
    index_bindings: dict[str, dict[str, Any]] = field(default_factory=dict)
    capacity_probe: Any = None
    source_id_for_chunk: Any = None
    database_queries: Any = None
    provider_observations: Any = None
    configure_deadline: Any = None
    langfuse_client: Any = None
    index_manifest_sha256: str | None = None
    entry_sha256: str | None = None
    settlement_observer: Any = None
    source_annotations_for_chunk: Any = None
    execution_contract: Any = None
    shared_retrieval_binding: Any = None


@dataclass(frozen=True)
class PairedSharedRetrievalBinding:
    """Exact per-QID native plans for the contract's separate RETRIEVAL Run."""
    execution_contract: Any
    plans: tuple[tuple[str, str, str, Any], ...]
    rerank_plans: tuple[tuple[str, str, str, Any], ...] = ()
    late_rerank: tuple[tuple[str, Any], ...] = ()
    execution_kind: str = 'REAL'

    def validate(self):
        from eval_center.paired_state import PairedCohortContract, ProviderBatchPlan
        if type(self) is not PairedSharedRetrievalBinding or type(self.execution_contract) is not PairedCohortContract:
            raise ValueError('paired_shared_retrieval_binding_invalid')
        self.execution_contract.validate()
        if self.execution_kind not in {'REAL', 'SIMULATED'}:
            raise ValueError('paired_retrieval_execution_kind_invalid')
        expected = {(s.dataset, q) for s in self.execution_contract.selections for q in s.qids}
        if (type(self.plans) is not tuple or len(self.plans) != len(expected)
                or any(type(r) is not tuple or len(r) != 4 or type(r[2]) is not str or not r[2]
                    for r in self.plans)
                or {(r[0], r[1]) for r in self.plans} != expected):
            raise ValueError('paired_shared_retrieval_plan_set')
        for _, _, _, plan in self.plans:
            if type(plan) is not ProviderBatchPlan or plan.purpose != 'query_embedding' or len(plan.calls) != 1:
                raise ValueError('paired_shared_retrieval_query_plan')
            plan.validate()
        if (type(self.rerank_plans) is not tuple
                or self.rerank_plans and (len(self.rerank_plans) != len(expected)
                    or any(type(r) is not tuple or len(r) != 4 or type(r[2]) is not str or not r[2]
                           for r in self.rerank_plans)
                    or {(r[0], r[1]) for r in self.rerank_plans} != expected)):
            raise ValueError('paired_shared_rerank_plan_set')
        if self.late_rerank:
            from eval_center.paired_preparation import LateRerankPreparation
            if (type(self.late_rerank) is not tuple or self.rerank_plans
                    or {d for d, _ in self.late_rerank} != {s.dataset for s in self.execution_contract.selections}
                    or len(self.late_rerank) != len(self.execution_contract.selections)
                    or any(type(policy) is not LateRerankPreparation for _, policy in self.late_rerank)):
                raise ValueError('paired_late_rerank_policy_set')
            for _, policy in self.late_rerank:
                policy.validate()
        for _, _, _, plan in self.rerank_plans:
            if type(plan) is not ProviderBatchPlan or plan.purpose != 'rerank' or len(plan.calls) != 1:
                raise ValueError('paired_shared_rerank_plan')
            plan.validate()

    @property
    def fingerprint(self):
        self.validate()
        payload = {'contract': self.execution_contract.fingerprint,
            'plans': [(d, q, batch, p.fingerprint) for d, q, batch, p in self.plans]}
        if self.execution_kind != 'REAL':
            payload['execution_kind'] = self.execution_kind
        if self.rerank_plans:
            payload['rerank_plans'] = [(d, q, batch, p.fingerprint) for d, q, batch, p in self.rerank_plans]
        if self.late_rerank:
            payload['late_rerank'] = [(d, p.identity) for d, p in self.late_rerank]
        return _canonical_hash(payload)

    def plan_for(self, dataset, qid, purpose='query_embedding'):
        self.validate()
        if purpose not in {'query_embedding', 'rerank'}:
            raise ValueError('paired_shared_retrieval_purpose_invalid')
        plans = self.rerank_plans if purpose == 'rerank' else self.plans
        return next(p for d, q, _, p in plans if (d, q) == (dataset, qid))

    def batch_id_for(self, dataset, qid, purpose='query_embedding'):
        self.plan_for(dataset, qid, purpose)
        plans = self.rerank_plans if purpose == 'rerank' else self.plans
        return next(batch for d, q, batch, _ in plans if (d, q) == (dataset, qid))


def _validate_execution_contract(contract, *, cohort, manifest, cohort_hash, source_hash,
                                 index_hash, run_id, scopes, identities=None):
    from eval_center.paired_state import PairedCohortContract, PairedContractError
    if type(contract) is not PairedCohortContract:
        raise PairedContractError('paired_execution_contract_invalid')
    contract.validate_cohort(cohort)
    if (contract.cohort_sha256 != cohort_hash or contract.source_manifest_sha256 != source_hash
            or contract.index_manifest_sha256 != index_hash or contract.cohort_run_id != run_id):
        raise PairedContractError('paired_execution_frozen_identity_mismatch')
    names = [r['dataset'] for r in manifest['datasets']]
    selected = {s.dataset: s for s in contract.selections}
    if (len(set(names)) != len(names) or not set(selected) <= set(names)
            or set(scopes) != set(selected)
            or any(scopes[n] != s.scope for n, s in selected.items())):
        raise PairedContractError('paired_execution_scope_or_dataset_mismatch')
    if contract.phase == 'BATCH' and set(names) != set(selected):
        raise PairedContractError('exact_public_dataset_manifest')
    if identities is not None and identities != {n: s.index_identity_sha256 for n, s in selected.items()}:
        raise PairedContractError('paired_execution_index_identity_mismatch')


class PairedRuntimeAssemblyBlocked(ExperimentInvalidError):
    def __init__(self, missing):
        self.missing = sorted(set(missing))
        super().__init__("paired_runtime_assembly_blocked")


def _validate_paired_query_embedding(item, *, qids, cohort, run_id,
                                     source_sha256, execution_kind, deadline_utc, required,
                                      owner_activity_id=None, execution_lease=None, retrieval_binding=None, dataset=None):
    """Admit a native query plan; an INDEXING-only permit grants no retrieval."""
    from backend.app.ports.xing_zero_reservation import SiliconFlowFreeBinding
    from backend.app.domain.model_registry import ModelRegistry
    from eval_center.paired_state import CurrentProviderSendClaims, stable_provider_request_id
    bindings = item.free_model_bindings
    if type(bindings) is not dict or set(bindings) - {'embedding', 'rerank'}:
        raise ValueError('free_model_bindings')
    binding = bindings.get('embedding') or getattr(item.embedding_provider, 'free_binding', None)
    if binding is None and not required:
        return
    if type(binding) is not SiliconFlowFreeBinding:
        raise ValueError('typed_query_embedding_binding')
    permit = binding.permit
    permit.validate(ModelRegistry.frozen_defaults().select('embedding'), binding.authorized_transport)
    query_run_id = run_id
    if retrieval_binding is not None:
        if type(retrieval_binding) is not PairedSharedRetrievalBinding:
            raise ValueError('paired_shared_retrieval_binding_invalid')
        retrieval_binding.validate()
        if retrieval_binding.execution_kind != execution_kind:
            raise ValueError('same_shared_retrieval_execution_kind_required')
        contract = retrieval_binding.execution_contract
        if (contract.cohort_run_id != run_id or contract.source_manifest_sha256 != source_sha256
                or contract.execution_lease is not execution_lease
                or permit.execution_lease is not execution_lease):
            raise ValueError('paired_shared_retrieval_contract_mismatch')
        query_run_id = contract.retrieval_run_id
        from backend.app.ports.xing_zero_reservation import free_dispatch_contract, free_dispatch_identity_sha256
        for call in permit.calls:
            plan = retrieval_binding.plan_for(dataset, call.qid)
            native = plan.calls[0]
            if (plan.provider != 'siliconflow' or plan.model != permit.model
                    or retrieval_binding.batch_id_for(dataset, call.qid) != call.batch_id
                    or plan.scope_sha256 != free_dispatch_contract(permit, call)['scope_sha256']
                    or plan.query_sha256 != call.query_sha256 or native.input_sha256 != call.input_sha256
                    or native.wire_request_sha256 != call.wire_sha256
                    or native.request_identity_sha256 != free_dispatch_identity_sha256(permit, call)):
                raise ValueError('paired_shared_retrieval_wire_plan_mismatch')
    elif required:
        raise ValueError('independent_shared_retrieval_binding_required')
    if (deadline_utc is None and (owner_activity_id is None
            or permit.owner_activity_id != owner_activity_id or permit.execution_lease is not execution_lease
            or permit.owner_deadline()!=execution_lease.deadline)):
        raise ValueError('shared_query_embedding_owner_activity')
    if (permit.run_id != query_run_id or permit.scope != item.scope
            or permit.source_manifest_sha256 != source_sha256
            or permit.deadline_utc != deadline_utc or permit.execution_kind != execution_kind
            or type(binding.authorized_transport.send_claim_callback) is not CurrentProviderSendClaims
            or {c.qid for c in permit.calls} != qids
            or any(c.purpose != 'query_embedding' or c.arm != 'SHARED_RETRIEVAL'
                   for c in permit.calls)):
        raise ValueError('independent_shared_query_embedding_plan')
    questions = {row['qid']: row['question'] for row in cohort if row['qid'] in qids}
    for call in permit.calls:
        query_sha = hashlib.sha256(questions[call.qid].encode('utf-8')).hexdigest()
        if (call.query_sha256 != query_sha or call.input_sha256 != (query_sha,)
                or call.item_count != 1 or call.timeout_seconds != 10
                or call.request_id != stable_provider_request_id(run_id=query_run_id, qid=call.qid,
                    arm='SHARED_RETRIEVAL', purpose='query_embedding', call_ordinal=call.call_ordinal)):
            raise ValueError('frozen_query_embedding_identity')


def _validate_paired_rerank(item, *, qids, cohort, run_id, source_sha256,
                            execution_kind, execution_lease, retrieval_binding, dataset):
    """Independent immutable plan; never admit ranking through the query permit."""
    declared = type(retrieval_binding) is PairedSharedRetrievalBinding and bool(retrieval_binding.rerank_plans or retrieval_binding.late_rerank)
    if not item.settings.rerank_enabled:
        if declared:
            raise ValueError('declared_rerank_requires_normal_ranker')
        return
    from backend.app.ports.xing_zero_reservation import SiliconFlowFreeBinding, free_dispatch_contract, free_dispatch_identity_sha256
    from backend.app.domain.model_registry import ModelRegistry
    from eval_center.paired_state import CurrentProviderSendClaims, stable_provider_request_id
    if not declared:
        raise ValueError('shared_retrieval_rerank_requires_separate_plan')
    if not item.settings.cloud_enabled or not item.settings.rerank_egress_enabled:
        raise ValueError('shared_rerank_egress_must_be_explicitly_enabled')
    retrieval_binding.validate()
    if retrieval_binding.late_rerank:
        from eval_center.paired_preparation import validate_late_rerank_preparation
        validate_late_rerank_preparation(item, retrieval_binding, dataset, execution_kind, source_sha256)
        return
    binding = item.free_model_bindings.get('rerank')
    if type(binding) is not SiliconFlowFreeBinding:
        raise ValueError('typed_shared_rerank_binding_required')
    permit = binding.permit
    permit.validate(ModelRegistry.frozen_defaults().select('rerank'), binding.authorized_transport)
    embedding = item.free_model_bindings['embedding'].permit
    if (permit.run_id != retrieval_binding.execution_contract.retrieval_run_id
            or retrieval_binding.execution_contract.cohort_run_id != run_id
            or permit.scope != item.scope or permit.source_manifest_sha256 != source_sha256
            or permit.execution_kind != execution_kind or permit.execution_lease is not execution_lease
            or permit.owner_activity_id != embedding.owner_activity_id
            or permit.authority_manifest_sha256 != embedding.authority_manifest_sha256
            or permit.deadline_utc != embedding.deadline_utc
            or type(binding.authorized_transport.send_claim_callback) is not CurrentProviderSendClaims
            or {c.qid for c in permit.calls} != qids
            or any(c.purpose != 'rerank' or c.arm != 'SHARED_RETRIEVAL' for c in permit.calls)):
        raise ValueError('independent_shared_rerank_plan')
    questions = {r['qid']:r['question'] for r in cohort if r['qid'] in qids}
    for call in permit.calls:
        plan = retrieval_binding.plan_for(dataset, call.qid, 'rerank')
        native = plan.calls[0]
        if (plan.provider != permit.provider or plan.model != permit.model
                or plan.scope_sha256 != free_dispatch_contract(permit, call)['scope_sha256']
                or plan.query_sha256 != hashlib.sha256(questions[call.qid].encode()).hexdigest()
                or plan.query_sha256 != call.query_sha256
                or (native.document_version_ids, native.chunk_ids, native.input_sha256, native.candidate_order)
                   != (call.document_version_ids, call.chunk_ids, call.input_sha256, call.candidate_order)
                or native.wire_request_sha256 != call.wire_sha256
                or native.request_identity_sha256 != free_dispatch_identity_sha256(permit, call)
                or call.batch_id != retrieval_binding.batch_id_for(dataset, call.qid, 'rerank')
                or call.item_count != len(call.candidate_order) or call.call_ordinal != 1
                or call.request_id != stable_provider_request_id(run_id=permit.run_id, qid=call.qid,
                    arm='SHARED_RETRIEVAL', purpose='rerank', call_ordinal=1)):
            raise ValueError('frozen_shared_rerank_identity')


class _NativeSharedRetrievalReranker:
    """Delegate unchanged normal ranker under its separate frozen native plan."""
    provider_kind = 'cloud'
    def __init__(self, ranker, binding, dataset):
        from backend.app.adapters.models.cloud import SiliconFlowRerank
        if type(ranker) is not SiliconFlowRerank:
            raise ValueError('normal_shared_reranker_required')
        self.original, self.binding, self.dataset = ranker, binding, dataset

    def __getattr__(self, name):
        return getattr(self.original, name)

    def rank(self, question, hits, chunks):
        from eval_center.paired_state import (CurrentProviderSendClaims, SQLiteProviderBatchSendClaims,
            provider_send_claim_scope, validate_paired_run_link, provider_send_claim_record)
        from backend.app.ports.xing_zero_reservation import FreeModelRequestContext, free_model_requests
        query_claims = CurrentProviderSendClaims._bound()
        contract = self.binding.execution_contract
        if (type(query_claims) is not SQLiteProviderBatchSendClaims
                or query_claims.run_id != contract.retrieval_run_id
                or query_claims.arm != 'SHARED_RETRIEVAL' or query_claims.plan.purpose != 'query_embedding'):
            raise ExperimentInvalidError('shared_rerank_requires_query_claim_scope')
        receipt = provider_send_claim_record(query_claims.path,
            request_id=query_claims.request_id(purpose='query_embedding'))
        if receipt is None or receipt['status'] not in {'succeeded', 'simulated'}:
            raise ExperimentInvalidError('shared_rerank_requires_query_receipt')
        def lease_check(pin):
            if pin != contract.lease_sha256:
                raise ExperimentInvalidError('shared_rerank_lease_changed')
            validate_paired_run_link(query_claims.path, contract,
                retrieval_binding_sha256=self.binding.fingerprint, for_dispatch=True)
        plan = self.binding.plan_for(self.dataset, query_claims.qid, 'rerank')
        claims = SQLiteProviderBatchSendClaims(query_claims.path, query_claims.run_id, query_claims.qid,
            query_claims.arm, query_claims.input_identity_sha256, plan, contract.lease_sha256, lease_check)
        context = FreeModelRequestContext(claims.run_id, claims.request_id(purpose='rerank'), 'rerank',
            self.binding.batch_id_for(self.dataset, claims.qid, 'rerank'),
            self.original.free_binding.permit.scope, batch_plan=plan)
        lease_check(contract.lease_sha256)
        with provider_send_claim_scope(claims), free_model_requests((context,)):
            return self.original.rank(question, hits, chunks)


def bind_native_paired_time_profile(profile, composition):
    """Bind the Run profile to registered native Owner/lease configuration."""
    from eval_center.public_paired_reporting import PairedLeaseTimeProfile
    if type(profile) is not PairedLeaseTimeProfile:return profile
    try:
        leases=[item.v6_binding.activity_gate.execution_lease for item in composition.datasets.values()]
        native=next((lease for lease in leases if lease is not None),None)
        if native is None:
            if composition.execution_kind=='REAL' or profile.owner_deadline_utc is not None or profile.owner_not_before_utc is not None:
                raise ValueError('registered_native_execution_lease_required')
            return profile  # historical SIMULATED finite-window fixture only
        if any(lease is not native for lease in leases):raise ValueError('same_native_execution_lease')
        return profile.with_native_execution_lease(native)
    except (AttributeError,TypeError,ValueError) as exc:
        raise PairedRuntimeAssemblyBlocked(['native_execution_lease_profile_binding:'+type(exc).__name__]) from None


def build_normal_paired_runtime(composition, *, source_manifest_path: Path,
                                index_manifest_path: Path | None,
                                 cohort_path: Path, run_id: str | None, time_profile=None,
                                 execution_contract=None, _retrieval_only=False) -> PairedRuntime:
    """Admit explicit composition, then build the ordinary backend per dataset.

    This function constructs no grants, counters, credentials, indexes or ready
    booleans. It uses normal build_container/Factory/QuickChain, after fail-closed
    structural, capacity, index/scope and current owner guards. Tests may inject
    SIMULATED composition; its execution label is retained throughout.
    """
    from eval_center.paired_state import DATASETS, CurrentProviderSendClaims, sha256_file, load_frozen_cohort
    from backend.app.application.rule_router_v1 import V6GenerationBinding, V6XingGenerationBinding
    from backend.app.adapters.models.cloud import V6XingRequestWindow
    from backend.app.ports.xing_zero_reservation import SiliconFlowFreePermit
    from backend.app.application.validation_usd_budget import OwnerActivityTokenGrant, OWNER_V6_TOKEN_GRANT_R3
    from eval_center.public_paired_reporting import PairedLeaseTimeProfile
    from backend.app.domain.model_registry import ModelRegistry
    from backend.app.ports.session_attempts import request_identity
    from eval_center.verified_index import validate_index_identity
    missing = []
    if type(composition) is not PairedRuntimeComposition or composition.execution_kind not in {"REAL", "SIMULATED"}:
        raise PairedRuntimeAssemblyBlocked(["typed_runtime_composition"])
    real = composition.execution_kind == "REAL"
    manifest = json.loads(Path(source_manifest_path).read_text(encoding="utf-8"))
    contract = execution_contract or composition.execution_contract
    if execution_contract is not None and composition.execution_contract is not execution_contract:
        raise PairedRuntimeAssemblyBlocked(['same_execution_contract_required'])
    cohort = load_frozen_cohort(cohort_path, execution_contract=contract)
    names=tuple(dict.fromkeys(row['dataset'] for row in cohort))
    from eval_center.public_paired_reporting import time_profile as resolve_time_profile
    profile=bind_native_paired_time_profile(time_profile or resolve_time_profile(),composition)
    profile.validate(question_count=len(cohort),omni_page_count=manifest.get('omni_page_count',0))
    if set(composition.datasets) != set(names):
        raise PairedRuntimeAssemblyBlocked(["exact_dataset_composition"])
    error=profile.availability_error(datetime.now(timezone.utc))
    if error:raise PairedRuntimeAssemblyBlocked([error])
    if not callable(composition.dispatch_guard):
        missing.append("current_owner_account_egress_budget_single_writer_guard")
    else:
        try:
            composition.dispatch_guard()
        except Exception as exc:
            missing.append("owner_guard:" + type(exc).__name__)
    cohort_hash, source_hash = sha256_file(cohort_path), sha256_file(source_manifest_path)
    expected_run = run_id or f"p7-v6-{'real' if real else 'sim'}-{cohort_hash[:12]}"
    retrieval_binding = composition.shared_retrieval_binding
    if contract is not None:
        try:
            _validate_execution_contract(contract, cohort=cohort, manifest=manifest,
                cohort_hash=cohort_hash, source_hash=source_hash,
                index_hash=sha256_file(index_manifest_path), run_id=expected_run,
                scopes={n: i.scope for n, i in composition.datasets.items()})
            if (type(retrieval_binding) is not PairedSharedRetrievalBinding
                    or retrieval_binding.execution_contract is not contract):
                raise ValueError('same_shared_retrieval_contract_required')
            retrieval_binding.validate()
            if retrieval_binding.execution_kind != composition.execution_kind:
                raise ValueError('same_shared_retrieval_execution_kind_required')
            if profile != profile.with_native_execution_lease(contract.execution_lease):
                raise ValueError('paired_contract_native_lease_required')
        except (AttributeError, TypeError, ValueError, OSError) as exc:
            raise PairedRuntimeAssemblyBlocked([str(exc)]) from None
    planned_deepseek_tokens, deepseek_bindings, window_ids = 0, [], {}
    if real:
        for name in ("source_id_for_chunk", "database_queries", "provider_observations", "configure_deadline"):
            if not callable(getattr(composition, name)):
                missing.append(name)
        if (index_manifest_path is None or not Path(index_manifest_path).is_file()
                or composition.index_manifest_sha256 != sha256_file(index_manifest_path)):
            missing.append("frozen_index_manifest")
        if not callable(composition.capacity_probe):
            missing.append("production_capacity_probe")
        else:
            try:
                estimates = composition.capacity_probe()
                if set(estimates) != {"chat_cheap", "chat_expensive"}:
                    raise ValueError("capacity_role_set")
                for name, estimate in estimates.items():
                    if getattr(estimate, "production_ready", None) is not True:
                        missing.append("production_capacity:" + name)
            except Exception as exc:
                missing.append("capacity_probe:" + type(exc).__name__)
    for name, item in composition.datasets.items():
        if type(item) is not PairedDatasetComposition or type(item.settings) is not Settings:
            missing.append("typed_dataset_settings:" + name)
            continue
        scope, v6, xing = item.scope, item.v6_binding, item.xing_binding
        if (type(scope) is not Scope or not scope.knowledge_base_ids
                or any(composition.cloud_allowed_by_kb.get(kb) is not True for kb in scope.knowledge_base_ids)):
            missing.append("server_scope_and_egress:" + name)
        if type(v6) is not V6GenerationBinding or type(v6.server_scope) is not Scope or v6.server_scope != scope:
            missing.append("exact_deepseek_server_scope:" + name)
        elif type(v6.send_claim_callback) is not CurrentProviderSendClaims:
            missing.append("native_deepseek_send_claim:" + name)
        if type(xing) is not V6XingGenerationBinding or type(getattr(xing.grant, "scope", None)) is not Scope or xing.grant.scope != scope:
            missing.append("exact_xing_server_scope:" + name)
        elif not _retrieval_only and type(getattr(xing.authorized_transport, "send_claim_callback", None)) is not CurrentProviderSendClaims:
            missing.append("native_xing_send_claim:" + name)
        qids = {row['qid'] for row in cohort if row['dataset'] == name}
        def answer_ids(arms, role):
            return {(qid, arm, 'answer'): request_identity(
                str(uuid.uuid5(uuid.NAMESPACE_URL, f'{expected_run}:{qid}:{arm}')),
                'quick.router.' + role, 1) for qid in qids for arm in arms}
        answer_window = getattr(xing, 'request_window', None)
        window = (item.free_model_bindings.get('embedding').permit
                  if _retrieval_only and item.free_model_bindings.get('embedding') is not None
                  else answer_window)
        if _retrieval_only and (contract is None or answer_window is not None
                or xing.authorized_transport is not None):
            missing.append('retrieval_preparation_requires_no_chat_permit:' + name)
        query_owner_activity_id,query_owner_deadline,query_execution_lease=None,profile.absolute_deadline_utc,None
        if contract is not None:
            query_execution_lease = contract.execution_lease
            if getattr(v6.activity_gate, 'execution_lease', None) is not query_execution_lease:
                missing.append('same_contract_native_lease:' + name)

        try:
            registered_r3=(type(window) is SiliconFlowFreePermit and window.owner_activity_id is not None)
            if registered_r3:
                # Read optional Owner bounds from the registered native lease;
                # the separate finite Run deadline is never an Owner constant.
                grant=v6.grant
                expected_grant=vars(OWNER_V6_TOKEN_GRANT_R3)
                if (type(profile) is not PairedLeaseTimeProfile
                        or type(grant) is not OwnerActivityTokenGrant
                        or (real and grant is not OWNER_V6_TOKEN_GRANT_R3)
                        or vars(grant)!={**expected_grant,'execution_kind':composition.execution_kind}
                        or grant is not v6.activity_gate.grant
                        or window.owner_activity_id!=OWNER_V6_TOKEN_GRANT_R3.grant_id
                        or window.execution_lease is not v6.activity_gate.execution_lease
                        or window.owner_deadline()!=v6.activity_gate.execution_lease.deadline
                        or window.owner_total_deadline_utc!=profile.owner_deadline_utc):
                    raise ValueError('registered_r3_owner_activity')
                query_owner_activity_id=OWNER_V6_TOKEN_GRANT_R3.grant_id
                query_owner_deadline=None
                query_execution_lease=v6.activity_gate.execution_lease
            elif real and type(profile) is PairedLeaseTimeProfile:
                raise ValueError('new_lease_requires_registered_r3_activity')
            if _retrieval_only:
                if not registered_r3:
                    raise ValueError('retrieval_preparation_requires_registered_owner')
                from backend.app.adapters.models.cloud import XingAuthorizedTransport
                XingAuthorizedTransport.validate_grant(xing.grant)
                if xing.grant.deadline_utc is not None or 'answer' not in xing.grant.allowed_purposes:
                    raise ValueError('registered_preparation_chat_grant_required')
                window_ids[name] = None  # no answer plan exists and none is admitted
            else:
                if (type(window) not in (V6XingRequestWindow, SiliconFlowFreePermit)
                        or window.run_id != expected_run or window.scope != scope
                        or window.source_manifest_sha256 != source_hash
                        or window.grant_id != xing.grant.grant_id
                        or window.deadline_utc != query_owner_deadline
                        or window.deadline_utc != xing.grant.deadline_utc
                        or xing.authorized_transport.grant is not xing.grant
                        or window.deadline_utc is not None and datetime.now(timezone.utc) >= datetime.fromisoformat(window.deadline_utc.replace('Z', '+00:00'))
                        or window.frozen_plan_sha256 != window.fingerprint):
                    raise ValueError('frozen_window_identity')
                if type(window) is V6XingRequestWindow:
                    if window.cohort_sha256 != cohort_hash:
                        raise ValueError('legacy_cohort_identity')
                elif (window.execution_kind != composition.execution_kind
                        or xing.authorized_transport.free_model_permit is not window):
                    raise ValueError('same_activity_free_permit_required')
                answers = { (c.qid, c.arm, c.purpose): c.request_id
                            for c in window.calls if c.purpose == 'answer' }
                if answers != answer_ids(('CHEAP_ONLY', 'DYNAMIC'), 'chat_cheap'):
                    raise ValueError('cheap_and_dynamic_worst_case_answers')
                if any(c.output_token_bound != PAIRED_ANSWER_OUTPUT_TOKEN_CAP
                       for c in window.calls if c.purpose == 'answer'):
                    raise ValueError('uniform_answer_output_token_cap')
                if any(c.qid not in qids for c in window.calls):
                    raise ValueError('helper_qid_outside_dataset')
                # The normal Factory validates every explicit helper, token bound,
                # native callback, grant, model and Scope before using this window.
                window.validate(ModelRegistry.frozen_defaults().select('chat_cheap'), xing.authorized_transport)
                window_ids[name] = window.fingerprint
        except (AttributeError, TypeError, ValueError):
            missing.append('frozen_xing_request_window:' + name)
        try:
            calls = item.deepseek_calls
            expected = answer_ids(('EXPENSIVE_ONLY', 'DYNAMIC'), 'chat_expensive')
            if (type(calls) is not tuple or len(calls) != len(expected)
                    or any(type(c) is not PairedProviderCallBudget for c in calls)
                    or {(c.qid, c.arm, c.purpose): c.request_id for c in calls} != expected
                    or len({c.request_id for c in calls}) != len(calls)
                    or any(type(c.input_token_bound) is not int or c.input_token_bound <= 0
                        or type(c.output_token_bound) is not int or c.output_token_bound != PAIRED_ANSWER_OUTPUT_TOKEN_CAP
                        or type(c.safety_tokens) is not int or c.safety_tokens < 0
                        or c.input_token_bound + c.output_token_bound + c.safety_tokens > 1_048_576
                        for c in calls)):
                raise ValueError('expensive_and_dynamic_worst_case_answers')
            planned_deepseek_tokens += sum(c.input_token_bound + c.output_token_bound + c.safety_tokens
                                           for c in calls)
            deepseek_bindings.append(v6)
        except (AttributeError, TypeError, ValueError):
            missing.append('deepseek_worst_case_calls:' + name)
        if real and any(getattr(getattr(binding, "grant", None), "execution_kind", None) != "REAL"
                        for binding in (v6, xing)):
            missing.append("real_typed_grants:" + name)
        settings = item.settings
        if (not settings.cloud_enabled or not settings.chat_egress_enabled or not settings.rule_router_enabled
                or settings.memory_local_principal is not None or settings.local_query_enabled
                or settings.local_answer_enabled or any((settings.rule_router_quality_escalation,
                    settings.rule_router_abstention_escalation, settings.rule_router_provider_fallback))):
            missing.append("fixed_policy_memory_and_egress_settings:" + name)
        if not callable(item.execution_context):
            missing.append("normal_run_lifecycle_context:" + name)
        try:
            _validate_paired_query_embedding(item, qids=qids, cohort=cohort,
                run_id=expected_run, source_sha256=source_hash,
                execution_kind=composition.execution_kind, deadline_utc=query_owner_deadline,
                required=real or query_owner_activity_id is not None,owner_activity_id=query_owner_activity_id,
                execution_lease=query_execution_lease, retrieval_binding=retrieval_binding, dataset=name)
            _validate_paired_rerank(item, qids=qids, cohort=cohort, run_id=expected_run,
                source_sha256=source_hash, execution_kind=composition.execution_kind,
                execution_lease=query_execution_lease, retrieval_binding=retrieval_binding, dataset=name)
        except (AttributeError, TypeError, ValueError):
            missing.append('independent_shared_query_embedding_plan:' + name)
        if real:
            try:
                binding = composition.index_bindings[name]
                actual = binding["actual"]
                source = next(row for row in manifest["datasets"] if row["dataset"] == name)
                if (binding["scope"] != scope or actual["dataset"] != name
                        or actual["dataset_version"] != source["dataset_version"]
                        or actual["corpus_sha256"] != source["corpus"]["sha256"]
                        or actual["development_cases_sha256"] != source["development_cases"]["sha256"]):
                    raise ValueError("scope_or_source_identity")
                receipt = validate_index_identity(binding["expected"], actual,
                    database_profile=binding.get('database_profile'), scope=scope)
                if contract is not None and receipt['identity_sha256'] != next(
                        s.index_identity_sha256 for s in contract.selections if s.dataset == name):
                    raise ValueError('paired_execution_index_identity_mismatch')
            except Exception as exc:
                missing.append("verified_readonly_index:" + name + ":" + type(exc).__name__)
    if len(deepseek_bindings) == len(names):
        first = deepseek_bindings[0]
        try:
            grant = first.grant
            if (any(b.grant is not grant or b.activity_gate is not first.activity_gate for b in deepseek_bindings)
                    or grant is not first.activity_gate.grant
                    or type(grant.token_limit) is not int or not 0 < grant.token_limit <= 1_000_000
                    or type(grant.baseline_used_tokens) is not int or grant.baseline_used_tokens < 0
                    or grant.baseline_used_tokens + planned_deepseek_tokens > grant.token_limit):
                raise ValueError('shared_activity_token_ceiling')
        except (AttributeError, TypeError, ValueError):
            missing.append('shared_deepseek_worst_case_1m_token_budget')
    if missing:
        raise PairedRuntimeAssemblyBlocked(missing)
    containers = {}
    for name, item in composition.datasets.items():
        # Normal bootstrap owns all provider wiring. No synthetic REAL role,
        # no factory substitution in production, no direct provider HTTP here.
        embedding_options = ({'v6_free_model_bindings': item.free_model_bindings}
            if item.free_model_bindings else {'embedding_provider': item.embedding_provider})
        late_rerank = bool(retrieval_binding is not None and retrieval_binding.late_rerank)
        if late_rerank:
            # No send-capable ranker exists until real candidates are sealed.
            bootstrap_item = replace(item, settings=replace(item.settings, rerank_enabled=False))
        else:
            bootstrap_item = item
        if _retrieval_only:
            # Chat is absent, including provider-factory enablement and grants.
            settings = replace(bootstrap_item.settings, chat_egress_enabled=False,
                rule_router_enabled=False, vision_egress_enabled=False)
            container = build_container(settings, model=None, agent_model=None, cloud_model=None,
                model_usage_guards={k: v for k, v in item.model_usage_guards.items()
                                    if k in ('embedding', 'rerank')},
                embedding_admission=item.embedding_admission, **embedding_options)
        else:
            container = build_container(bootstrap_item.settings, model=None, agent_model=None,
                v6_generation_binding=item.v6_binding, v6_xing_binding=item.xing_binding,
                model_usage_guards=item.model_usage_guards, embedding_admission=item.embedding_admission,
                **embedding_options)
        if contract is not None and item.settings.rerank_enabled:
            retriever = container.quick_chain.knowledge_gateway.retriever
            if late_rerank:
                from eval_center.paired_preparation import LateSharedRetrievalReranker
                retriever.ranker = LateSharedRetrievalReranker(item, retrieval_binding, name,
                    paths={'source_manifest_path': source_manifest_path, 'cohort_path': cohort_path,
                           'index_manifest_path': index_manifest_path}, dispatch_guard=composition.dispatch_guard)
            else:
                retriever.ranker = _NativeSharedRetrievalReranker(retriever.ranker, retrieval_binding, name)
        container = copy.copy(container)
        # The normal backend Container is frozen; attach only to this copy.
        object.__setattr__(container, 'execution_context',
            lambda run_id, c=container, item=item: item.execution_context(c, run_id, item.scope))
        containers[name] = container
    return PairedRuntime(containers=containers,
        scopes={name: item.scope for name, item in composition.datasets.items()},
        cloud_allowed_by_kb=composition.cloud_allowed_by_kb,
        dispatch_guard=composition.dispatch_guard, execution_kind=composition.execution_kind,
        index_bindings=composition.index_bindings, capacity_probe=composition.capacity_probe,
        source_id_for_chunk=composition.source_id_for_chunk, database_queries=composition.database_queries,
        provider_observations=composition.provider_observations, configure_deadline=composition.configure_deadline,
        langfuse_client=composition.langfuse_client, index_manifest_sha256=composition.index_manifest_sha256,
        settlement_observer=composition.settlement_observer,
        source_annotations_for_chunk=composition.source_annotations_for_chunk,
        execution_contract=contract, shared_retrieval_binding=retrieval_binding,
        composition_identity_sha256=_canonical_hash({'entry_sha256': composition.entry_sha256,
            'execution_contract': None if contract is None else contract.fingerprint,
            'shared_retrieval': None if retrieval_binding is None else retrieval_binding.fingerprint,
            'answer_output_token_cap': PAIRED_ANSWER_OUTPUT_TOKEN_CAP,
            'datasets': {name: {
            "scope": {"kb": sorted(item.scope.knowledge_base_ids), "documents": sorted(item.scope.document_ids)},
            "deepseek_grant": item.v6_binding.grant.grant_id,
            "xing_grant": item.xing_binding.grant.grant_id,
            "database_identity_sha256": hashlib.sha256(item.settings.database_url.encode()).hexdigest(),
            "xing_request_window_sha256": window_ids[name],
            "free_model_plan_sha256": {role: b.permit.fingerprint
                for role, b in item.free_model_bindings.items()},
            "deepseek_calls": [vars(c) for c in item.deepseek_calls],
        } for name, item in composition.datasets.items()}}))


def _paired_runtime_admission(runtime, *, manifest, index_manifest_path, execution_contract=None,
                               cohort=None, cohort_sha256=None, source_sha256=None, run_id=None,
                               _retrieval_only=False):
    from eval_center.paired_state import DATASETS, sha256_file
    missing, identities = [], {}
    if runtime is None:
        return ["owner_runtime_injection", "owner_grants", "guarded_roles",
                "production_capacity", "frozen_index_manifest"], identities
    if type(runtime) is not PairedRuntime or runtime.execution_kind not in {"SIMULATED", "REAL"}:
        return ["invalid_runtime_contract"], identities
    if not callable(runtime.dispatch_guard):
        return ["current_account_egress_budget_and_single_writer_guard"], identities
    try:
        runtime.dispatch_guard()
    except Exception as exc:
        missing.append("runtime_guard:" + type(exc).__name__)
    real = runtime.execution_kind == "REAL"
    if real and not callable(runtime.database_queries):
        missing.append("observed_database_query_counter")
    if real and not callable(runtime.source_id_for_chunk):
        missing.append("public_source_document_mapper")
    if real and not callable(runtime.configure_deadline):
        missing.append("bounded_synchronous_io_deadline_binding")
    if real:
        if (index_manifest_path is None or not Path(index_manifest_path).is_file()
                or runtime.index_manifest_sha256 != sha256_file(index_manifest_path)):
            missing.append("frozen_index_manifest")
        if not callable(runtime.capacity_probe):
            missing.append("production_capacity_probe")
        else:
            try:
                estimates = runtime.capacity_probe()
                if set(estimates) != {"chat_cheap", "chat_expensive"}:
                    raise ValueError("capacity_role_set")
                for role, estimate in estimates.items():
                    if getattr(estimate, "production_ready", None) is not True:
                        missing.append("production_capacity:" + role)
            except Exception as exc:
                missing.append("capacity_probe:" + type(exc).__name__)
    from eval_center.paired_state import FULL_QA_DATASETS
    names=tuple(row['dataset'] for row in manifest['datasets'])
    if execution_contract is not None:
        try:
            if (runtime.execution_contract is not execution_contract
                    or type(runtime.shared_retrieval_binding) is not PairedSharedRetrievalBinding
                    or runtime.shared_retrieval_binding.execution_contract is not execution_contract
                    or runtime.composition_identity_sha256 is None):
                raise ValueError('same_assembled_execution_contract_required')
            runtime.shared_retrieval_binding.validate()
            _validate_execution_contract(execution_contract, cohort=cohort, manifest=manifest,
                cohort_hash=cohort_sha256, source_hash=source_sha256,
                index_hash=sha256_file(index_manifest_path), run_id=run_id, scopes=runtime.scopes)
            names = tuple(s.dataset for s in execution_contract.selections)
        except (AttributeError, TypeError, ValueError, OSError) as exc:
            return ['execution_contract:' + str(exc)], identities
    elif set(names) not in (set(DATASETS),set(FULL_QA_DATASETS)) or len(set(names))!=len(names):
        return ['exact_public_dataset_manifest'], identities
    if set(runtime.containers)!=set(names) or set(runtime.scopes)!=set(names):
        missing.append('exact_runtime_dataset_set')
    for dataset in names:
        container = runtime.containers.get(dataset)
        scope = runtime.scopes.get(dataset)
        if (type(scope) is not Scope or not scope.knowledge_base_ids
                or not all(runtime.cloud_allowed_by_kb.get(k) is True for k in scope.knowledge_base_ids)):
            missing.append("server_scope_and_egress:" + dataset)
        chain = getattr(container, "quick_chain", None)
        if type(chain) is not LangChainQuickChain:
            missing.append("project_quickchain:" + dataset)
            continue
        if not callable(getattr(container, "execution_context", None)):
            missing.append("run_lifecycle_context:" + dataset)
        if _retrieval_only:
            if (chain.generation_roles or chain.answer_gateway is not None
                    or chain.cloud_answer_gateway is not None
                    or {'chat_cheap', 'chat_expensive', 'vision'} & container.provider_factory.enabled_roles):
                missing.append('retrieval_preparation_chat_capability:' + dataset)
        if not _retrieval_only:
            if real:
                # A normal backend must expose its actual guarded policy admission;
                # neither source-string heuristics nor injected ready bools prove it.
                policy_admission = getattr(chain, "validate_generation_policy", None)
                if not callable(policy_admission):
                    missing.append("backend_live_fixed_route_support:" + dataset)
                else:
                    try:
                        for arm in ("CHEAP_ONLY", "DYNAMIC", "EXPENSIVE_ONLY"):
                            policy_admission(RouterPolicy(mode=arm, threshold=Decimal("0.35"),
                                quality_escalation=False, abstention_escalation=False,
                                provider_fallback=False, truncated_escalation=False))
                    except Exception as exc:
                        missing.append("backend_live_fixed_route_support:" + dataset + ":" + type(exc).__name__)
            roles = chain.generation_roles
            for role_name in ("chat_cheap", "chat_expensive"):
                role = roles.get(role_name)
                from backend.app.application.rule_router_v1 import GenerationRole
                if type(role) is not GenerationRole:
                    missing.append("guarded_role:" + role_name)
                    continue
                try:
                    role.check()
                except Exception as exc:
                    missing.append("role_check:" + role_name + ":" + type(exc).__name__)
                usage_guard = getattr(role.gateway, "usage_guard", None) if role_name == "chat_cheap" else role.usage_guard
                if usage_guard is None:
                    missing.append("usage_guard:" + role_name)
                if real:
                    binding = getattr(role, "v6_binding" if role_name == "chat_expensive" else "xing_binding", None)
                    if role.simulated or getattr(getattr(binding, "grant", None), "execution_kind", None) != "REAL":
                        missing.append("real_typed_grant:" + role_name)
                    bound_scope = (getattr(binding, "server_scope", None) if role_name == "chat_expensive"
                                   else getattr(getattr(binding, "grant", None), "scope", None))
                    if type(bound_scope) is not Scope or bound_scope != scope:
                        missing.append("exact_model_server_scope:" + dataset + ":" + role_name)
                    from eval_center.paired_state import CurrentProviderSendClaims
                    if type(getattr(getattr(role.gateway, "transport", None), "send_claim_callback", None)) is not CurrentProviderSendClaims:
                        missing.append("native_persistent_send_claim:" + dataset + ":" + role_name)
                elif getattr(role, "execution_kind", None) != "SIMULATED" or getattr(role.gateway, "execution_kind", None) != "SIMULATED":
                    missing.append("explicit_simulated_role:" + role_name)
        entry = next(row for row in manifest["datasets"] if row["dataset"] == dataset)
        if not real:
            identities[dataset] = _canonical_hash({"execution_kind": "SIMULATED",
                "corpus_sha256": entry["corpus"]["sha256"], "index": "NO_REAL_INDEX"})
        else:
            try:
                from eval_center.verified_index import validate_index_identity
                binding = runtime.index_bindings[dataset]
                actual = binding["actual"]
                if (actual["dataset"] != dataset or actual["dataset_version"] != entry["dataset_version"]
                        or actual["corpus_sha256"] != entry["corpus"]["sha256"]
                        or actual["development_cases_sha256"] != entry["development_cases"]["sha256"]):
                    raise ValueError("index_source_binding")
                # KB/document scope must be independently bound by the owner
                # index observation, rather than inferred from model grants.
                if binding["scope"] != scope:
                    raise ValueError("index_scope_binding")
                receipt = validate_index_identity(binding["expected"], actual,
                    database_profile=binding.get('database_profile'), scope=scope)
                identities[dataset] = receipt["identity_sha256"]
            except Exception as exc:
                missing.append("verified_readonly_index:" + dataset + ":" + type(exc).__name__)
    if execution_contract is not None and identities != {
            s.dataset: s.index_identity_sha256 for s in execution_contract.selections}:
        missing.append('paired_execution_index_identity_mismatch')
    return sorted(set(missing)), identities


def _paired_cohort_success_matrix(cohort, results, receipts_by_qid, execution_kind):
    """Completion is exact successful evidence, not mere saved case/attempt rows."""
    from eval_center.paired_state import ARMS
    expected = {row['qid']: row['dataset'] for row in cohort}
    saved = {row['qid']: row for row in results}
    if (not expected or len(expected) != len(cohort) or len(saved) != len(results)
            or set(saved) != set(expected) or set(receipts_by_qid) != set(expected)):
        return False
    success = {'succeeded', 'simulated'} if execution_kind == 'SIMULATED' else {'succeeded'}
    arms = set(ARMS)
    for qid, dataset in expected.items():
        result, receipts = saved[qid], receipts_by_qid[qid]
        cells = result.get('arms', {})
        if (result.get('dataset') != dataset or result.get('status') != 'PAIRED_INPUTS_VERIFIED'
                or set(cells) != arms or set(receipts) != arms):
            return False
        for arm in arms:
            cell, receipt = cells[arm], receipts[arm]
            native_result = receipt.get('result')
            if (cell.get('attempt_status') not in success or cell.get('error_code')
                    or receipt.get('status') != cell.get('attempt_status') or receipt.get('error_code')
                    or not isinstance(native_result, dict) or native_result.get('error_code')
                    or any(child.get('status') not in success or child.get('error_code')
                           for child in receipt.get('provider_calls', ()) )):
                return False
    return True


def run_public_paired_cohort(*, cohort_path: Path, source_path: Path, data_root: Path,
                             state_path: Path, run_id: str | None, runtime_factory,
                              index_manifest_path=None, top_k=5, time_profile=None,
                              execution_contract=None) -> dict[str, Any]:
    """The single admission, scheduling, persistence and scoring path for both CLI phases."""
    from eval_center.paired_state import (ARMS, DATASETS, attempts_for_qid, close_run,
        exclusive_runner_lock, initialize_state, load_case_results, load_frozen_cohort,
        register_run, run_record, sha256_file)
    if type(top_k) is not int or not 1 <= top_k <= 10000:
        raise ExperimentInvalidError("paired_top_k_invalid")
    cohort_path, source_path = Path(cohort_path).resolve(), Path(source_path).resolve()
    cohort = load_frozen_cohort(cohort_path, execution_contract=execution_contract)
    manifest = json.loads(source_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "FROZEN_FOR_PREPARATION":
        raise ExperimentInvalidError("paired_source_manifest_not_frozen")
    cohort_hash, source_hash = sha256_file(cohort_path), sha256_file(source_path)
    datasets, cases = {}, {}
    names=tuple(dict.fromkeys(row['dataset'] for row in cohort))
    for name in names:
        entry = next(row for row in manifest["datasets"] if row["dataset"] == name)
        if name in DATASETS:
            dataset = load_public_dataset(name, data_root, split="development", phase="baseline")
        else:
            from eval_center.special_public_data import load_special_datasets
            dataset=load_special_datasets(entry['freeze_path'],expected_sha256=entry['freeze_sha256'],
                asset_root=entry.get('asset_root'))[name]
        if dataset.dataset_version != entry["dataset_version"]:
            raise ExperimentInvalidError("paired_dataset_version_mismatch")
        for key in ("corpus", "development_cases", "source_manifest"):
            if sha256_file(Path(entry[key]["path"])) != entry[key]["sha256"]:
                raise ExperimentInvalidError("paired_prepared_source_changed")
        datasets[name] = dataset
        cases.update({(name, case.qid): case for case in dataset.cases})
    for row in cohort:
        source_case = cases.get((row["dataset"], row["qid"]))
        if source_case is None:
            raise ExperimentInvalidError("paired_frozen_qid_missing_from_verified_source")
        if (source_case.question != row["question"] or source_case.qrels != row["qrels"]
                or source_case.answers != tuple(row.get("answers", ()))
                or source_case.evaluation_metadata != row.get('evaluation_metadata')
                or datasets[row["dataset"]].dataset_version != row["dataset_version"]):
            raise ExperimentInvalidError("paired_frozen_case_mismatch")
    repo = Path(__file__).resolve().parents[1]
    snapshot = hashlib.sha256()
    evaluator_paths = [repo / "eval_center" / name for name in
                       ("paired_state.py", "public_runner.py", "public_benchmark_cli.py", "paired_simulation.py", "paired_preparation.py",
                        "public_data.py", "public_paired_reporting.py", "special_public_data.py", "omni_contract.py")]
    for path in evaluator_paths:
        snapshot.update(path.relative_to(repo).as_posix().encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    code_sha = snapshot.hexdigest()
    state_path = Path(state_path).resolve()
    runtime = runtime_factory(state_path) if callable(runtime_factory) else None
    from eval_center.paired_preparation import PairedRetrievalPreparation
    preparation = runtime if type(runtime) is PairedRetrievalPreparation else None
    if preparation is not None:
        runtime = preparation.runtime
    missing, index_ids = _paired_runtime_admission(runtime, manifest=manifest,
        index_manifest_path=index_manifest_path, execution_contract=execution_contract,
        cohort=cohort, cohort_sha256=cohort_hash, source_sha256=source_hash, run_id=run_id,
        _retrieval_only=preparation is not None)
    if missing:
        return {"status": "BLOCKED_ZERO_SEND", "dispatch": "NOT_STARTED", "missing_gates": missing,
                "provider_calls": 0, "send_attempts": 0, "quickchain_invocations": 0,
                "database_queries": 0 if runtime is None else "UNKNOWN_PREFLIGHT_OBSERVER",
                "cohort_rows": len(cohort), "cohort_sha256": cohort_hash,
                "source_manifest_sha256": source_hash, "container": "EXPLICIT_RUNTIME_INJECTION_REQUIRED"}
    kind = runtime.execution_kind
    run_id = run_id or f"p7-v6-{'sim' if kind == 'SIMULATED' else 'real'}-{cohort_hash[:12]}"
    runtime_id = _canonical_hash({"execution_kind": kind, "indexes": index_ids, "top_k": top_k,
        "composition_identity_sha256": runtime.composition_identity_sha256,
        "native_xing_windows": {name: getattr(getattr(getattr(
            container.quick_chain.generation_roles.get('chat_cheap'), 'xing_binding', None),
            'request_window', None), 'frozen_plan_sha256', None)
            for name, container in runtime.containers.items()},
        "policy": {"threshold": "0.35", "memory": False, "fallback": False, "escalation": False,
                   "answer_output_token_cap": PAIRED_ANSWER_OUTPUT_TOKEN_CAP},
        "scopes": {name: {"kb": sorted(scope.knowledge_base_ids), "documents": sorted(scope.document_ids)}
                   for name, scope in runtime.scopes.items()}})
    from eval_center.public_paired_reporting import time_profile as resolve_time_profile
    profile=time_profile or resolve_time_profile()
    profile.validate(question_count=len(cohort),omni_page_count=manifest.get('omni_page_count',0))
    runtime_id=_canonical_hash({'runtime':runtime_id,'time_profile':vars(profile)})
    if profile.name in {'owner-40q-24page','owner-lease-40q-24page'}:
        record=manifest['omni_pages']
        if sha256_file(Path(record['path']))!=record['sha256']:raise ExperimentInvalidError('omni_page_contract_changed')
    now = datetime.now(timezone.utc)
    if profile.name.startswith('owner-lease-'):
        error=profile.availability_error(now)
        if error:raise PairedRuntimeAssemblyBlocked([error])
    hard,dispatch,end=profile.deadlines(now)
    failures, reused = [], 0
    initial_observed_calls = 0
    with exclusive_runner_lock(state_path):
        # Pairing tables extend the existing Evaluation Center registry in the
        # same SQLite file; no second experiment registry or billing ledger.
        from eval_center.store import initialize_database
        initialize_database(state_path)
        initialize_state(state_path)
        prior = run_record(state_path, run_id)
        if prior:
            dispatch = datetime.fromisoformat(prior["dispatch_deadline_utc"])
            end = datetime.fromisoformat(prior["round_deadline_utc"])
            hard = datetime.fromisoformat(prior["deadline_utc"])
        register_run(state_path, run_id=run_id, cohort_sha256=cohort_hash,
            source_manifest_sha256=source_hash, code_sha=_canonical_hash({"code": code_sha, "runtime": runtime_id}),
            deadline_utc=hard.isoformat(), dispatch_deadline_utc=dispatch.isoformat(),
            round_deadline_utc=end.isoformat(), expected_qids=[row["qid"] for row in cohort])
        if execution_contract is not None:
            from eval_center.paired_state import register_paired_run_link
            register_run(state_path, run_id=execution_contract.retrieval_run_id,
                cohort_sha256=cohort_hash, source_manifest_sha256=source_hash,
                code_sha=_canonical_hash({'code': code_sha, 'runtime': runtime_id}),
                deadline_utc=hard.isoformat(), dispatch_deadline_utc=dispatch.isoformat(),
                round_deadline_utc=end.isoformat(), expected_qids=[row['qid'] for row in cohort],
                execution_purpose='RETRIEVAL')
            register_paired_run_link(state_path, execution_contract,
                retrieval_binding_sha256=runtime.shared_retrieval_binding.fingerprint)
        if preparation is not None:
            runtime = preparation.bind_after_retrieval(state_path=state_path, cohort=cohort,
                manifest=manifest, index_manifest_path=index_manifest_path, top_k=top_k)
            missing, sealed_indexes = _paired_runtime_admission(runtime, manifest=manifest,
                index_manifest_path=index_manifest_path, execution_contract=execution_contract,
                cohort=cohort, cohort_sha256=cohort_hash, source_sha256=source_hash, run_id=run_id)
            if missing or sealed_indexes != index_ids:
                raise PairedRuntimeAssemblyBlocked(missing or ['prepared_index_identity_changed'])
        if callable(runtime.configure_deadline):
            runtime.configure_deadline(min(end, hard))

        def work_guard():
            if datetime.now(timezone.utc) >= min(end, hard):
                raise ExperimentInvalidError("paired_round_deadline_reached")

        def dispatch_guard():
            work_guard()
            if datetime.now(timezone.utc) >= min(dispatch, hard):
                raise ExperimentInvalidError("paired_dispatch_deadline_reached")
            if sha256_file(cohort_path) != cohort_hash or sha256_file(source_path) != source_hash:
                raise ExperimentInvalidError("paired_frozen_source_changed")
            if any(sha256_file(path) != initial_source_hashes[path] for path in evaluator_paths):
                raise ExperimentInvalidError("paired_evaluator_source_changed")
            runtime.dispatch_guard()

        initial_source_hashes = {path: sha256_file(path) for path in evaluator_paths}
        completed = {result["qid"] for result in load_case_results(state_path, run_id)}
        for row in cohort:
            for receipt in attempts_for_qid(state_path, run_id=run_id, qid=row["qid"]).values():
                initial_observed_calls += (receipt["result"] or {}).get("observed_quickchain_invocations", 0)
        for row in cohort:
            if row["qid"] in completed:
                reused += 1
                continue
            prior_attempts = attempts_for_qid(state_path, run_id=run_id, qid=row["qid"])
            if prior_attempts:
                # A crash may leave a partial QID. Preserve all cells, do not
                # reconstruct context by a new retrieval or automatically send.
                failures.append({"qid": row["qid"], "status": "CARRIED_NO_RETRY",
                                 "attempts": {arm: item["status"] for arm, item in prior_attempts.items()}})
                continue
            try:
                dispatch_guard()
                entry = next(item for item in manifest["datasets"] if item["dataset"] == row["dataset"])
                selected = [item for item in cohort if item["dataset"] == row["dataset"]]
                identity = {"corpus_sha256": entry["corpus"]["sha256"],
                    "pool_sha256": _canonical_hash(entry["candidate_pool"]),
                    "qrels_sha256": _canonical_hash({item["qid"]: item["qrels"] for item in selected}),
                    "gold_sha256": _canonical_hash({item["qid"]: {'answers':item.get("answers", []),
                        'evaluation_metadata':item.get('evaluation_metadata')} for item in selected}),
                    "index_sha256": index_ids[row["dataset"]]}
                # Keep the same timeout in all three envelopes. Do not start
                # a QID unless its three synchronous 30s calls fit the round.
                if (min(end, hard) - datetime.now(timezone.utc)).total_seconds() < 95:
                    raise ExperimentInvalidError("paired_round_insufficient_call_window")
                result = run_public_paired_generation_case(
                    runtime.containers[row["dataset"]], cases[(row["dataset"], row["qid"])],
                    runtime.scopes[row["dataset"]], run_id=run_id, dataset_name=row["dataset"],
                    dataset_version=row["dataset_version"], state_path=state_path,
                    shared_identity=identity, cloud_allowed_by_kb=runtime.cloud_allowed_by_kb,
                    dispatch_guard=dispatch_guard, work_guard=work_guard,
                    langfuse_client=runtime.langfuse_client, execution_kind=kind,
                    source_id_for_chunk=runtime.source_id_for_chunk, top_k=top_k,
                    settlement_observer=runtime.settlement_observer,
                    source_annotations_for_chunk=runtime.source_annotations_for_chunk,
                    shared_retrieval_binding=runtime.shared_retrieval_binding)
                if result["status"] != "PAIRED_INPUTS_VERIFIED":
                    failures.append({"qid": row["qid"], "status": result["status"]})
                for arm,cell in result['arms'].items():
                    if cell['attempt_status'] not in {'simulated','succeeded'} or cell['error_code']:
                        failures.append({'qid':row['qid'],'arm':arm,'status':cell['attempt_status'],
                            'error_code':cell['error_code']})
            except Exception as exc:
                failures.append({"qid": row["qid"], "status": "FAILED_RETAINED",
                                 "error_code": str(exc) if isinstance(exc, ExperimentInvalidError) else type(exc).__name__})
                if datetime.now(timezone.utc) >= min(dispatch, end, hard):
                    break
        results = load_case_results(state_path, run_id)
        # Reused cases carry the same immutable failures into the report.
        # Recovery performs no generation/retrieval and cannot hide a failed
        # cell merely because it skipped the original execution loop.
        for result in results:
            if result['status'] != 'PAIRED_INPUTS_VERIFIED':
                failure = {'qid': result['qid'], 'status': result['status']}
                if failure not in failures:
                    failures.append(failure)
            for arm, cell in result['arms'].items():
                if cell['attempt_status'] not in {'simulated', 'succeeded'} or cell['error_code']:
                    failure = {'qid': result['qid'], 'arm': arm, 'status': cell['attempt_status'],
                               'error_code': cell['error_code']}
                    if failure not in failures:
                        failures.append(failure)
        from eval_center.paired_state import pending_attempts
        current_run = run_record(state_path, run_id)
        native_receipts = {row['qid']: attempts_for_qid(state_path, run_id=run_id, qid=row['qid'])
                           for row in cohort}
        successful_matrix = (not failures and
            _paired_cohort_success_matrix(cohort, results, native_receipts, kind))
        if current_run["status"] not in {"completed", "partial", "paused"} and not pending_attempts(state_path, run_id):
            close_run(state_path, run_id=run_id, status="completed" if successful_matrix else "partial")
        native_completed = run_record(state_path, run_id)['status'] == 'completed'
    observed_calls, simulated_sends, real_sends, route_counts = 0, 0, 0, {}
    attempt_counts = {arm: 0 for arm in ARMS}
    attempt_status_counts, provider_claim_counts = {}, {}
    receipts_by_qid = {}
    for row in cohort:
        receipts_by_qid[row["qid"]] = attempts_for_qid(state_path, run_id=run_id, qid=row["qid"])
        for arm, receipt in receipts_by_qid[row["qid"]].items():
            attempt_counts[arm] += 1
            attempt_status_counts[receipt["status"]] = attempt_status_counts.get(receipt["status"], 0) + 1
            stored = receipt["result"] or {}
            for child in receipt.get("provider_calls", []):
                key = child["purpose"] + "/" + child["status"]
                provider_claim_counts[key] = provider_claim_counts.get(key, 0) + 1
            observed_calls += stored.get("observed_quickchain_invocations", 0)
            for attempt in (stored.get("route") or {}).get("attempts", []):
                label = '/'.join(str(attempt.get(key, "UNKNOWN")) for key in ("provider", "role", "execution_kind", "send_status"))
                route_counts[label] = route_counts.get(label, 0) + 1
                if attempt.get("send_status") != "NOT_SENT":
                    if attempt.get("execution_kind") == "SIMULATED":
                        simulated_sends += 1
                    else:
                        real_sends += 1
    all_cells_ok = successful_matrix and native_completed
    gold_rows = [row for row in cohort if row.get("answers")]
    result_by_qid = {row["qid"]: row for row in results}
    answer_scoring_by_arm = {}
    for arm in ARMS:
        counts = {"evaluated": 0, "failed": 0, "not_scored": 0,
                  "not_scored_no_answer_gold": len(cohort) - len(gold_rows)}
        for row in gold_rows:
            cell = result_by_qid.get(row["qid"], {}).get("arms", {}).get(arm, {})
            receipt = receipts_by_qid[row["qid"]].get(arm, {})
            if cell.get("answer_scoring_status") == "SCORED":
                counts["evaluated"] += 1
            elif (cell.get("answer_scoring_status") == "NOT_SCORED_ATTEMPT_FAILED"
                  or receipt.get("status") in {"failed", "blocked", "unknown"}):
                counts["failed"] += 1
            else:
                counts["not_scored"] += 1
        answer_scoring_by_arm[arm] = counts
    # Preserve the legacy Dynamic-only successful-row field for callers;
    # frozen Gold availability and each arm's outcomes are explicit below.
    scored = answer_scoring_by_arm["DYNAMIC"]["evaluated"]
    retrieval_scored = sum(row["retrieval"]["status"] == "SCORED" for row in results)
    macro = {}
    try:
        for arm in ARMS:
            work_guard()
            metric_rows = [{key: value for key, value in row["arms"][arm]["answer_metrics"].items()
                           if key in {"normalized_exact_match", "character_f1", "hotpot_answer_em", "hotpot_answer_token_f1"}}
                          if isinstance(row["arms"][arm]["answer_metrics"], dict) else {}
                           for row in results]
            macro[arm] = aggregate_metrics(metric_rows)
        work_guard()
        retrieval_macro = aggregate_metrics([row["retrieval"]["metrics"] or {} for row in results])
        work_guard()
        context_macro = aggregate_metrics([row["retrieval"]["context_metrics"] or {} for row in results])
        work_guard()
        scoring_status = "SAVED_CASE_SCORES_AGGREGATED"
    except ExperimentInvalidError:
        # Recovery can read durable outcomes after the cutoff. It must not
        # rescore/re-read evidence or start late aggregation work.
        macro = dict.fromkeys(ARMS, "NOT_RUN_DEADLINE_REACHED")
        retrieval_macro = context_macro = "NOT_RUN_DEADLINE_REACHED"
        scoring_status, all_cells_ok = "NOT_RUN_DEADLINE_REACHED", False
    from eval_center.public_paired_reporting import join_latest_attempt_report
    try:
        work_guard()
        report_view=join_latest_attempt_report(results,state_path=state_path,run_id=run_id)
        work_guard()
        report_closure_status='CLOSED_WITHIN_DEADLINE'
    except ExperimentInvalidError:
        report_view='NOT_RUN_DEADLINE_REACHED'
        report_closure_status='NOT_RUN_DEADLINE_REACHED'
    by_dataset={}
    for name in names:
        rows=[row for row in results if row['dataset']==name]
        by_dataset[name]={'question_count':sum(row['dataset']==name for row in cohort),
            'answer_gold_count':sum(row['dataset']==name and bool(row.get('answers')) for row in cohort),
            'qrels_kind':sorted({row.get('qrels_kind','official_document_qrels') for row in cohort if row['dataset']==name}),
            'execution_kind':kind}
    return {"status": ("SIMULATED_" if kind == "SIMULATED" else "REAL_") +
                ("PAIRED_COHORT_COMPLETED" if all_cells_ok else "PAIRED_COHORT_PARTIAL"),
        "execution_kind": kind, "simulation": kind == "SIMULATED",
        "dispatch": "SIMULATED_ONLY" if kind == "SIMULATED" else "GUARDED_RUNTIME",
        "scheduler": "run_public_paired_cohort", "provider_calls": real_sends, "send_attempts": real_sends,
        "quickchain_invocations": observed_calls, "new_quickchain_invocations": observed_calls - initial_observed_calls,
        "simulated_quickchain_arm_attempts": observed_calls if kind == "SIMULATED" else 0,
        "simulated_provider_attempts": simulated_sends,
        "simulated_deepseek_transport_calls": sum(count for label, count in route_counts.items()
                                                  if label.startswith("deepseek/") and "/SIMULATED/" in label and not label.endswith("NOT_SENT")),
        "database_queries": runtime.database_queries() if callable(runtime.database_queries) else "UNKNOWN",
        "current_provider_observations": runtime.provider_observations() if callable(runtime.provider_observations) else "UNKNOWN",
        "langfuse": "NOT_TOUCHED" if runtime.langfuse_client is None else "SEE_TELEMETRY_RECEIPTS",
        "run_id": run_id, "run_ledger": str(state_path), "run_status": run_record(state_path, run_id)["status"],
        "cohort_sha256": cohort_hash, "source_manifest_sha256": source_hash,
        "evaluator_source_snapshot_sha256": code_sha, "runtime_identity_sha256": runtime_id,
        "answer_output_token_cap": PAIRED_ANSWER_OUTPUT_TOKEN_CAP,
        "qid_count": len(results), "expected_qid_count": len(cohort), "reused_qids": reused,
        "dataset_counts": {name: sum(row["dataset"] == name for row in results) for name in names},
        "dataset_gold_contracts":by_dataset,"paired_arm_report":report_view,
        "report_closure_status":report_closure_status,"time_profile":vars(profile),
        **({'owner_deadline_utc':profile.owner_deadline_utc,'owner_not_before_utc':profile.owner_not_before_utc,
            'execution_lease_id':profile.lease_id,
            'execution_lease_not_before_utc':profile.not_before_utc,
            'execution_lease_deadline_utc':hard.isoformat()}
            if profile.name.startswith('owner-lease-') else {}),
        "omni_page_count":manifest.get('omni_page_count',0),"omni_rag_qa":"NOT_RUN_NEEDS_INDEPENDENT_PREMODEL_QA",
        "attempt_counts": attempt_counts, "attempt_status_counts": attempt_status_counts,
        "observed_route_counts": route_counts, "failures": failures, "concurrency": 1,
        "provider_claim_counts": provider_claim_counts,
        "answer_gold_scored_rows": scored, "answer_gold_denominator": len(gold_rows),
        "answer_gold_frozen_rows": len(gold_rows), "answer_scoring_by_arm": answer_scoring_by_arm,
        "answer_not_scored_no_gold_rows": sum(not row.get("answers") for row in cohort),
        "retrieval_qrels_available_rows": len(cohort), "retrieval_scored_rows": retrieval_scored,
        "retrieval_not_run_rows": len(cohort) - retrieval_scored,
        "answer_macro": macro, "retrieval_macro": retrieval_macro,
        "context_macro": context_macro, "cohort_scoring_status": scoring_status,
        "results_sha256": _canonical_hash(results), "hard_deadline_utc": hard.isoformat(),
        "dispatch_deadline_utc": dispatch.isoformat(), "round_deadline_utc": end.isoformat(),
        "control_plane": "MANUALLY_SUPERVISED_TRIAL"}


def _memory_free_container(container, *, envelope_guard=None):
    """Build an isolated QuickChain request context with memory explicitly off."""
    original = getattr(container, "quick_chain", None)
    if original is None:
        raise ExperimentInvalidError("paired_quick_chain_missing")
    custom_clone = getattr(original, "clone_without_memory", None)
    if callable(custom_clone):
        isolated_chain = custom_clone()
    else:
        context_manager = getattr(original, "context_manager", None)
        memory_free_context = copy.copy(context_manager) if context_manager is not None else None
        if memory_free_context is not None:
            memory_free_context.memory_retriever = None
        bindings = {
            role: copy.copy(binding)
            for role, binding in getattr(original, "generation_roles", {}).items()
        }
        isolated_chain = LangChainQuickChain(
            original.knowledge_gateway,
            answer_gateway=original.answer_gateway,
            cloud_answer_gateway=original.cloud_answer_gateway,
            budget_gate=original.budget_gate,
            router_policy=original.router_policy,
            generation_roles=bindings,
            input_token_estimator=original.input_token_estimator,
            context_manager=memory_free_context,
            query_understand=None,
        )
    context_manager = getattr(isolated_chain, "context_manager", None)
    if context_manager is not None:
        context_manager.memory_retriever = None
    bindings = getattr(isolated_chain, "generation_roles", {})
    marker_strings = (
        "UNTRUSTED LONG-TERM MEMORY:",
        "UNTRUSTED SHORT-TERM HISTORY:",
        "long_term_memory",
    )
    for role, binding in list(bindings.items()):
        original_invoke = getattr(binding, "invoke", None)
        if not callable(original_invoke):
            continue
        def guarded(envelope, *, request_id, run_id, scope, _original=original_invoke, _binding=binding):
            prompt = getattr(envelope, "prompt", None)
            if not isinstance(prompt, str) or any(marker in prompt for marker in marker_strings):
                from backend.app.ports.providers import ProviderRequestNotSent
                raise ProviderRequestNotSent("PAIRED_MEMORY_CONTEXT_FORBIDDEN")
            send_claim = None
            from eval_center.paired_state import CurrentProviderSendClaims
            native_claims = type(getattr(getattr(getattr(_binding, "gateway", None), "transport", None),
                                        "send_claim_callback", None)) is CurrentProviderSendClaims
            if envelope_guard is not None:
                send_claim = envelope_guard(envelope, request_id=request_id,
                               role=getattr(_binding, "role", None),
                               provider=getattr(_binding, "provider", None),
                               model=getattr(_binding, "model", None), native_claims=native_claims)
            v6 = getattr(_binding, "v6_binding", None)
            xing = getattr(_binding, "xing_binding", None)
            binding_grant = v6.grant if v6 is not None else getattr(xing, "grant", None)
            server_scope = getattr(v6, "server_scope", None)
            if server_scope is not None:
                if type(scope) is not Scope or scope != server_scope:
                    from backend.app.ports.providers import ProviderRequestNotSent
                    raise ProviderRequestNotSent("PAIRED_BOUND_SERVER_SCOPE_MISMATCH")
                effective_scope = server_scope
            elif xing is not None:
                if type(scope) is not Scope or scope != binding_grant.scope:
                    from backend.app.ports.providers import ProviderRequestNotSent
                    raise ProviderRequestNotSent("PAIRED_BOUND_SERVER_SCOPE_MISMATCH")
                effective_scope = scope
            else:
                # Legacy explicitly simulated bindings without a server bridge.
                effective_scope = binding_grant.scope if binding_grant is not None else scope
            try:
                from eval_center.paired_state import provider_send_claim_scope
                claim_context = provider_send_claim_scope(send_claim.bound) if send_claim is not None else __import__("contextlib").nullcontext()
                with claim_context:
                    if send_claim is not None:
                        try:
                            send_claim.before_send()
                            if not native_claims:
                                send_claim.bound.check_before_send(request_id=request_id)
                        except Exception:
                            from backend.app.ports.providers import ProviderRequestNotSent
                            raise ProviderRequestNotSent("PAIRED_SEND_DEADLINE_OR_GUARD_DENIED") from None
                    answer = _original(envelope, request_id=request_id, run_id=run_id, scope=effective_scope)
            except Exception as exc:
                if send_claim is not None and not native_claims:
                    from eval_center.paired_state import finish_provider_send
                    from backend.app.ports.providers import ProviderRequestNotSent
                    finish_provider_send(send_claim.path, request_id=request_id,
                        status="blocked" if isinstance(exc, ProviderRequestNotSent) else "unknown",
                        error_code=type(exc).__name__)
                raise
            if send_claim is not None and not native_claims:
                from eval_center.paired_state import finish_provider_send
                finish_provider_send(send_claim.path, request_id=request_id,
                    status="simulated" if getattr(_binding, "simulated", False) else "succeeded")
            return answer
        from backend.app.application.rule_router_v1 import GenerationRole
        if type(binding) is GenerationRole:
            # Preserve trusted binding type/fields for the backend's typed
            # policy admission. Only this request-local invocation is wrapped.
            guarded_binding = copy.copy(binding)
            object.__setattr__(guarded_binding, "invoke", guarded)
            bindings[role] = guarded_binding
            continue
        bindings[role] = SimpleNamespace(
            role=getattr(binding, "role", role),
            provider=getattr(binding, "provider", None),
            model=getattr(binding, "model", None),
            simulated=getattr(binding, "simulated", False),
            usage_guard=getattr(binding, "usage_guard", None),
            gateway=getattr(binding, "gateway", None),
            v6_binding=getattr(binding, "v6_binding", None),
            xing_binding=getattr(binding, "xing_binding", None),
            invoke=guarded,
        )
    return SimpleNamespace(
        quick_chain=isolated_chain,
        store=container.store,
        execution_context=getattr(container, "execution_context", None),
    )


def invoke_paired_generation_arm(
    container,
    question: str,
    scope: Scope,
    *,
    arm: str,
    qid: str,
    run_id: str,
    state_run_id: str,
    conversation_id: str,
    cloud_allowed_by_kb: dict[str, bool],
    dispatch_guard,
    state_path: Path,
    input_identity_sha256: str,
    is_cancelled=None,
    answer_timeout_seconds=30.0,
):
    """Invoke one fixed Quick-chain route arm through the wired provider gates.

    ``dispatch_guard`` must re-check the shared ledger, code/data freeze, and
    hard deadline immediately before each call. Generation is never sent via a
    direct HTTP client here; QuickChain's ProviderFactory bindings and their
    BudgetUsageGuards own provider admission, reservations, and settlement.
    """
    if arm not in {"CHEAP_ONLY", "DYNAMIC", "EXPENSIVE_ONLY"}:
        raise ExperimentInvalidError("paired_arm_invalid")
    if not isinstance(run_id, str) or not run_id or not isinstance(conversation_id, str) or not conversation_id:
        raise ExperimentInvalidError("paired_run_identity_invalid")
    if not callable(dispatch_guard):
        raise ExperimentInvalidError("paired_dispatch_guard_required")
    def envelope_guard(envelope, *, request_id, role, provider, model, native_claims):
        from eval_center.paired_state import checkpoint_envelope, SQLiteProviderSendClaims
        try:
            dispatch_guard()
            prepared = getattr(container, 'prepared_answer_envelopes', None)
            from eval_center.paired_preparation import assert_prepared_answer_envelope
            assert_prepared_answer_envelope(prepared, qid, arm, envelope)
            if type(envelope.max_tokens) is not int or envelope.max_tokens != PAIRED_ANSWER_OUTPUT_TOKEN_CAP:
                raise ExperimentInvalidError('paired_output_token_cap_mismatch')
            checkpoint_envelope(state_path, run_id=state_run_id, qid=qid, arm=arm,
                identity={"prompt_hash": hashlib.sha256(envelope.prompt.encode("utf-8")).hexdigest(),
                          "context_hash": envelope.context_hash, "envelope_identity": envelope.identity})
            bound = SQLiteProviderSendClaims(state_path, state_run_id, qid, arm, input_identity_sha256)
            claim = None
            if not native_claims:
                claim = bound(request_id=request_id,
                    request_identity_sha256=_canonical_hash({"envelope_identity": envelope.identity,
                    "role": role, "provider": provider, "model": model}), purpose="answer",
                    provider=provider, model=model, role=role)
            return SimpleNamespace(path=state_path, claim=claim, bound=bound, before_send=dispatch_guard)
        except Exception:
            from backend.app.ports.providers import ProviderRequestNotSent
            raise ProviderRequestNotSent("PAIRED_ENVELOPE_ADMISSION_DENIED") from None
    isolated_container = _memory_free_container(container, envelope_guard=envelope_guard)
    quick_chain = isolated_container.quick_chain
    roles = getattr(quick_chain, "generation_roles", {}) if quick_chain is not None else {}
    cheap, expensive = roles.get("chat_cheap"), roles.get("chat_expensive")
    if cheap is None or expensive is None:
        raise ExperimentInvalidError("paired_provider_factory_roles_missing")
    if getattr(getattr(cheap, "gateway", None), "usage_guard", None) is None:
        raise ExperimentInvalidError("paired_cheap_budget_guard_missing")
    if getattr(expensive, "usage_guard", None) is None:
        raise ExperimentInvalidError("paired_expensive_budget_guard_missing")
    dispatch_guard()
    policy = RouterPolicy(mode=arm, threshold=Decimal("0.35"), quality_escalation=False,
                          abstention_escalation=False, provider_fallback=False,
                          truncated_escalation=False)
    from eval_center.paired_state import begin_attempt, attempts_for_qid, finish_attempt
    prior = attempts_for_qid(state_path, run_id=state_run_id, qid=qid).get(arm)
    if prior is not None:
        if prior["status"] in {"pending", "unknown"}:
            raise ExperimentInvalidError("paired_attempt_pending_no_retry")
        if prior["input_identity_sha256"] != input_identity_sha256:
            raise ExperimentInvalidError("paired_attempt_input_identity_conflict")
        stored = prior["result"]
        if not isinstance(stored, dict):
            raise ExperimentInvalidError("paired_attempt_result_missing_no_retry")
        from backend.app.domain.models import EvidenceSnapshot
        evidence = tuple(EvidenceSnapshot.model_validate(row) for row in stored.get("evidence", []))
        result = SimpleNamespace(
            answer=stored.get("answer", ""), error_code=stored.get("error_code"),
            citations=tuple(stored.get("citations", [])), evidence=evidence,
        )
        return {"result": result, "route": stored.get("route"),
                "usage": stored.get("usage", {}), "attempt_status": prior["status"],
                "telemetry": stored.get("telemetry"),"latency":stored.get('latency')}
    begin_attempt(state_path, run_id=state_run_id, qid=qid, arm=arm,
                  input_identity_sha256=input_identity_sha256)
    try:
        dispatch_guard()
    except Exception as exc:
        result = SimpleNamespace(answer="", error_code="DISPATCH_GUARD_DENIED", citations=(), evidence=())
        receipt = {"answer": "", "error_code": result.error_code, "citations": [], "evidence": [],
                   "route": None, "usage": {}, "observed_quickchain_invocations": 0}
        finish_attempt(state_path, run_id=state_run_id, qid=qid, arm=arm,
                       status="blocked", error_code=type(exc).__name__, result=receipt)
        return {"result": result, "route": None, "usage": {}, "attempt_status": "blocked", "telemetry": None}
    from backend.app.application.run_metrics import collect_metrics
    from eval_center.public_paired_reporting import measure_quickchain, latency_receipt
    timing=measure_quickchain(quick_chain) if isinstance(quick_chain,LangChainQuickChain) else {}
    invoke_started=time.perf_counter()
    latency=None
    observed_invocations = 0
    try:
        with collect_metrics(run_id) as metrics:
            execution_context = getattr(isolated_container, "execution_context", None)
            context = execution_context(run_id) if callable(execution_context) else __import__("contextlib").nullcontext()
            with context:
                observed_invocations = 1
                result = quick_chain.invoke(
                    question,
                    scope,
                    settings=QuickSettings(
                        cloud_enabled=True,
                        local_query_enabled=False,
                        answer_timeout_seconds=answer_timeout_seconds,
                        router_policy=policy,
                        answer_output_token_cap=PAIRED_ANSWER_OUTPUT_TOKEN_CAP,
                    ),
                    run_id=run_id,
                    cloud_allowed_by_kb=cloud_allowed_by_kb,
                    conversation_id=conversation_id,
                    is_cancelled=is_cancelled or (lambda: False),
                )
        route = metrics.rule_router
        usage = metrics.usage.to_dict()
        latency=latency_receipt(timing,invoke_started,getattr(quick_chain,"knowledge_gateway",None))
    except Exception as exc:
        route = metrics.rule_router if 'metrics' in locals() else None
        usage = metrics.usage.to_dict() if 'metrics' in locals() else {}
        result = SimpleNamespace(answer="", error_code="DISPATCH_OUTCOME_UNKNOWN", citations=(), evidence=())
        latency=latency_receipt(timing,invoke_started,getattr(quick_chain,"knowledge_gateway",None))
        finish_attempt(state_path, run_id=state_run_id, qid=qid, arm=arm,
                       status="unknown", error_code=result.error_code,
                       result={"answer": "", "error_code": result.error_code, "citations": [], "evidence": [],
                               "route": route, "usage": usage,"latency":latency, "observed_quickchain_invocations": observed_invocations})
        return {"result": result, "route": route, "usage": usage,"latency":latency, "attempt_status": "unknown", "telemetry": None}
    attempts = route.get("attempts", []) if isinstance(route, dict) else []
    sent = [item.get("send_status") for item in attempts if isinstance(item, dict)]
    blocked_codes = {"P5_BLOCKED_REAL_AUTHORIZATION_AND_CAPACITY", "ROUTER_MODEL_IDENTITY_MISMATCH",
                     "MODEL_USAGE_GUARD_REQUIRED", "DEEPSEEK_GATED_ROLE_REQUIRED", "MODEL_BUDGET_DENIED"}
    if not attempts and getattr(result, "error_code", None) in blocked_codes:
        status = "blocked"
    elif any(value == "UNKNOWN" for value in sent):
        status = "unknown"
    elif getattr(result, "error_code", None):
        status = "failed"
    elif ((route or {}).get("simulation") == "SIMULATED"
          or any(isinstance(item, dict) and item.get("execution_kind") == "SIMULATED"
                 for item in attempts)):
        status = "simulated"
    elif sent and all(value == "NOT_SENT" for value in sent):
        status = "blocked"
    elif not attempts:
        status = "blocked"
    else:
        status = "succeeded"
    answer = getattr(result, "answer", "")
    citations = list(getattr(result, "citations", ()))
    evidence = [item.model_dump() if callable(getattr(item, "model_dump", None)) else item
                for item in getattr(result, "evidence", ())]
    safe_error = getattr(result, "error_code", None)
    if not isinstance(safe_error, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", safe_error):
        safe_error = "APPLICATION_ERROR" if safe_error else None
    finish_attempt(
        state_path, run_id=state_run_id, qid=qid, arm=arm, status=status,
        error_code=safe_error,
        result={"answer": answer, "citations": citations, "evidence": evidence,
                "error_code": safe_error, "route": route, "usage": usage,"latency":latency,
                "observed_quickchain_invocations": 1},
    )
    return {"result": result, "route": route, "usage": usage,"latency":latency, "attempt_status": status,
            "telemetry": None}


@contextmanager
def _shared_retrieval_context(binding, *, state_path, dataset, qid, scope, question,
                              execution_context=None):
    """Use the real product RETRIEVAL lifecycle, never a chat-arm budget FK."""
    from backend.app.application.run_lifecycle import RunLifecycle
    binding.validate()
    context = (execution_context(binding.execution_contract.retrieval_run_id)
        if callable(execution_context) else __import__('contextlib').nullcontext())
    with context:
        current = RunLifecycle.assert_current()
        if current[0].run_id != binding.execution_contract.retrieval_run_id:
            raise ExperimentInvalidError('paired_shared_retrieval_lifecycle_run_mismatch')
        with _shared_retrieval_claim_context(binding, state_path=state_path, dataset=dataset,
                qid=qid, scope=scope, question=question):
            yield


@contextmanager
def _shared_retrieval_claim_context(binding, *, state_path, dataset, qid, scope, question):
    """Switch only the retrieval stage to its linked native batch claim scope."""
    from eval_center.paired_state import (validate_paired_run_link, begin_provider_batch_scope,
        SQLiteProviderBatchSendClaims, provider_send_claim_scope, finish_attempt, attempts_for_qid)
    from backend.app.application.run_lifecycle import RunLifecycle
    from backend.app.ports.xing_zero_reservation import FreeModelRequestContext, free_model_requests
    binding.validate()
    contract, plan = binding.execution_contract, binding.plan_for(dataset, qid)
    selection = next(s for s in contract.selections if s.dataset == dataset)
    if scope != selection.scope or hashlib.sha256(question.encode()).hexdigest() != plan.query_sha256:
        raise ExperimentInvalidError('paired_shared_retrieval_scope_or_query_changed')
    RunLifecycle.assert_current()
    def validate_lease(pin):
        if pin != contract.lease_sha256:
            raise ExperimentInvalidError('paired_shared_retrieval_lease_changed')
        validate_paired_run_link(state_path, contract,
            retrieval_binding_sha256=binding.fingerprint, for_dispatch=True)
    validate_lease(contract.lease_sha256)
    identity = _canonical_hash({'contract': contract.fingerprint, 'binding': binding.fingerprint,
        'dataset': dataset, 'qid': qid, 'query': plan.query_sha256})
    begin_provider_batch_scope(state_path, run_id=contract.retrieval_run_id, qid=qid,
        input_identity_sha256=identity)
    claims = SQLiteProviderBatchSendClaims(state_path, contract.retrieval_run_id, qid,
        'SHARED_RETRIEVAL', identity, plan, contract.lease_sha256, validate_lease)
    request_id = claims.request_id(purpose='query_embedding')
    context = FreeModelRequestContext(contract.retrieval_run_id, request_id,
        'query_embedding', binding.batch_id_for(dataset, qid), scope, batch_plan=plan)
    try:
        with provider_send_claim_scope(claims), free_model_requests((context,)):
            yield
        children = attempts_for_qid(state_path, run_id=contract.retrieval_run_id, qid=qid)[
            'SHARED_RETRIEVAL']['provider_calls']
        required = {'query_embedding'} | ({'rerank'} if binding.rerank_plans or binding.late_rerank else set())
        from eval_center.paired_preparation import require_retrieval_receipt_status
        # Keep the historical missing-child error; status isolation is separate.
        if len(children) != len(required) or {c['purpose'] for c in children} != required:
            raise ExperimentInvalidError('paired_shared_retrieval_receipt_required')
        terminal_status = require_retrieval_receipt_status(children,
            execution_kind=binding.execution_kind, required_purposes=required)
    except BaseException:
        children = attempts_for_qid(state_path, run_id=contract.retrieval_run_id, qid=qid)[
            'SHARED_RETRIEVAL']['provider_calls']
        finish_attempt(state_path, run_id=contract.retrieval_run_id, qid=qid, arm='SHARED_RETRIEVAL',
            status='unknown' if children else 'blocked', error_code='PAIRED_SHARED_RETRIEVAL_FAILED')
        raise
    else:
        finish_attempt(state_path, run_id=contract.retrieval_run_id, qid=qid,
            arm='SHARED_RETRIEVAL', status=terminal_status)


class _FrozenCaseGateway:
    """Capture project retrieval/evidence once; only the model policy varies."""

    def __init__(self, gateway, *, strict_scope=None, retrieval_context=None):
        self.original = gateway
        self.evidence = _FrozenCaseEvidence(gateway.evidence, strict_scope=strict_scope)
        self.strict_scope = strict_scope
        self.retrieved = None
        self.retrieval_calls = 0
        self.retrieval_context = retrieval_context

    def plan(self, question):
        return self.original.plan(question)

    def retrieve_question(self, scope, question, **kwargs):
        tick=time.perf_counter()
        fresh=self.retrieved is None
        identity = (scope, question)
        if self.retrieved is None:
            self.retrieval_calls += 1
            context = (self.retrieval_context(scope, question) if self.retrieval_context is not None
                       else __import__('contextlib').nullcontext())
            with context:
                retrieved = self.original.retrieve_question(scope, question, **kwargs)
            if self.strict_scope is not None:
                for item in retrieved[1].items:
                    self.strict_scope.assert_contains(item.chunk.knowledge_base_id, item.chunk.document_id)
            self.retrieved = (identity, retrieved)
        if identity != self.retrieved[0]:
            raise ExperimentInvalidError("paired_retrieval_scope_or_question_changed")
        result=copy.deepcopy(self.retrieved[1])
        self.last_retrieval_ms=(time.perf_counter()-tick)*1000
        self.last_retrieval_basis='ACTUAL_RETRIEVAL_AND_COPY' if fresh else 'FROZEN_RESULT_COPY_NO_RETRIEVAL'
        return result


class _FrozenCaseEvidence:
    def __init__(self, evidence, *, strict_scope=None):
        self.original = evidence
        self.selected_bundle = None
        self.context_bundle = None
        self.strict_scope = strict_scope

    def __getattr__(self, name):
        return getattr(self.original, name)

    def bundle(self, plan, retrieval):
        if self.selected_bundle is None:
            self.selected_bundle = self.original.bundle(plan, retrieval)
            if self.strict_scope is not None:
                for item in self.selected_bundle.selected:
                    self.strict_scope.assert_contains(item.chunk.knowledge_base_id, item.chunk.document_id)
        return copy.deepcopy(self.selected_bundle)

    def with_context(self, bundle, run_id, citations):
        if self.context_bundle is None:
            self.context_bundle = self.original.with_context(bundle, run_id, citations)
        return copy.deepcopy(self.context_bundle)


def run_public_paired_generation_case(
    container,
    case: PublicCase,
    scope: Scope,
    *,
    run_id: str,
    dataset_name: str,
    dataset_version: str,
    state_path: Path,
    shared_identity: dict[str, str],
    cloud_allowed_by_kb: dict[str, bool],
    dispatch_guard,
    langfuse_client=None,
    work_guard=None,
    answer_timeout_seconds=30.0,
    execution_kind=None,
    source_id_for_chunk=None,
    top_k=5,
    settlement_observer=None,
    source_annotations_for_chunk=None,
    shared_retrieval_binding=None,
) -> dict[str, Any]:
    """Score one QID in three arms and reject any prompt/context divergence.

    Langfuse is opt-in via an injected client; telemetry carries only public
    identifiers, hashes, token counts, and numeric scores, never text.
    """
    from eval_center.paired_state import ARMS, record_telemetry_status, save_case_result
    required_hashes = {"corpus_sha256", "pool_sha256", "qrels_sha256", "gold_sha256", "index_sha256"}
    if (not isinstance(shared_identity, dict) or set(shared_identity) != required_hashes
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                   for value in shared_identity.values())):
        raise ExperimentInvalidError("paired_shared_identity_invalid")
    case_identity = _canonical_hash({"dataset": dataset_name, "dataset_version": dataset_version,
        "qid": case.qid, "question_sha256": hashlib.sha256(case.question.encode("utf-8")).hexdigest(),
        "qrels": case.qrels, "answers": case.answers,'evaluation_metadata':case.evaluation_metadata,
        'answer_output_token_cap':PAIRED_ANSWER_OUTPUT_TOKEN_CAP, **shared_identity})
    work_guard = work_guard or (lambda: None)
    # Gold is confined to identity/scoring above and below. Only the question
    # enters the QuickChain; retrieval receives server-bound Scope, never qrels.
    frozen_gateway = None
    prepared_gateways = getattr(container, 'prepared_case_gateways', None)
    prepared_envelopes = getattr(container, 'prepared_answer_envelopes', None)
    if isinstance(container.quick_chain, LangChainQuickChain):
        container = _memory_free_container(container)
        if prepared_gateways is not None:
            if case.qid not in prepared_gateways:
                raise ExperimentInvalidError('prepared_qid_missing')
            frozen_gateway = prepared_gateways[case.qid]
            container.prepared_answer_envelopes = prepared_envelopes
        else:
            frozen_gateway = _FrozenCaseGateway(container.quick_chain.knowledge_gateway,
                strict_scope=scope if execution_kind == "REAL" else None,
                retrieval_context=(None if shared_retrieval_binding is None else lambda s, q:
                    _shared_retrieval_context(shared_retrieval_binding, state_path=state_path,
                        dataset=dataset_name, qid=case.qid, scope=s, question=q,
                        execution_context=getattr(container, 'execution_context', None))))
        container.quick_chain.knowledge_gateway = frozen_gateway
    outputs: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        work_guard()
        arm_run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{case.qid}:{arm}"))
        outputs[arm] = invoke_paired_generation_arm(
            container, case.question, scope, arm=arm, qid=case.qid, run_id=arm_run_id,
            state_run_id=run_id,
            conversation_id=arm_run_id, cloud_allowed_by_kb=cloud_allowed_by_kb,
            dispatch_guard=dispatch_guard, state_path=state_path,
            input_identity_sha256=case_identity,
            is_cancelled=lambda: bool(work_guard()),
            answer_timeout_seconds=answer_timeout_seconds,
        )
    try:
        common_route_hashes = validate_route_triplet(outputs)
        pairing_status = "PAIRED_INPUTS_VERIFIED"
    except ExperimentInvalidError:
        common_route_hashes = None
        pairing_status = "PAIRED_INPUTS_NOT_VERIFIED_FAILURE"
    arms = {}
    for arm, output in outputs.items():
        work_guard()
        scoring_started=time.perf_counter()
        result = output["result"]
        simulated = execution_kind == "SIMULATED" or output["attempt_status"] == "simulated"
        readbacks = {} if simulated else _readback_citations(result, container.store, scope, work_guard=work_guard)
        answer_scored = bool(case.answers) and not result.error_code and output["attempt_status"] in {"simulated", "succeeded"}
        arms[arm] = {
            "attempt_status": output["attempt_status"],
            "answer": result.answer,
            "error_code": result.error_code,
            "citations": list(result.citations),
            "citation_readbacks": readbacks,
            "citation_readback_rate": (sum(readbacks.values()) / len(readbacks)) if readbacks else None,
            "answer_metrics": __import__('eval_center.public_paired_reporting',fromlist=['score_answer']).score_answer(
                result.answer,case.answers,dataset=dataset_name,metadata=case.evaluation_metadata) if answer_scored else "NOT_SCORED",
            "answer_scoring_status": ("SCORED" if answer_scored else "NOT_SCORED_NO_ANSWER_GOLD"
                                      if not case.answers else "NOT_SCORED_ATTEMPT_FAILED"),
            "usage": output["usage"],
            "latency":output.get('latency') or {'status':'NOT_OBSERVED','quickchain_end_to_end_ms':None},
            "route": output["route"],
            "citation_support": "NOT_EVALUATED",
            "judge": "NOT_EVALUATED",
            "citation_readback_status": "NOT_RUN_SIMULATED" if simulated else "EXECUTED",
        }
        telemetry = output.get("telemetry") or {
            "status": "not_configured", "trace_id": None, "error_code": None,
        }
        from eval_center.paired_state import attempts_for_qid
        from eval_center.public_paired_reporting import settlement_cost
        children=attempts_for_qid(state_path,run_id=run_id,qid=case.qid).get(arm,{}).get('provider_calls',[])
        facts=None
        if callable(settlement_observer):
            try:
                work_guard()
                facts=settlement_observer(run_id=run_id,qid=case.qid,arm=arm,
                    provider_request_ids=tuple(c['request_id'] for c in children))
                work_guard()
            except Exception:
                facts=None
        arms[arm]['cost']=settlement_cost(children=children,facts=facts,execution_kind=execution_kind)
        if langfuse_client is not None and output.get("telemetry") is None and common_route_hashes is not None:
            answer_metrics = arms[arm]["answer_metrics"]
            telemetry_metadata = {
                "dataset": dataset_name,
                "dataset_version": dataset_version,
                "status": output["attempt_status"],
                **shared_identity,
                "prompt_sha256": common_route_hashes["prompt_hash"],
                "context_sha256": common_route_hashes["context_hash"],
                "envelope_sha256": common_route_hashes["envelope_identity"],
            }
            cost=arms[arm]['cost']
            if cost['status']=='AVAILABLE' and set(cost['actual_by_currency_microunits'])=={'USD'}:
                telemetry_metadata['cost_microusd']=cost['actual_by_currency_microunits']['USD']
            usage = output["usage"] if isinstance(output["usage"], dict) else {}
            for key in ("input_tokens", "output_tokens"):
                value = usage.get(key)
                if type(value) is int and value >= 0:
                    telemetry_metadata[key] = value
            telemetry_scores = {}
            if isinstance(answer_metrics, dict):
                telemetry_scores = {
                    "answer_exact_match": answer_metrics.get("normalized_exact_match",answer_metrics.get('hotpot_answer_em')),
                    "answer_character_f1": answer_metrics.get("character_f1",answer_metrics.get('hotpot_answer_token_f1')),
                }
            if arms[arm]["citation_readback_rate"] is not None:
                telemetry_scores["citation_readback_rate"] = arms[arm]["citation_readback_rate"]
            try:
                work_guard()
                from eval_center.public_langfuse import write_and_readback_public_event
                receipt = write_and_readback_public_event(
                    langfuse_client, run_id=run_id, qid=case.qid, arm=arm,
                    metadata=telemetry_metadata, scores=telemetry_scores,
                )
                work_guard()
                telemetry = {"status": "flushed_and_read_back", "trace_id": receipt["trace_id"],
                             "error_code": None}
            except Exception as exc:
                # Do not replay the generation or telemetry write after an
                # uncertain outcome. Preserve a stable error class only.
                telemetry = {"status": "unknown", "trace_id": None,
                             "error_code": type(exc).__name__}
            record_telemetry_status(state_path, run_id=run_id, qid=case.qid, arm=arm,
                                    status=telemetry["status"], trace_id=telemetry["trace_id"],
                                    error_code=telemetry["error_code"])
        arms[arm]["telemetry"] = telemetry
        arms[arm]['latency']['evaluation_scoring_readback_telemetry_ms']=(time.perf_counter()-scoring_started)*1000
        arms[arm]['gold_contract']={'qrels_kind':case.qrels_kind,'metadata':case.evaluation_metadata,
            'answer_gold_available':bool(case.answers)}
    case_result = {"qid": case.qid, "dataset": dataset_name, "dataset_version": dataset_version,
                   "answer_output_token_cap": PAIRED_ANSWER_OUTPUT_TOKEN_CAP,
                   **shared_identity, "paired_input_hashes": common_route_hashes,
                   "arms": arms, "status": pairing_status,
                   "execution_kind": execution_kind or "UNKNOWN"}
    case_result["retrieval"] = {"status": "NOT_RUN", "metrics": None, "context_metrics": None}
    from eval_center.public_paired_reporting import score_retrieval_annotations
    case_result['retrieval_annotations']=score_retrieval_annotations(case)
    if frozen_gateway is not None:
        case_result["observed_retrieval_calls"] = frozen_gateway.retrieval_calls
        if (execution_kind == "REAL" and frozen_gateway.retrieved is not None
                and callable(source_id_for_chunk)):
            work_guard()
            retrieval = frozen_gateway.retrieved[1][1]
            ranked = list(dict.fromkeys(source_id_for_chunk(item.chunk) for item in retrieval.items))
            selected = frozen_gateway.evidence.selected_bundle
            context_ids = list(dict.fromkeys(source_id_for_chunk(item.chunk) for item in selected.selected)) if selected else []
            case_result["retrieval"] = {"status": "SCORED", "ranked_source_ids": ranked,
                "context_source_ids": context_ids, "result_unit": "public_source_document",
                "qrels_kind":case.qrels_kind,
                "metrics": ranking_metrics(ranked, case.qrels, k=top_k),
                "context_metrics": ranking_metrics(context_ids, case.qrels, k=top_k)}
            if callable(source_annotations_for_chunk):
                work_guard()
                annotations=[source_annotations_for_chunk(item.chunk) for item in retrieval.items]
                case_result['retrieval_annotations']=score_retrieval_annotations(case,annotations)
    work_guard()
    case_result["local_store"] = save_case_result(state_path, run_id=run_id, result=case_result)
    return case_result


def retry_public_paired_telemetry(*, state_path: Path, run_id: str, client, work_guard) -> dict[str, int]:
    """Explicit telemetry-only replay, using stable IDs and saved scores.

    No runtime container, model, retrieval or QuickChain is accepted here.
    Immutable original case evidence is retained; only Attempt telemetry
    receipts append delivery history. The injected client must bound its I/O.
    """
    from eval_center.paired_state import (ARMS, attempts_for_qid, exclusive_runner_lock,
        load_case_results, record_telemetry_status)
    from eval_center.public_langfuse import write_and_readback_public_event
    delivered, unknown = 0, 0
    with exclusive_runner_lock(state_path):
        for result in load_case_results(state_path, run_id):
            hashes = result.get("paired_input_hashes")
            if hashes is None:
                continue
            receipts = attempts_for_qid(state_path, run_id=run_id, qid=result["qid"])
            for arm in ARMS:
                work_guard()
                receipt = receipts[arm]
                telemetry = (receipt["result"] or {}).get("telemetry", {})
                if telemetry.get("status") == "flushed_and_read_back":
                    continue
                cell = result["arms"][arm]
                metadata = {key: result[key] for key in (
                    "dataset", "dataset_version", "corpus_sha256", "pool_sha256",
                    "qrels_sha256", "gold_sha256", "index_sha256")}
                metadata.update(status=receipt["status"], prompt_sha256=hashes["prompt_hash"],
                    context_sha256=hashes["context_hash"], envelope_sha256=hashes["envelope_identity"])
                scores = {}
                metrics = cell["answer_metrics"]
                if isinstance(metrics, dict):
                    scores = {"answer_exact_match": metrics.get("normalized_exact_match",metrics.get('hotpot_answer_em')),
                              "answer_character_f1": metrics.get("character_f1",metrics.get('hotpot_answer_token_f1'))}
                if cell["citation_readback_rate"] is not None:
                    scores["citation_readback_rate"] = cell["citation_readback_rate"]
                try:
                    event = write_and_readback_public_event(client, run_id=run_id,
                        qid=result["qid"], arm=arm, metadata=metadata, scores=scores)
                    work_guard()
                    status, trace_id, error_code = "flushed_and_read_back", event["trace_id"], None
                    delivered += 1
                except Exception as exc:
                    status, trace_id, error_code = "unknown", None, type(exc).__name__
                    unknown += 1
                record_telemetry_status(state_path, run_id=run_id, qid=result["qid"], arm=arm,
                    status=status, trace_id=trace_id, error_code=error_code,
                    retry_telemetry_only=True)
    return {"delivered": delivered, "unknown": unknown, "generation_dispatches": 0}


def validate_route_triplet(outputs: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Reject results unless all arms report the same actual prompt and context."""
    expected = {"CHEAP_ONLY", "DYNAMIC", "EXPENSIVE_ONLY"}
    if not isinstance(outputs, dict) or set(outputs) != expected:
        raise ExperimentInvalidError("paired_route_arm_set_invalid")
    identities = {}
    for arm, output in outputs.items():
        route = output.get("route") if isinstance(output, dict) else None
        if not isinstance(route, dict) or route.get("mode") != arm:
            raise ExperimentInvalidError("paired_route_trace_missing")
        for key in ("context_hash", "prompt_hash", "envelope_identity"):
            value = route.get(key)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ExperimentInvalidError("paired_route_identity_missing")
            identities.setdefault(key, set()).add(value)
    if any(len(values) != 1 for values in identities.values()):
        raise ExperimentInvalidError("paired_prompt_or_context_mismatch")
    return {key: next(iter(values)) for key, values in identities.items()}


class RecordingRetriever(HybridRetriever):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def retrieve(self, *args, **kwargs):
        result = super().retrieve(*args, **kwargs)
        self.records.append(result)
        return result


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_answer(value: str) -> str:
    return "".join(char.lower() for char in value if char.isalnum())


def _character_f1(prediction: str, reference: str) -> float:
    from collections import Counter
    predicted = Counter(_normalize_answer(prediction))
    expected = Counter(_normalize_answer(reference))
    if not predicted or not expected:
        return float(predicted == expected)
    overlap = sum((predicted & expected).values())
    precision, recall = overlap / sum(predicted.values()), overlap / sum(expected.values())
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _answer_metrics(answer: str, references: tuple[str, ...]) -> dict[str, Any]:
    normalized = _normalize_answer(answer)
    normalized_references = [_normalize_answer(reference) for reference in references]
    exact = max((int(normalized == reference) for reference in normalized_references), default=None)
    char_f1 = max((_character_f1(answer, reference) for reference in references), default=None)
    return {"normalized_exact_match": exact, "character_f1": char_f1,
            "references_count": len(references), "judge": "NOT_EVALUATED",
            "citation_support": "NOT_EVALUATED"}


def _readback_citations(answer_result, repository, scope, *, work_guard=None) -> dict[str, bool]:
    snapshots = {snapshot.label: snapshot for snapshot in answer_result.evidence}

    def read_quote(version_id: str, chunk_id: str | None) -> str:
        if work_guard is not None:
            work_guard()
        chunk = repository.get_chunk(chunk_id or "")
        if chunk is None or chunk.version_id != version_id:
            raise ValueError("citation source is unreadable")
        scope.assert_contains(chunk.knowledge_base_id, chunk.document_id)
        return chunk.content

    resolver = EvidenceResolver(snapshots, read_quote)
    readbacks = {}
    for label in answer_result.citations:
        if work_guard is not None:
            work_guard()
        try:
            resolver.resolve(label)
            readbacks[label] = True
        except Exception:
            readbacks[label] = False
    return readbacks


def _retrieval_modes(result) -> dict[str, list[str]]:
    modes = {
        "keyword": [hit.chunk_id for hit in result.candidate_rankings.get("keyword", ())],
        "vector": [hit.chunk_id for hit in result.candidate_rankings.get("vector", ())],
        "hybrid": [hit.chunk_id for hit in result.fused_ranking],
    }
    return {name: ranking for name, ranking in modes.items() if ranking or name in result.candidate_rankings}


def _items_for_rank(repository, chunk_ids: list[str], hits: dict[str, Any], top_k: int) -> list[RetrievalItem]:
    output = []
    for chunk_id in chunk_ids[:top_k]:
        chunk = repository.get_chunk(chunk_id)
        if chunk is None:
            raise ExperimentInvalidError("ranked_chunk_not_readable")
        output.append(RetrievalItem(chunk=chunk, hit=hits[chunk_id]))
    return output


def _case_row(
    case: PublicCase,
    result,
    container,
    index: dict[str, Any],
    context_builder: ContextBuilder,
    config: dict[str, Any],
    tokenizer: Any | None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    chunk_to_document = {chunk_id: span.document_id for chunk_id, span in index["spans"].items()}
    hit_by_id = {hit.chunk_id: hit for stage in result.candidate_rankings.values() for hit in stage}
    hit_by_id.update({hit.chunk_id: hit for hit in result.fused_ranking})
    stage_chunk_ranks = _retrieval_modes(result)
    mode_documents: dict[str, list[str]] = {}
    mode_context_documents: dict[str, list[str]] = {}
    mode_metrics: dict[str, dict[str, Any]] = {}
    contexts: dict[str, dict[str, Any]] = {}
    context_times: dict[str, float] = {}
    for mode, chunk_ids in stage_chunk_ranks.items():
        document_ranks = fold_chunk_ranking(chunk_ids, chunk_to_document)
        mode_documents[mode] = document_ranks
        items = _items_for_rank(container.store, chunk_ids, hit_by_id, config["top_k"])
        selected = context_builder.select(items)
        run_id = f"public-{case.qid}-{mode}"
        citations = CitationService(InMemoryCitationStore())
        context_started = time.perf_counter()
        context, labels = context_builder.build(run_id, selected, citations)
        context_times[mode] = (time.perf_counter() - context_started) * 1000
        selected_chunk_ids = [citations.snapshots[(run_id, label)].chunk_id for label in labels]
        context_docs = fold_chunk_ranking(selected_chunk_ids, chunk_to_document)
        mode_context_documents[mode] = context_docs
        mode_metrics[mode] = {}
        for k in sorted(set((3, 5, 8, 10, 20, config["top_k"]))):
            mode_metrics[mode][f"document_metrics_at_{k}"] = ranking_metrics(document_ranks, case.qrels, k=k)
            mode_metrics[mode][f"context_document_metrics_at_{k}"] = ranking_metrics(context_docs, case.qrels, k=k)
        judged = set(case.qrels)
        mode_metrics[mode]["judgment_coverage"] = {
            "judged_pool_documents": len(judged),
            "unjudged_pool_documents": len(index["document_ids"] - judged),
            "unjudged_pool_fraction": len(index["document_ids"] - judged) / len(index["document_ids"]),
            "retrieved_unjudged_documents_at_k": sum(doc_id not in judged for doc_id in document_ranks[:config["top_k"]]),
            "qrels_kind": case.qrels_kind,
        }
        contexts[mode] = {"text": context, "selected_chunk_ids": selected_chunk_ids,
                          "selected_document_ids": context_docs, "citation_count": len(labels)}
    metric_row: dict[str, Any] = {}
    for mode, stages in mode_metrics.items():
        for metric_set, values in stages.items():
            for metric_name, value in values.items():
                if isinstance(value, (int, float)) and value is not None:
                    metric_row[f"{mode}.{metric_set}.{metric_name}"] = value
    timing = result.stage_latency_ms
    metrics_by_mode: dict[str, dict[str, Any]] = {}
    for mode in mode_metrics:
        selected_metrics = {key: value for key, value in metric_row.items() if key.startswith(mode + ".")}
        selected_metrics.update({
            "keyword_retrieval_ms": timing.get("keyword_retrieval_ms"),
            "vector_retrieval_ms": timing.get("vector_retrieval_ms"),
            "embedding_ms": timing.get("embedding_ms"),
            "fusion_ms": timing.get("fusion_ms") if mode == "hybrid" else None,
            "context_building_ms": context_times[mode],
            "retrieval_ms": result.latency_ms,
        })
        metrics_by_mode[mode] = selected_metrics
    estimated_tokens = {}
    for mode, value in contexts.items():
        estimated_tokens[mode] = len(tokenizer.encode(value["text"])) if tokenizer is not None else None
    row = {
        "qid": case.qid,
        "split": case.split,
        "question": case.question,
        "qrels": case.qrels,
        "qrels_kind": case.qrels_kind,
        "qrels_count": len(case.qrels),
        "positive_qrels_count": sum(grade > 0 for grade in case.qrels.values()),
        "explicit_zero_qrels_count": sum(grade == 0 for grade in case.qrels.values()),
        "paired_source_doc_id": case.paired_source_doc_id,
        "chunk_rankings": stage_chunk_ranks,
        "document_rankings": mode_documents,
        "context_document_rankings": mode_context_documents,
        "context": contexts,
        "estimated_context_tokens": estimated_tokens,
        "metrics": metrics_by_mode,
        "timings_ms": timing,
        "query_mode": "q0",
        "generation": "NOT_RUN",
    }
    return row, metrics_by_mode


def _source_content(document) -> str:
    title = document.title.strip()
    return f"# {title}\n\n{document.text}" if title else document.text


def _expected_normalized_text(content: str) -> str:
    """Mirror TextParser's newline normalization for the verified index binding."""
    return content.replace("\r\n", "\n").replace("\r", "\n")


def run_public_retrieval(
    *,
    repository_root: Path,
    data_root: Path,
    dataset_name: str,
    admin_url: str,
    output_root: Path | None = None,
    split: str = "development",
    phase: str = "baseline",
    profile: str = "standard",
    chunk_size: int = 1200,
    chunk_overlap: int = 120,
    top_k: int = 5,
    candidate_k: int = 32,
    rrf_k: int = 60,
    context_budget_chars: int = 8000,
    max_cases: int | None = None,
    generation_limit: int = 0,
    retrieval_variants: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ingest and retrieve through production components in a fresh local DB."""
    if profile not in {"smoke", "standard"}:
        raise ValueError("unsupported run profile")
    for value in (chunk_size, top_k, candidate_k, rrf_k, context_budget_chars):
        if type(value) is not int or value <= 0:
            raise ValueError("invalid run configuration")
    if type(chunk_overlap) is not int or chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("invalid chunk overlap")
    if type(generation_limit) is not int or generation_limit < 0:
        raise ValueError("invalid generation limit")
    if generation_limit and dataset_name != "longbench-zh":
        raise ValueError("public Qwen QA is limited to LongBench Chinese")
    if split == "locked_holdout" and (phase != "validate" or profile != "standard" or max_cases is not None or generation_limit):
        raise ValueError("locked validation requires one complete standard retrieval run")
    if retrieval_variants is None:
        retrieval_variants = [{"variant_id": "baseline", "top_k": top_k,
                               "candidate_k": candidate_k, "rrf_k": rrf_k}]
    if not retrieval_variants or len({item.get("variant_id") for item in retrieval_variants}) != len(retrieval_variants):
        raise ValueError("invalid or duplicate retrieval variant")
    for item in retrieval_variants:
        if set(item) != {"variant_id", "top_k", "candidate_k", "rrf_k"}:
            raise ValueError("retrieval variant fields are invalid")
        if not isinstance(item["variant_id"], str) or not item["variant_id"]:
            raise ValueError("retrieval variant identity is invalid")
        if any(type(item[field]) is not int or item[field] <= 0 for field in ("top_k", "candidate_k", "rrf_k")):
            raise ValueError("retrieval variant values are invalid")
    code_sha = committed_code_sha(repository_root)
    dataset = load_public_dataset(dataset_name, data_root, split=split, phase=phase)
    validation_lock_path = Path(data_root) / "runs" / "locks" / f"{dataset_name}-locked-holdout.json"
    if split == "locked_holdout" and validation_lock_path.exists():
        raise ExperimentInvalidError("locked_split_already_used")
    documents = dataset.documents
    cases = dataset.cases
    if profile == "smoke":
        selected_cases = []
        requested_ids: set[str] = set()
        case_limit = min(len(cases), max_cases or 12)
        for case in cases:
            additions = set(case.qrels)
            if case.paired_source_doc_id is not None:
                additions.add(case.paired_source_doc_id)
            if len(requested_ids | additions) > 50:
                continue
            selected_cases.append(case)
            requested_ids.update(additions)
            if len(selected_cases) >= case_limit:
                break
        documents = tuple(document for document in documents if document.doc_id in requested_ids)
        remaining = max(0, 50 - len(documents))
        selected_ids = {document.doc_id for document in documents}
        documents += tuple(document for document in dataset.documents if document.doc_id not in selected_ids)[:remaining]
        present = {document.doc_id for document in documents}
        cases = tuple(case for case in selected_cases if set(case.qrels) <= present and
                      (case.paired_source_doc_id is None or case.paired_source_doc_id in present))
    elif max_cases is not None:
        cases = cases[:max_cases]
    if not cases or not documents:
        raise ExperimentInvalidError("empty_public_run_scope")
    token = uuid.uuid4().hex[:10]
    experiment_id = str(uuid.uuid4())
    db_name = f"rag_eval_trust_{token}_{dataset_name.replace('-', '_')[:10]}"
    database_url = create_isolated_database(admin_url, db_name)
    output_root = Path(output_root or (Path(data_root) / "runs"))
    output_dir = output_root / dataset_name / split / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{token}"
    env = {**os.environ, "RAG_DATABASE_URL": database_url, "RAG_CLOUD_ENABLED": "false", "RAG_LANGFUSE_ENABLED": "false"}
    subprocess.run([sys.executable, "-m", "alembic", "-c", str(repository_root / "alembic.ini"), "upgrade", "head"],
                   cwd=repository_root, env=env, check=True)
    settings = Settings(database_url=database_url, storage_root=output_dir / "storage", cloud_enabled=False,
                        langfuse_enabled=False, inline_ingestion_enabled=False, max_chunk_chars=chunk_size,
                        chunk_overlap=chunk_overlap)
    container = build_container(settings, agent_model=None)
    try:
        models = model_identities(container.ollama)
        kb_id = container.store.create_knowledge_base(f"public-{token}", cloud_allowed=False)["id"]
        bindings: dict[str, dict[str, str]] = {}
        document_external_ids: set[str] = set()
        started_at = datetime.now(timezone.utc).isoformat()
        ingest_started = time.perf_counter()
        for number, document in enumerate(documents, start=1):
            content = _source_content(document)
            payload = content.encode("utf-8")
            stored = container.storage.put_stream(BytesIO(payload))
            file_suffix = hashlib.sha256(document.doc_id.encode("utf-8")).hexdigest()[:24]
            receipt = container.store.create_upload(kb_id, f"{file_suffix}.md", "text/markdown", stored)
            bindings[receipt["document_id"]] = {"document_id": document.doc_id,
                                                 "source_version": hashlib.sha256(payload).hexdigest(),
                                                 "text": _expected_normalized_text(content)}
            document_external_ids.add(document.doc_id)
            if number % 250 == 0:
                print(json.dumps({"event": "ingestion_queued", "dataset": dataset_name, "documents": number}), flush=True)
        worker_id = f"public-{token}"
        for number in range(len(documents)):
            job = run_once(container.store, worker_id=worker_id)
            if job is None or job.get("status") != "succeeded":
                raise ExperimentInvalidError("real_public_ingestion_failed")
            if (number + 1) % 250 == 0:
                print(json.dumps({"event": "ingestion_completed", "dataset": dataset_name,
                                  "documents": number + 1}), flush=True)
        ingestion_ms = (time.perf_counter() - ingest_started) * 1000
        index = read_index_snapshot(container.store, kb_id, bindings, container.ollama.embedding_model)
        if index["index_counts"]["documents"] != len(documents) or index["index_counts"]["embeddings"] != index["index_counts"]["chunks"]:
            raise ExperimentInvalidError("public_index_count_mismatch")
        index["document_ids"] = document_external_ids
        context_builder = ContextBuilder(context_budget_chars)
        retrievers = {}
        gateways = {}
        actual_configs = {}
        profile_id = container.store.get_embedding_profile_id(container.ollama.embedding_model, 1024)
        if not profile_id:
            raise ExperimentInvalidError("query_embedding_profile_unavailable")
        query_embedding_cache = RunScopedQueryEmbeddingCache(
            container.ollama,
            EmbeddingCacheIdentity(
                model_digest=models["embedding"]["digest"],
                profile=profile_id,
                dimension=1024,
            ),
        )
        for variant in retrieval_variants:
            variant_id = variant["variant_id"]
            retriever = RecordingRetriever(container.store, embedding_provider=query_embedding_cache,
                top_k=variant["top_k"], candidate_k=variant["candidate_k"], rrf_k=variant["rrf_k"])
            retrievers[variant_id] = retriever
            actual_configs[variant_id] = effective_configuration(retriever, container.store, context_builder,
                declared={"top_k": variant["top_k"], "candidate_k": variant["candidate_k"], "rrf_k": variant["rrf_k"],
                          "chunk_size": chunk_size, "chunk_overlap": chunk_overlap,
                          "context_budget_chars": context_budget_chars})
            gateways[variant_id] = KnowledgeGateway(
                retriever, evidence_service=EvidenceService(context_builder=context_builder))
        base_variant = retrieval_variants[0]["variant_id"]
        retriever = retrievers[base_variant]
        gateway = gateways[base_variant]
        scope = Scope.from_ids([kb_id])
        if generation_limit:
            warmup_error = None
            with capture_usage() as warmup_usage:
                try:
                    container.ollama.answer("Reply READY.", 120)
                except Exception as exc:
                    warmup_error = type(exc).__name__
        else:
            warmup_usage = None
            warmup_error = None
        try:
            import tiktoken
            tokenizer = tiktoken.get_encoding("cl100k_base")
        except (ImportError, OSError):
            tokenizer = None
        rows: list[dict[str, Any]] = []
        per_variant_metrics: dict[str, dict[str, list[dict[str, Any]]]] = {
            variant["variant_id"]: {mode: [] for mode in RETRIEVAL_MODES} for variant in retrieval_variants}
        if split == "locked_holdout":
            validation_lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_record = {"status": "started", "dataset": dataset_name,
                           "dataset_version": dataset.dataset_version, "run_id": token,
                           "git_sha": code_sha, "candidate_config": actual_configs[base_variant],
                           "query_set_hash": _canonical_hash([case.qid for case in cases]),
                           "started_at": datetime.now(timezone.utc).isoformat()}
            try:
                with validation_lock_path.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(lock_record, sort_keys=True, indent=2) + "\n")
            except FileExistsError as exc:
                raise ExperimentInvalidError("locked_split_already_used") from exc
        for number, case in enumerate(cases, start=1):
            variant_rows = {}
            metric_for_case = {}
            for variant in retrieval_variants:
                variant_id = variant["variant_id"]
                current_gateway = gateways[variant_id]
                started = time.perf_counter()
                with capture_usage() as usage:
                    result = current_gateway.retrieve_query(scope, case.question)
                variant_row, variant_metrics = _case_row(case, result, container, index, context_builder,
                                                          actual_configs[variant_id], tokenizer)
                variant_row["timings_ms"]["retrieval_context_ms"] = (time.perf_counter() - started) * 1000
                variant_row["model_usage"] = usage.to_dict()
                variant_rows[variant_id] = variant_row
                metric_for_case[variant_id] = variant_metrics
                for mode, metric in variant_metrics.items():
                    per_variant_metrics[variant_id][mode].append(metric)
            row = variant_rows[base_variant]
            row["variants"] = {variant_id: {
                "effective_config": actual_configs[variant_id],
                "document_rankings": detail["document_rankings"],
                "chunk_rankings": detail["chunk_rankings"],
                "context_document_rankings": detail["context_document_rankings"],
                "metrics": detail["metrics"],
                "timings_ms": detail["timings_ms"],
                "estimated_context_tokens": detail["estimated_context_tokens"],
                "model_usage": detail["model_usage"],
            } for variant_id, detail in variant_rows.items()}
            if generation_limit and number <= generation_limit:
                retriever = retrievers[base_variant]
                retriever.records.clear()
                qa_started = time.perf_counter()
                with capture_usage() as answer_usage:
                    answer_result = LangChainQuickChain(gateways[base_variant], answer_gateway=container.ollama).invoke(
                        case.question, scope,
                        settings=QuickSettings(cloud_enabled=False, local_query_enabled=False,
                                               answer_timeout_seconds=120),
                        cloud_allowed_by_kb={kb_id: False},
                    )
                readbacks = _readback_citations(answer_result, container.store, scope)
                row["qa"] = {
                    "answer": answer_result.answer,
                    "error_code": answer_result.error_code,
                    "citations": [snapshot.model_dump() for snapshot in answer_result.evidence],
                    "citation_readbacks": readbacks,
                    "citation_readback_rate": (sum(readbacks.values()) / len(readbacks)) if readbacks else None,
                    "answer_metrics": _answer_metrics(answer_result.answer, case.answers),
                    "model_usage": answer_usage.to_dict(),
                    "retrieval_attempts": [{"latency_ms": attempt.latency_ms,
                                            "degradation_flags": list(attempt.degradation_flags),
                                            "returned_chunk_ids": [item.chunk.chunk_id for item in attempt.items]}
                                           for attempt in retriever.records],
                    "generation_latency_ms": sum(call.latency_ms for call in answer_usage.calls
                                                  if call.stage == "answer") or None,
                    "qa_latency_ms": (time.perf_counter() - qa_started) * 1000,
                    "context_tokens_estimated": (len(tokenizer.encode("\n".join(snapshot.quote for snapshot in answer_result.evidence)))
                                                  if tokenizer is not None and answer_result.evidence else None),
                    "citation_support": "NOT_EVALUATED",
                    "judge": "NOT_EVALUATED",
                }
                row["generation"] = "real_qwen" if any(call.stage == "answer" and call.status == "ok"
                                                         for call in answer_usage.calls) else "attempted_no_successful_answer_call"
            rows.append(row)
            if number % 25 == 0:
                print(json.dumps({"event": "queries_completed", "dataset": dataset_name,
                                  "split": split, "queries": number, "total": len(cases)}), flush=True)
        if model_identities(container.ollama) != models:
            raise ExperimentInvalidError("model_identity_changed_during_run")
        if committed_code_sha(repository_root) != code_sha:
            raise ExperimentInvalidError("code_changed_during_run")
        if read_index_snapshot(container.store, kb_id, bindings, container.ollama.embedding_model)["index_version"] != index["index_version"]:
            raise ExperimentInvalidError("index_changed_during_run")
        effective_qrels = {case.qid: case.qrels for case in cases}
        runtime_manifest = {
            "experiment_id": experiment_id,
            "git_sha": code_sha,
            "dataset": dataset_name,
            "dataset_version": dataset.dataset_version,
            "phase": phase,
            "source_urls": list(SOURCE_URLS[dataset_name]),
            "source_files": dataset.manifest["source_files"],
            "split": split,
            "profile": profile,
            "locked_split_scored": split == "locked_holdout",
            "corpus_hash": next(record["sha256"] for record in dataset.manifest["files"] if record["path"] == "corpus.jsonl"),
            "qrels_hash": _canonical_hash(effective_qrels),
            "query_set_hash": _canonical_hash([case.qid for case in cases]),
            "prepared_manifest_hash": _sha256((Path(data_root) / "prepared" / "adapters" / dataset_name / "manifest.json").read_bytes()),
            "models": models,
            "embedding_dimension": 1024,
            "query_embedding_cache": query_embedding_cache.snapshot(),
            "effective_config": actual_configs[base_variant],
            "effective_configs": actual_configs,
            "retrieval_mode_ablation": "keyword/vector rankings are the real candidate sets from one production hybrid call; hybrid uses the production fused ranking. ContextBuilder is applied independently to each stage ranking.",
            "qrels_kind": sorted({case.qrels_kind for case in cases}),
            "index_version": index["index_version"],
            "index_counts": index["index_counts"],
            "sample_count": len(rows),
            "generation_case_count": min(generation_limit, len(rows)),
            "warmup_usage": warmup_usage.to_dict() if warmup_usage is not None else "NOT_RUN",
            "warmup_error": warmup_error,
            "environment": {"os_family": platform.system().lower(), "python_version": platform.python_version(),
                            "architecture": platform.machine()},
            "evaluation_mode": "real_postgres_local_production_ingestion_retrieval",
            "started_at": started_at,
            "ended_at": datetime.now(timezone.utc).isoformat(),
        }
        summary = {variant_id: {mode: aggregate_metrics(metrics) for mode, metrics in stages.items() if metrics}
                   for variant_id, stages in per_variant_metrics.items()}
        report = {"manifest": runtime_manifest, "metrics": summary, "cases": rows,
                  "database_name": db_name, "knowledge_base_id": kb_id,
                  "ingestion_ms": ingestion_ms, "chunk_to_document_count": len(index["spans"]),
                  "tokenizer": "cl100k_base (estimated for Qwen)" if tokenizer is not None else "unavailable"}
        runtime_manifest["run_hash"] = _canonical_hash({"manifest": runtime_manifest, "metrics": summary})
        _write(output_dir / "manifest.json", runtime_manifest)
        _write(output_dir / "metrics.json", summary)
        _write(output_dir / "report.json", report)
        with (output_dir / "cases.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
        md = [f"# {dataset_name} {split} {profile} run", "", f"Code SHA: `{code_sha}`",
              f"Dataset version: `{dataset.dataset_version}`", f"Documents: {len(documents)}",
              f"Queries: {len(cases)}", f"Index counts: {index['index_counts']}",
              "", "Span-level recall: NOT_AVAILABLE for these document-level qrels.",
              "LongBench paired-context relevance is a derived diagnostic, not official retrieval qrels."]
        (output_dir / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        if split == "locked_holdout":
            lock_record.update(status="completed", run_dir=str(output_dir.resolve()),
                               completed_at=datetime.now(timezone.utc).isoformat(),
                               run_manifest_sha256=_sha256((output_dir / "manifest.json").read_bytes()))
            _write(validation_lock_path, lock_record)
        return {"status": "REAL_PUBLIC_RETRIEVAL_COMPLETED", "dataset": dataset_name,
                "split": split, "profile": profile, "run_dir": str(output_dir),
                "git_sha": code_sha, "documents": len(documents), "queries": len(cases),
                "index_counts": index["index_counts"], "metrics": summary}
    finally:
        container.engine.dispose()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
