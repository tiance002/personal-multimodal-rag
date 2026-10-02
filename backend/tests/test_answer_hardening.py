import json
import pytest

from backend.app.application.answer_hardening import detect_intents, generation_budget, citation_occurrences, HardeningPolicy
from backend.app.adapters.answer_audit import LocalAnswerAudit
from backend.app.application.citations import CitationChunk, CitationService, InMemoryCitationStore
from backend.app.application.knowledge_gateway import EvidenceService
from backend.app.application.run_metrics import collect_metrics
from backend.app.domain.errors import EvidenceIntegrityError


def evidence():
    service = CitationService(InMemoryCitationStore())
    chunk = CitationChunk('c', 'doc', 'kb', 'v', '缓存用于减少重复读取。备份通过保存副本恢复数据。', {'start': 0})
    service.freeze('run', chunk)
    return chunk, tuple(service.snapshots.values())


def test_conservative_intents():
    assert len(detect_intents('缓存和备份的关系是什么？').intents) == 1
    assert len(detect_intents('缓存是什么，同时备份如何操作？').intents) == 2
    assert len(detect_intents('缓存有什么作用？备份如何操作？').intents) == 2
    assert len(detect_intents('分别解释缓存和备份。').intents) == 2
    assert detect_intents('读取目录，以及文件内容。').route_reason == 'SINGLE_OR_AMBIGUOUS'
    assert len(detect_intents('项目说明中，何时备份，缓存如何关闭？').intents) == 2
    assert len(detect_intents('为节省空间，哪些文件可以清理？').intents) == 1
    assert len(detect_intents('日志如何备份？应该保留多久？').intents) == 2


def test_follow_up_background_does_not_become_current_intents():
    from backend.app.application.follow_up import resolve_follow_up
    question, used = resolve_follow_up('刚才的缓存如何关闭？', '备份是什么？日志怎么保存？')
    plan = detect_intents(question)
    assert plan.intents == ('刚才的缓存如何关闭？',)
    assert not used and question == '刚才的缓存如何关闭？'
    assert plan.route_reason == 'SINGLE_OR_AMBIGUOUS'


def test_budget_bounded_without_continuation():
    simple = generation_budget(detect_intents('缓存是什么？'), 100)
    complex_ = generation_budget(detect_intents('缓存有什么作用？备份如何操作？'), 6000)
    assert simple.max_tokens < complex_.max_tokens <= 1536
    assert complex_.max_calls == 1


def test_identity_is_reused_and_conflict_rejected():
    service = CitationService(InMemoryCitationStore())
    c = CitationChunk('c', 'doc', 'kb', 'v', 'fact', {})
    assert service.freeze('run', c).label == service.freeze('run', c).label
    assert len(service.snapshots) == 1
    service.freeze('run', CitationChunk('d', 'doc', 'kb', 'v', 'other', {}))
    with pytest.raises(EvidenceIntegrityError):
        service.freeze('run', c, 'E2')


def test_occurrences_include_identity_and_span_not_text():
    chunk, snapshots = evidence()
    rows = citation_occurrences('缓存减少读取 [E1]。备份保存副本 [E1]。', snapshots, [chunk])
    assert len(rows) == 2
    assert rows[0]['citation_occurrence'] == 1
    assert rows[1]['document_id'] == 'doc'
    assert rows[0]['chunk_id'] == rows[1]['chunk_id'] == 'c'
    assert isinstance(rows[0]['answer_span'], list)
    assert '缓存' not in json.dumps(rows, ensure_ascii=False)


def test_occurrence_locator_excludes_embedded_source_quote():
    service = CitationService(InMemoryCitationStore())
    chunk = CitationChunk('c', 'doc', 'kb', 'v', 'private text', {'start': 0, 'end': 12, 'quote': 'private text'})
    service.freeze('run', chunk)
    rows = citation_occurrences('claim [E1]', tuple(service.snapshots.values()), [chunk])
    assert rows[0]['locator'] == {'start': 0, 'end': 12}
    assert 'private text' not in json.dumps(rows)


def test_rejection_audit_then_exact_evidence_fallback(tmp_path):
    chunk, snapshots = evidence()
    policy = HardeningPolicy(LocalAnswerAudit(tmp_path))
    plan = EvidenceService().plan('缓存有什么作用？')
    result = policy.finalize('run', 'unsupported candidate', snapshots, [chunk], plan, 'UNSUPPORTED_ANSWER')
    assert result.error is None
    assert result.fallback == 'EVIDENCE_ONLY_FALLBACK'
    assert 'unsupported candidate' not in result.answer
    assert '缓存用于减少重复读取' in result.answer
    record = json.loads(next(tmp_path.glob('*.json')).read_text(encoding='utf-8'))
    assert record['candidate_present'] is True
    assert record['candidate_local_audit_payload']['text'] == 'unsupported candidate'
    assert record['validator_reason'] == 'UNSUPPORTED_ANSWER'
    assert record['supporting_context_chunk_ids'] == ['c']


