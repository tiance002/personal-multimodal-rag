import pytest
from backend.app.application.answer_hardening import citation_occurrences
from backend.app.domain.evidence import freeze_evidence

@pytest.mark.parametrize('separator',['',' ','\n'])
def test_inline_multi_sentence_group_keeps_latest_sentence_span(separator):
    first='Earlier statement\u3002'
    latest='Latest statement\u3002'
    answer=first+latest+'[E1]'+separator+'[E2]'
    sources=[freeze_evidence(label,'v',label,latest,{}) for label in ('E1','E2')]
    rows=citation_occurrences(answer,sources,[])
    assert [row['answer_span'][0] for row in rows]==[len(first),len(first)]
    assert [row['relevance'] for row in rows]==['SOURCE_TEXT_MATCH','SOURCE_TEXT_MATCH']

def test_similar_words_do_not_establish_textual_or_semantic_support():
    answer='Earlier statement\u3002Latest statement\u3002[E1][E2]'
    sources=[freeze_evidence(label,'v',label,'Earlier latest statement words.',{}) for label in ('E1','E2')]
    rows=citation_occurrences(answer,sources,[])
    assert all(row['relevance']=='CITATION_RELEVANCE_UNKNOWN' for row in rows)
