"""SIMULATED rule expectations, not model quality or statistical calibration."""
from decimal import Decimal
import pytest
from backend.app.application.router_feature_extraction import extract_features
from backend.app.application.rule_router_v1 import RouterPolicy, decide


@pytest.mark.parametrize("question,task,score", [
    ("列出甲方解除条款原文", "extract", "0.02"),
    ("根据合同甲方能否解除", "apply_rule", "0.16"),
    ("列出‘能否解除协议’的相关条款，不需要判断", "extract", "0.02"),
    ("摘录原因", "extract", "0.02"),
    ("推断原因", "infer_plan", "0.18"),
    ("翻译这段文本", "transform", "0.04"),
    ("总结报告", "summarize", "0.06"),
    ("计算总和", "calculate", "0.11"),
    ("比较两个版本", "compare", "0.09"),
    ("调和两个版本冲突", "reconcile", "0.18"),
    ("制定应急计划", "infer_plan", "0.18"),
    ("你好", "unknown", "0.1"),
    ("先计算两个增量，再用结果算占比", "calculate", "0.17"),
])
def test_task_examples(question, task, score):
    result = decide(question, "complete prompt", RouterPolicy(mode="DYNAMIC"))
    assert task in result.features.task_labels
    assert result.score == Decimal(score)


def test_multiple_tasks_use_max_not_sum_and_printable_rules():
    from backend.app.application.router_feature_extraction import TASK_RULES
    f = extract_features("摘录条款并比较差异", "prompt")
    assert set(f.task_labels) == {"extract", "compare"}
    assert f.contributions[0] == 9
    assert all(isinstance(regex, str) for _, regex in TASK_RULES.values())
    assert "摘录" in dict(f.anchors).values()


@pytest.mark.parametrize("count,c", [(0,0),(1,3),(2,7),(3,11),(4,15),(5,19),(6,19)])
def test_explicit_conditions(count, c):
    q = "无条件，能否报销" if not count else "若" + "，且".join(f"审批{i}为真" for i in range(count)) + "，能否报销"
    f = extract_features(q, "prompt")
    assert f.condition_count == count
    assert f.contributions[1] == c


@pytest.mark.parametrize("text,j", [
    ("若金额≤5000，且负责人已批准，且安全审批通过，能否报销",3),
    ("若金额≤5000，或负责人已批准，能否报销",3),
    ("若金额≤5000，且负责人已批准，或安全审批通过，能否报销",6),
    ("若金额≤5000，且负责人已批准，且安全审批通过，能否报销；除非紧急情况豁免上述条件",9),
    ("若（金钱≤5000或负责人已批准）且安全审批通过，能否报销；紧急例外豁免上述条件",13),
    ("若金额不超过5000，能否报销",0),
])
def test_logic_exclusive(text, j):
    assert extract_features(text, "prompt").contributions[2] == j


def test_spec_scores_and_exception_scope_unknown():
    q = "若金额≤5000，且负责人已批准，且安全审批通过，能否报销"
    assert decide(q, "p", RouterPolicy(mode="DYNAMIC")).score == Decimal(".30")
    assert decide(q + "；除非紧急情况豁免上述条件", "p", RouterPolicy(mode="DYNAMIC")).score == Decimal(".36")
    f = extract_features("能否报销，除非紧急情况", "p")
    assert f.statuses[2] == "UNKNOWN_NOT_INFERRED" and f.contributions[2] == 0


def test_no_context_pollution_unknown_counts_and_deduplication():
    f = extract_features("根据合同能否解除", "必须五条件，且、或、除非" * 100)
    assert f.condition_count is None and f.estimated_input_tokens is None
    assert f.contributions == (16,0,0,0,0,0)
    assert extract_features("若金额≤5000，且金额≤5000，能否报销", "p").condition_count == 1
    assert extract_features("列出三个城市三个年份", "p").condition_count is None


