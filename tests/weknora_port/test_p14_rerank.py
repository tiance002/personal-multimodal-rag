from copy import deepcopy
import hashlib
import pytest
from scripts.weknora_port_p14_rerank import validate,wire,binding
from scripts.weknora_port_p13_rerank import claim


def frozen():
    pool=[{'index':i,'candidate_id':str(i),'text':'evidence '+str(i),'generated_candidate':i==8} for i in range(31)]
    original=[r['text'] for r in pool];near=[t if i==8 else 'neighbor\n\n'+t for i,t in enumerate(original)];length=[t if i==8 else 'farblock\n\n'+t for i,t in enumerate(original)]
    pools={'original':original,'near':near,'length':length};queries={'row-0434':'frozen query0434','row-0959':'frozen query0959'};cases=[]
    for n,(qid,v) in enumerate([('row-0434','original'),('row-0434','near'),('row-0434','length'),('row-0959','near')]):
        body={'model':'BAAI/bge-reranker-v2-m3','query':queries[qid],'documents':pools[v]};raw=wire(body)
        cases.append({'case_id':'R'+str(n),'qid':qid,'pool_variant':v,'query':queries[qid],'body':body,'wire_sha256':hashlib.sha256(raw).hexdigest(),'wire_bytes':len(raw),'candidate_id_order':[r['candidate_id'] for r in pool]})
    return {'proposal':{'pool':pool,'pools':pools,'cases':cases,'request_cap':4},'queries':queries}


def test_four_fixed_conditions_and_matched_lengths():
    assert len(validate(frozen())['cases'])==4


@pytest.mark.parametrize('mutation',[
    lambda f:f['proposal'].update(request_cap=5),
    lambda f:f['proposal']['cases'].append(deepcopy(f['proposal']['cases'][0])),
    lambda f:f['proposal']['cases'][1].update(case_id='R0'),
    lambda f:f['proposal']['pool'][0].update(index=4),
    lambda f:f['proposal']['pool'][1].update(candidate_id='0'),
    lambda f:f['proposal']['pools']['near'].__setitem__(0,'no anchor'),
    lambda f:f['proposal']['pools']['near'].__setitem__(0,'x'*1201+f['proposal']['pool'][0]['text']),
    lambda f:f['proposal']['pools']['near'].__setitem__(8,f['proposal']['pool'][8]['text']+' summary extra'),
    lambda f:f['proposal']['cases'][0].update(query='new query'),
    lambda f:f['proposal']['cases'][0]['body'].update(model='other-model'),
    lambda f:f['proposal']['cases'][0].update(wire_sha256='0'*64),
    lambda f:f['proposal']['cases'][0].update(candidate_id_order=list(reversed(f['proposal']['cases'][0]['candidate_id_order']))),
])
def test_mutated_contract_rejected(mutation):
    f=frozen();mutation(f)
    with pytest.raises((ValueError,KeyError)):validate(f)


def test_source_binding_checks_original_content_metadata_and_scope():
    a={'id':'id','knowledge_id':'doc','knowledge_base_id':'kb','content':'actual original','metadata':{'source_version':'v1'},'is_enabled':True}
    for key,value in [('content','altered'),('metadata',{'source_version':'v2'}),('knowledge_id','other-doc'),('knowledge_base_id','other-kb')]:
        b=deepcopy(a);b[key]=value;assert binding(a)!=binding(b)


def test_duplicate_attempt_never_overwrites_or_replays(tmp_path):
    c=frozen()['proposal']['cases'][0];folder=claim(tmp_path,c);before=(folder/'intent.json').read_bytes()
    with pytest.raises(FileExistsError):claim(tmp_path,c)
    assert (folder/'intent.json').read_bytes()==before
