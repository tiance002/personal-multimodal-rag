"""Offline S8.3 replay from fixed saved receipts, never a live DB or Provider.

The production ScopedKnowledgeRepository is backed by a strict SELECT-only saved
row pool. Missing rows are recorded; source.bin authenticates text, never creates
chunks. Search/Rerank results and scores are frozen inputs, not recomputed.
"""
from __future__ import annotations
import argparse, asyncio, copy, hashlib, json, re, sys
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.weknora_port.storage.knowledge import ScopedKnowledgeRepository
from backend.weknora_port.models.types import CallContext, TrustedScope, ChatOptions
from backend.weknora_port.models.provider import ProviderModel
from backend.weknora_port.models.catalog import resolve_model_config
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget
from backend.weknora_port.retrieval.merge import merge
from backend.weknora_port.retrieval.query import default_segmenter
from backend.weknora_port.retrieval.filter_top_k import filter_top_k
from backend.weknora_port.retrieval.source_evidence import expand_source_evidence
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.generation.context import Evidence, prepare_generation, SOURCE_DATA_BOUNDARY
from backend.weknora_port.generation.types import GenerationRequest

IDS=('miracl-zh-91294#0','scifact-1101','scifact-241')
LOCKED={'scifact-1303','scifact-338','scifact-921'}
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def digest(v):return hashlib.sha256(v.encode() if isinstance(v,str) else v).hexdigest()
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def require(ok,code):
 if not ok:raise ValueError(code)
def save(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x',encoding='utf-8') as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')
def state_at(snapshot,stage):return next(s['state'] for s in snapshot['stages'] if s['stage']==stage and s['status']=='completed')
def as_result(row):return SearchResult(**{f.name:copy.deepcopy(row[f.name]) for f in fields(SearchResult) if f.name in row})

class SavedConnection:
 def __init__(self,rows,scope):self.rows,self.scope,self.reads,self.missing=rows,scope,[],set()
 async def execute(self,sql,params):
  require(sql.lstrip().startswith('SELECT '),'NON_SELECT_FORBIDDEN')
  self.reads.append({'sql_sha256':digest(sql),'row_query':sql.startswith('SELECT id FROM chunks')})
  if sql.startswith('SELECT id FROM chunks'):
   tenant,kb,doc,version,first,last,limit=params
   matches=[r for r in self.rows.values() if r['tenant_id']==tenant and r['knowledge_base_id']==kb and r['knowledge_id']==doc and r['chunk_type']=='text' and r['metadata'].get('source_version')==version and first<=r['chunk_index']<=last]
   values=sorted(matches,key=lambda r:(r['chunk_index'],r['id']))[:limit]
  else:
   match=re.search(r'c\.(id|parent_chunk_id) IN \(([^)]+)\)',sql);require(bool(match),'UNSUPPORTED_SNAPSHOT_SELECT')
   col,bindings=match.groups();n=bindings.count('%s');ids=list(params[3:3+n]);tenant=params[0]
   require(params[:3]==[tenant]*3 and tenant==332,'FROZEN_TENANT_REQUIRED')
   values=[r for r in self.rows.values() if r.get(col) in ids and r['tenant_id']==tenant and r['knowledge_base_id'] in self.scope.knowledge_base_ids and (not self.scope.document_ids or r['knowledge_id'] in self.scope.document_ids)]
   if col=='id':self.missing.update(set(ids)-{r['id'] for r in values})
  async def fetchall():return copy.deepcopy(values)
  return SimpleNamespace(fetchall=fetchall)

class NoSend:
 def __getattr__(self,name):raise AssertionError('PROVIDER_TRANSPORT_FORBIDDEN:'+name)

