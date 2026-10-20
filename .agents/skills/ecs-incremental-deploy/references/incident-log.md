# ECS 历史故障库

## 读取方法与可信等级

按症状查ID；所有记录来自已保存历史，本轮未连ECS。`VERIFIED`只覆盖列出的原因/修复范围；`PARTIAL`不能补成确定根因。未发生过的分支仅在runbook中作为诊断方案，不算历史事故。

来源A：[D8发布报告](../../../../docs/deployment/ECS_INCREMENTAL_UPDATE.md)及同名results JSON，提交`501c739305a19ee3f12b847be85591349dc3439b`；原始收据根`E:/RAG quention/var/reports/d8-ecs-incremental-update-20261020/`。

来源B：[S8.3-R报告](../../../../docs/evaluation/S8_3_R_REAL_AB.md)及同名results JSON，交付`86d42a7374f738c996e7804a067b0052291ec997`、运行源码`29ecf2f5d3037eed99a4de783546dee272a761bd`；原始收据根`E:/RAG quention/var/reports/s8-3-r-20261020/`。仓库完整报告永久保留；外部收据不可读时如实降级，不宣称重新核实。

## ECS-001 构建成功但依赖漂移（VERIFIED）

- **阶段/现象**：D8本机构建exit0，验证语义`BLOCKED_ARTIFACT_DIFFERENCE`；certifi由2026.10.18浮到2026.10.19。未部署该镜像，不是ECS停机。
- **直接/根本原因**：pip解析了更新的传递依赖，原构建未维持既有包清单；exit0不能证明工件身份。
- **失败尝试**：第一镜像完成构建/import仍被依赖检查阻止；保留`-dependency-drift`标签。
- **有效修复**：该次使用一次构建层`pip install --no-deps certifi==2026.10.18`恢复当时已部署依赖，全量实际包diff为空、508源码匹配后接受。**不是未来固定certifi版本的指令**。
- **识别→动作→后继/停止**：比较实际package manifest与批准目标；无依赖变化却diff非空→阻止切换、修构建输入；无法证明包/平台兼容则STOP，不在ECS升级依赖试错。
- **预防/证据**：发布清单同时记进程exit和语义status。来源A的`images.dependency_note/local_verification`；`local-image-verification.json`与`local-image-stable-verification.json`，第一失败和后续通过均保留。

## ECS-002 旧参考计数导致数据保护误报（VERIFIED）

- **阶段/现象**：D8 `remote_data_before.py` exit2/`REVIEW_REQUIRED`，物理数和ready数不匹配旧参考。
- **直接/根本原因**：此前已批准来源对齐新增28文档/657向量、停用52旧版本/474旧向量；混用了历史准备池、物理总数、当前启用版本三个口径。
- **失败尝试**：旧参考检查原件保留，未改旧JSON或把它改写成PASS。
- **有效修复**：按现有ID/版本证据解释11708+28=11736、28000+657=28657及历史池ready11683-27=11656；冻结本次实际before→after，14项保护通过。没有删数据或新建索引，补充exact-ID SQL是NOT_RUN而非已执行。
- **识别→动作→后继/停止**：计数变化先查批准的来源/版本变更，比较同一次发布的实际before；无法解释→STOP，不改期望值凑PASS。
- **预防/证据**：数据基线包含口径和对齐版本。来源A `data_protection.historical_reference_reconciliation`、`data_protection.checks`；原`data-before.receipt.json`、`data-before.stdout.json`。

## ECS-003 将静默Ready检查当JSON（VERIFIED）

- **阶段/现象**：S8.3-R预检exit1，`JSONDecodeError`，stdout为空，Provider0。
- **直接/根本原因**：正式`python -m scripts.s8_ecs_runtime check`成功只返回状态码；工具未经合同核对就json.loads(stdout)。
- **失败尝试/修复**：保留初次traceback；改读现有鉴权`/readyz`结构合同，预检`READY_ZERO_SEND`/exit0。
- **识别→动作→后继/停止**：先查CLI实际输出约定；静默检查读exit，需结构身份才读鉴权Ready；解析/身份仍未知则停切换，不伪造空JSON。
- **预防/证据**：来源B §4；`preflight.receipt.json`/`preflight.stderr.log`与`preflight-fixed.receipt.json`；`scripts/s8_ecs_runtime.py::check/main`。

## ECS-004 完整镜像传输超时（VERIFIED，链路根因UNKNOWN）