def test_no_direct_evidence_no_fallback(tmp_path):
    chunk, snapshots = evidence()
    result = HardeningPolicy(LocalAnswerAudit(tmp_path)).finalize('run', 'wrong', snapshots, [chunk], EvidenceService().plan('火星人口是多少？'), 'UNSUPPORTED_ANSWER')
    assert result.error == 'UNSUPPORTED_ANSWER'
    assert result.answer == ''


def test_numeric_request_cannot_fallback_to_generic_resource_advice(tmp_path):
    chunk = CitationChunk('c', 'doc', 'kb', 'v', 'GPU 上可以运行模型，建议使用 8 GB 设备。', {'start': 0})
    service = CitationService(InMemoryCitationStore())
    service.freeze('run', chunk)
    result = HardeningPolicy(LocalAnswerAudit(tmp_path)).finalize('run', 'bad', tuple(service.snapshots.values()), [chunk], EvidenceService().plan('GPU 实测使用多少 MiB？'), 'UNSUPPORTED_ANSWER')
    assert result.error == 'UNSUPPORTED_ANSWER'
    assert result.fallback is None


def test_exact_topic_with_generic_source_prefix_extracts_operations(tmp_path):
    chunk = CitationChunk('c', 'doc', 'kb', 'v', '恢复工具——执行备份校验，之后加载副本。', {'start': 0})
    service = CitationService(InMemoryCitationStore())
    service.freeze('run', chunk)
    result = HardeningPolicy(LocalAnswerAudit(tmp_path)).finalize('run', 'bad', tuple(service.snapshots.values()), [chunk], EvidenceService().plan('资料所说的恢复工具具体怎么做？'), 'UNSUPPORTED_ANSWER')
    assert result.error is None
    assert '执行备份校验' in result.answer


def test_obvious_contradiction_and_intent_omission_are_observable(tmp_path):
    chunk, snapshots = evidence()
    policy = HardeningPolicy(LocalAnswerAudit(tmp_path))
    with collect_metrics('run') as metrics:
        result = policy.finalize('run', '资料没有提及缓存。缓存用于减少重复读取 [E1]。', snapshots, [chunk], EvidenceService().plan('缓存有什么作用？'), None)
        assert result.rejection == 'SELF_CONTRADICTION'
        assert 'candidate_local_audit_payload' not in json.dumps(metrics.snapshot(citations=(), error=None))
    result = policy.finalize('r2', '缓存用于减少重复读取 [E1]。', snapshots, [chunk], EvidenceService().plan('缓存有什么作用？备份如何操作？'), None)
    assert result.coverage == ['MENTIONED', 'MISSING_OR_UNKNOWN']


def test_citation_without_overlap_is_unknown_not_supported(tmp_path):
    chunk, snapshots = evidence()
    result = HardeningPolicy(LocalAnswerAudit(tmp_path)).finalize('run', '木星公转时间 [E1]。', snapshots, [chunk], EvidenceService().plan('木星公转时间？'), None)
    assert result.occurrences[0]['relevance'] == 'CITATION_RELEVANCE_UNKNOWN'


def test_whitespace_marker_canonicalized_without_inventing_identity(tmp_path):
    chunk, snapshots = evidence()
    service = EvidenceService(answer_audit=LocalAnswerAudit(tmp_path))
    result = service.finalize_answer('run', '缓存用于减少重复读取 [ E1 ]。', snapshots, [chunk], service.plan('缓存有什么作用？'))
    assert result.rejection is None
    assert '[E1]' in result.answer
    assert '[ E1 ]' not in result.answer


def test_placeholder_and_range_citations_are_not_valid_labels(tmp_path):
    chunk, snapshots = evidence()
    plan = EvidenceService().plan('火星有多少人口？')
    for bad in ('no information [E#] [E1]', 'bad [E1-E4]'):
        result = HardeningPolicy(LocalAnswerAudit(tmp_path)).finalize('run', bad, snapshots, [chunk], plan, None)
        assert result.rejection == 'INVALID_CITATION'
        assert result.error == 'INVALID_CITATION'


def test_marker_after_sentence_binds_to_previous_claim():
    chunk, snapshots = evidence()
    answer = '缓存用于减少重复读取。[E1]'
    rows = citation_occurrences(answer, snapshots, [chunk])
    assert rows[0]['answer_span'] == [0, len(answer)]


def test_quick_rejected_candidate_is_audited_and_never_released(tmp_path):
    from backend.tests.test_quick_chain_quality import _chain, RecordingAnswerModel
    from backend.app.domain.scope import Scope
    chain = _chain(['A方案成本1000元。'], RecordingAnswerModel('A方案成本999元 [E1]'))
    chain.knowledge_gateway.evidence.hardening = HardeningPolicy(LocalAnswerAudit(tmp_path))
    result = chain.invoke('A 的成本是多少？', Scope.from_ids(['kb']))
    assert result.error_code is None
    assert '999' not in result.answer
    assert '1000' in result.answer
    assert 'EVIDENCE_ONLY_FALLBACK' in result.trace.reason_codes
    assert len(list(tmp_path.glob('*.json'))) == 1


