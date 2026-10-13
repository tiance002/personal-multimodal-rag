"""Actual isolated SQL retrieval/history probe with synthetic vectors, no models."""
import argparse
import asyncio
import json
from pathlib import Path
import selectors
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.storage.postgres import IsolationPolicy, PostgresRetriever, RetrieveParams
from backend.weknora_port.storage.knowledge import ScopedKnowledgeRepository
from backend.weknora_port.storage.indexing import IndexInfo
from backend.weknora_port.storage.chunks import StoredChunk, ChunkPlan, DocumentScope
from backend.weknora_port.storage.history import LocalPrincipal, PostgresHistoryRepository
from backend.weknora_port.streaming.replay import FinalCommit


async def run(options):
    import psycopg
    from psycopg.rows import dict_row
    ids = {key:str(uuid4()) for key in ('kb','other_kb','doc','other_doc','chunk','other_chunk','session','request','q','a')}
    owner, tenant = 'local-pg-probe', 10001
    policy = IsolationPolicy(options.expected_system_identifier, frozenset(options.prohibited_system_identifier))
    async with await psycopg.AsyncConnection.connect(options.dsn, autocommit=True, row_factory=dict_row) as conn:
        repo = PostgresRetriever(conn,policy,writable_knowledge_base_ids=frozenset((ids['kb'],ids['other_kb'])))
        identity = await repo.verify_identity()
        for kb,doc in ((ids['kb'],ids['doc']),(ids['other_kb'],ids['other_doc'])):
            await conn.execute("INSERT INTO knowledge_bases(id,name,tenant_id,embedding_model_id,summary_model_id,rerank_model_id) "
                               "VALUES(%s,'probe',%s,'synthetic-3d','synthetic-chat','synthetic-rerank')",(kb,tenant))
            await conn.execute("INSERT INTO knowledges(id,tenant_id,knowledge_base_id,type,title,source,parse_status,file_name) "
                               "VALUES(%s,%s,%s,'file','probe','local-fixture','completed','probe.txt')",(doc,tenant,kb))
        async def resolve_tenant(subject):
            if subject != owner: raise PermissionError('UNKNOWN_OWNER')
            return tenant
        knowledge = ScopedKnowledgeRepository(repo, resolve_tenant)
        context = CallContext(ids['request'],TrustedScope(owner,(ids['kb'],),(ids['doc'],)),session_id=ids['session'])
        scope = DocumentScope(tenant,owner,ids['kb'],ids['doc'],'fixture-v1')
        chunk = StoredChunk(ids['chunk'],scope,'retrievalalpha source body',0,0,26,'text','')
        indexed = IndexInfo(ids['chunk'],0,ids['chunk'],ids['doc'],ids['kb'],'retrievalalpha index title')
        await knowledge.create_chunks(ChunkPlan((chunk,),(indexed,)),context)
        await conn.execute("INSERT INTO chunks(id,tenant_id,knowledge_base_id,knowledge_id,content,chunk_index,start_at,end_at) "
                           "VALUES(%s,%s,%s,%s,'retrievalalpha other KB',0,0,23)",
                           (ids['other_chunk'],tenant,ids['other_kb'],ids['other_doc']))
        other = IndexInfo(ids['other_chunk'],0,ids['other_chunk'],ids['other_doc'],ids['other_kb'],'retrievalalpha other KB')
        await repo.batch_save((indexed,other),{indexed.source_id:(1.,0.,0.),other.source_id:(1.,0.,0.)})
        params = RetrieveParams((ids['kb'],),(ids['doc'],),query='retrievalalpha',embedding=(1.,0.,0.),top_k=10,threshold=.2)
        vectors = await repo.vectors(params)
        keywords = await repo.keywords(params)
        assert {row['chunk_id'] for row in vectors} == {ids['chunk']}, vectors
        assert {row['chunk_id'] for row in keywords} == {ids['chunk']}, keywords
        readback = await knowledge.get_chunks((ids['chunk'],ids['other_chunk']),context)
        assert set(readback) == {ids['chunk']} and readback[ids['chunk']].content == chunk.content
        await conn.execute("INSERT INTO sessions(id,tenant_id,subject_id,title) VALUES(%s,%s,%s,'probe')",(ids['session'],tenant,owner))
        history = PostgresHistoryRepository(repo,LocalPrincipal(tenant,owner))
        run_identity = await history.create_turn(ids['session'],ids['request'],ids['q'],ids['a'],'fixture question',context.scope)
        duplicate = await history.create_turn(ids['session'],ids['request'],str(uuid4()),str(uuid4()),'fixture question',context.scope)
        assert duplicate == run_identity
        references = [{'chunk_id':ids['chunk'],'knowledge_id':ids['doc'],'knowledge_base_id':ids['kb']}]
        commit = FinalCommit(ids['session'],ids['a'],ids['request'],'fixture answer',references,{'status':'UNKNOWN'})
        await history.commit_final(commit)
        await history.commit_final(commit)
        saved = await (await conn.execute('SELECT content,knowledge_references,usage,is_completed FROM messages WHERE id=%s',(ids['a'],))).fetchone()
        assert saved == {'content':'fixture answer','knowledge_references':references,'usage':{'status':'UNKNOWN'},'is_completed':True}
        try:
            await history.commit_final(FinalCommit(ids['session'],ids['a'],ids['request'],'overwrite',[]))
        except ValueError as error:
            assert str(error) == 'FINAL_COMMIT_CONFLICT'
        else: raise AssertionError('completed answer was overwritten')
        evidence = {'status':'PASS','kind':'ACTUAL_ISOLATED_PG_RETRIEVAL_HISTORY',
                    'database':identity.database,'system_identifier':identity.system_identifier,'fixture_ids':ids,
                    'vector_ids':[row['chunk_id'] for row in vectors],'keyword_ids':[row['chunk_id'] for row in keywords],
                    'citation_readback':{'chunk_id':ids['chunk'],'body':readback[ids['chunk']].content},
                    'persisted_answer':saved,'real_provider_calls':0,'old_database_writes':0,
                    'not_run':['real embeddings','full RAG generation','HTTP/SSE recovery']}
        target = Path(options.report)
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(evidence,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dsn',required=True)
    parser.add_argument('--expected-system-identifier',required=True)
    parser.add_argument('--prohibited-system-identifier',action='append',required=True)
    parser.add_argument('--report',required=True)
    options = parser.parse_args()
    if sys.platform == 'win32':
        asyncio.run(run(options),loop_factory=lambda:asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else: asyncio.run(run(options))
