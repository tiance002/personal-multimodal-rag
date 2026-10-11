"""Additional exact Scope and checkpoint source contracts on existing test PG."""
import uuid
import hashlib
from sqlalchemy import text
from backend.tests.test_p55c_query_integration import data,memory,build,answer,rows,query_service,claims,understand
from backend.tests.test_p55b_context_integration import configured,long_history,Execution,messages
from backend.app.domain.scope import Scope
from backend.app.application.query_understand import QueryUnderstandService

def test_document_A_not_B_pending_confirm_and_delete(data,memory):
    m=memory;other=str(uuid.uuid4())
    with data.engine.begin() as c:
        c.execute(text("INSERT INTO documents(id,knowledge_base_id,file_name,media_type,original_size) VALUES (:id,:kb,'SIMULATED-B.txt','text/plain',0)"),{'id':other,'kb':data.kb})
    A=Scope.from_ids([data.kb],[data.doc]);B=Scope.from_ids([data.kb],[other])
    with data.engine.begin() as c:
        m.repo._subject(c,m.principal)
        from backend.app.ports.memory import item_value
        item=m.repo._write(c,A,item_value('preference','language','Chinese'), 'extracted',[{'type':'SIMULATED_CONFIRM_FIXTURE'}])
    assert m.repo.list_items(m.principal,B)==[] and m.repo.list_items(m.principal,A,active_only=True)==[]
    from backend.app.ports.memory import MemoryDenied
    import pytest
    with pytest.raises(MemoryDenied):m.repo.transition(m.principal,B,item,'active')
    m.repo.transition(m.principal,A,item,'active')
    assert m.repo.list_items(m.principal,A,active_only=True)[0]['id']==item and m.repo.list_items(m.principal,B,active_only=True)==[]
    with pytest.raises(MemoryDenied):m.repo.transition(m.principal,B,item,'deleted')
    m.repo.transition(m.principal,A,item,'deleted');assert m.repo.list_items(m.principal,A,active_only=True)==[]

def test_query_reuses_real_valid_checkpoint_without_model_or_repeat_compaction(data,monkeypatch):
    service,life,chain,manager,store,p,gate=configured(data,monkeypatch,size=3000)
    long_history(data,service)
    with Execution(life,data) as run:
        messages(manager,data,run)
        cp=store.load_checkpoint(data.conversation['id'],Scope.from_ids([data.kb]),run)
        assert cp and len(p.calls)==1
        query=QueryUnderstandService(manager)
        u=query.understand('它的成本是多少？',conversation=data.conversation['id'],scope=Scope.from_ids([data.kb]),run=run)
        assert u.rewrite_query=='A 的成本是多少？' and not u.needs_clarification
        assert len(p.calls)==1  # no Query model or extra compaction
        before=rows(data,'SELECT id,content FROM conversation_messages WHERE conversation_id=:id ORDER BY id',id=data.conversation['id'])
        assert before and all(r['content'] for r in before)
