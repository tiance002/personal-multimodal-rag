"""Read-only rerun of current full search on the existing frozen actual-Go corpus."""
from pathlib import Path
import sys,os,json,re,dataclasses,hashlib
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT))
from tests.weknora_port.test_memory_storage_pg import actual_pg_run,connection
from backend.weknora_port.storage.hybrid import PostgresKnowledgeRepository
from backend.weknora_port.retrieval.search import SearchParams,SearchTarget
from backend.weknora_port.models.types import CallContext,TrustedScope
from backend.weknora_port.generation import Evidence
OUT=Path(__file__).parent
OLD=Path(r'E:\WK\go-linux\evidence\pg-readonly-corpus-20261015')
PREVIOUS=OLD.parent/'search-image-completion-9d6d355-20261015'
text=(ROOT/'docs/weknora-port/AUTOMATIC_MEMORY_CHECKPOINT_20261013.md').read_text(encoding='utf-8-sig')
dsn=next(v for v in re.findall(r'postgres(?:ql)?://[^\s\x27\x22`]+',text) if urlsplit(v).hostname=='127.0.0.1' and urlsplit(v).port==56423 and urlsplit(v).path=='/weknora_python_port_20261013')
def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
frozen=read(OLD/'committed-corpus.frozen.json')
case=next(c for c in read(OLD/'queries.frozen.json') if c['id']=='keyword-blue')
go_raw=read(OLD/'go-retry.raw.json')
go={r['id']:r for c in go_raw['rows'] if c['id']==case['id'] for r in c['hydrated']}
prior=read(PREVIOUS/'python-full-hybrid.raw.json')
fields=['id','content','knowledge_id','knowledge_base_id','chunk_type','chunk_index','start_at','end_at','parent_chunk_id','knowledge_title','knowledge_filename','knowledge_source','knowledge_description','knowledge_channel','metadata','chunk_metadata','matched_content','seq']
def normalized(raw):return sorted(json.loads(raw or '[]'),key=lambda x:json.dumps(x,sort_keys=True))
async def scenario():
 conn,guard=await connection(dsn)
 identity=await guard.verify_identity()
 attempts=[]
 async def forbidden(*a,**kw):attempts.append('UNEXPECTED_MODEL');raise AssertionError('NO_MODEL_ALLOWED')
 try:
  await conn.execute('SET default_transaction_read_only=on')
  ro=await (await conn.execute('SHOW default_transaction_read_only')).fetchone();assert next(iter(ro.values()))=='on'
  kbs=[k['id'] for k in frozen['knowledge_bases']]
  async def index_rows():return await (await conn.execute('SELECT id,source_id,source_type,chunk_id,knowledge_id,knowledge_base_id,tag_id,content,dimension,embedding::text AS embedding,is_enabled FROM embeddings WHERE knowledge_base_id=ANY(%s) ORDER BY id',(kbs,))).fetchall()
  current=await index_rows();assert current==frozen['rows'],'FROZEN_EMBEDDING_ROWS_CHANGED'
  async def source_rows():return await (await conn.execute('SELECT id,content,chunk_type,parent_chunk_id,image_info,chunk_index,content_revision,is_enabled,index_status FROM chunks WHERE knowledge_base_id=%s ORDER BY id',(case['kbs'][0],))).fetchall()
  before=await source_rows();assert before==prior['source_rows'],'FROZEN_SOURCE_ROWS_CHANGED'
  stats_before=await (await conn.execute("SELECT * FROM paradedb.index_info('embeddings_search_idx'::regclass,true)")).fetchall()
  async def tenant(subject):
   assert subject=='independent-committed-image-oracle'
   return case['tenant']
  repo=PostgresKnowledgeRepository(guard,tenant,embed_query=forbidden,model_identity=forbidden)
  context=CallContext('seq-image-existing-frozen',TrustedScope('independent-committed-image-oracle',tuple(case['kbs'])))
  params=SearchParams(case['query'],match_count=case['top_k'],vector_threshold=case['threshold'],keyword_threshold=case['threshold'],skip_context_enrichment=True)
  results=await repo.hybrid_search((SearchTarget(case['kbs'][0]),),params,context,query_embedding=tuple(case['embedding']))
  assert {r.id for r in results}==set(go)
  for r in results:
   for field in fields:assert getattr(r,field)==go[r.id][field],(r.id,field,getattr(r,field),go[r.id][field])
   assert normalized(r.image_info)==normalized(go[r.id]['image_info']),(r.id,'image_info')
   e=Evidence.from_result(r)
   assert e.seq==go[r.id]['seq'] and e.image_info==r.image_info
   assert e.match_type==go[r.id]['match_type']
   if r.chunk_type in ('image_ocr','image_caption'):assert r.image_info==go[r.id]['image_info']
  assert await source_rows()==before and await index_rows()==current
  stats_after=await (await conn.execute("SELECT * FROM paradedb.index_info('embeddings_search_idx'::regclass,true)")).fetchall()
  assert not attempts
  inputs=[OLD/'committed-corpus.frozen.json',OLD/'queries.frozen.json',OLD/'go-retry.raw.json',PREVIOUS/'python-full-hybrid.raw.json']
  receipt={'kind':'ACTUAL_CURRENT_FULL_SEARCH_VS_EXISTING_ACTUAL_GO_FROZEN_CORPUS','case':case,'readonly':True,'physical_pg_system_id':identity.system_identifier,'results':[dataclasses.asdict(r) for r in results],'evidence_projection':[dataclasses.asdict(Evidence.from_result(r)) for r in results],'shared_fields_equal':fields,'image_info_equal':True,'child_literal_equal':True,'source_rows_and_12_index_rows_unchanged':True,'input_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},'stats_before':stats_before,'stats_after':stats_after,'stats_stable':stats_before==stats_after,'scores':'NOT_REASSERTED; global segment statistics differ from prior Go','tie_order':'UNDEFINED_FUSION_TIE_ORDER; compare candidate IDs and fields, no tied order assertion','model_attempts':attempts,'real_provider_calls':0,'db_writes':0,'ddl':0,'existing_go_reused_no_rerun':True}
  destination=OUT/'frozen-current.pg.json';assert not destination.exists();destination.write_text(json.dumps(receipt,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
  print('FROZEN_FULL_SEARCH_RESULTS',len(results),'SHARED_FIELDS',len(fields),'IMAGE_AND_SEQ_EQUAL; NO_WRITES_OR_MODELS')
 finally:await conn.close()
actual_pg_run(scenario())
