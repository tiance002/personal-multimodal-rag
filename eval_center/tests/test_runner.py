from types import SimpleNamespace

from backend.app.application.retrieval import InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord
from eval_center.gold import SourceSpan
from eval_center.runner import evaluate_case


def test_real_business_retriever_and_context_are_observed_without_model_or_mock_scores():
    store=InMemoryRetrievalRepository()
    store.max_chunk_chars=400; store.chunk_overlap=40
    store.add(ChunkRecord('chunk','kb','doc','v','Budget 2024 is 1000.',{'kind':'text','start':0,'end':20}))
    container=SimpleNamespace(store=store,ollama=None)
    index={'spans':{'chunk':SourceSpan('doc','a'*64,0,20)}}
    case={'case_id':'real-core-unit','question':'Budget 2024','answerable':True,'required_answer_points':[['1000']],
          'gold_evidence':[{'evidence_id':'g','document_id':'doc','source_version':'a'*64,
            'locator':{'kind':'text','start':0,'end':20,'coordinate_space':'normalized_source_text'}}]}
    config={'top_k':5,'candidate_k':32,'rrf_k':60,'chunk_size':400,'chunk_overlap':40,'context_budget_chars':8000}
    row,errors=evaluate_case(container,'kb',index,case,config,generate=False,tokenizer=None)
    assert row['statistics']['context_mode']=='prepared'
    assert row['metrics']['context_recall']==1
    assert row['metrics']['business_total_tokens'] is None
    assert row['metrics']['answer_point_coverage'] is None
    assert row['statistics']['retrieval_attempts'][0]['keyword']==['chunk']
    assert row['statistics']['dedup']['tokenizer']=='unavailable'
    assert '[E1]' in row['context']
    assert not errors
