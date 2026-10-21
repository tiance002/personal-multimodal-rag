"""Offline synthetic source contracts; real parser, no service/Provider/DB."""
import json,zipfile
from types import SimpleNamespace
import pytest
from scripts.s8_full_corpus_ingestion import prepare,render_source,DATASETS
from scripts.s8_overnight_ingestion import MODEL_ID,canonical,sha,source_key,write


def fixture(tmp_path,long_text=None):
    corpus_root=tmp_path/'corpora';missing=[];old=[]
    for ds in DATASETS:
        directory=corpus_root/ds;directory.mkdir(parents=True)
        previous={'doc_id':'old','title':'Original heading  ','text':'Existing synthetic source body unchanged.'}
        current={'doc_id':'new','title':'New genuine heading  ','text':long_text if long_text and ds=='longbench-zh' else 'Synthetic raw source: value is 17. No Answer Gold.'}
        lines=[(json.dumps(r,ensure_ascii=False)+'\n').encode() for r in (previous,current)]
        raw=b''.join(lines);(directory/'corpus.jsonl').write_bytes(raw)
        body=render_source(previous,legacy=True)
        old.append({'dataset':ds,'source_document_id':'old','source_sha256':sha(body),
            'knowledge_base_id':'kb-'+ds,'chunking_config':{},'status':'REUSE_MIGRATED'})
        missing.append({'dataset':ds,'full_source_id':'new','original_corpus_sha256':sha(raw),
            'record_line':2,'original_record_sha256':sha(lines[1]),
            'faithful_markdown_sha256':sha(render_source(current))})
    prior={'schema':'s8-overnight-ingestion/v1','tenant_id':332,'subject_sha256':sha(b'offline-synthetic-subject'),
        'sources':old,'mineru_receipts_sha256':'b'*64,'gold_sent_to_parser':False,
        'embedding':{'provider':'siliconflow','model':'BAAI/bge-m3','model_id':MODEL_ID,
            'dimension':1024,'max_batch_size':16,'max_input_chars':0},'settings':{'summary_model_id':None}}
    oldsha=sha(canonical(prior).encode())
    cp={'state':'INDEX_READY','plan_sha256':oldsha,'documents':{source_key(r):{
        'state':'REUSED_INDEXED','original_sha256':r['source_sha256'],'document_id':'old-doc',
        'source_version':'old-version'} for r in old}}
    parent={'status':'CLOSED','plan_sha256':oldsha}
    for name,data in [('prior.json',prior),('checkpoint.json',cp),('parent.json',parent)]:write(tmp_path/name,data)
    missing_path=tmp_path/'missing.jsonl';missing_path.write_text(''.join(json.dumps(r)+'\n' for r in missing),encoding='utf-8')
    return SimpleNamespace(corpus_root=corpus_root,prior_plan=tmp_path/'prior.json',
        prior_checkpoint=tmp_path/'checkpoint.json',prior_authorization=tmp_path/'parent.json',
        missing_manifest=missing_path,output=tmp_path/'output'),prior,missing


def test_exact_completed_ids_remain_and_new_raw_sources_use_normal_parser(tmp_path):
    args,old,_=fixture(tmp_path);before=args.prior_checkpoint.read_bytes();plan=prepare(args)
    assert args.prior_checkpoint.read_bytes()==before
    assert plan['schema']=='s8-overnight-ingestion/v1' and len(plan['sources'])==3
    assert {r['unit_id'] for r in plan['sources']}=={'new'}
    assert all(r['status']=='NEW_SOURCE' and r['child_count']>0 and r['embedding_calls_upper']==1 for r in plan['sources'])
    assert plan['supplement']['totals']['documents_over_execution_capacity']==0
    with zipfile.ZipFile(args.output/'full-corpus-additional.zip') as archive:
        for row in plan['sources']:
            raw=archive.read(row['member'])
            assert raw.startswith(b'# New genuine heading  \n\n') and sha(raw)==row['source_sha256']
    assert plan['gold_sent_to_parser'] is False and plan['new_provider_calls']==0


@pytest.mark.parametrize('mutation',['source','completed-state','binding','unfrozen-record'])
def test_changed_input_or_incomplete_historical_proof_never_becomes_reuse(tmp_path,mutation):
    args,prior,missing=fixture(tmp_path)
    if mutation=='source':
        p=args.corpus_root/'scifact/corpus.jsonl';p.write_bytes(p.read_bytes().replace(b'Existing synthetic',b'Changed synthetic'))
    elif mutation=='completed-state':
        cp=json.loads(args.prior_checkpoint.read_text());next(iter(cp['documents'].values()))['state']='UNKNOWN_OR_UNSETTLED_STOP';write(args.prior_checkpoint,cp)
    elif mutation=='binding':
        prior['embedding']['model']='different-model';write(args.prior_plan,prior)
        changed=sha(canonical(prior).encode())
        for path in (args.prior_checkpoint,args.prior_authorization):
            data=json.loads(path.read_text());data['plan_sha256']=changed;write(path,data)
    else:
        missing[0]['original_record_sha256']='c'*64
        args.missing_manifest.write_text(''.join(json.dumps(r)+'\n' for r in missing),encoding='utf-8')
    with pytest.raises(ValueError):prepare(args)


def test_gold_columns_cannot_become_source_and_existing_artifact_is_not_overwritten(tmp_path):
    with pytest.raises(ValueError,match='NO_GOLD'):
        render_source({'doc_id':'id','title':'title','text':'source','answer':'synthetic Gold'})
    args,_,_=fixture(tmp_path);args.output.mkdir();(args.output/'marker').write_text('keep')
    with pytest.raises(ValueError,match='NO_OVERWRITE'):prepare(args)
    assert (args.output/'marker').read_text()=='keep'


def test_long_source_reports_capacity_failure_without_relaxing_local_execution(tmp_path):
    args,_,_=fixture(tmp_path,long_text='Synthetic numbered source field = 123. '*5000)
    plan=prepare(args)
    assert plan['status']=='OFFLINE_PREPARED_CAPACITY_BLOCKED'
    violations=plan['supplement']['capacity_violations']
    assert len(violations)==1 and violations[0]['dataset']=='longbench-zh'
    assert violations[0]['token_reservation_upper']>100000
    assert plan['embedding']['max_batch_size']==16 and plan['new_provider_calls']==0
