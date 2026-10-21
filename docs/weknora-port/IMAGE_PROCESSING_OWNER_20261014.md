# 图片处理独立分支交付（2026-10-14）

## 范围和固定参照

- 接手提交：`b961e7a7513a97b08b993c4aa0bbf892576af4d1`；分支／工作树：`codex/weknora-image-20261014`、`E:\WK\image`。
- 上游唯一对照：`3e8b0bfc80b845b2d4b2ed683994748741450a97`。没有切换为最新分支；没有改回答路由阈值、Cheap／Expensive 规则、React 或记忆队列。
- 上游原行为：`knowledge_process.go` 在图片任务使用 `QueueMultimodal`、`MaxRetry(3)`、30 分钟超时；`image_multimodal.go` 的 `Handle` 分别调用 OCR 与 Caption 提示词，但两次共用 `resolveVLM` 的同一模型；`GetVLMModel` 只加载租户模型，不证明视觉效果；Lite 执行器是内存队列。
- Owner 批准的适配：OCR 明确选本地 Tesseract 或目录登记的 VLM；Caption 明确选视觉模型，或在 OCR 是已校验 VLM 时继承。Tesseract 不会继承成 Caption。登记 `vision_user_declared=true` 的自定义 API 能力显示 `USER_DECLARED_UNTESTED`，绝不显示实测；已知文本模型不能靠勾选升级。普通文字连接测试不构成视觉质量认证。

## 配置和入口

知识库 `PATCH /api/v1/knowledge-bases/{id}/image-processing` 支持 `enable_multimodal`、`ocr_adapter`（`tesseract` / `vlm`）、`ocr_model_id`、`caption_mode`（`follow_ocr` / `explicit`）、`caption_model_id`、`description_language`、`custom_instructions`。正式上传 `POST /api/v1/knowledge-bases/{id}/documents` 的可选表单字段 `process_overrides` 是 JSON 对象。空值不覆盖知识库有效值；上传指定 `caption_model_id` 会选显式模式，上传指定 `follow_ocr` 会清除知识库 Caption 模型。旧 `vlm_config.model_id` 仅在未填 OCR 模型时作为兼容来源。显式模型失效不会自动继承 OCR。

`POST /api/v1/settings/vision-models` 使用现有模型表、供应商适配器和加密凭据存储。任务仅保存模型 ID、配置版本／来源、文档版本、状态和结果，不保存密钥。模型目录能力、适配器图片请求能力、租户可见性和启用状态在选择时校验，执行前重查。没有视觉自动探测或真实模型调用。

图片及含扫描页的 PDF 从正式上传写入独立 `port_image_tasks` 后由后台处理。OCR 与 Caption 各自记录结果、错误和图片位置；有效输出进入普通图片子切块、索引和引用回读。图片子切块的原文回读须文档完成且切块版本等于当前文档版本。任务状态可用 `GET /api/v1/knowledge-bases/{kb}/documents/{doc}/image-task` 查看，并可用对应 `/cancel` 取消。任务重试上限遵照固定源码 3 次重试（最多 4 次领取）；30 分钟租约加心跳。送出状态未确定时标记 `unknown`，不通过恢复重新请求。重复领取有 claim token 栅栏；最终提交同时核对文档版本、删除状态、租户／知识库及任务 claim。图片任务不复用 `RedisMemoryExtractionQueue` 的记忆 payload 或 max_retry=2。

## 上游函数映射

| 固定上游 | Python 适配 | 说明 |
| --- | --- | --- |
| `frontend/src/components/modelSelectorFilter.ts`、`internal/types/model.go SupportsVision` | `model_registry.py`、`image_configuration.py`、`storage/models.py` | 复用目录视觉输入信息；自定义声明单独标未实测 |
| `CreateModel`／`UpdateModel`／`ValidateRow`／`GetVLMModel` | 现有模型表／`ModelStore.resolve`、`runtime.register_vision_model` | 复用租户模型 ID、启用状态及适配器，新增图片绑定校验 |
| `image_multimodal.go resolveVLM` | `effective_image_config`、`resolve_image_selection` | KB 默认和上传覆盖；OCR／Caption 分离为 Owner 批准差异 |
| `image_multimodal.go Handle`／`models/vlm Predict` | `SourceImageProcessor.process_plan`、现有 `ProviderModel.vision` | 两个独立提示词及调用，保留 HTML／表格 OCR 转换与正常索引路径 |
| `knowledge_process.go` 图片入队／最终收口 | `documents.py`、`image_tasks.py`、迁移 `010_image_tasks.sql` | 持久领取、重试／恢复、孤立处理、竞争栅栏；不是复用记忆队列 |

## 验证证据与限制

新逻辑数据库：`wk_image_20261014`，只连接隔离端口 56423；Redis 使用 56424 且每次测试使用唯一 `wk-image:20261014:<uuid>` 前缀；资产写到 `E:\WK\run\image-*`。运行前核对了容器身份与端口健康。模型响应全部来自 `httpx.MockTransport`；真实供应商请求 0，未读取密钥正文。真实 Tesseract `eng`／`chi_sim` 数据文件存在，本地合成图 OCR 得到 `OCR TEST 1234`。隔离 HTTP 验证了 PNG、混合扫描 PDF、显式模型、继承模型、上传覆盖、禁用模型、原件及切块回读；任务测试验证领取、4 次上限、租约恢复、UNKNOWN、孤立、重复提交、删除、版本变更、取消与 owner 栅栏。独立单测覆盖局部失败、空输出及执行期撤销。

最终定向离线命令 `python -B -m pytest tests/weknora_port/test_image_configuration.py tests/weknora_port/test_image_processing.py tests/weknora_port/test_image_multimodal.py tests/weknora_port/test_documents.py tests/weknora_port/test_provider_transport.py tests/weknora_port/test_model_store.py tests/weknora_port/test_model_configuration.py tests/weknora_port/test_http_api.py tests/weknora_port/test_http_consent.py -q -p no:cacheprovider --tb=short --junitxml=docs/weknora-port/evidence/IMAGE_OFFLINE_20261014.junit.xml`：退出码 0，76 passed。

隔离服务命令（设置 `WEKNORA_IMAGE_TEST_DSN` 为上述新逻辑库，`PYTHONPATH` 指向既有 PDF 运行依赖）：`python -B -m pytest tests/weknora_port/test_image_tasks_pg.py -q -p no:cacheprovider --tb=short --junitxml=docs/weknora-port/evidence/IMAGE_ISOLATED_PG_REDIS_20261014.junit.xml`：退出码 0，4 passed、5 个 PyMuPDF SWIG deprecation warnings。`git diff --check`、新模块 import 和受影响 Python 模块 compileall 均退出码 0。模拟响应验证配置和请求次数，不构成模型质量认证。旧 `test_runtime_pg.py` 对同步图片上传及未登记的 Qwen2.5-VL 的断言保留为历史证据；此旧整合脚本未在本任务隔离数据库上执行，不将其声明通过。未做真实供应商调用、整批语料评测、部署、Push 或协调者合并。

## 协调者串行整合顺序

先审阅图片专属模块、模型目录／适配器的最小共享改动，再审阅 `documents.py`、`runtime.py`、`http_api.py` 正式入口，随后应用独立迁移 `010_image_tasks.sql` 和 `image_tasks_migrate.py`，最后运行隔离数据库／Redis 与 MockTransport 验收。共享修改仅在本分支：`documents.py`、`runtime.py`、`http_api.py`、`model_registry.py`、`models/provider.py`、`storage/models.py`；无依赖锁变更。迁移必须显式执行，应用启动或 import 不会暗中迁移。
