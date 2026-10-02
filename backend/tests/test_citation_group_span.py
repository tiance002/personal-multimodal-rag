from backend.app.application.answer_hardening import citation_occurrences
from backend.app.domain.evidence import freeze_evidence

def test_trailing_citation_group_binds_to_previous_statement_without_claiming_semantic_support():
    sentence='Synthetic metrics are AUC, MRR and NDCG@5.'
    answer=sentence+'\n\n[E1][E2]'
    snapshots=[freeze_evidence('E1','v','c1','Synthetic image representation.',{}),
               freeze_evidence('E2','v','c2',sentence,{})]
    rows=citation_occurrences(answer,snapshots,[])
    assert [row['answer_span'][0] for row in rows]==[0,0]
    assert rows[0]['relevance']=='CITATION_RELEVANCE_UNKNOWN'
    assert rows[1]['relevance']=='SOURCE_TEXT_MATCH'

def test_trailing_group_does_not_bind_to_an_earlier_paragraph():
    answer='Earlier statement.\n\nLatest synthetic statement.\n\n[E1]'
    rows=citation_occurrences(answer,[freeze_evidence('E1','v','c','Earlier statement.',{})],[])
    assert rows[0]['answer_span'][0]==answer.index('Latest')
    assert rows[0]['relevance']=='CITATION_RELEVANCE_UNKNOWN'