- **阶段/现象**：144MB级归档SCP超180秒，部分文件保留；没有load不完整包。
- **直接/根本原因**：规定传输窗口内未完成；带宽/SSH/云盘等底层成因未单独测量，不能写成“网络故障已定位”。
- **有效修复**：仅两份合法源码变化，已有远端底座与依赖可核实，改传2文件和manifest离线delta build；两地508文件/依赖吻合后切换，记录不同Image ID。
- **识别→动作→后继/停止**：先看退出码和两端大小/SHA；保留部分包；基础镜像及依赖一致才走delta。否则修传输或报告阻断，不能将任意源码COPY到未知镜像。
- **预防/证据**：来源B §1/4；`root_transfer_release.py`的180秒上限、`transfer-delta.json`、`remote-image.stdout.json`、`application-update.stdout.json`。

## ECS-005 SSE已完成却被工具误报（VERIFIED，非模型失败）

- **阶段/现象**：首个MIRACL A HTTP200、SSEcomplete、PG/历史一致、4调用SETTLED/7941Token，但工具报`S8_3_WIRE_RESPONSE_INCOMPLETE`。
- **直接/根本原因**：解析到最终`[DONE]`后停止读取，未读socket EOF；诊断把协议完成与传输EOF混为一谈。
- **有效修复**：仅对HTTP200/SETTLED/实际usage匹配/finish_reason stop/最终DONE合同放行；只读回查原request_id的SSE、PG、历史及来源，恢复同一次结果，不补POST。原FAIL/STOP保留。
- **识别→动作→后继/停止**：原问题已提交且汇总失败→先只读核对；缺usage/终态或UNKNOWN不能放行。不得换request_id重试。
- **预防/证据**：来源B §4；`reviewer-result-firstA.json`、`first-recovery.receipt.json`及下载包原/恢复结果。该合同仅适用相应流协议，不把任何`[DONE]`字符串当成功。

## ECS-006 恢复工具把存在当运行，且重复输出名（VERIFIED）

- **阶段/现象**：保留的已停止容器在`docker ps -a`中，被误认为active；之后固定输出文件名碰撞，均为零POST工具失败。
- **原因**：存在状态与执行活性混淆；每次包装器输出未独立命名。这是两个工具条件，不是新模型UNKNOWN。
- **有效修复**：用`docker ps`/`.State.Running`核对当前活性；每次唯一文件名保留零POST失败，仅续未提交案例。保留的已完成B不重发。
- **识别→动作→后继/停止**：先查原claim/账本/实际运行状态，再判可否继续；状态或文件所有权不明STOP。
- **证据**：来源B §4；`zero-post-wrapper-preserved.stdout.json`、`zero-post-recovery.stdout.json`、parent进度历史；`remote_s8_3_r_case_controller.py`。

## ECS-007 内存指标落盘丢失（缺失VERIFIED，逐次信号根因PARTIAL）

- **阶段/现象**：12/12无memory.json，谷值/峰值NOT_RECORDED；1GiB只证明限额，候选快照无OOMKilled。
- **原因**：旧工具只在serve外层finally落盘；Uvicorn0.35.0重发捕获信号可导致外层finally前退出，但历史每次信号未记录，具体机制不能逐次确认。
- **失败/修复等级**：历史数据不能补回；改为存活期fsync日志＋原子快照，正常退出/native terminate等11项**离线**测试通过；未重跑真实问题，未证明真实ECS采样收益。
- **识别→动作→后继/停止**：需要资源验收先确认周期性样本存在；缺失写NOT_RECORDED，不从服务健康/限额推断峰值或候选无OOM，不重开关闭授权补指标。
- **证据**：来源B §3；仓库`docs/evaluation/evidence/s8-3-r-20261020/tools-offline-verification-memory-checkpoint.json`与helper原SHA。

## 非事故观察：两地Image ID不同

S8.3-R两地508文件和包版本相同但Image ID不同，未观察到因此发生兼容失败。构建元数据差异为报告解释，不等于镜像逐字节相同。保留两者并证明平台、底座、源码、依赖、有效配置和模块加载；不要把“不同ID”计成已证实停机，也不要只凭manifest等价泛化到所有可执行层。

## 新事件模板

`ID / REAL或SIMULATED / 阶段 / 现象 / 直接原因 / 根本原因 / VERIFIED|PARTIAL|UNKNOWN / 失败尝试 / 有效修复及验证等级 / 识别条件 / 预防 / 命令与exit/语义status / 脱敏证据路径或SHA / 关联既有规则 / 是否需要规则变更`。

新证据与旧案例前提不同先调查，不机械套用。模拟案例单独标SIM，不混入以上7类历史计数；没有修复验证就不能写“已解决”。