@pytest.mark.parametrize("q,depth", [("计算(440-200)/(1300-1000)",2),
    ("计算((8-2)/3)+4",3), ("先阅读再摘录",None),
    ("先计算两个差额，最终输出一个比例",None)])
def test_depth_not_operation_count(q, depth):
    assert extract_features(q, "p").dependency_depth == depth


@pytest.mark.parametrize("q,count,m", [("分别输出三个城市两个指标",6,4),
    ("先计算两个差额，最终输出一个比例",1,0), ("输出七项结果",7,6),
    ("输出两项指标",2,2), ("能否解除",None,0)])
def test_explicit_output_count(q, count, m):
    f = extract_features(q, "p")
    assert f.output_count == count and f.contributions[5] == m


@pytest.mark.parametrize("tokens,l", [(None,0),(2000,0),(2001,2),(6000,2),(6001,5),(12000,5),(12001,8)])
def test_load_boundaries(tokens,l):
    f = extract_features("摘录条款", "p", estimated_tokens=tokens, estimator="UNKNOWN" if tokens is None else "SIMULATED/full-prompt/v1")
    assert f.contributions[4] == l and f.estimated_input_tokens == tokens


def test_threshold_equality_and_stability():
    q = "摘录条款"
    p = RouterPolicy(mode="DYNAMIC", threshold=Decimal(".02"))
    assert decide(q, "p", p).role == "chat_expensive"
    assert decide(q, "p", p) == decide(q, "p", p)
    assert decide(q, "p", RouterPolicy(mode="CHEAP_ONLY")).role == "chat_cheap"
    assert decide(q, "p", RouterPolicy(mode="EXPENSIVE_ONLY")).role == "chat_expensive"


@pytest.mark.parametrize("tokens", [True,0,-1,1.5])
def test_invalid_estimator_never_invents_tokens(tokens):
    with pytest.raises(ValueError):
        extract_features("q", "p", estimated_tokens=tokens, estimator="SIMULATED")


# Owner Review R1: identical predicates/scope with punctuation-only changes.
R1_SCORE_CASES = [
    (f"若金额≤5000{sep}且负责人已批准{sep}且安全审批通过{sep}除非紧急情况豁免上述条件{sep}能否报销", ".36", 3, None, None)
    for sep in ("，", "；", ",", ";")
] + [
    ("若金额≤5000，且负责人已批准，且安全审批通过，除非紧急情况，能否报销", ".30", 3, None, None),
    ("若金额≤5000，且情况待核实，且负责人已批准，能否报销", ".26", 2, None, None),
    ("比较2026-10-09与2026-10-10", ".09", None, None, None),
    ("比较版本1.2-3与版本1.2-4", ".09", None, None, None),
    ("摘录编号123-456", ".02", None, None, None),
    ("摘录ID:123-456-789", ".02", None, None, None),
    ("比较v1.2-3与v1.2-4", ".09", None, None, None),
    ("计算2026-10-09的(440-200)/(1300-1000)", ".17", None, 2, None),
    ("计算123-45", ".11", None, 1, None),
    ("根据合同能不能解除", ".16", None, None, None),
    ("列出‘能不能解除’的原文，不需要判断", ".02", None, None, None),
    ("摘录条款，不要判断能不能解除", ".02", None, None, None),
    ("分别列出三个城市两个指标", ".06", None, None, 6),
    ("分别列出三个实体的两个指标", ".06", None, None, 6),
    ("分别列出北京、上海、广州的成本和收入", ".06", None, None, 6),
    ("分别列出多个实体与指标", ".02", None, None, None),
    ("列出‘分别列出三个城市两个指标’这句话", ".02", None, None, None),
    ("不要分别列出三个城市两个指标，摘录原文", ".02", None, None, None),
    ("比较2026-10-10与2026-10-11", ".09", None, None, None),
    ("计算版本号1.2-3的成本", ".08", None, None, None),
    ("计算编号为123-456对应的费用", ".08", None, None, None),
    ("计算ID:123-456的(8-2)/3", ".17", None, 2, None),
    ("计算版本v1.2.3对应的成本", ".08", None, None, None),
    ("摘录123-456", ".02", None, None, None),
]


