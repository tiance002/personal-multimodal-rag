# S8.6.3 固定证据包

本目录保存四个冻结请求，供后续在授权条件满足后做同一证据输入的 Cheap/Expensive 对照。`manifest.json` 列出每个 case 的角色、请求文件、原始 body 与 messages 的 SHA-256，以及历史请求记录。四例为 **D_FAILURE 2 例、CORRECT_CONTROL 2 例**。

## 离线检查与渲染

在仓库根目录、已安装项目依赖的 Python 环境中运行：

```powershell
python -m scripts.s8_6_3_fixed_evidence docs/evaluation/evidence/s8_6_3/fixed_evidence
```

脚本逐例读取 manifest 指定的 `requests/*.json`，检查请求 body 和 messages 的 SHA-256、允许的参数集合及固定输入契约。契约要求原始 messages 不变、`max_tokens=2048`、`enable_thinking=false`。随后使用现有 `ProviderModel` 将同一组 messages/options 分别渲染成 Cheap 与 Expensive 请求体，并再次核对 messages 哈希、输出上限和 thinking 关闭状态。成功输出标为 `OFFLINE_ONLY` / `OFFLINE_RENDER_PASS`，且 `provider_calls=0`。这只证明本地读取、哈希核对和请求体渲染通过，不代表请求已发送或模型结果有效。

Python API `load_inputs(root, case_id)` 返回 `(messages, options, case)`；`render_pair(root, case_id, models)` 接收已解析的 Cheap/Expensive `ProviderModel` 实例，纯本地返回两份请求体。输入路径越出 `requests/`、哈希不符、出现不支持参数或固定契约改变时会报错。

## 隔离与身份边界

`scoring_only.json` 是隔离的评分材料；该脚本不读取它，也不将评分内容传入模型请求。两个 CORRECT_CONTROL 保留历史原始 wire，其中可能包含旧的 composite ID；它们是对照输入，不能当作已修复身份逻辑的新 HTTP 证明。

本包没有实现实时 Provider 调用，也没有提供授权、grant、账本或发送流程。任何未来获准的实时作业都必须在独立授权下，接入项目现有 `Registry`、`SendGate`、`LocalExecution` 和后台 runner，并使用新的授权及新生成的 request ID；不得复用 manifest 中的历史请求 ID 或历史 grant。这里记录的 `OFFLINE_ONLY` 结果不构成该授权，也不声称发生过实时调用。
