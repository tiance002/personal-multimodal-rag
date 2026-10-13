"""Explicit gated memory model/embedding adapters, never P5 routing substitutes."""
import uuid,hashlib
from .models.types import CallContext,TrustedScope,Message,ChatOptions,ProviderError
from .conversation.maintenance_types import ModelResponse

class RuntimeMemoryModels:
    def __init__(self,application):self.app=application

    async def origin_knowledge_bases(self,scope):
        app=self.app
        if (scope.tenant_id,scope.subject_id)!=(app.principal.tenant_id,app.principal.subject_id):
            raise ProviderError('MEMORY_OWNER_MISMATCH')
        # Whole-subject memory may derive from several sessions. Use only the
        # server's recorded source scope, conservatively covering all such
        # sessions. The existing gate still requires every originating KB to be
        # readable/cloud-allowed; an absent scope remains denied, never inferred.
        rows=await app._rows("SELECT DISTINCT jsonb_array_elements_text(knowledge_base_scope) AS kb "
            "FROM sessions WHERE tenant_id=%s AND subject_id=%s",
            (scope.tenant_id,scope.subject_id))
        knowledge_bases=tuple(sorted({row['kb'] for row in rows if row.get('kb')}))
        return knowledge_bases

    async def context(self,scope,identity):
        knowledge_bases = await self.origin_knowledge_bases(scope)
        return CallContext('memory/'+identity,TrustedScope(scope.subject_id,knowledge_bases))

    async def workspace_chat_model(self,scope):
        await self.context(scope,'workspace-model')
        rows=await self.app._rows("SELECT id FROM models WHERE tenant_id=%s AND subject_id=%s "
            "AND type='KnowledgeQA' AND status IN ('','active') AND deleted_at IS NULL ORDER BY created_at,id",
            (scope.tenant_id,scope.subject_id))
        return rows[0]['id'] if rows else ''

    async def complete(self,request):
        context=await self.context(request.scope,request.operation_id or str(uuid.uuid4()))
        registry=self.app.registry_for_context()
        try:
            model=await registry.chat(request.model_id,context)
            response=await model.chat((Message('system',request.system),Message('user',request.user)),
                ChatOptions(temperature=request.temperature,max_completion_tokens=request.max_completion_tokens,
                    thinking=request.thinking,format=request.schema_json),context)
            return ModelResponse(response.content,response.finish_reason) if response is not None else None
        finally:await registry.close()

    async def embed_query(self,scope,model_id,text):
        context=await self.context(scope,'embedding/'+str(uuid.uuid4()))
        registry=self.app.registry_for_context()
        try:
            model=await registry.embedding(model_id,context)
            response=await model.embed((text,),context)
            return response.vectors[0] if response.vectors else ()
        finally:await registry.close()
