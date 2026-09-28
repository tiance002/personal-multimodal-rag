"""Freeze genuine project documents and reviewed source anchors, never chunks.

This is an acceptance corpus for project-document RAG, not a public benchmark.
All questions are labelled from pre-existing documentation before A/B results.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


DESIGN='docs/superpowers/specs/2026-09-26-personal-rag-v1-design.md'
SOURCES={'design':DESIGN,'readme':'README.md'}

# (category, question, [(source, exact source anchor)], answer point alternatives)
QUESTIONS=[
 ('single','设计规定旧学习规划项目可以作为运行时依赖吗？', [('design','No database, service, account, learning-domain model, or business API from that repository is a runtime dependency.')], [['No','不能','不得']]),
 ('single','设计中的 V1 是否实现登录和注册？', [('design','V1.0 does not implement login, registration, multi-user SaaS, RBAC, public sharing, Wiki behavior, a multi-agent cluster, external MCP, approval workflows, shell execution, third-party Skills, sandbox execution, artifact downloading, or the V2 database tables and API routes reserved for those capabilities.')], [['不','does not']]),
 ('single','设计采用模块化单体还是细粒度微服务？', [('design','The system is a modular monolith with a separate worker process, not a collection of fine-grained microservices.')], [['monolith','单体']]),
 ('single','按照设计，基础设施应该在哪里装配？', [('design','Dependency direction is `frontend -> API -> application -> domain/ports`; infrastructure implements ports and is wired only in the composition root.')], [['composition root','组合根']]),
 ('scope','模型输出可以扩大服务端的检索范围吗？', [('design','Model output cannot expand scope.')], [['cannot','不能','不得']]),
 ('scope','混合范围中存在 cloud_allowed=false 的知识库时，外发策略是什么？', [('design','A mixed scope containing any knowledge base with `cloud_allowed=false` is treated as local-only for the whole operation; the server does not silently remove that knowledge base.')], [['local-only','本地']]),
 ('version','替换上传如何影响已有文档版本？', [('design','`DocumentVersion` is immutable after it becomes ready. A replacement upload creates a new version.')], [['new version','新版本'],['immutable','不可变']]),
 ('version','候选版本索引失败会替换当前可用版本吗？', [('design','Failed candidates never replace a ready active version.')], [['never','不会','不能']]),
 ('version','历史运行保留的是哪个版本和引用快照？', [('design','Historical runs retain the exact version and quote snapshot used at answer time.')], [['answer time','回答时'],['snapshot','快照']]),
 ('multi_evidence','设计中 OCR 派生内容如何定位来源，能否直接冒充原文？', [('design','OCR and captions are derived assets and must point to their source image or page.'),('design','Derived text is never presented as original text without its derived status.')], [['source','来源'],['derived','派生']]),
 ('terminology','keyword/v1 采用哪些规范化和中文检索基础方法？', [('design','Keyword retrieval uses NFKC normalization, lower-casing, special-token preservation, and a deterministic Chinese bigram/term-frequency baseline named `keyword/v1`.')], [['NFKC'],['bigram','二元'],['term-frequency','词频']]),
 ('multi_evidence','独立 Embedding profile 在设计中记录哪些属性？', [('design','Vector retrieval uses a dedicated embedding profile containing provider, model name, model revision, dimension, distance, and fingerprint.')], [['provider','提供者'],['dimension','维度'],['fingerprint','指纹']]),
 ('single','初期向量检索是精确比较还是 ANN？', [('design','Initial PostgreSQL vector retrieval uses exact comparison; ANN indexes and BM25 are not added until an ADR is backed by real evaluation evidence.')], [['exact','精确']]),
 ('single','HyDE 假设文本可以保存为引用或知识片段吗？', [('design','HyDE is query-only and cannot be stored as a citation or knowledge chunk.')], [['query-only','查询'],['cannot','不能']]),
 ('single','设计允许检索重试多少次，能否扩大范围？', [('design','The retry is bounded to one pass and cannot broaden the server scope.')], [['one','一次'],['cannot','不能']]),
 ('multi_evidence','生成前 E1 标签冻结哪些来源信息？', [('design','The server freezes `E1 -> version_id, chunk_id, locator, quote, quote_sha256` before generation.')], [['version_id'],['locator'],['quote_sha256']]),
 ('single','未知或跨范围的引用标签如何处理？', [('design','Final answer labels are resolved server-side; unknown, expired, cross-scope, or unsupported labels are rejected or removed and the run is marked non-successful rather than silently accepted.')], [['rejected','removed','拒绝','移除'],['non-successful','不成功']]),
 ('single','本地聊天模型不可用时，会静默切换云端吗？', [('design','If the local chat model is unavailable, the system returns `MODEL_UNAVAILABLE` or a retrieval-only insufficient result; it never silently switches to cloud.')], [['MODEL_UNAVAILABLE'],['never','不会']]),
 ('multi_evidence','设计允许云端调用需要哪些许可和预算条件？', [('design','Cloud generation, embedding, OCR/VLM, reranking, and query rewriting are only allowed when the global switch is enabled, every selected knowledge base permits cloud egress, and the monthly budget reservation succeeds.')], [['global','全局'],['every','全部','每个'],['budget','预算']]),
 ('single','已发送但未确认的模型调用如何处理预算占用？', [('design','A budget reservation is created before the provider call and is settled using actual usage; an unconfirmed sent call keeps a conservative reservation.')], [['conservative','保守']]),
 ('single','设计默认日志是否包含完整私人原文？', [('design','Logs contain request/run IDs, stage, error codes, provider metadata, and token/cost counters, but not full private source text by default.')], [['not','不']]),
 ('single','上传成功代表 stored 还是 ready？', [('design','A document/version/job record is created transactionally; upload success means `stored`, not `ready`.')], [['stored']]),
 ('multi_evidence','摄取任务的处理阶段和终态分别是什么？', [('design','Stages are `queued -> processing -> indexing -> ready`, with `failed` and `cancelled` terminal states.')], [['queued'],['processing'],['indexing'],['ready'],['failed'],['cancelled']]),
 ('single','图谱提取失败是否影响已就绪的索引？', [('design','Graph extraction is a separate optional job with an independent `graph_status`; graph failure leaves `index_status=ready`.')], [['index_status=ready']]),
 ('single','设计中的 Smart Agent 受哪些资源限制？', [('design','Limits cover steps, tokens, time, and cost; cancellation produces a terminal `run.cancelled` state.')], [['steps','步数'],['tokens','Token'],['time','时间'],['cost','成本']]),
 ('single','README 记录的独立 Embedding 模型和维数是什么？', [('readme','Ollama：当前默认 `qwen3.5:4b`（本地 Chat/Smart）与 `bge-m3:latest`（独立 Embedding，1024 维）；本机能力见 ADR-004')], [['bge-m3:latest'],['1024']]),
 ('single','README 中 OCR 不可用和运行无文字分别报什么错误？', [('readme','OCR 无法启动报 `OCR_UNAVAILABLE`，正常运行但无文字报 `OCR_EMPTY`，均不伪装索引成功。')], [['OCR_UNAVAILABLE'],['OCR_EMPTY']]),
 ('terminology','README 记录 Quick 与 Smart 分别使用哪个 LangChain 执行方式？', [('readme','Quick 使用固定 LangChain `Runnable` Chain，Smart 使用 LangChain `create_agent`；两者共用 `KnowledgeGateway`、证据覆盖、引用冻结和答案校验。')], [['Runnable'],['create_agent']]),
 ('multi_evidence','README 中 Smart 默认开放哪三个知识工具，图谱工具何时追加？', [('readme','Smart 默认只开放 `list_documents`、`search_knowledge`、`read_document` 三个只读知识工具；只有服务端选中的全部知识库显式启用 `graph_enabled` 时，才追加 `query_knowledge_graph`。')], [['list_documents'],['search_knowledge'],['read_document'],['graph_enabled']]),
 ('cross_document','设计和 README 分别怎样要求引用来源冻结和点击回读？', [('design','The server freezes `E1 -> version_id, chunk_id, locator, quote, quote_sha256` before generation.'),('readme','索引显示完成后再提问，并点击回答中的 `E1` 等引用查看原文。')], [['quote_sha256'],['E1'],['原文','source']]),
 ('no_answer','这些资料中项目负责人的私人手机号码是什么？', [], []),
 ('no_answer','这些资料中阿里云账户的信用卡号码是什么？', [], []),
 ('no_answer','这些资料中给出了哪个自动批准 Release Tag 的负责人授权日期？', [], []),
]


def build(root:Path):
    output=root/'evaluations/trust_v1'
    (output/'corpus').mkdir(parents=True,exist_ok=True)
    texts={}
    sources=[]
    artifacts={}
    for identity,path in SOURCES.items():
        raw=(root/path).read_bytes()
        # Same normalized text is frozen, indexed and annotated; original hash retained.
        normalized=raw.decode('utf-8-sig').replace('\r\n','\n')
        frozen=normalized.encode('utf-8')
        artifacts[output/'corpus'/f'{identity}.md']=frozen
        digest=hashlib.sha256(frozen).hexdigest()
        texts[identity]=(normalized,digest)
        sources.append({'document_id':identity,'path':f'corpus/{identity}.md','source_version':digest,
                        'origin':path,'original_sha256':hashlib.sha256(raw).hexdigest(),
                        'normalization':'UTF-8 without BOM, CRLF to LF; no content edits',
                        'license':'existing project documentation; no public redistribution asserted'})
    cases=[]
    for number,(category,question,anchors,points) in enumerate(QUESTIONS,1):
        evidence=[]
        for source,anchor in anchors:
            content,digest=texts[source]
            if content.count(anchor)!=1:
                raise ValueError(f'ambiguous/missing reviewed anchor: case {number}')
            start=content.index(anchor)
            evidence_id=hashlib.sha256(f'{source}:{digest}:{start}:{start+len(anchor)}'.encode()).hexdigest()[:24]
            evidence.append({'evidence_id':'gold_'+evidence_id,'document_id':source,'source_version':digest,
                'locator':{'kind':'markdown','start':start,'end':start+len(anchor),
                           'coordinate_space':'normalized_source_text'},'grade':1,'reviewed_quote':anchor})
        cases.append({'case_id':f'trust-{number:03}','dataset_version':'trust-docs-v1',
            'question':question,'expected_chunk_ids':[],'answer_points':[group[0] for group in points],
            'kb_scope':['acceptance'],'knowledge_base_scope':['acceptance'],'document_scope':[],
            'gold_evidence':evidence,'required_answer_points':points,'answerable':bool(evidence),
            'category':category,'split':'locked_test','metadata':{'review_method':'agent source-anchor inspection before A/B',
                'human_review':'NOT_RUN','annotation_basis':'source documentation, not model output'}})
    case_payload=''.join(json.dumps(case,ensure_ascii=False)+'\n' for case in cases).encode('utf-8')
    manifest={'schema_version':'stable-gold-v1','dataset_version':'trust-docs-v1','sources':sources,'sample_count':len(cases),
        'locked_sha256':hashlib.sha256(case_payload).hexdigest(),
        'coverage_threshold':.8,'split_policy':'all acceptance questions locked; legacy fixture development corpus separate',
        'scope':'project documentation; does not establish general-domain benchmark quality'}
    artifacts[output/'sources.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    artifacts[output/'locked.jsonl']=case_payload
    # Reject the entire update before writing any changed file. New annotations
    # require a separately versioned dataset, never an in-place locked rebuild.
    def identical(path,payload):
        if path.name=='sources.json':
            return json.loads(path.read_bytes())==json.loads(payload)
        return path.read_bytes()==payload
    if any(path.exists() and not identical(path,payload) for path,payload in artifacts.items()):
        raise ValueError('frozen dataset already exists with different content; create a new dataset version')
    for path,payload in artifacts.items():
        if not path.exists(): path.write_bytes(payload)
    print(json.dumps({'status':'SOURCE_ANCHORS_VERIFIED','sample_count':len(cases),'sources':len(sources)}))


if __name__=='__main__':
    build(Path(__file__).resolve().parents[1])