@pytest.mark.parametrize("question,score,count,depth,output", R1_SCORE_CASES)
def test_owner_r1_score_counterexamples(question, score, count, depth, output):
    decision = decide(question, "p" * 20000, RouterPolicy(mode="DYNAMIC"))
    f = decision.features
    assert decision.score == Decimal(score)
    assert (f.condition_count, f.dependency_depth, f.output_count) == (count, depth, output)
    assert f.estimated_input_tokens is None and f.statuses[4] == "UNKNOWN"


def test_owner_r1_partial_conditions_are_explicit_lower_bound():
    f = extract_features("若金额≤5000，且情况待核实，且负责人已批准，能否报销", "p")
    assert f.condition_count == 2
    assert f.statuses[1] == "OBSERVED_LOWER_BOUND_UNKNOWN_REMAINDER"
    assert f.public()["condition_count_basis"] == "LOWER_BOUND"


def test_owner_r1_save_actual_scores(request, tmp_path):
    import json
    from pathlib import Path
    rows = []
    for question, expected, *_ in R1_SCORE_CASES:
        d = decide(question, "p" * 20000, RouterPolicy(mode="DYNAMIC"))
        rows.append({"question": question, "expected_score": expected, "actual_score": str(d.score),
                     "role": d.role, "features": d.features.public()})
    xmlpath = request.config.getoption("xmlpath", default=None)
    output = (Path(xmlpath).parent if xmlpath else tmp_path) / "scores.json"
    with output.open("x", encoding="utf8") as handle:
        handle.write(json.dumps(rows, ensure_ascii=False, indent=2))


R11_DIVISION_CASES = [
    ("比较2026/10/10与2026/10/11", ".09", None),
    ("比较2026-10-10与2026-10-11", ".09", None),
    ("比较2026 / 10 / 10与2026 / 10 / 11", ".09", None),
    ("比较2025/2026年度与2026/2027年度", ".09", None),
    ("比较年度范围2025/2026与2026/2027", ".09", None),
    ("计算2025/2026年度对应的成本", ".08", None),
    ("计算年度范围（2025/2026）的成本", ".08", None),
    ("计算2026/10/10当天的(440-200)/(1300-1000)", ".17", 2),
    ("计算12/3", ".11", 1),
    ("计算(2026/10)/10", ".17", 2),
    ("计算2025/2026", ".11", 1),
    ("计算(2025 / 2026)", ".11", 1),
    ("计算(440-200)/(1300-1000)", ".17", 2),
]


@pytest.mark.parametrize('question,score,depth', R11_DIVISION_CASES)
def test_owner_r11_dates_ranges_and_true_division(question, score, depth):
    d = decide(question, 'p', RouterPolicy(mode='DYNAMIC'))
    assert d.score == Decimal(score) and d.features.dependency_depth == depth
    assert d.features.statuses[4] == 'UNKNOWN'


def test_owner_r11_score_capture_without_junit(request, tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(request.config.option, 'xmlpath', None)
    test_owner_r1_save_actual_scores(request, tmp_path)
    assert len(json.loads((tmp_path / 'scores.json').read_text(encoding='utf8'))) == len(R1_SCORE_CASES)


def test_owner_r11_score_capture_never_overwrites_history(request, tmp_path, monkeypatch):
    monkeypatch.setattr(request.config.option, 'xmlpath', str(tmp_path / 'junit.xml'))
    historical = tmp_path / 'scores.json'
    original = b'{"history":"frozen"}\n'
    historical.write_bytes(original)
    with pytest.raises(FileExistsError):
        test_owner_r1_save_actual_scores(request, tmp_path)
    assert historical.read_bytes() == original
