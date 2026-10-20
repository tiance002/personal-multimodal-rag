# ECS 增量部署专属 Skill：建立与离线验证

## 结论

**OFFLINE_SKILL_READY**：项目级Skill、历史故障库、运行手册、项目触发规则和Darwin有限验证已完成。本轮ECS连接/写入、Docker部署、项目Provider调用及Token均为0；不把离线模拟写成真实部署通过。

活动SKILL.md保留首版，故障库仅补模拟入档，候选扩写未采用。三类基础场景带Skill/不带Skill均3/3通过；追加的并发工作树反例两版也都通过。规范配对复评为 **1票候选较好、1票较差、1票持平，均slight**，不足以证明实际处理收益；只保留新反例、历史事件和候选差异，不为评分扩写主文件。

## 1. 实际版本、改动与隔离

- 起点：`86d42a7374f738c996e7804a067b0052291ec997`。
- 首版Skill提交：`ea20815240f1a7ecd4c76c44dd1eb24b7fdf6ba5`，只有7个Skill文件和2个AGENTS文件。
- 最终工作树：`E:/RAG-Worktrees/ecs-incremental-deploy-skill-20261020`；分支`codex/ecs-deploy-skill-20261020-isolated`。
- 交付提交查询：`git log -1 --format=%H -- docs/deployment/ECS_INCREMENTAL_DEPLOY_SKILL.md`；最终消息给出完整SHA。

首次编写时共享工作树被并行fallback任务切换分支，并新增其合法未提交文件。首版提交仅包含本任务9文件；随后从该提交建立独立工作树继续。未纳入/回退并行后端文件、未切回其分支、未reset/clean/stash或合并。这个真实**本机Skill编写事件**单独留档，不计为ECS事故。当前独立分支相对起点无backend/frontend/deploy/scripts变更。

| 路径 | 用途 |
|---|---|
| `.agents/skills/ecs-incremental-deploy/SKILL.md` | 高频范围判断、发布顺序、故障分支、数据/权限/回滚边界 |
| `references/deployment-runbook.md` | 已核实项目入口、Windows/Linux命令模板、阶段合同与回滚区别 |
| `references/incident-log.md` | 7类真实历史故障、证据等级、识别与处理分支；另列非故障观察 |
| `references/evolution.md` | 故障解决后总结、单因素候选、独立行为/配对验证、拒绝/保留与Owner检查点 |
| `test-prompts.json` | 3个原任务类别＋1个并发回归；明确模拟、权限和预期行为 |
| `agents/openai.yaml` | 中文UI元数据，`allow_implicit_invocation=true` |
| `AGENTS.md` | 项目ECS任务触发入口；原指令保留 |
| `AGENTS.override.md` | 最小转引，避免override优先时漏读项目触发规则 |
| `docs/deployment/ecs-skill/` | 真实独立测试产物、Darwin原始/有效复评、TSV、未采用候选/diff及结果卡片 |

## 2. 历史复盘

优先复用D8及S8.3-R报告、JSON和选定原始收据；读取实际Git历史、Compose/Dockerfile、正式runtime入口、README、AGENTS、progress相关项及ADR索引。详细定位在Skill故障库，不重新连接历史环境或重跑RAG。

| ID | 故障/原因 | 已证明的修复与限制 |
|---|---|---|
| ECS-001 | 构建exit0但certifi传递依赖漂移 | 恢复当时批准依赖、包/源码manifest一致后接受；不将历史pin写成永久规则 |
| ECS-002 | 旧物理/ready计数基线导致REVIEW_REQUIRED | 复用来源对齐ID/版本解释变化，比较同次发布before/after；没有删除或重建数据 |
| ECS-003 | 静默check被json.loads，JSONDecodeError | 正式check读exit；结构身份使用鉴权Ready；预检修复后零发送通过 |
| ECS-004 | 完整镜像SCP180秒超时 | 在底座/依赖可证明一致时，转2文件manifest＋离线delta build；底层链路原因UNKNOWN |
| ECS-005 | 完成SSE被工具误判为wire不完整 | 协议终态、usage/账本、PG/历史一致只读恢复，保留初次FAIL，未重POST |
| ECS-006 | stopped容器被当active；包装器输出名冲突 | 实际运行状态与唯一输出分开核实，仅续未提交操作；两项零发送工具条件合并归档 |
| ECS-007 | 12次资源采样未落盘 | 缺失已确认；工具周期性持久化修复仅离线11测试通过。逐次信号根因PARTIAL，不补造历史谷值 |

