"""Fixed, versioned failure presentation; never a factual assistant answer."""
from backend.app.ports.providers import PUBLIC_PROVIDER_REASONS

VERSION = 'run-presentation-v1'


def clarification_presentation(text):
    if not isinstance(text,str) or not text.strip() or len(text)>300:
        return None
    return dict(schema_version=VERSION,kind='clarification',text=text,clarification_required=True)


def valid_clarification_presentation(value):
    return (isinstance(value,dict) and value.get('kind')=='clarification'
        and value.get('clarification_required') is True
        and value.get('schema_version',VERSION)==VERSION
        and set(value)<= {'schema_version','kind','text','clarification_required'}
        and clarification_presentation(value.get('text')) is not None)
MESSAGES = {
    'permission_required': '云端处理尚未获许可，请检查系统设置中的知识库外发许可与模型配置。',
    'retrieval_error': '检索能力不可用，请检查模型配置、知识库外发许可和索引状态。',
    'no_match': '没有检索到相关内容，请提出与当前资料有关的具体问题。',
    'evidence_unavailable': '现有证据不足以确认答案，请补充具体问题或资料。',
    'generation_error': '答案模型未能完成请求，请稍后重试或检查模型服务。',
    'input_limit': '当前输入或上下文超过模型容量，请检查输入和所选模型的容量配置。',
    'failed': '问答未完成，请检查运行状态后再试。',
}
REASON_MESSAGES = {
    'MODEL_OUTPUT_TRUNCATED': '本次回答达到输出长度上限，未保存为完整答案。可以重试或将问题拆成几部分。',
    'OUTPUT_CAPACITY_UNVERIFIED': '所选模型的回答输出容量尚未验证，本次未发送生成请求。',
    'OUTPUT_CAPACITY_UNSUPPORTED': '所选模型不支持本次回答所需的输出长度，本次未发送生成请求。',
}


def failure_presentation(error_code, metrics=None):
    if not error_code:
        return None
    category, reason = 'failed', 'RUN_FAILED'
    if error_code in {'CLOUD_EGRESS_DISABLED', 'MODEL_ROLE_EGRESS_DENIED'}:
        category, reason = 'permission_required', error_code
    elif error_code == 'NO_CANDIDATES':
        category, reason = 'retrieval_error', 'RETRIEVAL_DIAGNOSTIC_MISSING'
        diagnostics = metrics.get('retrieval_diagnostics') if isinstance(metrics, dict) else None
        if (isinstance(diagnostics, list) and diagnostics
                and all(isinstance(row, dict) and row.get('schema_version') == 'retrieval-diagnostic-v1'
                        and isinstance(row.get('channels'), dict) for row in diagnostics)):
            channels = [channel for row in diagnostics for channel in row['channels'].values()]
            if channels and all(isinstance(row, dict) and row.get('status') in
                    {'NOT_SELECTED','NOT_SENT','FAILED','EMPTY','OK'} for row in channels):
                failed = next((row for row in channels if row['status'] in {'FAILED','NOT_SENT'}), None)
                selected = [row for row in channels if row['status'] != 'NOT_SELECTED']
                if failed:
                    reason = failed.get('reason') if failed.get('reason') in PUBLIC_PROVIDER_REASONS else 'RETRIEVAL_UNAVAILABLE'
                elif selected and all(row['status'] == 'EMPTY' for row in selected):
                    category, reason = 'no_match', 'NO_CANDIDATES'
                else:
                    category, reason = 'evidence_unavailable', 'EVIDENCE_READBACK_OR_SELECTION_FAILED'
    elif error_code in {'MODEL_CONTEXT_WINDOW_EXCEEDED','INPUT_LIMIT','QUERY_INPUT_LIMIT','EMBEDDING_TOKEN_LIMIT_EXCEEDED'}:
        category, reason = 'input_limit', error_code
    elif error_code in {'MODEL_UNAVAILABLE','MODEL_OUTPUT_TRUNCATED','MODEL_CONTEXT_CAPACITY_UNKNOWN',
                        'OUTPUT_CAPACITY_UNVERIFIED','OUTPUT_CAPACITY_UNSUPPORTED'}:
        category, reason = 'generation_error', error_code
    elif error_code in {'INSUFFICIENT_EVIDENCE','UNSUPPORTED_ANSWER','INVALID_CITATION','NO_EVIDENCE'}:
        category, reason = 'evidence_unavailable', error_code
    return dict(schema_version=VERSION, kind='run_diagnostic', category=category,
        reason_code=reason, text=REASON_MESSAGES.get(reason, MESSAGES[category]))


def valid_failure_presentation(value):
    return (isinstance(value, dict) and set(value) == {'schema_version','kind','category','reason_code','text'}
        and value.get('schema_version') == VERSION and value.get('kind') == 'run_diagnostic'
        and value.get('category') in MESSAGES and isinstance(value.get('text'),str)
        and isinstance(value.get('reason_code'),str)
        and value['text'] in {MESSAGES[value['category']], REASON_MESSAGES.get(value.get('reason_code'))}
        and isinstance(value.get('reason_code'), str)
        and value['reason_code'] in (PUBLIC_PROVIDER_REASONS | {
            'RUN_FAILED','CLOUD_EGRESS_DISABLED','RETRIEVAL_DIAGNOSTIC_MISSING','RETRIEVAL_UNAVAILABLE','NO_CANDIDATES',
            'EVIDENCE_READBACK_OR_SELECTION_FAILED','MODEL_CONTEXT_WINDOW_EXCEEDED','INPUT_LIMIT','QUERY_INPUT_LIMIT',
            'MODEL_UNAVAILABLE','MODEL_OUTPUT_TRUNCATED','OUTPUT_CAPACITY_UNVERIFIED','OUTPUT_CAPACITY_UNSUPPORTED',
            'INSUFFICIENT_EVIDENCE','UNSUPPORTED_ANSWER','INVALID_CITATION','NO_EVIDENCE'}))