def frozen_rows(case,stages):
 """Only saved SearchResult/readback IDs; original bytes authenticate offsets."""
 pool={}
 for s in stages['stages']:
  for name in ('search_results','rerank_results','merge_results'):
   for r in s.get('state',{}).get(name,[]):
    if r and r['id'] not in pool:pool[r['id']]=r
 readback=Path(case['files']['pg_after_stop']['path']).parent/'readback'
 authenticated={};rows={};rejected=[]
 for rid,r in pool.items():
  source=readback/('source-'+r['knowledge_id']+'.bin');meta=r.get('metadata',{})
  if not source.is_file():rejected.append({'id':rid,'reason':'MISSING_SAVED_SOURCE_BYTES'});continue
  raw=source.read_bytes();require(digest(raw)==meta.get('sha256'),'SAVED_SOURCE_SHA_MISMATCH:'+rid)
  version=meta.get('source_version');require(bool(version),'FROZEN_SOURCE_VERSION_REQUIRED')
  if r['chunk_type']=='text':
   original=raw.decode('utf-8-sig');start,end=r['start_at'],r['end_at']
   if type(start) is not int or type(end) is not int or not 0<=start<=end or original[start:end]!=r['content']:
    rejected.append({'id':rid,'reason':'SAVED_ROW_NOT_EXACT_SOURCE_SLICE'});continue
   require(r['chunk_metadata'].get('source_version')==version,'SAVED_ROW_VERSION_MISMATCH')
  elif r['chunk_type'] not in ('summary','table_summary'):continue
  authenticated[rid]={'source_path':str(source),'source_sha256':digest(raw),'source_version':version,'start_at':r['start_at'],'end_at':r['end_at'],'content_sha256':digest(r['content']),'chunk_type':r['chunk_type'],'original_text':r['chunk_type']=='text'}
  rows[rid]={**{k:copy.deepcopy(r[k]) for k in ('id','content','knowledge_id','knowledge_base_id','chunk_type','chunk_index','start_at','end_at','parent_chunk_id','pre_chunk_id','next_chunk_id','is_enabled','content_revision')},'tenant_id':332,'metadata':copy.deepcopy(r['chunk_metadata']),'knowledge_metadata':copy.deepcopy(meta),'knowledge_enable_status':'enabled','knowledge_parse_status':'completed','deleted_at':None,'knowledge_source':r['knowledge_source'],'knowledge_title':r['knowledge_title'],'knowledge_filename':r['knowledge_filename'],'knowledge_description':r['knowledge_description'],'knowledge_custom_metadata':{},'image_info':r['image_info']}
 return rows,authenticated,rejected

async def replay(case,mode):
 st=read(case['files']['stage_snapshot']['path']);saved=state_at(st,'rerank');ret=read(case['files']['retrieval_snapshot']['path']);wire=read(case['files']['final_provider_request']['path']);pg=read(case['files']['pg_after_stop']['path'])
 inspection=read(Path(case['files']['pg_after_stop']['path']).parents[2]/'metadata/aligned-inspection.json')
 settings=inspection['conversation_settings'];language=inspection['language']['effective']
 saved_user=st['finalized_context']['user_content'];saved_context=st['finalized_context']['rendered_contexts']
 template=saved_user.replace(saved_context,'{{contexts}}').replace(case['question'],'{{query}}')
 system=next(m['content'] for m in wire['body']['messages'] if m['role']=='system').split('\n\n'+SOURCE_DATA_BOUNDARY,1)[0]
 rows,provenance,rejected=frozen_rows(case,st);scope=TrustedScope(ret['scope']['owner_id'],tuple(ret['scope']['knowledge_base_ids']),tuple(ret['scope']['document_ids']))
 conn=SavedConnection(rows,scope)
 async def tenant(owner):require(owner==scope.owner_id,'FROZEN_SCOPE_OWNER_REQUIRED');return 332
 repo=ScopedKnowledgeRepository(SimpleNamespace(connection=conn,require_verified=lambda:None),tenant)
 ctx=CallContext('offline-s8-3-'+case['case_id']+'-'+mode,scope,session_id='offline-snapshot')
 state=PipelineState(query=case['question'],rewrite_query=saved['rewrite_query'],intent=saved['intent'],needs_retrieval=saved['needs_retrieval'],search_results=[as_result(r) for r in saved['search_results']],rerank_results=[as_result(r) for r in saved['rerank_results']],targets=tuple(SearchTarget(t['knowledge_base_id']) for t in ret['targets']),source_evidence_mode=mode,rerank_context_enabled=True,rerank_top1_floorless=True,rerank_top_k=settings['rerank_top_k'],language=language,context_template=template,system_prompt_override=st['finalized_context']['system_prompt_override'])
 out={'mode':mode,'scope':'FIXED_SAVED_SOURCE_SNAPSHOT_NOT_CURRENT_DB','prompt_reconstruction':'RECONSTRUCTED_CONTEXT_ONLY_NOT_HISTORICAL_PROMPT_IDENTITY','fixture_statuses':'tenant332/enabled/completed reconstructed from saved scope/results; not current DB observations','search_ids_unchanged':[r.id for r in state.search_results],'rerank_ids_scores_unchanged':[{'id':r.id,'score':r.score} for r in state.rerank_results],'reconstruction_configuration':{'language':language,'rerank_top_k':settings['rerank_top_k'],'faq_priority_enabled':False,'faq_note':'saved three cases contain no FAQ; historical custom FAQ settings unavailable','context_template_origin':'saved finalized user wrapper with context/query substitutions','system_prompt_origin':'saved actual system prefix before fixed source boundary'},'source_rows':provenance,'rejected_saved_rows':rejected}
 try:
  await merge(state,ctx,repo,default_segmenter());out['post_merge_ids']=[r.id for r in state.merge_results];filter_top_k(state)
  out['post_topk_ids']=[r.id for r in state.merge_results]
  await expand_source_evidence(state,ctx,repo)
  now=datetime.fromisoformat(pg['turns'][0]['created_at']);final=await into_chat_message(state,ctx,now=now)
  params=wire['body'];opts=ChatOptions(temperature=params['temperature'],max_tokens=params['max_tokens'],thinking=params.get('enable_thinking'),reasoning_effort='off')
  req=GenerationRequest(ctx.request_id,ctx.session_id,'offline-message',case['question'],final,tuple(Evidence.from_result(r) for r in state.merge_results),options=opts,rendered_at=now,language=language,system_prompt=system)
  prepared=prepare_generation(req,now=now);cfg=resolve_model_config(wire['provider'],wire['model'],model_id=wire['model_id'],role='cheap');body=ProviderModel(cfg,NoSend()).build_request_body(prepared.messages,opts,stream=True)
  require({k:v for k,v in body.items() if k!='messages'}=={k:v for k,v in params.items() if k!='messages'},'REPLAY_MODEL_PARAMETERS_CHANGED')
  user='\n'.join(m['content'] for m in body['messages'] if m['role']=='user')
  target=case['target_source_document_id'];tracked={r['id']:r for r in saved['search_results'] if r['knowledge_id']==target}
  out.update(status='PASS_OFFLINE_WIRE_BUILT',selected_ids=final.result_ids,selected_rows=[{'id':r.id,'type':r.chunk_type,'chars':len(r.content),'score':r.score,'version':r.metadata.get('source_version'),'start_at':r.start_at,'end_at':r.end_at} for r in state.merge_results],rendered_context_chars=len(final.rendered_contexts),actual_user_wire_chars=len(user),context_char_limit=final.context_char_limit,wire_body_sha256=digest(canonical(body)),target_content_membership=[{'id':r['id'],'type':r['chunk_type'],'exact_raw_content_in_wire':r['content'] in user,'escaped_content_in_wire':r['content'].replace('&','&amp;').replace('<','&lt;').replace('>','&gt;') in user} for r in tracked.values()],evidence_trace=state.evidence_trace,request_body=body)
 except Exception as exc:out.update(status='BLOCKED_OFFLINE_REPLAY',error=type(exc).__name__+':'+str(exc),evidence_trace=state.evidence_trace)
 out.update(snapshot_completeness='SNAPSHOT_LIMITED' if conn.missing else 'AVAILABLE_REQUESTED_ROWS_ONLY_NOT_DB_PROOF',missing_snapshot_row_ids=sorted(conn.missing),select_count=len(conn.reads),provider_sends=0,database_connections=0)
 return out

