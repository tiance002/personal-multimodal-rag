"""Formal source stage order; finalized context is the only P5 handoff."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from .types import SearchState
from .search import SearchTarget, search
from .query import understand_query, needs_retrieval, default_segmenter
from .rerank import rerank
from .merge import merge
from .context import into_chat_message
from .filter_top_k import filter_top_k
DEFAULT_REWRITE_SYSTEM = 'You are an intelligent assistant that performs THREE tasks on the user\'s question:\n1. Understand and rewrite the question (coreference resolution and ellipsis completion)\n2. Classify the intent of the question\n3. Analyze attached images (when present)\n\n## Task 1: Query Understanding\nBased on the conversation history, rewrite the current user question:\n- Perform coreference resolution: replace pronouns such as "it", "this", "that", "they", "them", etc. with explicit subjects\n- Complete omitted key information to ensure the question is semantically complete\n- Preserve the original meaning and expression style of the question\n- Preserve the request type: a question remains a question and an instruction remains an instruction.\n- Preserve source restrictions, requested actions, output format, language requirements, and other substantive constraints. Be concise without an arbitrary word limit that drops requirements.\n- Keep the original request language unless the user asks for translation; {{language}} is a default, not a reason to change explicit language requirements.\n- CRITICAL: The rewritten question will be used for knowledge base retrieval. It MUST preserve specific entities, keywords, and core search terms. Do NOT generate meta-instructions like "请在知识库中查找..." or "请搜索..." — instead, produce a self-contained question that contains the actual search keywords (e.g. person names, concepts, technical terms)\n- EXCEPTION to the above: when the user wants to broadly read, browse, organize, or export knowledge base content WITHOUT specifying particular search terms (e.g. "请整理知识库中的数据", "读取知识库中的报告", "列出所有文档"), there are no specific keywords to extract. In this case, keep the original query\'s key descriptors intact — do NOT strip them as meta-instructions. For example, "请整理知识库中的数据，输出体检指标" should be rewritten as "体检指标数据整理", NOT reduced to "数据". Preserve any mentioned content types (报告/文档), labels (标签名), or file names.\n\n## Task 2: Intent Classification\nClassify the user\'s intent into exactly ONE of the following categories.\nFollow the decision priority below — check from top to bottom, use the FIRST match:\n\n1. `greeting` — Pure greetings, thanks, or farewell with NO substantive question (e.g. "你好", "谢谢", "再见").\n2. `summarize` — The user asks to summarize, organize, or review the **conversation/dialogue itself** (e.g. "总结一下我们的对话", "回顾一下我们聊了什么"). **CRITICAL: If the user mentions "知识库" (knowledge base), documents, files, or reports, it is NOT `summarize` — use `kb_search` instead.** For example, "整理知识库中的数据" is `kb_search`, NOT `summarize`.\n3. `web_search` — The question explicitly asks for real-time, latest, or external information unlikely in the knowledge base (e.g. "今天天气怎么样", "最新的新闻").\n4. `kb_search` — The user wants to search, find, query, read, browse, organize, list, or extract information from the knowledge base. This includes both specific searches (e.g. "帮我查一下这个") AND broad access requests (e.g. "整理知识库中的数据", "读取知识库中的报告", "列出所有文档"). **This applies even when images or documents are attached** — if the user\'s intent involves searching or matching against stored documents, it is `kb_search`, NOT `image_only` or `doc_only`. **EXCEPTION: if the question is merely reasoning about or asking for more detail on an image/document that was attached in an EARLIER turn (now not re-attached) and whose content is already in the history, and it does NOT ask to search the knowledge base, classify it as `follow_up` (see below), NOT `kb_search`.**\n5. `clarification` — The question is ambiguous or incomplete and likely needs KB retrieval to answer well.\n6. `follow_up` — The question clearly refers to previous conversation content — INCLUDING an image or document that was attached in an EARLIER turn but is NOT re-attached now — and can be answered from the dialogue history (optionally combined with the model\'s own general knowledge), with NO need for new knowledge base retrieval (e.g. "上面第三点展开讲讲", "你刚才说的那个方案再详细说说"). **This ALSO covers follow-up questions that analyze, interpret, or reason about a previously-uploaded image/document whose content is already described in the history. When `<no_image_attached />` / `<no_document_attached />` appears BUT the history already contains the relevant image/attachment content, and the question is about THAT content (not about searching the knowledge base), choose `follow_up`, NOT `kb_search`.**\n7. `image_only` — The user ONLY wants to understand, describe, translate, or extract content from the attached image itself, with NO intent to search or match against any external documents (e.g. "这张图片是什么", "描述一下图片内容", "翻译图中文字"). **CRITICAL: This intent requires `<images_uploaded>` to be present. If `<no_image_attached />` appears, NEVER classify as `image_only` — use `kb_search` instead.**\n8. `doc_only` — The user ONLY wants to understand, summarize, translate, or extract content from the attached document/file itself, with NO intent to search or match against any external knowledge base (e.g. "总结一下这个文档", "这份文件讲了什么"). **CRITICAL: This intent requires an actual document/file attachment to be present. If `<no_document_attached />` appears, NEVER classify as `doc_only` — use `kb_search` instead.**\n9. `chitchat` — Casual conversation or small talk that needs no retrieval (e.g. "你是谁", "讲个笑话").\n\n**Default: when unsure, always choose `kb_search`.**\n\nKey distinction — `image_only` / `doc_only` vs `kb_search` with attachments:\n- User uploads image/doc + "这是什么" / "总结一下" → `image_only` / `doc_only` (only wants to analyze the attachment)\n- User uploads image/doc + "知识库里有这个吗" → `kb_search` (wants to search KB)\n- User uploads image/doc + "帮我找相关文档" → `kb_search` (wants to search KB)\n- User uploads image/doc + "翻译文件内容" → `image_only` / `doc_only` (only wants to analyze the attachment)\n\nKey distinction — `follow_up` vs `kb_search`:\n- "上面第二点再详细说说" with sufficient context in history → `follow_up`\n- "这个话题还有什么相关的内容" → `kb_search` (needs new retrieval)\n- a follow-up asking to analyze/interpret an image or document from a PREVIOUS turn (described in history, not re-attached now) → `follow_up` (reasoning about already-described content, no KB search needed)\n- a follow-up that explicitly asks to search the knowledge base for related documents → `kb_search`\n\n## Task 3: Image Analysis (only when images are attached)\nIf the user\'s message includes images, you MUST provide a non-empty description in `image_description`. It must NOT be empty when images are present.\nInclude objects, scene, layout, relationships, and any visible key details. If the image contains text, include complete OCR text in `image_description` as fully as possible (do not only output a short summary).\nIf both visual description and OCR exist, include both in `image_description`.\nOnly when there are no images at all, set `image_description` to an empty string.\n\n## Output Format\nYou MUST output ONLY a single JSON object.\nDo NOT output markdown, code fences, explanations, or any extra text.\nJSON schema:\n{"rewrite_query":"string","intent":"string","image_description":"string"}\n\n## Examples\nInput: "你好"\nOutput: {"rewrite_query":"你好","intent":"greeting","image_description":""}\n\nInput: "什么是RAG架构" (no history)\nOutput: {"rewrite_query":"什么是RAG架构","intent":"kb_search","image_description":""}\n\nInput: "它和传统搜索有什么区别" (history mentions RAG)\nOutput: {"rewrite_query":"RAG架构和传统搜索有什么区别","intent":"kb_search","image_description":""}\n\nInput: "再帮我查查他的信息" (history discusses 张三)\nOutput: {"rewrite_query":"张三的详细信息是什么","intent":"kb_search","image_description":""}\nWRONG output: {"rewrite_query":"请重新在知识库中查找关于张三的更多信息","intent":"kb_search","image_description":""}\n(WRONG: contains meta-instruction "在知识库中查找" instead of actual search keywords)\n\nInput: "上面第二点再展开讲讲" (history has detailed answer with numbered points)\nOutput: {"rewrite_query":"请展开讲讲上面回答中的第二点","intent":"follow_up","image_description":""}\n\nInput: [no image now; an image was uploaded and described in a PREVIOUS turn] "(a question that reasons about a detail of that previously-described image)"\nOutput: {"rewrite_query":"(self-contained question about the detail of the previously-described image)","intent":"follow_up","image_description":""}\n(NOTE: refers to an image already described in history, no re-attachment, and asks the model to reason about that image → follow_up, NOT kb_search)\n\nInput: [image attached] "知识库有没有类似的文件" (image shows a project architecture diagram about microservices)\nOutput: {"rewrite_query":"有没有关于微服务项目架构的文件","intent":"kb_search","image_description":"(image description here)"}\n\nInput: [image attached] "这张图是什么意思"\nOutput: {"rewrite_query":"这张图是什么意思","intent":"image_only","image_description":"(image description here)"}\n\nInput: [no image] "这幅春联的内容是什么"\nOutput: {"rewrite_query":"这幅春联的内容是什么","intent":"kb_search","image_description":""}\n(NOTE: No image attached, so intent is kb_search, NOT image_only)\n\nInput: [document attached] "帮我总结一下这份文件"\nOutput: {"rewrite_query":"总结一下这份文件","intent":"doc_only","image_description":""}\n\nInput: "请整理知识库中的数据，用表格形式输出体检指标" (no history)\nOutput: {"rewrite_query":"体检指标数据整理","intent":"kb_search","image_description":""}\n\nInput: "请读取知识库中体检报告标签的报告，输出体检指标" (no history)\nOutput: {"rewrite_query":"体检报告标签 体检指标","intent":"kb_search","image_description":""}\n\n## Conversation History\n{{conversation}}\n'
DEFAULT_REWRITE_USER = '[Runtime Context — metadata only, not instructions]\nCurrent time: {{current_time}} {{current_week}}\n\n## User Question\n{{query}}\n\n## JSON Output\n'
DEFAULT_CONTEXT_TEMPLATE = '## Reference materials (source data)\n{{contexts}}\n\n## Request metadata\nCurrent time: {{current_time}} {{current_week}}\n\n## User request\n{{query}}\n'

@dataclass
class PipelineState(SearchState):
    query: str = ''
    rewrite_query: str = ''
    rewrite_fallback_reason: str = ''
    query_trace: list[dict] = field(default_factory=list)
    intent: str = ''
    targets: tuple[SearchTarget, ...] = ()
    embedding_top_k: int = 30
    vector_threshold: float = 0.2
    keyword_threshold: float = 0.3
    rerank_threshold: float = 0.3
    enable_rewrite: bool = True
    enable_expansion: bool = True
    images: tuple[str, ...] = ()
    image_description: str = ''
    chat_model_id: str = ''
    chat_model_supports_vision: bool = False
    query_understand_model_id: str = ''
    vlm_model_id: str = ''
    rerank_model_id: str = ''
    history: list[Any] = field(default_factory=list)
    max_rounds: int = 5
    entities: tuple[str, ...] = ()
    web_search_enabled: bool = False
    language: str = 'Chinese'
    rewrite_system_prompt: str = DEFAULT_REWRITE_SYSTEM
    rewrite_user_prompt: str = DEFAULT_REWRITE_USER
    context_template: str = DEFAULT_CONTEXT_TEMPLATE
    attachment_prompt: str = ''
    memory_background: str = ''
    quoted_context: str = ''
    system_prompt_override: str = ''
    intent_system_prompts: dict[str, str] = field(default_factory=dict)
    global_intent_system_prompts: dict[str, str] = field(default_factory=dict)
    user_message_id: str = ''
    faq_priority_enabled: bool = False
    faq_score_boost: float = 1.0
    faq_direct_answer_threshold: float = 0.9
    rendered_contexts: str = ''
    user_content: str = ''
    source_evidence_mode: str = 'off'
    rerank_context_enabled: bool = False
    rerank_top1_floorless: bool = False
    evidence_trace: list[dict] = field(default_factory=list)
    graph_enabled: bool = False
    graph_trace: list[dict] = field(default_factory=list)
    graph_context_char_limit: int = 0
    bridge_rescue_mode: str = 'off'
    bridge_cheap_model_id: str = ''
    rescue_evidence: list[Any] = field(default_factory=list)
    bridge_normal_qualifications: dict[str, dict[str, Any]] = field(default_factory=dict)
    bridge_trace: list[dict] = field(default_factory=list)
    bridge_context_char_limit: int = 16000
    retrieval_strategy_mode: str = field(default='off', kw_only=True)
    query_type: str = field(default='UNKNOWN', kw_only=True)
    strategy_plan: Any = field(default=None, kw_only=True)
    strategy_rounds: list[Any] = field(default_factory=list, kw_only=True)
    strategy_trace: list[dict] = field(default_factory=list, kw_only=True)
    strategy_coverage: dict[str, Any] = field(default_factory=dict, kw_only=True)
    strategy_units_used: int = field(default=0, kw_only=True)
    strategy_queries_seen: set[str] = field(default_factory=set, kw_only=True)
    strategy_rerank_scores: dict[str, float] = field(default_factory=dict, kw_only=True)
    strategy_rerank_accepted_ids: set[str] = field(default_factory=set, kw_only=True)

class RetrievalPipeline:

    def __init__(self, repository, registry, *, segmenter=None, embedding_resolver=None, web_search=None, entity_search=None, history_loader=None, caption_sink=None, persist_sink=None, on_progress=None, affinity_resolver=None):
        self.repository, self.registry = (repository, registry)
        self.segmenter, self.embedding_resolver = (segmenter or default_segmenter(), embedding_resolver)
        self.web_search, self.entity_search = (web_search, entity_search)
        self.history_loader, self.caption_sink, self.persist_sink = (history_loader, caption_sink, persist_sink)
        self.on_progress = on_progress
        self.affinity_resolver = affinity_resolver

    async def _progress(self, stage, status, **fields):
        if self.on_progress is None:
            return
        try:
            value = self.on_progress(stage, status, **fields)
            if hasattr(value, "__await__"):
                await value
        except Exception as error:
            from ..models.types import CallCancelled, RunDeadlineExceeded
            if isinstance(error, (CallCancelled, RunDeadlineExceeded)):
                raise

    async def retrieve_once(self, state, context, *, empty_allowed=False):
        """Search through scoped originals once; no QU, bridge, Context or persistence."""
        import asyncio
        from ..models.types import CallCancelled, RunDeadlineExceeded
        from .search import SearchNothing, SearchError
        from .source_evidence import SourceEvidenceBudgetError
        from .strategy_executor import RoundResult
        def check_context():
            if context.cancelled and context.cancelled.is_set():
                raise CallCancelled()
            if context.deadline is not None and context.deadline <= asyncio.get_running_loop().time():
                raise RunDeadlineExceeded()
        check_context()
        try:
            ordinary_empty = False
            await self._progress("retrieval", "started")
            try:
                await search(state, context, self.repository, embedding_resolver=self.embedding_resolver, web_search=self.web_search, entity_search=self.entity_search, segmenter=self.segmenter)
            except SearchNothing:
                if not empty_allowed:
                    raise
                ordinary_empty = True
            await self._progress("retrieval", "completed", candidate_count=sum(r is not None for r in state.search_results))
            check_context()
            await self._progress("rerank", "started")
            try:
                if not ordinary_empty:
                    await rerank(state, context, self.registry, self.segmenter)
            except SearchNothing:
                if not empty_allowed:
                    raise
                ordinary_empty = True
            from .memory_affinity import apply_memory_affinity
            await apply_memory_affinity(state, context, self.affinity_resolver)
            await self._progress("rerank", "completed", candidate_count=sum(r is not None for r in state.rerank_results))
            check_context()
            await self._progress("merge", "started")
            if ordinary_empty:
                # A graph rescue must not resurrect all threshold-rejected Search rows.
                state.merge_results = []
            else:
                await merge(state, context, self.repository, self.segmenter)
            await self._progress("merge", "completed", candidate_count=sum(r is not None for r in state.merge_results))
            check_context()
            if not ordinary_empty:
                filter_top_k(state)
            check_context()
            if state.source_evidence_mode != 'off' and not ordinary_empty:
                from .source_evidence import expand_source_evidence
                await self._progress('source_unit', 'started')
                await expand_source_evidence(state, context, self.repository)
                await self._progress('source_unit', 'completed', candidate_count=len(state.merge_results))
                check_context()
                if not state.merge_results:
                    if not empty_allowed:
                        raise SearchNothing()
        except SearchNothing:
            if not getattr(state, '_strategy_serial', False):
                raise
            state.merge_results = []
            return RoundResult(state, status='EMPTY', reason='SEARCH_NOTHING')
        except SourceEvidenceBudgetError:
            if not getattr(state, '_strategy_serial', False):
                raise
            state.merge_results = []
            return RoundResult(state, status='BUDGET_OMITTED', reason='SOURCE_UNIT_BUDGET_EXCEEDED')
        except SearchError as error:
            from .search import RetrievalScopeError
            if isinstance(error, RetrievalScopeError) or not getattr(state, '_strategy_serial', False):
                raise
            state.merge_results = []
            return RoundResult(state, status='ERROR', reason=str(error))
        return RoundResult(state, status='EMPTY' if ordinary_empty else 'OK',
                           reason='SEARCH_NOTHING' if ordinary_empty else '')

    async def run(self, state, context):
        import asyncio
        from ..models.types import CallCancelled, RunDeadlineExceeded
        from .strategy import validate_strategy_mode, strategy_applicable
        validate_strategy_mode(state.retrieval_strategy_mode, state.source_evidence_mode)
        if type(state.bridge_rescue_mode) is not str or state.bridge_rescue_mode not in ('off', 'cheap_bridge_v1'):
            raise ValueError('BRIDGE_RESCUE_MODE_INVALID')
        if state.bridge_rescue_mode != 'off' and (state.graph_enabled or state.rerank_top1_floorless):
            raise ValueError('BRIDGE_EXPERIMENT_CONFLICT')
        if type(state.bridge_context_char_limit) is not int or not 1 <= state.bridge_context_char_limit <= 16000:
            raise ValueError('BRIDGE_CONTEXT_BUDGET_INVALID')
        if type(state.rerank_top1_floorless) is not bool:
            raise ValueError('RERANK_TOP1_SETTING_INVALID')
        if state.graph_enabled and state.rerank_top1_floorless:
            raise ValueError('GRAPH_TOP1_EXPERIMENT_CONFLICT')

        def check_context():
            if context.cancelled and context.cancelled.is_set():
                raise CallCancelled()
            if context.deadline is not None and context.deadline <= asyncio.get_running_loop().time():
                raise RunDeadlineExceeded()
        check_context()
        await self._progress("understand", "started")
        await understand_query(state, context, self.registry, history_loader=self.history_loader, caption_sink=self.caption_sink)
        query_audit = state.query_trace[-1] if state.query_trace else {}
        await self._progress("understand", "completed", rewrite_reason=query_audit.get('reason',''),
                             q0_sha256=query_audit.get('q0_sha256',''), rewrite_sha256=query_audit.get('rewrite_sha256',''))
        check_context()
        from .query import resolve_intent
        state.intent = resolve_intent(state)
        state.needs_retrieval = needs_retrieval(state.intent, state.web_search_enabled)
        if state.needs_retrieval:
            state.system_prompt_override = ''
        candidate = state.retrieval_strategy_mode == 'query_structure_v1' and strategy_applicable(state)
        if state.retrieval_strategy_mode != 'off' and not candidate:
            state.strategy_trace.append({'stage':'strategy','decision':'STRATEGY_NOT_APPLICABLE'})
        if candidate and state.needs_retrieval:
            from .strategy import plan_query
            from .strategy_executor import execute_plan, apply_strategy_result, fit_context_budget
            from .search import SearchNothing, SearchError
            plan = plan_query(state.query, state.rewrite_query or state.query, state.query_type)
            state.strategy_plan = plan
            result = await execute_plan(state, context, plan, self.retrieve_once)
            apply_strategy_result(state, result)
            if not state.merge_results:
                if any(r.status == 'ERROR' for r in state.strategy_rounds):
                    raise SearchError('STRATEGY_RETRIEVAL_FAILED')
                raise SearchNothing()
            await fit_context_budget(state, context)
            if not state.merge_results:
                raise SearchNothing()
            if plan.query_type == 'DEPENDENT':
                from .bridge_strategy import rescue_strategy_bridge
                from .strategy_executor import isolated_state, RoundResult, MAX_RETRIEVAL_UNITS
                from ..models.types import ProviderError
                async def recall_bridge(q1, child_context):
                    check_context()
                    key = ' '.join(q1.split()).casefold()
                    sub = isolated_state(state, q1)
                    if key in state.strategy_queries_seen:
                        return RoundResult(sub, 'BUDGET_OMITTED', 'DUPLICATE_QUERY')
                    if state.strategy_units_used >= MAX_RETRIEVAL_UNITS:
                        return RoundResult(sub, 'BUDGET_OMITTED', 'RETRIEVAL_UNIT_LIMIT')
                    state.strategy_units_used += 1
                    state.strategy_queries_seen.add(key)
                    try:
                        unit = await self.retrieve_once(sub, child_context)
                    except ProviderError as error:
                        if error.code != 'LOCAL_EXECUTION_LIMIT_DENIED' or error.sent:
                            raise
                        unit = RoundResult(sub, 'BUDGET_OMITTED', error.code)
                    state.strategy_rounds.append(unit)
                    state.strategy_trace.append({'stage':'strategy_round', 'subquery_id':'round-1',
                        'request_id':child_context.request_id,'status':unit.status,'reason':unit.reason})
                    return unit
                assessment = await rescue_strategy_bridge(state, context, self.repository,
                                                          self.registry, recall_bridge)
                state.strategy_trace.append({'stage':'strategy_bridge','status':assessment.status.value,
                    'reason':assessment.reason,'source_validity':assessment.source_validity,
                    'semantic_support':assessment.semantic_support,'new_recall':assessment.new_recall})
            await self._progress('context', 'started')
            finalized = await into_chat_message(state, context, persist_sink=self.persist_sink)
            await self._progress('context','completed',selected_evidence_count=len(finalized.result_ids))
            return finalized
        graph_paths = ()
        if state.graph_enabled and state.needs_retrieval:
            from .search import validate_targets
            from ..graph.retrieval import find_paths
            validate_targets(state.targets, context)
            await self._progress('graph', 'started')
            batches = await self.repository.get_graph_batches(context)
            graph_paths = find_paths(state.query + '\n' + state.rewrite_query, batches, context,
                                     tenant_id=self.repository.tenant_id)
            await self._progress('graph', 'completed', path_count=len(graph_paths))
            check_context()
        round_result = await self.retrieve_once(state, context, empty_allowed=bool(graph_paths))
        ordinary_empty = round_result.status == 'EMPTY'
        if graph_paths:
            from ..graph.retrieval import fuse_paths
            await self._progress('graph_fusion', 'started')
            added = await fuse_paths(state, context, self.repository, graph_paths)
            await self._progress('graph_fusion', 'completed', added_chunks=added)
            if ordinary_empty and not state.merge_results:
                raise SearchNothing()
            check_context()
        if state.bridge_rescue_mode != 'off' and not ordinary_empty:
            from copy import copy
            from .bridge_rescue import rescue_bridge
            from .source_evidence import SourceEvidenceBudgetError
            await self._progress('bridge', 'started')
            await rescue_bridge(state, context, self.repository, self.registry)
            check_context()
            if state.rescue_evidence:
                try:
                    await into_chat_message(copy(state), context, record_bridge=False)
                except SourceEvidenceBudgetError:
                    state.rescue_evidence = []
                    state.bridge_trace.append({'decision': 'SKIPPED', 'reason': 'BRIDGE_CONTEXT_BUDGET_EXCEEDED'})
            await self._progress('bridge', 'completed', added_chunks=len(state.rescue_evidence))
        await self._progress("context", "started")
        result = await into_chat_message(state, context, persist_sink=self.persist_sink)
        await self._progress("context", "completed", selected_evidence_count=len(result.result_ids))
        return result
