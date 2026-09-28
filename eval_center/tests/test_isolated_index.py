import pytest

from eval_center.isolated_index import guarded_database_url
from eval_center.verification import ExperimentInvalidError


@pytest.mark.parametrize('url',['postgresql+psycopg://localhost/rag',
    'postgresql+psycopg://127.0.0.1/rag_eval_audit_old',
    'postgresql+psycopg://example.com/rag_eval_trust_x','sqlite:///rag_eval_trust_x'])
def test_normal_or_remote_database_is_rejected_before_connection(url):
    with pytest.raises(ExperimentInvalidError,match='nonisolated_database'): guarded_database_url(url)


def test_named_local_acceptance_database_is_allowed():
    assert guarded_database_url('postgresql+psycopg://127.0.0.1:25436/rag_eval_trust_0928_a').database=='rag_eval_trust_0928_a'
