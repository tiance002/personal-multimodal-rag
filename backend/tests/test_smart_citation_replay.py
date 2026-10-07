"""SIMULATED actual Smart adapter with a saved real candidate; zero live models/DB."""
from pathlib import Path
import json
import pytest
from backend.app.application.langchain_agent import LangChainAgentAdapter
from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.knowledge_tools import KnowledgeToolGateway
from backend.app.application.evidence_accumulator import EvidenceAccumulator
from backend.app.application.retrieval import RetrievalResult,RetrievalItem
from backend.app.domain.models import ChunkRecord,RankedHit
from backend.app.domain.scope import Scope
from backend.app.domain.text_normalization import normalize_query
from backend.tests.test_langchain_agent import ScriptedChatModel

@pytest.mark.parametrize('candidate_key,error',[('original_candidate','UNSUPPORTED_ANSWER'),('marker_only_candidate','UNSUPPORTED_ANSWER'),('contract_format_candidate',None)])
def test_saved_smart_candidate_keeps_strict_citation_format(candidate_key,error):
    data=json.loads((Path(__file__).parent/'fixtures/offline_smart_citation/response.json').read_text(encoding='utf-8'))
    chunks=[ChunkRecord(**source) for source in data['sources']]
    kb=chunks[0].knowledge_base_id;doc=chunks[0].document_id
    class Retrieval:
        top_k=5
        def retrieve(self,scope,question,query_plan=None):
            assert scope==Scope.from_ids([kb],[doc])
            return RetrievalResult(query_plan or normalize_query(question),[RetrievalItem(c,RankedHit(chunk_id=c.chunk_id,rank=i)) for i,c in enumerate(chunks,1)],('SIMULATED',))
    core=KnowledgeGateway(Retrieval());evidence=EvidenceAccumulator();model=ScriptedChatModel(final_answer=data[candidate_key])
    result=LangChainAgentAdapter(model).run('conv',data['question'],Scope.from_ids([kb],[doc]),run_id='saved-response-offline',
        gateway=KnowledgeToolGateway(knowledge_gateway=core,evidence_accumulator=evidence),evidence=evidence)
    assert result.error_code==error and model.calls==2 and result.evidence_hint is None
    if error:assert result.status=='failed' and not result.answer and not result.citations and not result.evidence
    else:
        assert result.status=='completed' and result.citations==('E1',) and len(result.evidence)==1
        assert '312' in result.answer and '千元' in result.answer
        assert result.evidence[0].chunk_id==chunks[0].chunk_id and result.evidence[0].version_id==chunks[0].version_id
