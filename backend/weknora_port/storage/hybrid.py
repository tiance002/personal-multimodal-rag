"""PostgreSQL binding for fixed WeKnora HybridSearch and FAQ postprocessing.

Tencent/WeKnora@3e8b0bfc (MIT). Search/merge/context remain separate from P5.
Only callbacks authorized by the application may invoke embedding providers.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Awaitable, Callable, Mapping, Sequence

from ..models.types import CallContext, CallCancelled, RunDeadlineExceeded
from ..retrieval.fusion import Hit, RetrievalConfig, fuse_or_deduplicate
from ..retrieval.search import SearchParams, SearchTarget, validate_targets, validate_results
from ..retrieval.types import SearchResult
from ..retrieval.merge import collect_image_info
from .knowledge import ScopedKnowledgeRepository, _json_object
from .postgres import RetrieveParams


class RetrievalEngineUnavailable(Exception):
    """Typed infrastructure failure that must not silently become partial FAQ."""


class PostgresKnowledgeRepository(ScopedKnowledgeRepository):
    def __init__(self, retriever, resolve_tenant, *,
                 embed_query: Callable[[str,str,CallContext],Awaitable[Sequence[float]]] | None = None,
                 model_identity: Callable[[str,CallContext],Awaitable[str]] | None = None,
                 retrieval_config: Callable[[CallContext],Awaitable[RetrievalConfig | None]] | None = None):
        super().__init__(retriever,resolve_tenant)
        self.embed_query, self.model_identity, self.retrieval_config = embed_query, model_identity, retrieval_config

    async def hybrid_search(self, targets: Sequence[SearchTarget], params: SearchParams,
                            context: CallContext, query_embedding: Sequence[float] | None = None) -> Sequence[SearchResult]:
        self.retriever.require_verified()
        validate_targets(targets,context)
        if not targets:
            return []
        # Fixed parallel search batches only untagged full-KB targets. Partial
        # document/tag queries are separate calls, never a global document filter
        # imposed on their full-KB siblings.
        if len(targets)>1 and any(t.target_type!='knowledge_base' or t.tag_ids or t.scope_tag_ids for t in targets):
            raise ValueError('INCOMPATIBLE_SEARCH_TARGET_BATCH')
        tenant=await self._tenant(context)
        kb_ids=tuple(dict.fromkeys(t.knowledge_base_id for t in targets))
        cursor=await self.retriever.connection.execute(
            'SELECT id,tenant_id,type,embedding_model_id,indexing_strategy FROM knowledge_bases '
            'WHERE tenant_id = %s AND deleted_at IS NULL AND id IN ('+','.join(['%s']*len(kb_ids))+')',
            (tenant,*kb_ids))
        kbs={row['id']:row for row in await cursor.fetchall()}
        if set(kbs)!=set(kb_ids) or any(row['tenant_id']!=tenant for row in kbs.values()):
            raise PermissionError('KNOWLEDGE_BASE_IDENTITY_MISMATCH')
        primary=kbs[targets[0].knowledge_base_id]
        match_count=params.match_count if params.match_count>0 else 50
        pool=min(500,max(match_count*5,50)*len(kb_ids))
        models={row.get('embedding_model_id') for row in kbs.values() if row.get('embedding_model_id')}
        if len(models)>1:
            if self.model_identity is None:
                raise ValueError('EMBEDDING_IDENTITY_RESOLVER_REQUIRED')
            identities=await asyncio.gather(*(self.model_identity(model,context) for model in models))
            if len(set(identities))!=1 or not identities[0]:
                raise ValueError('KNOWLEDGE_BASE_EMBEDDING_MODELS_DIFFER')
        def flags(row):
            strategy=_json_object(row.get('indexing_strategy'))
            if not any(strategy.get(key,False) for key in ('vector_enabled','keyword_enabled','wiki_enabled','graph_enabled')):
                return True,True
            return bool(strategy.get('vector_enabled')),bool(strategy.get('keyword_enabled'))
        vector_ids=tuple(t.knowledge_base_id for t in targets if flags(kbs[t.knowledge_base_id])[0]
                         and t.vector_index_enabled and kbs[t.knowledge_base_id].get('embedding_model_id'))
        keyword_ids=tuple(t.knowledge_base_id for t in targets if flags(kbs[t.knowledge_base_id])[1]
                          and t.keyword_index_enabled and kbs[t.knowledge_base_id].get('type','document')!='faq')
        primary_vector=flags(primary)[0] and bool(primary.get('embedding_model_id'))
        embedding=tuple(query_embedding or ())
        if vector_ids and not params.disable_vector and not embedding and primary_vector:
            if self.embed_query is None:
                raise ValueError('EMBEDDING_ADAPTER_REQUIRED')
            embedding=tuple(await self.embed_query(primary['id'],params.query_text,context))
        vector_ids=vector_ids if embedding and not params.disable_vector else ()
        keyword_ids=keyword_ids if not params.disable_keywords else ()
        if not vector_ids and not keyword_ids:
            return []
        documents=targets[0].knowledge_ids if targets[0].target_type=='knowledge' else ()
        tags=targets[0].tag_ids
        config=await self.retrieval_config(context) if self.retrieval_config else None

        async def retrieve(depth):
            async def vector():
                rows=await self.retriever.vectors(RetrieveParams(vector_ids,documents,tags,
                    params.query_text,embedding,depth,params.vector_threshold))
                return [self._hit(row,'embedding') for row in rows]
            async def keyword():
                rows=await self.retriever.keywords(RetrieveParams(keyword_ids,documents,tags,
                    params.query_text,(),depth,params.keyword_threshold))
                return [self._hit(row,'keywords') for row in rows]
            # One dedicated SQL connection cannot run concurrent transactions.
            # A single PostgreSQL store retains the source candidates and scores;
            # multi-store source fanout belongs to separate verified connections.
            vectors=await vector() if vector_ids else []
            keywords=await keyword() if keyword_ids else []
            return vectors,keywords

        vectors,keywords=await retrieve(pool)
        fused=fuse_or_deduplicate(vectors,keywords,config)
        if primary.get('type')=='faq':
            if len(fused)<match_count and len(vectors)==pool:
                fused=await self._iterative_faq(retrieve,match_count,params.query_text,context)
            else:
                fused=await self._negative_filter(fused,params.query_text,context)
        fused=fused[:match_count]
        if not fused:
            return []
        source_rows=await self._read_chunks([hit.chunk_id for hit in fused],context,searchable=True)
        results=[]
        for hit in fused:
            source=source_rows.get(hit.chunk_id)
            if source is None:
                continue
            results.append(replace(source,score=hit.score,match_type=hit.metadata['match_type'],matched_content=hit.content))
        # Fixed processSearchResults enriches empty ImageInfo unconditionally,
        # including the formal RAG path that skips standalone context enrichment.
        empty_ids = [row.id for row in results if row.image_info == '']
        if empty_ids:
            images = await collect_image_info(empty_ids, SimpleNamespace(targets=targets),
                context, self, source_chunks={row.id: row for row in results})
            results = [replace(row, image_info=images[row.id])
                       if row.image_info == '' and row.id in images else row for row in results]
        if not params.skip_context_enrichment:
            # Formal RAG explicitly asks for skip=true and handles this in merge.
            # Standalone enriched search is a separate required adapter, not a
            # falsely successful unimplemented branch.
            raise NotImplementedError('STANDALONE_SEARCH_ENRICHMENT_PENDING')
        return validate_results(results,targets,context)

    @staticmethod
    def _hit(row: Mapping, match_type: str) -> Hit:
        return Hit(chunk_id=row['chunk_id'],score=row['score'],content=row.get('content') or '',
            knowledge_base_id=row['knowledge_base_id'],knowledge_id=row['knowledge_id'],
            source_id=row.get('source_id') or '',source_type=row.get('source_type',0),tag_id=row.get('tag_id') or '',
            metadata={'match_type':match_type})

    async def _negative_filter(self,hits,query,context):
        lowered=query.strip().lower()
        if not lowered or not hits:
            return hits
        try:
            chunks=await self.get_chunks([hit.chunk_id for hit in hits],context)
        except (PermissionError,CallCancelled,RunDeadlineExceeded):
            raise
        except Exception:
            return hits
        def negative(hit):
            chunk=chunks.get(hit.chunk_id)
            if chunk is None or chunk.chunk_type!='faq': return False
            questions=chunk.chunk_metadata.get('negative_questions',())
            if not isinstance(questions,(list,tuple)) or not all(isinstance(q,str) for q in questions): return False
            return any(isinstance(q,str) and q.strip() and q.strip().lower()==lowered for q in questions)
        return [hit for hit in hits if not negative(hit)]

    async def _iterative_faq(self,retrieve,match_count,query,context):
        depth=min(500,match_count*3)
        unique={}
        filtered=set()
        cached={}
        lowered=query.strip().lower()
        for _ in range(5):
            try:
                vectors,keywords=await retrieve(depth)
            except (RetrievalEngineUnavailable,PermissionError,CallCancelled,RunDeadlineExceeded):
                raise
            except Exception:
                break
            rows=[*vectors,*keywords]
            if not rows: break
            new_ids=[hit.chunk_id for hit in rows if hit.chunk_id not in cached and hit.chunk_id not in filtered]
            if new_ids:
                try: cached.update(await self.get_chunks(new_ids,context))
                except (PermissionError,CallCancelled,RunDeadlineExceeded): raise
                except Exception: pass
            for hit in rows:
                if hit.chunk_id in filtered: continue
                chunk=cached.get(hit.chunk_id)
                negatives=chunk.chunk_metadata.get('negative_questions',()) if chunk else ()
                if not isinstance(negatives,(list,tuple)) or not all(isinstance(q,str) for q in negatives): negatives=()
                if chunk and chunk.chunk_type=='faq' and any(
                        isinstance(q,str) and q.strip() and q.strip().lower()==lowered
                        for q in negatives):
                    filtered.add(hit.chunk_id); unique.pop(hit.chunk_id,None); continue
                if hit.chunk_id not in unique or hit.score>unique[hit.chunk_id].score:
                    unique[hit.chunk_id]=hit
            if len(unique)>=match_count or len(rows)<depth or depth>=500: break
            depth=min(500,depth*2)
        return sorted(unique.values(),key=lambda hit:hit.score,reverse=True)
