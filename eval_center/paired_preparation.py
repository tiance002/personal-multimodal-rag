"""Non-send preparation for the ordinary paired runtime, with one late binding.

No grants, permits, counters or transports are manufactured here. The owner
composition callback creates fresh immutable answer bindings from actual wires.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType

from backend.app.application.quick_chain import QuickSettings
from backend.app.adapters.models.cloud import CloudChat
from backend.app.ports.session_attempts import request_identity
from eval_center.verification import ExperimentInvalidError


def require_retrieval_receipt_status(children, *, execution_kind, required_purposes):
    """Read-side status isolation; never infer execution identity from receipts."""
    if execution_kind not in {'REAL', 'SIMULATED'}:
        raise ExperimentInvalidError('paired_retrieval_execution_kind_invalid')
    expected = 'succeeded' if execution_kind == 'REAL' else 'simulated'
    if (len(children) != len(required_purposes)
            or {c['purpose'] for c in children} != required_purposes
            or any(c['status'] != expected for c in children)):
        raise ExperimentInvalidError('paired_retrieval_receipt_execution_kind_mismatch')
    return expected


@dataclass(frozen=True)
class LateRerankPreparation:
    """Non-send policy: a verified counter and an owner binding callback only."""
    admission: object
    bind: object

    def validate(self):
        from backend.app.adapters.models.cloud import RerankAdmission
        if type(self.admission) is not RerankAdmission or not callable(self.bind):
            raise ValueError('late_rerank_counter_and_callback_required')

    @property
    def identity(self):
        self.validate()
        return {'schema': 'paired-late-rerank/v1',
                'profile': self.admission.input_profile_sha256,
                'execution_kind': self.admission.execution_kind}


@dataclass(frozen=True)
class PreparedRerankWire:
    dataset: str
    qid: str
    question: str
    scope: object
    index_identity_sha256: str
    request_id: str
    document_version_ids: tuple[str, ...]
    chunk_ids: tuple[str, ...]
    documents: tuple[str, ...]
    wire_bytes: bytes
    input_profile_sha256: str
    input_token_bound: int

    @property
    def wire_sha256(self):
        return hashlib.sha256(self.wire_bytes).hexdigest()


def validate_late_rerank_preparation(item, binding, dataset, execution_kind, source_sha256):
    policy = dict(binding.late_rerank)[dataset]
    policy.validate()
    admission = policy.admission
    if (item.free_model_bindings.get('rerank') is not None
            or item.model_usage_guards.get('rerank') is not None
            or admission.execution_kind != execution_kind
            or execution_kind == 'REAL' and (
                not hasattr(admission, '_frozen_tokenizer')
                or getattr(admission, '_frozen_source_manifest_sha256', None) != source_sha256)):
        raise ValueError('late_rerank_non_send_preparation_required')


class LateSharedRetrievalReranker:
    """Seal actual fused candidates once, then use unchanged normal ranker guards."""
    provider_kind = 'cloud'

    def __init__(self, item, binding, dataset, *, paths, dispatch_guard):
        self.item, self.binding, self.dataset = item, binding, dataset
        self.paths, self.dispatch_guard = dict(paths), dispatch_guard
        self.policy = dict(binding.late_rerank)[dataset]
        self._consumed = set()
        self.seals = ()

    def _guard(self, claims):
        from eval_center.paired_state import sha256_file, validate_paired_run_link
        from backend.app.application.run_lifecycle import RunLifecycle
        RunLifecycle.assert_current()
        contract = self.binding.execution_contract
        for key, digest in (('source_manifest_path', contract.source_manifest_sha256),
                            ('cohort_path', contract.cohort_sha256),
                            ('index_manifest_path', contract.index_manifest_sha256)):
            if sha256_file(self.paths[key]) != digest:
                raise ExperimentInvalidError('late_rerank_frozen_input_changed:' + key)
        self.dispatch_guard()
        validate_paired_run_link(claims.path, contract,
            retrieval_binding_sha256=self.binding.fingerprint, for_dispatch=True)

    def rank(self, question, hits, chunks):
        import copy
        from backend.app.adapters.models.cloud import SiliconFlowRerank
        from backend.app.adapters.models.factory import ProviderFactory
        from backend.app.domain.model_registry import ModelRegistry
        from backend.app.application.provider_usage import BudgetUsageGuard
        from backend.app.ports.xing_zero_reservation import (SiliconFlowFreeBinding,
            FreeModelRequestContext, free_model_requests, free_dispatch_contract,
            free_dispatch_identity_sha256)
        from eval_center.paired_state import (CurrentProviderSendClaims, SQLiteProviderBatchSendClaims,
            ProviderBatchPlan, ProviderBatchCall, stable_provider_request_id,
            provider_send_claim_record, provider_send_claim_scope)
        claims = CurrentProviderSendClaims._bound()
        contract = self.binding.execution_contract
        if (type(claims) is not SQLiteProviderBatchSendClaims
                or claims.run_id != contract.retrieval_run_id or claims.arm != 'SHARED_RETRIEVAL'
                or claims.plan.purpose != 'query_embedding'):
            raise ExperimentInvalidError('late_rerank_requires_query_claim_scope')
        if claims.qid in self._consumed:
            raise ExperimentInvalidError('late_rerank_already_sealed_no_retry')
        self._consumed.add(claims.qid)
        self._guard(claims)
        receipt = provider_send_claim_record(claims.path, request_id=claims.request_id(purpose='query_embedding'))
        query_plan = self.binding.plan_for(self.dataset, claims.qid)
        if (receipt is None
                or hashlib.sha256(question.encode()).hexdigest() != query_plan.query_sha256):
            raise ExperimentInvalidError('late_rerank_requires_exact_q0_and_query_receipt')
        require_retrieval_receipt_status((receipt,), execution_kind=self.binding.execution_kind,
                                        required_purposes={'query_embedding'})
        # Copies prevent a callback changing caller-owned candidate objects.
        hits, chunks = copy.deepcopy(tuple(hits)), copy.deepcopy(chunks)
        ids = tuple(h.chunk_id for h in hits)
        scope = self.item.scope
        if (not ids or len(ids) != len(set(ids)) or any(key not in chunks for key in ids)
                or any(not scope.contains(chunks[key].knowledge_base_id, chunks[key].document_id)
                    or chunks[key].is_current is not True or chunks[key].chunk_role != 'child'
                    or chunks[key].chunk_id != key for key in ids)):
            raise ExperimentInvalidError('late_rerank_candidate_scope_invalid')
        documents = tuple(chunks[key].content for key in ids)
        versions = tuple(str(chunks[key].version_id) for key in ids)
        spec = ModelRegistry.frozen_defaults().select('rerank')
        payload = SiliconFlowRerank.render_payload(spec.model_id, question, documents)
        wire = json.dumps(payload).encode()
        admission = self.policy.admission
        prepared = PreparedRerankWire(self.dataset, claims.qid, question, scope,
            next(s.index_identity_sha256 for s in contract.selections if s.dataset == self.dataset),
            stable_provider_request_id(run_id=claims.run_id, qid=claims.qid,
                arm=claims.arm, purpose='rerank'), versions, ids, documents, wire,
            admission.input_profile_sha256, admission.count(question, documents))
        self._guard(claims)
        free, guard = self.policy.bind(prepared)
        self._guard(claims)
        if type(free) is not SiliconFlowFreeBinding or free.admission is not admission:
            raise ExperimentInvalidError('late_rerank_new_exact_binding_required')
        permit = free.permit
        permit.validate(spec, free.authorized_transport)
        ep = self.item.free_model_bindings['embedding'].permit
        if (permit.run_id != claims.run_id or permit.scope != scope
                or permit.source_manifest_sha256 != contract.source_manifest_sha256
                or permit.authority_manifest_sha256 != ep.authority_manifest_sha256
                or permit.owner_activity_id != ep.owner_activity_id or permit.deadline_utc != ep.deadline_utc
                or permit.execution_lease is not contract.execution_lease
                or permit.execution_kind != ep.execution_kind or len(permit.calls) != 1
                or type(free.authorized_transport.send_claim_callback) is not CurrentProviderSendClaims
                or type(guard) is not BudgetUsageGuard or guard.zero_price_permit is not permit
                or guard.estimate_microunits != 0):
            raise ExperimentInvalidError('late_rerank_authority_changed')
        call = permit.calls[0]
        if (call.request_id != prepared.request_id or call.qid != claims.qid
                or call.arm != claims.arm or call.purpose != 'rerank' or call.call_ordinal != 1
                or call.wire_sha256 != prepared.wire_sha256
                or call.input_profile_sha256 != prepared.input_profile_sha256
                or call.input_token_bound != prepared.input_token_bound
                or call.output_token_bound != 0 or call.item_count != len(ids) or call.timeout_seconds != 30
                or call.document_version_ids != versions or call.chunk_ids != ids or call.candidate_order != ids
                or call.input_sha256 != tuple(hashlib.sha256(t.encode()).hexdigest() for t in documents)
                or call.query_sha256 != query_plan.query_sha256):
            raise ExperimentInvalidError('late_rerank_exact_actual_candidates_required')
        native = ProviderBatchCall(1, versions, ids, call.input_sha256, call.wire_sha256,
            free_dispatch_identity_sha256(permit, call), ids)
        plan = ProviderBatchPlan('rerank', spec.provider, spec.model_id,
            query_plan.embedding_identity_sha256, query_plan.index_profile_sha256,
            free_dispatch_contract(permit, call)['scope_sha256'], (native,), call.query_sha256)
        plan.validate()
        self.seals += ((prepared, free, guard, plan),)
        factory = ProviderFactory(ModelRegistry.frozen_defaults(), enabled_roles=('rerank',),
            usage_guards={'rerank': guard}, free_model_bindings={'rerank': free})
        ranker = factory.build('rerank')
        def check(pin):
            if pin != contract.lease_sha256:
                raise ExperimentInvalidError('late_rerank_lease_changed')
            self._guard(claims)
        rank_claims = SQLiteProviderBatchSendClaims(claims.path, claims.run_id, claims.qid,
            claims.arm, claims.input_identity_sha256, plan, contract.lease_sha256, check)
        context = FreeModelRequestContext(claims.run_id, prepared.request_id, 'rerank',
            call.batch_id, scope, batch_plan=plan)
        check(contract.lease_sha256)
        with provider_send_claim_scope(rank_claims), free_model_requests((context,)):
            return ranker.rank(question, hits, chunks)


@dataclass(frozen=True)
class PreparedAnswerWire:
    dataset: str
    qid: str
    arm: str
    role: str
    request_id: str
    envelope: object
    wire_bytes: bytes
    input_profile_sha256: str | None
    input_token_bound: int | None
    output_token_bound: int

    @property
    def wire_sha256(self):
        return hashlib.sha256(self.wire_bytes).hexdigest()


def assert_prepared_answer_envelope(expected, qid, arm, envelope):
    """Final non-send check shared by the paired envelope admission boundary."""
    if expected is not None and expected.get((qid, arm)) != envelope.identity:
        raise ExperimentInvalidError('prepared_answer_context_or_payload_changed')


def prepare_normal_paired_runtime(composition, *, bind_answers, is_cancelled=lambda: False,
                                 checkpoint_path=None, checkpoint_sha256=None, **paths):
    """Stage 1: normal structural/owner/query/index admission, no chat capability.

    ``bind_answers(tuple[PreparedAnswerWire, ...])`` must return a new normal
    PairedRuntimeComposition. It must not mutate the preparation composition.
    The normal cohort runner registers Run/link before consuming this object.
    """
    from eval_center.public_runner import build_normal_paired_runtime
    if not callable(bind_answers) or not callable(is_cancelled):
        raise ValueError('preparation_callbacks_required')
    runtime = build_normal_paired_runtime(composition, _retrieval_only=True, **paths)
    return PairedRetrievalPreparation(composition, runtime, dict(paths), bind_answers, is_cancelled,
                                     checkpoint_path=checkpoint_path, checkpoint_sha256=checkpoint_sha256)


class PairedRetrievalPreparation:
    """A preparation handle, deliberately rejected by normal runtime admission."""
    def __init__(self, composition, runtime, paths, bind_answers, is_cancelled,
                 *, checkpoint_path=None, checkpoint_sha256=None):
        self.composition, self.runtime, self.paths = composition, runtime, paths
        self._bind_answers, self._is_cancelled = bind_answers, is_cancelled
        self._consumed = False
        self.wires = ()
        self.checkpoint_path, self.checkpoint_sha256 = checkpoint_path, checkpoint_sha256

    def _checkpoint_identity(self):
        return {'schema': 'paired-prepared-context/v1',
                'contract': self.runtime.execution_contract.fingerprint,
                'binding': self.runtime.shared_retrieval_binding.fingerprint}

    def _read_checkpoint(self, state_path, cohort):
        from pathlib import Path
        from eval_center.paired_state import attempts_for_qid
        if self.checkpoint_sha256 is None:
            return None
        raw = Path(self.checkpoint_path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.checkpoint_sha256:
            raise ExperimentInvalidError('preparation_checkpoint_hash_changed')
        data = json.loads(raw)
        if (data['identity'] != self._checkpoint_identity()
                or set(data['cases']) != {r['dataset'] + '\0' + r['qid'] for r in cohort}):
            raise ExperimentInvalidError('preparation_checkpoint_identity_changed')
        contract = self.runtime.execution_contract
        if self.runtime.shared_retrieval_binding.execution_kind != self.composition.execution_kind:
            raise ExperimentInvalidError('preparation_checkpoint_execution_kind_changed')
        for row in cohort:
            record = attempts_for_qid(state_path, run_id=contract.retrieval_run_id, qid=row['qid'])
            expected = 'succeeded' if self.composition.execution_kind == 'REAL' else 'simulated'
            if (record.get('SHARED_RETRIEVAL', {}).get('status') != expected
                    or record != data['cases'][row['dataset'] + '\0' + row['qid']]['receipt']):
                raise ExperimentInvalidError('preparation_checkpoint_receipt_changed_or_unknown')
            binding = self.runtime.shared_retrieval_binding
            required = {'query_embedding'} | ({'rerank'} if binding.rerank_plans or binding.late_rerank else set())
            require_retrieval_receipt_status(record['SHARED_RETRIEVAL']['provider_calls'],
                execution_kind=self.composition.execution_kind, required_purposes=required)
        return data['cases']

    def _save_checkpoint(self, cases):
        if self.checkpoint_path is None or self.checkpoint_sha256 is not None:
            return
        import os
        from pathlib import Path
        raw = json.dumps({'identity': self._checkpoint_identity(), 'cases': cases},
                         sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        # Exclusive creation never silently replaces an earlier frozen Context.
        with Path(self.checkpoint_path).open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        self.checkpoint_sha256 = hashlib.sha256(raw).hexdigest()

    def _guard(self):
        from eval_center.paired_state import sha256_file
        contract = self.runtime.execution_contract
        lease = contract.execution_lease
        now = datetime.now(timezone.utc)
        if self._is_cancelled():
            raise ExperimentInvalidError('preparation_cancelled')
        if not lease.not_before <= now < lease.deadline:
            raise ExperimentInvalidError('preparation_lease_expired_or_not_started')
        for key, digest in (('source_manifest_path', contract.source_manifest_sha256),
                            ('cohort_path', contract.cohort_sha256),
                            ('index_manifest_path', contract.index_manifest_sha256)):
            if sha256_file(self.paths[key]) != digest:
                raise ExperimentInvalidError('preparation_frozen_input_changed:' + key)
        self.runtime.dispatch_guard()

    def bind_after_retrieval(self, *, state_path, cohort, manifest, index_manifest_path, top_k):
        from eval_center.public_runner import (
            _memory_free_container, _FrozenCaseGateway, _shared_retrieval_context,
            build_normal_paired_runtime, _paired_runtime_admission,
        )
        from eval_center.paired_state import attempts_for_qid, validate_paired_run_link
        from backend.app.application.citations import CitationService, InMemoryCitationStore
        from backend.app.adapters.models.deepseek import _wire_payload
        from backend.app.domain.model_registry import ModelRegistry

        if self._consumed:
            raise ExperimentInvalidError('preparation_already_bound_no_retry')
        self._consumed = True  # failure/UNKNOWN is not a retry permission
        self._guard()
        contract, binding = self.runtime.execution_contract, self.runtime.shared_retrieval_binding
        validate_paired_run_link(state_path, contract,
            retrieval_binding_sha256=binding.fingerprint, for_dispatch=True)
        recovered = self._read_checkpoint(state_path, cohort)
        # Check the whole cohort before any retrieval. A crash/terminal receipt
        # never leads to a new embedding or answer send on preparation resume.
        for row in cohort:
            if (attempts_for_qid(state_path, run_id=contract.cohort_run_id, qid=row['qid'])
                    or recovered is None and attempts_for_qid(state_path,
                        run_id=contract.retrieval_run_id, qid=row['qid'])):
                raise ExperimentInvalidError('preparation_existing_receipt_no_retry')
        from pydantic import TypeAdapter
        from backend.app.application.knowledge_gateway import EvidenceBundle
        bundle_adapter = TypeAdapter(EvidenceBundle)
        frozen, wires, checkpoints = {}, [], {}
        for row in cohort:
            self._guard()
            name, qid, question = row['dataset'], row['qid'], row['question']
            item, scope = self.composition.datasets[name], self.runtime.scopes[name]
            container = _memory_free_container(self.runtime.containers[name])
            chain = container.quick_chain
            gateway = _FrozenCaseGateway(chain.knowledge_gateway, strict_scope=scope,
                retrieval_context=lambda s, q, n=name, i=qid, c=container:
                    _shared_retrieval_context(binding, state_path=state_path, dataset=n, qid=i,
                        scope=s, question=q, execution_context=c.execution_context))
            key = name + '\0' + qid
            if recovered is None:
                plan, retrieval = gateway.retrieve_question(scope, question,
                    local_query_gateway=None, local_query_enabled=False,
                    local_query_timeout_seconds=4.0, understanding=None)
                self._guard()
                bundle = gateway.evidence.bundle(plan, retrieval)
                if not bundle.selected or not bundle.decision.accepted:
                    raise ExperimentInvalidError('preparation_no_accepted_evidence')
                bundle = gateway.evidence.with_context(bundle,
                    str(uuid.uuid5(uuid.NAMESPACE_URL, f'{contract.cohort_run_id}:{qid}:CHEAP_ONLY')),
                    CitationService(InMemoryCitationStore()))
            else:
                bundle = bundle_adapter.validate_json(recovered[key]['bundle'])
                if bundle.plan.question != question or not bundle.selected or not bundle.decision.accepted:
                    raise ExperimentInvalidError('preparation_checkpoint_question_or_evidence_changed')
                for entry in (*bundle.retrieval.items, *bundle.selected):
                    scope.assert_contains(entry.chunk.knowledge_base_id, entry.chunk.document_id)
                gateway.retrieved = ((scope, question), (bundle.plan, bundle.retrieval))
                gateway.evidence.selected_bundle = bundle
                gateway.evidence.context_bundle = bundle
            if self.checkpoint_path is not None:
                checkpoints[key] = {'bundle': bundle_adapter.dump_json(bundle).decode(),
                    'receipt': attempts_for_qid(state_path, run_id=contract.retrieval_run_id, qid=qid)}
            if not bundle.labels:
                raise ExperimentInvalidError('preparation_no_context_labels')
            settings = QuickSettings(answer_output_token_cap=512, answer_timeout_seconds=30)
            for arm in ('CHEAP_ONLY', 'EXPENSIVE_ONLY', 'DYNAMIC'):
                arm_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'{contract.cohort_run_id}:{qid}:{arm}'))
                prompt, budget = chain.render_answer_prompt(question, bundle, settings)
                if chain.context_manager is not None:
                    prompt = chain.context_manager.quick_prompt(prompt, conversation=arm_id,
                        scope=scope, run=arm_id, roles=('chat_cheap', 'chat_expensive'),
                        output_tokens=budget.max_tokens, query=question, allow_global_memory=False)
                payload = dict(question=question, bundle=bundle, settings=settings)
                envelope, _, _, _ = chain.render_generation_envelope(payload, prompt, budget.max_tokens)
                roles = (('chat_cheap', 'chat_expensive') if arm == 'DYNAMIC' else
                         ('chat_cheap',) if arm == 'CHEAP_ONLY' else ('chat_expensive',))
                for role in roles:
                    if role == 'chat_cheap':
                        spec = ModelRegistry.frozen_defaults().select(role)
                        wire_payload = CloudChat.render_xing_payload(spec.model_id,
                            [{'role': 'user', 'content': envelope.prompt}], envelope.max_tokens)
                        wire = json.dumps(wire_payload).encode()
                        estimate = item.xing_binding.capacity_counter.measure(spec.provider, spec.model_id,
                            wire_payload, scope=item.xing_binding.grant.scope_id, wire_bytes=wire)
                        estimate.assert_wire_bytes(wire)
                        profile = hashlib.sha256(estimate.renderer_identity.encode()).hexdigest()
                        bound = estimate.reservation_bound
                    else:
                        wire = _wire_payload(item.v6_binding.grant.model, envelope.prompt, envelope.max_tokens)
                        profile, bound = None, None  # normal DeepSeek admission measures and binds this wire
                    wires.append(PreparedAnswerWire(name, qid, arm, role,
                        request_identity(arm_id, 'quick.router.' + role, 1), envelope,
                        wire, profile, bound, envelope.max_tokens))
            if len({w.envelope.identity for w in wires if (w.dataset, w.qid) == (name, qid)}) != 1:
                raise ExperimentInvalidError('preparation_arm_context_divergence')
            frozen[(name, qid)] = gateway
        self._guard()
        self._save_checkpoint(checkpoints)
        self.wires = tuple(wires)
        final = self._bind_answers(self.wires)
        # Only new immutable chat bindings/usage guards may change. The authority,
        # native query plan, owner lease, sources, scopes and factories stay pinned.
        if type(final) is not type(self.composition) or set(final.datasets) != set(self.composition.datasets):
            raise ExperimentInvalidError('preparation_composition_changed')
        for key, value in vars(self.composition).items():
            if key != 'datasets' and getattr(final, key) != value:
                raise ExperimentInvalidError('preparation_composition_changed:' + key)
        for name, old in self.composition.datasets.items():
            new = final.datasets[name]
            for key, value in vars(old).items():
                if key not in ('xing_binding', 'model_usage_guards') and getattr(new, key) != value:
                    raise ExperimentInvalidError('preparation_dataset_changed:' + key)
            if (set(new.model_usage_guards) != set(old.model_usage_guards) | {'chat_cheap'}
                    or any(new.model_usage_guards[key] is not guard
                           for key, guard in old.model_usage_guards.items() if key != 'chat_cheap')):
                raise ExperimentInvalidError('preparation_non_chat_budget_guard_changed')
            if (new.v6_binding is not old.v6_binding
                    or new.xing_binding.grant is not old.xing_binding.grant
                    or new.xing_binding.capacity_counter is not old.xing_binding.capacity_counter
                    or new.xing_binding.authorized_transport is old.xing_binding.authorized_transport
                    or new.xing_binding.request_window.authority_manifest_sha256 != old.free_model_bindings['embedding'].permit.authority_manifest_sha256):
                raise ExperimentInvalidError('preparation_new_immutable_chat_binding_required')
        self._guard()
        runtime = build_normal_paired_runtime(final, **self.paths)
        missing, _ = _paired_runtime_admission(runtime, manifest=manifest,
            index_manifest_path=index_manifest_path, execution_contract=contract,
            cohort=cohort, cohort_sha256=contract.cohort_sha256,
            source_sha256=contract.source_manifest_sha256, run_id=contract.cohort_run_id)
        if missing:
            raise ExperimentInvalidError('preparation_final_admission:' + ','.join(missing))
        for prepared in self.wires:
            if prepared.role != 'chat_cheap':
                continue
            permit = final.datasets[prepared.dataset].xing_binding.request_window
            call = next(c for c in permit.calls if c.request_id == prepared.request_id)
            if (call.wire_sha256 != prepared.wire_sha256
                    or call.input_profile_sha256 != prepared.input_profile_sha256
                    or call.input_token_bound < prepared.input_token_bound
                    or call.output_token_bound != prepared.output_token_bound
                    or call.timeout_seconds != prepared.envelope.timeout_seconds):
                raise ExperimentInvalidError('preparation_exact_answer_wire_required')
        for name, container in runtime.containers.items():
            object.__setattr__(container, 'prepared_case_gateways', MappingProxyType(
                {qid: gateway for (dataset, qid), gateway in frozen.items() if dataset == name}))
            object.__setattr__(container, 'prepared_answer_envelopes', MappingProxyType(
                {(w.qid, w.arm): w.envelope.identity for w in self.wires if w.dataset == name}))
        self._guard()
        return runtime