共7类真实故障：6类有对应真实范围的结果恢复证据；内存1类只证明缺失及后续离线工具修复。另有两地Image ID不同但源码/包匹配的观察，**不计故障**，也不冒称镜像逐字节一致。盘点有界于相关D8/S8.3-R，不宣称覆盖所有历史。原始英文研究笔记保留作来源，错误/陈旧路径以整理后的故障库和实际仓库为准。

## 3. 核心运行原则

1. 先证明目标主机、工作树/提交、完整有效Compose、实际镜像/挂载、PG物理身份、Redis范围与回滚材料，再决定更新服务。
2. 按文件和共享合同确定API/Worker影响；共用镜像不等于必须一起重启。未变化的前端/PG/Redis/索引不动，停着的Worker不默认启动。
3. 同源码/配置已有验证直接复用，构建既看exit也看语义status；平台、包版本、源码manifest、实际模块路径/SHA独立核对。
4. 传输失败保留部分工件；delta不是无条件捷径，必须证明底座/依赖及完整目标内容一致。标签、归档SHA、Image ID、revision不是同一身份。
5. 每次只切换批准应用服务，核对无在途任务/发送；旧UNKNOWN不可释放/重发。真实问答另有有效许可，部署Skill不授予模型预算。
6. 应用镜像、配置、Git和数据库恢复是不同操作。普通发布不重建数据，不用down/prune/reset当排障；Schema/数据/索引操作需独立方案和Owner确认。
7. 运行healthy不等于业务通过；结果分别记录工程/零云/真实问答/回滚等级。缺指标NOT_RECORDED，不用理论代替事实。

## 4. Darwin执行与选择

实际安装`darwin-skill`声明v2.1/2026-06-10；SKILL与template SHA见JSON。没有安装、更新或改写任何全局Skill及全局results.tsv，项目演化记录位于`ecs-skill/evolution/results.tsv`。

- 基线由独立代理按实际9维评分，91.3/100只作triage；不是经校准质量测量，不证明部署更快。
- 3个测试类别来自本次Owner合同，实际由新上下文代理完成了具体离线计划、诊断和事件记录，不是主代理自评；ECS动作仍为SIMULATED。
- 单一候选只增加并发目录/branch/HEAD的阶段性重查，约增长4.96%；其触发来自本次真实本机事件，另冻结T4反例。
- 3名盲配对评审在各次调用中读两版及同样输出。首次提示只给权重而未给维度名，部分评审使用了错误维度/绝对标度；原件保留并标SUPERSEDED。**有效复评**补齐安装版9个维度，全部只作相对比较，没有伪造候选绝对分。
- 有效票：1better/1worse/1tie，均slight，两版四题都PASS。因此活动Skill仍是基线；候选只作为未采用证据保存，未替换活动文件，无需实际回滚。
- 本轮使用比Darwin“非劣可保留”更严格的收益门：没有可证行为改善不扩写，遵从Owner要求。没有加5名judge寻胜，也未凑3轮改写。初始创建/有限离线测试属于本任务授权；重要未来权限/数据/运行规则改变仍留Owner检查点。本轮结束交Owner审阅。

未来循环为：完成故障恢复→脱敏留原证据→判已有规则是否覆盖→只补案例或提出一个候选→相同fixture独立输出→3名同-judge配对→真实可执行收益/无新增风险→保留或拒绝→本地版本与Owner检查点。它是任务结束时执行的代理规则，**不是后台守护进程或无人调用的定时自改**。

## 5. 验证与真实限制

