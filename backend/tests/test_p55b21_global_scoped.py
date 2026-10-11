"""SIMULATED union and token admission; never vendor tokens or transport."""
import copy
import pytest
from backend.app.application.memory import MemoryRetriever
from backend.app.ports.memory import memory_scope,memory_scope_hash,MemoryDenied
from backend.app.domain.scope import Scope
from backend.app.ports.context_budget import ContextDenied
from backend.tests.test_p55b_context_window import window,SCOPE

class Union:
    def __init__(self):
        self.rows=[dict(id='G',scope_type='GLOBAL',fact_key='language',kind='preference',content='Chinese',version=1),
                   dict(id='S',scope_type='SCOPED',fact_key='language',kind='preference',content='English',version=2)]
    def recall_items(self,*a):return copy.deepcopy(self.rows)

def test_formal_type_not_implicit_empty_kb():
    empty=Scope.from_ids([])
    assert memory_scope(empty,'GLOBAL')=={}
    assert memory_scope_hash(empty,'GLOBAL')!=memory_scope_hash(SCOPE)
    with pytest.raises(MemoryDenied):memory_scope(empty)
    with pytest.raises(MemoryDenied):memory_scope(SCOPE,'GLOBAL')
    with pytest.raises(MemoryDenied):memory_scope(SCOPE,'OTHER')

def test_scoped_override_stable_without_removing_global():
    repo=Union();r=MemoryRetriever(repo,'SIMULATED')
    assert [x['id'] for x in r.recall('language',SCOPE)]==['S']
    repo.rows.reverse();assert [x['id'] for x in r.recall('language',SCOPE)]==['S']
    assert len(repo.rows)==2

def test_keys_not_natural_language_guess_and_atomic_token_budget():
    repo=Union();repo.rows[1]['fact_key']='different-key'
    r=MemoryRetriever(repo,'SIMULATED',token_budget=1)
    assert len(r.recall('language',SCOPE))==2
    prompt='SYSTEM\nQuestion: current\nEvidence: exact 1000 [E1]'
    assert r.quick_context(prompt,scope=SCOPE,windows=[window()],output_tokens=100)==prompt

def test_frozen_global_overridden_updated_disabled_is_rechecked():
    repo=Union();repo.rows=repo.rows[:1];r=MemoryRetriever(repo,'SIMULATED',token_budget=1000)
    p=r.quick_context('SYSTEM\nQuestion: language\nEvidence: current [E1]',scope=SCOPE,windows=[window()],output_tokens=100)
    r.validate_frozen(p,SCOPE)
    repo.rows[0]['version']=2
    with pytest.raises(ContextDenied,match='REVOKED'):r.validate_frozen(p,SCOPE)
    repo.rows=[]
    with pytest.raises(ContextDenied,match='REVOKED'):r.validate_frozen(p,SCOPE)