def test_quick_multi_intent_single_generation_prompt():
    from backend.tests.test_quick_chain_quality import _chain, RecordingAnswerModel
    from backend.app.domain.scope import Scope
    model = RecordingAnswerModel('缓存用于减少重复读取 [E1]。备份通过保存副本恢复数据 [E1]。')
    chain = _chain(['缓存用于减少重复读取。备份通过保存副本恢复数据。'], model)
    result = chain.invoke('缓存有什么作用？备份如何操作？', Scope.from_ids(['kb']))
    assert result.error_code is None
    assert len(model.prompts) == 1
    assert 'Question intents' in model.prompts[0]


def test_length_candidate_retained_with_explicit_budget(monkeypatch):
    from backend.app.adapters.models.ollama import OllamaGateway
    from backend.app.ports.providers import ProviderUnavailable
    gateway = OllamaGateway('http://localhost', 'test', 'embed')
    requests = []
    def post(path, payload, timeout):
        requests.append(payload)
        return {'message': {'content': 'partial private answer'}, 'done_reason': 'length'}, 1
    monkeypatch.setattr(gateway, '_post', post)
    with pytest.raises(ProviderUnavailable) as exc:
        gateway.answer_with_budget('prompt', 10, 896)
    assert exc.value.candidate == 'partial private answer'
    assert str(exc.value) == 'MODEL_OUTPUT_TRUNCATED'
    assert requests[0]['options']['num_predict'] == 896


def test_audit_failure_refuses_instead_of_unlogged_fallback():
    class BrokenAudit:
        def save(self, record):
            raise OSError('disk unavailable')
    chunk, snapshots = evidence()
    with collect_metrics('run') as metrics:
        result = HardeningPolicy(BrokenAudit()).finalize('run', 'wrong', snapshots, [chunk], EvidenceService().plan('缓存是什么？'), 'UNSUPPORTED_ANSWER')
        assert result.error == 'UNSUPPORTED_ANSWER'
        assert metrics.hardening['audit_status'] == 'AUDIT_WRITE_FAILED'


def test_fallback_keeps_scope_and_zero_cloud(tmp_path):
    from backend.tests.test_quick_chain_quality import RecordingAnswerModel
    from backend.app.application.quick_chain import LangChainQuickChain
    from backend.app.application.knowledge_gateway import KnowledgeGateway
    from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
    from backend.app.domain.models import ChunkRecord
    from backend.app.domain.scope import Scope
    repo = InMemoryRetrievalRepository()
    repo.add(ChunkRecord('allowed', 'kb', 'd', 'v', 'A方案成本1000元。', {}))
    repo.add(ChunkRecord('secret', 'other', 'd2', 'v2', 'A方案成本5000元。 PRIVATE', {}))
    core = KnowledgeGateway(HybridRetriever(repo), evidence_service=EvidenceService(answer_audit=LocalAnswerAudit(tmp_path)))
    with collect_metrics('run') as metrics:
        result = LangChainQuickChain(core, answer_gateway=RecordingAnswerModel('bad')).invoke('A 的成本是多少？', Scope.from_ids(['kb']))
        assert '1000' in result.answer and '5000' not in result.answer
        assert metrics.snapshot(citations=result.citations, error=result.error_code)['cloud_called'] is False
    record = json.loads(next(tmp_path.glob('*.json')).read_text(encoding='utf-8'))
    assert record['supporting_context_chunk_ids'] == ['allowed']


def test_smart_length_candidate_audited_without_extra_call(tmp_path):
    from backend.tests.test_langchain_agent import ScriptedChatModel
    from backend.app.application.langchain_agent import LangChainAgentAdapter
    from backend.app.application.knowledge_gateway import KnowledgeGateway
    from backend.app.application.knowledge_tools import KnowledgeToolGateway
    from backend.app.application.evidence_accumulator import EvidenceAccumulator
    from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
    from backend.app.domain.models import ChunkRecord
    from backend.app.domain.scope import Scope
    class LengthModel(ScriptedChatModel):
        def _generate(self, *args, **kwargs):
            result = super()._generate(*args, **kwargs)
            if self.calls == 2:
                result.generations[0].message.response_metadata = {'done_reason': 'length'}
            return result
    repo = InMemoryRetrievalRepository()
    repo.add(ChunkRecord('chunk-1', 'kb', 'doc', 'v', 'question 缓存用于减少重复读取。', {'start': 0}))
    core = KnowledgeGateway(HybridRetriever(repo), evidence_service=EvidenceService(answer_audit=LocalAnswerAudit(tmp_path)))
    accumulated = EvidenceAccumulator()
    model = LengthModel(final_answer='partial private content')
    result = LangChainAgentAdapter(model).run('conv', '缓存有什么作用？', Scope.from_ids(['kb']), run_id='run',
        gateway=KnowledgeToolGateway(knowledge_gateway=core, evidence_accumulator=accumulated), evidence=accumulated)
    assert result.error_code is None
    assert result.model_calls == 2
    assert 'partial private content' not in result.answer
    assert '缓存用于减少重复读取' in result.answer
    record = json.loads(next(tmp_path.glob('*.json')).read_text(encoding='utf-8'))
    assert record['validator_reason'] == 'MODEL_OUTPUT_TRUNCATED'