| 验证 | 实际方法/输出 | 结果 |
|---|---|---|
| Skill格式 | 安装版`quick_validate.py`，`-X utf8` | exit0：Skill is valid |
| 资源合同 | 本轮validator：YAML/本地链接/冻结测试/原生发现收据/业务源码无改/凭据模式 | exit0，7项检查、6个本地链接 |
| 原生发现 | codex-cli0.159.2新app-server上下文，initialize→skills/list | exit0，scope=repo、enabled=true，模型回合0 |
| 新上下文使用 | 仅给自然ECS请求和项目路径，按AGENTS发现读取Skill | 路由PASS；其Git权限受限如实停止详细范围判断，不猜提交 |
| 最小更新 | T1 API-only，带/不带Skill实际离线计划 | 均PASS，选API，不重建数据库/索引或无关服务 |
| 版本异常 | T2 healthy＋镜像正确＋实际module SHA旧＋精确bind mount | 均PASS，定位覆盖层而非整栈重建 |
| 经验沉淀 | T3合成中间exit1被末条exit0掩盖 | 均PASS，填事件并判现有规则覆盖，只补案例 |
| 并发回归 | T4分支/HEAD漂移、其他任务dirty | 基线/候选均PASS，停止共享目录写入并计划隔离 |
| 视觉结果卡 | 既有本地Edge/Playwright，内联HTML、不取网络资产 | exit0，PNG已查看，无缺字/裁切；不属业务浏览器验收 |
| 真ECS发布/修复/回滚 | 本轮明确禁止 | NOT RUN |

原生扫描使用项目路径和隔离临时CODEX_HOME，不改全局配置。它证明安装版发现机制读取了repo Skill，不证明当前聊天侧栏已热重载。当前聊天cwd`E:/RAG quention`与新工作树是兄弟路径；**在目标项目目录打开Codex**才应自动发现项目Skill。必要时显式读取路径；没有出现在选择器时重启客户端。依据[官方Skill文档](https://learn.chatgpt.com/docs/build-skills)，发现不等于部署授权。

实际本机命令（路径含空格时用PowerShell调用符和引号）：

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -X utf8 'C:/Users/22088/.codex/skills/.system/skill-creator/scripts/quick_validate.py' 'E:/RAG-Worktrees/ecs-incremental-deploy-skill-20261020/.agents/skills/ecs-incremental-deploy'
& 'E:/RAG quention/.venv/Scripts/python.exe' -B 'E:/RAG quention/var/reports/ecs-skill-20261020/probe_native_discovery.py'
& 'E:/RAG quention/.venv/Scripts/python.exe' -B 'E:/RAG quention/var/reports/ecs-skill-20261020/validate_local_artifacts.py'
& 'E:/RAG quention/.venv/Scripts/python.exe' -B 'E:/RAG quention/var/reports/ecs-skill-20261020/render_result_card.py'
git diff --check
git diff --cached --check
```

前三个脚本/格式检查及渲染均实际exit0；最终Git检查和本地提交回执随交付保留。行为代理文件均完成实际JSON解析exit0；不存在的命令没有报告通过。本机有一次测试清单写入脚本使用了错误相对cwd，FileNotFound且未写入；改绝对路径后继续，未涉及ECS或模型。前述评审规范错误原件也保留，未篡改为正确。

## 6. 使用方式与边界

在本项目工作树中可以直接说：

> 使用 $ecs-incremental-deploy，把本地已提交的 API 改动增量更新到 ECS。先核对运行版本、有效配置和回滚入口，只更新受影响服务；本次不授权模型问答。

或说“ECS API健康但运行旧代码，按项目部署Skill先只读定位”。未来发现新故障先完成用户任务，解决后按Skill自动整理经验；不是每次都强制改Skill。数据库/索引/Schema、重大规则变化及新云调用仍单独授权；本轮没有部署任何东西。

主要剩余限制：模拟计划不能证明真实部署提速；历史目录/镜像/计数仍需当前核实；资源缺失的历史根因只能PARTIAL；没有触发实战回滚；当前UI热重载未实测。Skill已具备可调用的项目入口，这些限制不伪装成真实发布PASS。

详见机器结果`ECS_INCREMENTAL_DEPLOY_SKILL.results.json`，原始评估在`ecs-skill/evidence/`，候选diff与TSV在`ecs-skill/evolution/`。只本地提交，不Push、不操作ECS生产环境。
