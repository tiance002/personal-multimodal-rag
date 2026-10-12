"""Failure presentation contract; no model/DB calls or factual promotion."""
import pytest
from backend.app.ports.run_presentation import failure_presentation, valid_failure_presentation


def test_egress_gate_before_retrieval_is_permission_required():
    result=failure_presentation('CLOUD_EGRESS_DISABLED')
    assert result['category']=='permission_required'
    assert result['reason_code']=='CLOUD_EGRESS_DISABLED'
    assert valid_failure_presentation(result)
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository


def metrics(status, reason=None):
    return {'retrieval_diagnostics':[{'schema_version':'retrieval-diagnostic-v1','channels':{
        'keyword':{'status':'NOT_SELECTED','count':None,'reason':None},
        'vector':{'status':status,'count':0 if status=='EMPTY' else None,'reason':reason}}}]}


@pytest.mark.parametrize('data,category,reason', [
    (None,'retrieval_error','RETRIEVAL_DIAGNOSTIC_MISSING'),
    (metrics('EMPTY'),'no_match','NO_CANDIDATES'),
    (metrics('NOT_SENT','MODEL_ROLE_EGRESS_DENIED'),'retrieval_error','MODEL_ROLE_EGRESS_DENIED'),
    (metrics('FAILED','SECRET-provider-raw-message'),'retrieval_error','RETRIEVAL_UNAVAILABLE'),
    (metrics('OK'),'evidence_unavailable','EVIDENCE_READBACK_OR_SELECTION_FAILED'),
])
def test_empty_recall_is_not_transport_failure_or_missing_diagnostics(data,category,reason):
    value=failure_presentation('NO_CANDIDATES',data)
    assert value['category']==category and value['reason_code']==reason
    assert valid_failure_presentation(value) and 'SECRET' not in str(value)


@pytest.mark.parametrize('role,citations,expected', [('user',[],True),('assistant',[],False),('user',['E1'],False)])
def test_committed_history_diagnostic_is_only_on_failed_user_without_citations(role,citations,expected):
    value=PostgresKnowledgeRepository._message_row(dict(role=role,run_id='run',citations=citations,
        presentation=None,run_error_code='MODEL_UNAVAILABLE',run_metrics={}))
    assert ('presentation' in value)==expected
    assert 'run_error_code' not in value and 'run_metrics' not in value
    if expected:
        assert value['presentation']==failure_presentation('MODEL_UNAVAILABLE',{})


def test_untrusted_diagnostic_text_and_success_are_not_failure_authority():
    value=failure_presentation('MODEL_UNAVAILABLE')
    assert not valid_failure_presentation({**value,'text':'pretend factual answer'})
    assert failure_presentation(None) is None