def planned_manifest(inv):
 cases=[];model_freezes={}
 for c in inv['dev_cases']:
  require(c['case_id'] not in LOCKED,'HOLDOUT_FORBIDDEN')
  stpath=Path(c.get('files',{}).get('stage_snapshot',{}).get('path') or c['raw_stage_files'][0]['path']);ev=stpath.parent;raw=ev.parents[2];st=read(stpath);ret=read(next(ev.glob('*.retrieval.json')))
  requests=[(p,read(p)) for p in ev.glob('*.request.json')];qp,q=next((p,w) for p,w in requests if w['request_id'].endswith('/query_rewrite'));fp,f=next((p,w) for p,w in requests if w['request_id'].endswith('/answer/1/cheap'))
  casepath=raw/'cases'/ev.parent.name;question_path=next((casepath/'api-cases').glob('*/question.json'));question=read(question_path)
  # The question receipt is the exact actual request payload, never Gold.
  payload=question.get('payload',question);q0=payload.get('content') or payload.get('question') or c['question']
  expected_q0=c['question']['content'] if isinstance(c['question'],dict) else c['question']
  require(q0==expected_q0,'FROZEN_Q0_MISMATCH')
  frozen_sources={}
  for row in state_at(st,'retrieval')['search_results']:
   key=(row['knowledge_base_id'],row['knowledge_id'],row['metadata'].get('source_version'))
   frozen_sources[key]={'knowledge_base_id':key[0],'document_id':key[1],'source_version':key[2],'source_sha256':row['metadata'].get('sha256')}
  inspection=read(raw/'metadata/aligned-inspection.json');model_freezes[inspection['stage_id']]=inspection['identity']['models']
  cases.append({'case_id':c['case_id'],'purpose':c['purpose'],'q0':q0,'q0_sha256_utf8':digest(q0),'knowledge_base_scope':list(ret['scope']['knowledge_base_ids']),'document_scope':[],'whole_KB_with_distractors':True,'saved_search_source_versions':list(frozen_sources.values()),'source_scope_completeness':'saved candidate source versions only; future preflight must verify full KB active version inventory','receipt_files':{'question':str(question_path),'question_sha256':digest(question_path.read_bytes()),'QU':str(qp),'QU_sha256':digest(qp.read_bytes()),'generation':str(fp),'generation_sha256':digest(fp.read_bytes())},'model_parameters':{'QU':{k:v for k,v in q['body'].items() if k!='messages'},'generation':{k:v for k,v in f['body'].items() if k!='messages'}},'scope_freeze_sha256':digest(canonical(list(frozen_sources.values())))})
 require(len(cases)==6,'EXACT_SIX_PLANNED_DEV_CASES_REQUIRED')
 return {'schema':'s8-3-planned-six-case-ecs-ab/v1','status':'PLAN_ONLY_CLOUD_NOT_RUN','authorization_present':False,'owner_numeric_grant':False,'locked_holdouts_excluded':sorted(LOCKED),'arms':{'A':{'source_evidence_mode':'off'},'B':{'source_evidence_mode':'bounded_source_v1'}},'same_in_both_arms':{'graph_enabled':False,'rerank_top1_floorless':True,'rerank_context_enabled':True,'rerank_threshold':.3,'normal_DYNAMIC':True,'source_versions':'must remain frozen; no upload/reembedding','fresh_session_request_per_arm':True},'software_envelope_only_not_authorization':{'cases':6,'arms_per_case':2,'question_submissions':12,'child_calls':12,'child_tokens':100000,'shared_provider_calls':144,'shared_tokens':1200000,'unknown':'STOP_NO_AUTOMATIC_RESEND'},'models_from_receipts':model_freezes,'cases':cases}

