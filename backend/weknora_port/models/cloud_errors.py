"""Known send denials must survive optional retrieval-model fallbacks.

Messages are static: provider exception text and credentials never reach UI.
"""

CLOUD_ERRORS = {
    'MODEL_CLOUD_DISABLED': '云问答尚未开启。请在本地配置中明确启用并重启工作台。',
    'MODEL_ORIGIN_KB_EGRESS_DENIED': '当前知识库未允许云调用，请先检查知识库的外发许可。',
    'MODEL_ORIGIN_KB_ACCESS_DENIED': '无法访问本次请求的知识库，请检查当前用户与知识库范围。',
    'MODEL_DOCUMENT_ACCESS_DENIED': '无法访问本次请求的文档，请重新选择有效资料。',
    'MODEL_NOT_FOUND': '模型未配置、已停用或不属于当前用户，请检查模型设置。',
    'MODEL_CALL_ID_REUSE_DENIED': '此操作已有调用记录，不能重复发送。请查看原运行的状态。',
    'MODEL_SEND_AUTHORIZATION_FAILED': '云调用授权检查失败，本次发送已停止，请检查本地运行配置。',
    'LOCAL_EXECUTION_SCOPE_DENIED': '本次请求超出本地云调用许可的用户、知识库或模型范围。',
    'LOCAL_EXECUTION_LIMIT_DENIED': '本地云调用的次数或 Token 限额不足，已停止新增发送。',
    'LOCAL_EXECUTION_INVALID': '本地云调用限额文件无效，已拒绝启用受限模式。',
    'LOCAL_EXECUTION_BOUND_INVALID': '本次请求无法建立有效的 Token 预留，已停止发送。',
    'LOCAL_EXECUTION_BOUND_UNVERIFIABLE': '此请求的 Token 上界无法可靠验证，受限模式已拒绝发送。',
    'LOCAL_EXECUTION_UNKNOWN_STOP': '存在结果不确定或中断的调用，已保留占用并停止新增发送。',
    'LOCAL_EXECUTION_CLOSED': '本地云调用许可已关闭，已停止新增发送。',
    'LOCAL_EXECUTION_REUSE_DENIED': '此操作已占用本地调用额度，不能重复发送。请查看原运行。',
    'LOCAL_EXECUTION_SETTLEMENT_FAILED': '调用用量未能确认持久化，已保留占用并停止新增发送。',
}


def raise_if_send_denied(error):
    if getattr(error, 'code', None) in CLOUD_ERRORS:
        raise error


def public_cloud_error(code):
    return CLOUD_ERRORS.get(code, '')
