const CLOUD_ERROR_MESSAGES: Record<string, string> = {
  MODEL_CLOUD_DISABLED: "全局云端调用已关闭。请在 local.json 中明确授权后重启 wk，再重试。",
  MODEL_ORIGIN_KB_EGRESS_DENIED: "当前知识库不允许向云端外发内容。请启用该知识库的外发授权后重试。",
  MODEL_ORIGIN_KB_ACCESS_DENIED: "当前请求的知识库范围不允许执行此操作。",
  MODEL_DOCUMENT_ACCESS_DENIED: "当前请求的资料范围不允许执行此操作。",
  MODEL_NOT_FOUND: "所选模型未启用，或当前服务无权访问该模型。",
  MODEL_CALL_ID_REUSE_DENIED: "该操作已有调用记录，系统已拒绝重复发送。请查看原运行状态。",
  MODEL_SEND_AUTHORIZATION_FAILED: "云调用授权检查失败，已停止发送，请检查本地运行配置。",
  LOCAL_EXECUTION_SCOPE_DENIED: "本地执行授权不涵盖当前知识库、模型或租户范围。",
  LOCAL_EXECUTION_LIMIT_DENIED: "本地执行次数或 Token 额度不足，已停止本次请求。",
  LOCAL_EXECUTION_INVALID: "本地执行授权账本无效，已停止本次请求。",
  LOCAL_EXECUTION_BOUND_UNVERIFIABLE: "无法确认本次请求的 Token 上界，已停止受限发送。",
  LOCAL_EXECUTION_BOUND_INVALID: "本次请求无法建立有效的 Token 预留，已停止发送。",
  LOCAL_EXECUTION_REUSE_DENIED: "此操作已占用本地调用额度，已拒绝重复发送。请查看原运行状态。",
  LOCAL_EXECUTION_UNKNOWN_STOP: "存在历史状态未知或孤立调用，相关额度仍被占用，已停止本次请求。",
  LOCAL_EXECUTION_CLOSED: "本地执行授权已关闭，已停止本次请求。",
  LOCAL_EXECUTION_SETTLEMENT_FAILED: "用量记录未能持久化，已停止本次请求。",
};

export function cloudErrorMessage(code: unknown): string {
  if (typeof code !== "string" || !/^[A-Z][A-Z0-9_]*$/.test(code)) return "";
  return CLOUD_ERROR_MESSAGES[code] ?? "";
}
