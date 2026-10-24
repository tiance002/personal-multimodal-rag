"""英文版默认提示词的契约测试（2026-10-25 重写）。

历史背景：R2 实际生效的提示词是三层叠加——
DEFAULT_SYSTEM_PROMPT + PROMPT_B_SUFFIX + s88-source-precision/v3，
主指令合计约 2586 字符，其中「不要编造」重复三次，并且同时存在
「缺值就声明不确定」和「必须完成计算不要拒答」两条互相冲突的指令。

2026-10-25 起改为英文版 `EN_SYSTEM_PROMPT`：骨架与硬约束措辞移植自
Tencent/WeKnora@3e8b0bfc 的 default_kb，但保留本项目身份与强制语言语义，
不移植上游 MCP/tool/图片约定与自检回路。

本测试守住五条底线：
1. 身份与强制语言语义不被上游措辞带偏；
2. 上游那些与本项目口径相反或已禁止的句子不得混入；
3. 四条针对性约束（计算不拒答、冲突择一、步骤不混、枚举全覆盖）都在；
4. 长度显著短于旧三层叠加，且不因英文改写而膨胀；
5. 端到端拼装后仍保持上述性质。
"""
from backend.weknora_port.generation import Evidence, GenerationRequest, prepare_generation
from backend.weknora_port.generation.context import (
    SOURCED_ANSWER_OUTPUT,
    DEFAULT_SYSTEM_PROMPT,
    EN_SYSTEM_PROMPT,
)
from backend.weknora_port.generation.prompt_candidate import candidate_profile
from backend.weknora_port.generation.prompt_candidates import CANDIDATE_SUFFIXES, apply_candidate
from backend.weknora_port.retrieval.context import FinalizedContext


def _prepared(system_prompt):
    source = Evidence('chunk-1', '资料正文：示例数值 42。',
                      knowledge_id='document-1', knowledge_base_id='kb-1',
                      knowledge_title='示例文档', knowledge_filename='sample.txt')
    return prepare_generation(GenerationRequest(
        run_id='english-run', session_id='english-session', message_id='english-message',
        q0='示例问题？', language='Chinese (Simplified)', citations_enabled=False,
        system_prompt=system_prompt,
        finalized=FinalizedContext('资料正文：示例数值 42。\n示例问题？', source.content, (source.id,)),
        evidence=(source,)))


def test_english_prompt_keeps_project_identity_and_forced_language():
    """身份、品牌与强制语言语义必须保持本项目口径。"""
    assert EN_SYSTEM_PROMPT.startswith('You are EviRoute RAG,')
    assert 'Answer language: {{language}}' in EN_SYSTEM_PROMPT
    assert 'regardless of the language of the question or reference materials' in EN_SYSTEM_PROMPT
    # 上游品牌与措辞一律不得出现
    assert 'WeKnora' not in EN_SYSTEM_PROMPT
    assert 'Tencent' not in EN_SYSTEM_PROMPT


def test_english_prompt_excludes_upstream_rules_this_project_rejected():
    """上游与本项目口径相反、或本项目明确禁止的句子不得混入。"""
    # 上游是「默认语言、可被覆盖」，本项目要求强制配置语言
    assert 'Default response language:' not in EN_SYSTEM_PROMPT
    # 本项目禁止自检回路
    assert 'Before finishing' not in EN_SYSTEM_PROMPT
    assert 'silently verify' not in EN_SYSTEM_PROMPT
    # 旧的弃权指令（题 48 拒答的直接来源）不得回流
    assert 'state the uncertainty briefly instead of guessing a repair' not in EN_SYSTEM_PROMPT
    # 上游的 MCP/tool/图片协议不在基础段里
    assert 'MCP' not in EN_SYSTEM_PROMPT
    assert 'image' not in EN_SYSTEM_PROMPT.lower()


def test_english_prompt_carries_the_four_targeted_rules():
    """四条针对性约束分别对应一类已知失败，缺一条就少救一类题。"""
    # 题 48：可算的问题被拒答
    assert 'You may reason, calculate' in EN_SYSTEM_PROMPT
    assert 'never refuse a question that reasoning over the materials can answer' in EN_SYSTEM_PROMPT
    # 题 13/12：资料冲突时并列而不择一
    assert 'follow the better-supported account' in EN_SYSTEM_PROMPT
    # 题 2：互斥流程被揉合
    assert 'one complete, executable method' in EN_SYSTEM_PROMPT
    # 题 04：枚举漏项
    assert 'cover every item in the requested scope' in EN_SYSTEM_PROMPT
    # 题 22/45：关系与量纲错配
    assert 'do not guess missing' in EN_SYSTEM_PROMPT


def test_english_prompt_forbids_empty_retrieval_as_absence_proof():
    """上游最有价值的一条：检索为空不能当成「不存在」的证据。"""
    assert 'Never treat an empty retrieval result as proof that something does not exist' in EN_SYSTEM_PROMPT


def test_english_prompt_is_shorter_than_the_legacy_three_layer_stack():
    """旧三层叠加约 2586 字符，英文版必须显著更短且不因改写而膨胀。"""
    legacy = (DEFAULT_SYSTEM_PROMPT
              + '\n\n' + candidate_profile()['baseline_prompt_b_suffix']
              + '\n\n' + CANDIDATE_SUFFIXES['s88-source-precision/v3'])
    assert len(EN_SYSTEM_PROMPT) < len(legacy) / 2
    assert len(EN_SYSTEM_PROMPT) < 1300
    # 基础段 + 输出段仍应控制在一个紧凑区间内
    assert len(EN_SYSTEM_PROMPT) + len(SOURCED_ANSWER_OUTPUT) < 1450


def test_candidate_catalog_stays_frozen_and_english_prompt_needs_no_patch():
    """不新增候选版本：英文版本身已包含规则，评测时用 off 即可。"""
    assert tuple(CANDIDATE_SUFFIXES) == tuple('s88-source-precision/' + v for v in ('v1', 'v2', 'v3'))
    assert apply_candidate(EN_SYSTEM_PROMPT, version='off') == EN_SYSTEM_PROMPT


def test_prepared_system_message_uses_english_rules_without_abstention():
    """端到端：拼装后的 system 消息不应再带弃权指令，且语言已渲染。"""
    system = _prepared(EN_SYSTEM_PROMPT).messages[0].content
    assert system.startswith('You are EviRoute RAG,')
    assert 'Answer language: Chinese (Simplified)' in system
    assert 'You may reason, calculate' in system
    assert 'state the uncertainty briefly instead of guessing a repair' not in system
    assert 'Return the final answer directly' in system
