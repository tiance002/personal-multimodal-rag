# 2026-10-13 暂停复核交接

按负责人本轮要求停止实施，等待问题与方案复核。完整 Goal 未完成，不能标记验收通过。本文是暂停时状态；历史证据见 [LIVE_ACCEPTANCE_20261013.md](LIVE_ACCEPTANCE_20261013.md)，不要将历史中间状态当作当前状态。

## 1. 已完成和当前版本

- 普通输出档位 1024/2048/4096、助手侧执行进度/失败诊断、SSE 恢复已有实现。动态路由评分、外发授权、预算账本、历史身份、答案验证及引用原子提交保留。
- 原问题 Run `171ca042-996e-4e7e-8edb-9516ebd142b2`（2048）和短题 Run `cc79fb74-4721-4910-8e52-5ed614d5ba35`（1024）：真实 PASS，各 4 条引用，HTTP/PG Scope、版本及片段哈希回读一致；点击引用、刷新恢复、SSE 游标恢复有证据。
- 当前 HEAD `e6848a92430688240064399ac05c3a4cfd77b1b9`：新增纯 SSE 累加器及测试，尚未部署。G3 客户端修复提交 `02190463662932d32ee161390b0d1c3370a8c5e7`。
- 实际 API/worker 镜像 `personal-rag:weknora-unit-guard-20261013`，镜像身份 `sha256:a94a1696a6e41bd7b15b502607d4d9e605428b29ee26fb8914b2f9fdf3dcb82c`，后端源码 `ce3398a`。普通调用仍为非流式，服务等待 120 秒，客户端默认等待 300 秒。
- V4 任务额度门仍生效；最新 UNKNOWN 未确认继续，因此阻止新模型调用。配置就绪不等于可以继续问答。原数据库、存储、迁移 0024、配置 revision `bcd271c7-e820-45d4-9910-039384d268cd` 保留。

## 2. 当前失败和已知原因

长题 4096 未通过。90 秒 Run `85eb1ecd-6600-4916-ac0b-7372afa99a6d` 和 120 秒 Run `9043e892-2f0a-423c-a54a-488f20c43d06` 均为 Xing REQUEST_TIMEOUT / MODEL_UNAVAILABLE。最新实际等待约 122.63 秒，提供商调用耗时 120322ms。未收到有效响应、usage、finish reason 或可提交答案；没有继续调用 DeepSeek。不能推断服务端排队、计费或生成成功。

此前额度预留偏紧造成的 MODEL_BUDGET_DENIED 与这两次超时是不同问题。当前额度充足：保守完整预留 189408/350000，余量 160592；已知用量 43321，UNKNOWN 53915，二者占用合计 97236。完整预留不是实际消费。UNKNOWN 不释放、旧 Run 不重发。

7 次探针和 7 次网页尝试均已使用。新增 UNKNOWN 仍触发停止条件；不能把扩大额度理解为允许无限重试。G4 长题 FAIL，G5 冗余清理 NOT RUN，仅有待清理清单。

## 3. 待负责人复核的解决方法

建议先完成内部流式接收候选：供应商 SSE 分块只在后端累加，获得完整终态与实际 usage 后，继续经过原答案验证和引用原子提交；不提前向用户展示未验证答案。缺终态、usage 或协议不符仍保守失败并保留占用。新流式协议使用独立能力证明，不能直接沿用非流式证明。

这是针对长响应传输风险的候选，不是已经证明的超时根因修复。继续时先做 Linux 原生计数/传输及关联不变量回归，再构建候选；源码验证完成后，报告新的公开能力探针和长题回放具体次数、上限、服务替换方案，等待批准。不得直接重试旧 Run。

备选是先向模型服务侧查超时原因，或经负责人决定更换 cheap 角色模型并重新验证容量、协议和实际身份。单纯再延长等待没有成功证据，120 秒已失败，不建议把它作为默认解决方法。

## 4. 尚未完成的逐文件候选

以下 7 个文件已作为暂停检查点提交至本地 `main`，仍未验证、未部署，保留供后续继续；不得报告功能通过：

| 文件 | 候选职责 |
| --- | --- |
| `backend/app/adapters/models/cloud.py` | 普通调用内部 SSE 接收、绝对截止时间、终态解析；保留旧冻结路径 |
| `backend/app/adapters/models/service_capacity.py` | 流式容量协议与计数/能力证明绑定 |
| `backend/app/adapters/models/service_configuration.py` | 按内部开关装配流式计数器 |
| `backend/app/config.py` | 默认关闭的内部流式开关 |
| `backend/app/ports/output_capability.py` | 独立 v4 流式证明及固定公开探针原始响应捕获 |
| `backend/app/adapters/models/ordinary_stream_capacity.py`（未跟踪） | 复用固定模型 prompt framing 的流式计数包装 |
| `backend/tests/test_ordinary_stream_integration.py`（未跟踪） | 传输/协议/终态/取消/证明边界模拟测试 |

已提交的 `backend/app/ports/ordinary_chat_stream.py` 和 `backend/tests/test_ordinary_chat_stream.py` 只证明纯解析器，不证明真实提供商流式可用。当前候选 Linux socket/read1 行为、原生 DeepSeek framing、完整关联回归、候选镜像及真实流式请求均 NOT RUN。

## 5. 已执行验证与证据边界

从仓库根目录执行：

```powershell
.venv\Scripts\python.exe -m pytest --noconftest backend/tests/test_ordinary_chat_stream.py -q --disable-warnings
.venv\Scripts\python.exe -m pytest --noconftest backend/tests/test_ordinary_stream_integration.py backend/tests/test_ordinary_chat_stream.py -q --disable-warnings
```

分别 exit 0：47 passed；56 passed、1 skipped。均为 Windows 离线/模拟验证。跳过项为本机未安装 deepseek-recipe 的原生 framing 验证，不算通过。初次组合测试 exit 1（6 failed、51 passed），修复后才得到后一结果。

从 `frontend` 执行（使用已安装 Chrome）：

```powershell
$env:PLAYWRIGHT_CHROME_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'
.\node_modules\.bin\playwright.cmd test tests/run-progress.spec.ts tests/sse.spec.ts --reporter=list
npm.cmd run build
```

均 exit 0：19 项模拟浏览器测试通过，TypeScript/Vite 构建通过。对应 G3 已提交源码，不是新流式候选的真实验收。此前单位提示 70 项回归 exit 0、V4 Linux 14 项测试 exit 0 见主报告。全局契约仍 FAIL（31 项基线分层违规、0 新增），不称全套通过。

真实网页收集器 exit 0 只代表采集成功：两次成功题功能 PASS；最新长题功能 FAIL。最新任务回读 exit 1 是 NEW_UNKNOWN_STOP。引用/恢复回读 exit 0，均只读、无新模型调用。

本轮只做状态回读和交接文档，没有新模型请求、部署、数据库操作、代码修复、Push、Tag 或发布。暂停后下一步由负责人复核方案决定。
