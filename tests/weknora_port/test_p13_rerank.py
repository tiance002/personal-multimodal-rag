from copy import deepcopy
import hashlib
import pytest
from scripts.weknora_port_p13_rerank import validate,wire,claim


def frozen():
    pool=[{'index':i,'candidate_id':str(i),'text':'passage '+str(i)} for i in range(31)]
    original=[r['text'] for r in pool];context=list(original);context[14]+='\n\nreal chapter context'
    cases=[]
    for n,(q,l,v) in enumerate([('row-0434','EN','original'),('row-0434','ZH','original'),('row-0434','EN','context'),('row-0434','ZH','context'),('row-0959','EN','original'),('row-0959','ZH','original')]):
        query=q+' '+l;body={'model':'BAAI/bge-reranker-v2-m3','query':query,'documents':original if v=='original' else context};raw=wire(body)
        cases.append({'case_id':'R'+str(n),'qid':q,'language':l,'target_text_variant':v,'query':query,'body':body,'wire_sha256':hashlib.sha256(raw).hexdigest(),'wire_bytes':len(raw),'candidate_id_order':[r['candidate_id'] for r in pool]})
    p={'request_cap':6,'pool':pool,'target_slot':14,'context_bundle':{'text':context[14]},'cases':cases,'single_factor_edges':[{'cases':['R0','R1']},{'cases':['R0','R2']},{'cases':['R1','R3']},{'cases':['R4','R5']}]}
    return {'proposal':p}


def test_fixed_factorial_contract():
    assert len(validate(frozen())['cases'])==6


@pytest.mark.parametrize('mutation',[
    lambda p:p.update(request_cap=7),
    lambda p:p['cases'].append(deepcopy(p['cases'][0])),
    lambda p:p['cases'][1].update(case_id='R0'),
    lambda p:p['pool'][0].update(index=2),
    lambda p:p['pool'][1].update(candidate_id='0'),
    lambda p:p['cases'][0]['body']['documents'].__setitem__(0,'unfrozen text'),
    lambda p:p['cases'][0]['body'].update(model='different-model'),
    lambda p:p['cases'][0]['body'].update(top_n=1),
    lambda p:p['cases'][0].update(wire_sha256='0'*64),
    lambda p:p['cases'][0].update(candidate_id_order=list(reversed(p['cases'][0]['candidate_id_order']))),
    lambda p:p['single_factor_edges'].append({'cases':['R0','R3']}),
    lambda p:p['single_factor_edges'].append({'cases':['R0','R4']}),
])
def test_mutated_freeze_rejected(mutation):
    f=frozen();mutation(f['proposal'])
    with pytest.raises((ValueError,KeyError)):validate(f)


def test_uncertain_attempt_cannot_be_reclaimed(tmp_path):
    c=frozen()['proposal']['cases'][0];d=claim(tmp_path,c);before=(d/'intent.json').read_bytes()
    with pytest.raises(FileExistsError):claim(tmp_path,c)
    assert (d/'intent.json').read_bytes()==before

@pytest.mark.parametrize('current,summary,version,expected_error',[
    ({'doc':'v1'},True,None,None),
    ({'doc':'v1'},False,'v1',None),
    ({'doc':'v2'},True,None,'P13_STALE_SOURCE'),
    ({},True,None,'P13_STALE_SOURCE'),
    ({'doc':'v1'},False,None,'P13_UNVERSIONED_NON_SUMMARY'),
    ({'doc':'v1'},False,'v0','P13_CANDIDATE_VERSION_MISMATCH'),
])
def test_summary_uses_frozen_document_version_without_waiving_staleness(current,summary,version,expected_error):
    from scripts.weknora_port_p13_rerank import check_source_versions
    p={'scope':{'source_versions':['v1']},'pool':[{'generated_candidate':summary,'provenance':{'document_id':'doc','source_version':version,'chunk_type':'summary' if summary else 'text'}}]}
    if expected_error:
        with pytest.raises(ValueError,match=expected_error):check_source_versions(p,{'doc':'v1'},current)
    else:check_source_versions(p,{'doc':'v1'},current)
