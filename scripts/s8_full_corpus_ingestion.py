"""Offline full text-corpus supplement; reuse the existing finite ingestion runner.

No database, Provider, grant creation, or Gold access. Preserve the completed
748-source job; generate ordinary v1 plan rows only for exact missing source IDs.
"""
from __future__ import annotations
import argparse,json,sys,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.s8_overnight_ingestion import (MODEL_ID,BATCH_SIZE,canonical,sha,read,write,source_key)
from backend.weknora_port.ingestion.core import DocumentRequest,ParserRegistry,ingest_document
from backend.weknora_port.storage.chunks import DocumentScope,prepare_chunks
from backend.weknora_port.storage.indexing import sanitize_for_embedding

DATASETS=('scifact','miracl-zh','longbench-zh')

def require(ok,code):
    if not ok:raise ValueError(code)

def render_source(row,*,legacy=False):
    require(set(row)=={'doc_id','title','text'} and all(isinstance(row[k],str) for k in row)
        and bool(row['doc_id']) and bool(row['text'].strip()),'CORPUS_SOURCE_FIELDS_REQUIRED_NO_GOLD')
    # Old frozen wrapper strips title whitespace. New missing-source manifest
    # freezes the faithful untrimmed title; neither changes source body text.
    title=row['title'].strip() if legacy else row['title']
    return ('# '+title+'\n\n'+row['text']).encode('utf-8')

def calculate_chunks(raw,filename,kb,registry):
    result=ingest_document(DocumentRequest(filename,raw,'offline-owner',kb,'offline-doc','offline-version'),registry)
    require(result.parsed.ok and bool(result.parsed.content.strip()),'OFFLINE_NORMAL_PARSE_FAILED')
    chunks=prepare_chunks(DocumentScope(332,'offline-owner',kb,'offline-doc','offline-version'),result.chunks,
        title=Path(filename).stem)
    texts=[sanitize_for_embedding(r.content) for r in chunks.indexed]
    require(bool(texts),'NO_INDEXABLE_CHILDREN')
    bodies=[{'model':'BAAI/bge-m3','input':texts[i:i+BATCH_SIZE]} for i in range(0,len(texts),BATCH_SIZE)]
    return {'parsed_sha256':sha(result.parsed.content.encode()),'child_count':len(texts),
        'embedding_input_chars':sum(map(len,texts)),'embedding_calls_upper':len(bodies),
        'token_reservation_upper':sum(len(json.dumps(body,ensure_ascii=False).encode())+4096 for body in bodies),
        'indexed_texts_sha256':sha(canonical(texts).encode())}

