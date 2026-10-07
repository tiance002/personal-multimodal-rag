# 产物归属与保护规范

本规范用于导航，不授权移动、删除、重导入、重建索引或清理环境。本次整理仅新增文档入口，保留所有旧成果。

| 类型 | 项目相对路径或对象 | 保护规则 |
|---|---|---|
| 产品源码与契约 | `backend/app`、`frontend/src`、`eval_center`、`alembic`、`contracts`、`deploy` | 保留包路径、迁移顺序、接口与安全保护；未跟踪源码不是垃圾 |
| 开发状态与计划 | `progress.md`、`task_plan.md`、`.planning`、`docs/superpowers` | 保留阶段原文，新增索引区分当前与历史，不覆盖旧执行记录 |
| 运行数据与引用 | `var/local-first/storage/objects`、`answer-audit`、`var/storage`及真实DB | CAS、文档版本、引用和回答审计原位保留；空目录或被ignore不等于无用 |
| 题库与fixture | `var/local-first/real-material-eval.jsonl`、`evaluations`、测试fixtures | 保留版本、身份和消费路径；导航更新不能顺带重导入资料 |
| 冻结质量证据 | `var/reports/real-material-quality-baseline`、`reference-sanity-20260930`、`real-materials-20260930`、`local-v1.1-hardening` | gold、checkpoint、human overlay、源码清单与旧结果不得覆盖或改名破坏回读 |
| 秘密与用量状态 | `.env`、provider凭据、服务配置、账本/attempt/receipt | 不复制到公开文档，不读取其内容作整理，不修改或按未知=零清理；模板与真实秘密分开 |
| 模型与外部依赖 | 模型/embedding/OCR资产、外部挂载、`frontend/node_modules`链接 | 不遍历盘符或跟随链接，不哈希整个模型存储，不删链接目标 |
| 运行/构建文件 | PID、日志、`frontend/dist`、缓存目录 | 活跃归属未核实时保留；不按“可再生”标签直接移除 |
| 自动测试报告 | 根 `output.json`、`pytest_html_report.html`、`archive` | 原位保留；旧报告无前哈希，完全未变状态UNKNOWN。报告插件副作用见测试说明 |
| 独立任务证据 | 本轮盘点、备份、diff、hash manifest、封存真实评测与WeKnora评审 | 本机位置见独立交付说明；不把原始资料、敏感映射或账本内容放入仓库 |

`.gitignore`中的ignore只是版本控制行为，**不是可删除许可**。尤其 `var/local-first/storage`、题库和审计不能按“var其他残留”的概括处理。现有ignore保护规则本轮不改。

## 后续归档须单独审核

少量自动HTML/旧报告只能先列候选；建立来源、消费者、复制校验、原路径恢复映射后，才讨论获授权的可恢复归档。不得直接移动或删除旧报告，不调整权限让曾被拒的移动成功。缓存读取被拒时跳过；不追查或重试拒绝目录。

原始资料的冻结版本不一定等于当前README/设计文档字节。新增导航不能替换CAS、更新gold或重建已有索引。既有最终生成器对照不等同端到端测试，旧指标和历史阶段报告都保留原义。

## 不属于整理动作

不使用gitclean/reset/stash/checkout，不批量删未跟踪项，不动其他工作树或教学snapshot，不改包路径、大重构或削弱保护。平台已拒绝的OID/预算清理仍暂停；不通过本次整理重试，也不修改Docker权限。

具体当前边界见 [SOURCE-STATE](SOURCE-STATE.md) 和 [KNOWN-ISSUES](KNOWN-ISSUES.md)；入口见 [文档索引](README.md)。