async def main_async(a):
 inv=read(a.inventory);cases=[c for c in inv['dev_cases'] if c['case_id'] in IDS];require(len(cases)==3,'EXACT_THREE_DEV_CASES_REQUIRED')
 results=[]
 for c in cases:
  require(c['case_id'] not in LOCKED,'HOLDOUT_FORBIDDEN')
  arms={}
  for arm,mode in (('A','off'),('B','bounded_source_v1')):
   result=await replay(c,mode);body=result.pop('request_body',None)
   if body is not None:save(a.output/(c['case_id'].replace('#','_')+'-'+arm+'.request-body.json'),body)
   arms[arm]=result
  results.append({'case_id':c['case_id'],'question':c['question'],'existing_manual_conflict_note':c['manual_review_note'],'new_semantic_judge':False,'arms':arms,'search_rerank_recomputed':False,'search_rerank_input_identical':arms['A']['rerank_ids_scores_unchanged']==arms['B']['rerank_ids_scores_unchanged']})
 fingerprints={str(p.relative_to(ROOT)):digest(p.read_bytes()) for p in [ROOT/'backend/weknora_port'/s for s in ('retrieval/source_evidence.py','retrieval/merge.py','retrieval/pipeline.py','storage/knowledge.py','generation/context.py','models/provider.py')]}
 report={'schema':'s8-3-offline-saved-source-replay/v1','status':'OFFLINE_ONLY_NOT_REAL_API_OR_MODEL_TEST','inventory_sha256':digest(a.inventory.read_bytes()),'code_fingerprints':fingerprints,'provider_sends':0,'database_connections':0,'cases':results}
 report['command']={'argv':sys.argv,'script_sha256':digest(Path(__file__).read_bytes()),'mode':'offline no event loop socket; all awaits use synchronous saved adapters'}
 save(a.output/'planned-six-case-ecs-ab.json',planned_manifest(inv))
 save(a.output/'results.json',report)
 print(json.dumps({'cases':len(results),'A_B_statuses':{c['case_id']:{k:v['status'] for k,v in c['arms'].items()} for c in results},'provider_sends':0,'results':str(a.output/'results.json')}))
 return 0 if all(v['status']=='PASS_OFFLINE_WIRE_BUILT' for c in results for v in c['arms'].values()) else 2

def finish_inline(coroutine):
 # All adapters are saved in-memory SELECTs, with no suspension or sockets.
 try:
  coroutine.send(None)
 except StopIteration as done:return done.value
 finally:coroutine.close()
 raise RuntimeError('UNEXPECTED_ASYNC_IO_FORBIDDEN')

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inventory',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();return finish_inline(main_async(a))
if __name__=='__main__':raise SystemExit(main())