def prepare(args):
    output=Path(args.output);require(not output.exists(),'EXISTING_SUPPLEMENT_NO_OVERWRITE')
    prior,checkpoint,parent=map(read,(args.prior_plan,args.prior_checkpoint,args.prior_authorization))
    prior_sha=sha(canonical(prior).encode())
    require(prior['schema']=='s8-overnight-ingestion/v1' and prior['tenant_id']==332
        and checkpoint['state']=='INDEX_READY' and checkpoint['plan_sha256']==prior_sha
        and parent['status']=='CLOSED' and parent['plan_sha256']==prior_sha,'COMPLETED_PRIOR_JOB_REQUIRED')
    require(prior['embedding']=={'provider':'siliconflow','model':'BAAI/bge-m3','model_id':MODEL_ID,
        'dimension':1024,'max_batch_size':BATCH_SIZE,'max_input_chars':0}
        and prior.get('gold_sent_to_parser') is False,'VERIFIED_EXISTING_BGE_BINDING_REQUIRED')
    require(set(checkpoint['documents'])=={source_key(r) for r in prior['sources']}
        and all(r['state'] in ('INDEXED','REUSED_INDEXED') for r in checkpoint['documents'].values()),
        'PRIOR_EXACT_COMPLETED_SOURCE_SET_REQUIRED')
    old={};kbs={}
    for row in prior['sources']:
        if row['dataset'] not in DATASETS:continue
        identity=(row['dataset'],str(row['source_document_id']))
        require(identity not in old,'DUPLICATE_PRIOR_SOURCE_ID')
        proof=checkpoint['documents'][source_key(row)]
        require(proof.get('original_sha256')==row['source_sha256'] and proof.get('source_version')
            and proof.get('document_id') and row['chunking_config']=={},'PRIOR_SOURCE_COMPLETION_PROOF_REQUIRED')
        old[identity]=row;kbs.setdefault(row['dataset'],row['knowledge_base_id'])
        require(kbs[row['dataset']]==row['knowledge_base_id'],'AMBIGUOUS_DATASET_KB')
    missing_raw=Path(args.missing_manifest).read_bytes()
    missing=[json.loads(line) for line in missing_raw.decode('utf-8-sig').splitlines() if line.strip()]
    expected={(r['dataset'],str(r['full_source_id'])):r for r in missing}
    require(len(expected)==len(missing) and not(set(expected)&set(old)),'MISSING_ID_SET_OVERLAPS_COMPLETED')
    require(all(ds in DATASETS for ds,_ in expected),'UNSUPPORTED_CORPUS_DATASET')
    registry=ParserRegistry();rows=[];seen=set();old_seen=set();source_hashes=set();written_assets=set();corpora={};groups={}
    output.mkdir(parents=True);archive=output/'full-corpus-additional.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as zipped:
        for ds in DATASETS:
            corpus=Path(args.corpus_root)/ds/'corpus.jsonl';corpus_sha=sha(corpus.read_bytes())
            corpora[ds]={'path':str(corpus),'sha256':corpus_sha,'rows':0}
            with corpus.open('rb') as stream:
                for number,line in enumerate(stream,1):
                    if not line.strip():continue
                    record=json.loads(line);identity=(ds,str(record['doc_id']))
                    require(identity not in seen,'DUPLICATE_CORPUS_SOURCE_ID');seen.add(identity);corpora[ds]['rows']+=1
                    if identity in old:
                        require(sha(render_source(record,legacy=True))==old[identity]['source_sha256'],
                            'COMPLETED_SOURCE_ID_BODY_CHANGED')
                        old_seen.add(identity);continue
                    frozen=expected.get(identity);require(frozen is not None,'UNFROZEN_ADDITIONAL_SOURCE')
                    raw=render_source(record);digest=sha(raw)
                    require(frozen['original_corpus_sha256']==corpus_sha and frozen['record_line']==number
                        and frozen['original_record_sha256']==sha(line)
                        and frozen['faithful_markdown_sha256']==digest,'FROZEN_RAW_SOURCE_MAPPING_CHANGED')
                    require((ds,digest) not in source_hashes,'DUPLICATE_BODY_REQUIRES_EXPLICIT_ALIAS_CONTRACT')
                    source_hashes.add((ds,digest));filename='full-'+digest[:24]+'.md';member='objects/'+digest
                    if digest not in written_assets:
                        zipped.writestr(member,raw);written_assets.add(digest)
                    row={'dataset':ds,'unit_id':identity[1],'source_document_id':identity[1],
                        'filename':filename,'media_type':'text/markdown','source_sha256':digest,
                        'archive':archive.name,'member':member,'source_bytes':len(raw),'knowledge_base_id':kbs[ds],
                        'status':'NEW_SOURCE','existing_document':None,'chunking_config':{},'parser_engine':'',
                        'embedding_model_id':MODEL_ID,'corpus_source':{
                            'corpus_sha256':corpus_sha,'record_line':number,'original_record_sha256':sha(line),
                            'projection':'UTF8(# + untrimmed title + two LF + exact text); no Gold'}}
                    row.update(calculate_chunks(raw,filename,kbs[ds],registry));rows.append(row)
            subset=[r for r in rows if r['dataset']==ds]
            groups[ds]={'corpus_sources':corpora[ds]['rows'],'kept_completed_sources':sum(k[0]==ds for k in old_seen),
                'new_sources':len(subset),'new_child_chunks':sum(r['child_count'] for r in subset),
                'provider_calls_upper':sum(r['embedding_calls_upper'] for r in subset),
                'reservation_tokens_upper':sum(r['token_reservation_upper'] for r in subset)}
            print(json.dumps({'dataset':ds,**groups[ds]},ensure_ascii=False),flush=True)
    require(old_seen==set(old) and {(r['dataset'],r['unit_id']) for r in rows}==set(expected),'CORPUS_SOURCE_SET_INCOMPLETE')
    archive_sha=sha(archive.read_bytes())
    for r in rows:r['archive_sha256']=archive_sha
    violations=[{k:r[k] for k in ('dataset','unit_id','child_count','embedding_calls_upper','token_reservation_upper')}
        for r in rows if r['embedding_calls_upper']>12 or r['token_reservation_upper']>100000 or r['source_bytes']>50*1024*1024]
    totals={'new_sources':len(rows),'new_child_chunks':sum(r['child_count'] for r in rows),
        'provider_calls_upper':sum(r['embedding_calls_upper'] for r in rows),
        'reservation_tokens_upper':sum(r['token_reservation_upper'] for r in rows),
        'max_document_calls':max((r['embedding_calls_upper'] for r in rows),default=0),
        'max_document_tokens':max((r['token_reservation_upper'] for r in rows),default=0),
        'documents_over_execution_capacity':len(violations)}
    plan={'schema':'s8-overnight-ingestion/v1','status':'OFFLINE_PREPARED_CAPACITY_BLOCKED' if violations else 'OFFLINE_PREPARED_NOT_AUTHORIZED',
        'tenant_id':332,'subject_sha256':prior['subject_sha256'],'source_snapshot_sha256':sha(Path(args.prior_checkpoint).read_bytes()),
        'mineru_receipts_sha256':prior['mineru_receipts_sha256'],'embedding':prior['embedding'],'settings':prior['settings'],
        'datasets':groups,'sources':rows,'gold_sent_to_parser':False,'new_provider_calls':0,
        'supplement':{'prior_plan_sha256':prior_sha,'prior_parent_status':'CLOSED','prior_job_preserved':True,
            'missing_manifest_sha256':sha(missing_raw),'raw_corpora':corpora,'capacity_violations':violations,'totals':totals}}
    write(output/'ingestion-plan.json',plan);write(output/'bounds.json',{'datasets':groups,'totals':totals,'violations':violations,
        'plan_sha256':sha(canonical(plan).encode()),'archive_sha256':archive_sha,'provider_calls':0,'database_writes':0})
    print(json.dumps({'status':plan['status'],**totals,'output':str(output)},ensure_ascii=False),flush=True)
    return plan

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('corpus-root','prior-plan','prior-checkpoint','prior-authorization','missing-manifest','output'):
        parser.add_argument('--'+name,required=True,type=Path)
    prepare(parser.parse_args())

if __name__=='__main__':main()
