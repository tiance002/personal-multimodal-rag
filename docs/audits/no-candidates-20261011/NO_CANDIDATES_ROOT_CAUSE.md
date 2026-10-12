# 2026-10-11 失败归因与证据边界

首轮段落是 G0 历史快照。以下 2026-10-12 补充记录为最新验证状态，保留首轮 UNKNOWN。

服务端确认的 KB：`d78f3434-0e8f-40bf-afcb-f1c2c311ff51`，document_scope=[]。
用户确认原问题仅是测试用的 hello / 你好。没有用其他问题代替原失败记录。

| 原问题 | Run | 本地时间 | 终态 |
| --- | --- | --- | --- |
| hello | d04fed31-22d1-4285-8e1f-9fb97cfdb965 | 20:14:14 +08:00 | failed / NO_CANDIDATES |
| 你好 | eb3b6fc5-b743-4274-991e-281657de3da1 | 20:22:44 +08:00 | failed / NO_CANDIDATES |

每个 Run 有四个事件：run.created、retrieval.started、retrieval.completed(count=0)、run.failed。
存储的 retrieval_hits 为零，用户消息存在，没有已提交事实答案。
旧运行代码没有 query-understanding、向量阶段原因和逐路计数，以上字段为 UNKNOWN。
旧 retrieval 会吞掉向量异常。当前连接拒绝不能倒推为原 Run 当时的唯一原因。

Scope 内六份 active 文档、六份 ready、47 个 chunk，47 个有 term、47 个有向量。
旧 profile 为 Ollama bge-m3:latest，1024 维，fingerprint=ollama-local-1024。
数据库尚无当前 child/index_identity 字段；对应统计为 UNKNOWN，不填写零。
对原查询 terms 的事后词法检查没有匹配，属于检查时的观察，不能代替原 Run 的逐路日志。

伴随的预览故障已独立定位：数据库 source/normalized key 指向的 11 个唯一内容对象，
在实际容器 storage 卷中缺失；宿主 `var/local-first/storage` 中存在可校验对象。
限定脚本逐一校验 CAS key、source SHA、原 Run Scope 与容器身份后，只补齐缺失对象，
不覆盖文件、不写数据库、不变更版本指针、不改历史 fingerprint。
修复后经原网页代理检查六份文档的 source/preview，12/12 HTTP 200。
证据在 `var/reports/no-candidates-20261011/storage-restore.json` 和 `source-preview-check.json`。
这验证原文/预览恢复，不能单独证明向量检索或新云端生成可用。

源码可确认而非本次旧 Run 唯一根因：普通入口缺少容量/持续服务策略；
新检索版本可能排除 legacy identity；旧查询错误被混成空候选；恢复会丢失澄清分类；
API 100000 与查询 4096 限额不一致。当前补丁让错误与装配缺失可见，
没有删去 Scope、活动版本、二次回读、预算或上下文保护。

没有证据支持先调低阈值、扩大 Top-K、删掉指纹过滤或重建全库。
新云端模型端到端 NOT RUN；普通服务的协议容量和持续授权装配未完成，整体尚未验收。

## 2026-10-12 最新证据

后续补充（不改写原失败原因）：负责人说明原问候只是可用性检查，授权用实际资料问题检验RAG。最终普通入口未启用待定闲聊草稿；真实资料问题在原KB、空document_scope下成功，run `f3dd7ba1-5566-4de9-8bc1-d28e83636f4f`，一次网页POST、最终引用200、刷新同终态。不能称原问候是成功资料事实问答。

新的DeepSeek未认证探针确认该本地路由对HTTP/1.1 ALPN曾在connect阶段EOF，而无ALPN严格证书验证请求成功返回401。普通服务限定使用显式SSLContext、原CA/hostname/minTLS策略；冻结评测传输未改。受观察的connect失败发生在第一HTTP字节发送前，可标NOT_SENT并只释放本次预留。发送后的TLS错误仍UNKNOWN。此修复后已有DeepSeek真实成功调用，但后续仍出现可证明未发送的连接EOF；不能承诺供应商网络持续可用。

- 已完成普通生产装配、实际0013→0023迁移、独立普通额度政策及 API/Worker 配置一致性验证。
- 迁移后实际确认原六文档的 legacy/v1 指纹不兼容；通过正常版本 API 建立六个新版本并激活，没有 UPDATE 历史指纹。原7版本/47片段聚合hash不变，12次原文/预览回读HTTP200；无效新版本失败不切active、0模型调用。
- 公开小样本普通入库、生成、Validator、最终提交、引用与网页历史刷新已实际通过；这是明确的实例闭环，不能外推全部资料回答准确。
- 原问题/原 Scope 回放：先发生 MODEL_CONTEXT_WINDOW_EXCEEDED，普通 Xing 窗口与旧评测小窗口分离后，候选恢复、生成仍曾 MODEL_UNAVAILABLE。旧失败的具体 transport 原因仍 UNKNOWN。
- 新固定诊断运行 `c1f82e19-bc8f-4aa4-8307-4f025610c8e4`（hello，原会话与 KB/document_scope=[]）实际得到32个向量候选；BGE及Xing成功，Xing输出未通过 UNSUPPORTED_ANSWER 后按既有策略升级到 DeepSeek，后者抛TLS层异常，最终 MODEL_UNAVAILABLE / 0引用。`provider-diagnostic/v1` 的 TLS_HANDSHAKE_FAILED 是 SSLError 类型分类，不能证明异常必然发生在握手阶段。
- 同一容器随后无密钥/无模型调用的默认TLS与TLS1.2连接探测均成功；不能据此声称重现失败已修复，也没有关闭证书校验、切换模型或自动重试。新DeepSeek UNKNOWN5883 Token预留与旧Xing两笔UNKNOWN22980 Token均保留。
- 新KB默认false，实际摄取固定MODEL_ROLE_EGRESS_DENIED；问答在检索前 CLOUD_EGRESS_DISABLED，0模型调用。修复前页面通用失败，现 permission_required DTO在POST/GET相同；原记录保留。
- hello/你好及隐私失败的真实UI两轮回读分类相同，0提交/0模型调用。

原 q0 是问候测试，不是基于文档的事实问题；不能为了通过测试把未经验证的问候候选变成事实答案。当前原失败复现已验证新阶段和原因，原问题成功回答仍未证明。
具体类型与证据索引见 root-cause.json；整体验收尚未通过，不批准延期或发布。
