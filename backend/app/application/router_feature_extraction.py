"""draft-v0.4: bounded, inspectable request features; never inspect evidence."""
from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import re

UNKNOWN = "UNKNOWN_NOT_INFERRED"
TASK_RULES = {
    "extract": (2, r"列出|摘录|提取|原文|查找|\b(?:extract|list|quote)\b"),
    "transform": (4, r"翻译|转换|改写|格式化|\b(?:translate|convert)\b"),
    "summarize": (6, r"总结|概括|摘要|\bsummari[sz]e\b"),
    "calculate": (8, r"计算|算(?:占比|差额|增量|比例)|\bcalculate\b"),
    "compare": (9, r"比较|对比|差异|\bcompare\b"),
    "apply_rule": (16, r"能否|能不能|是否(?:可以|能够|允许|符合|适用)|可否|判断|如何应用|\b(?:eligible|permitted)\b"),
    "reconcile": (18, r"冲突|矛盾|调和|哪个版本(?:有效|适用)|\breconcile\b"),
    "infer_plan": (18, r"推断|推理|推测|制定.*?计划|建议.*?方案|分析原因|\b(?:infer|plan)\b"),
}
QUOTES = re.compile(r"‘[^’]*’|“[^”]*”|'[^']*'|\"[^\"]*\"")
NEGATED = re.compile(r"(?:不要|不需要|无需|不必|不要求|do not|don't)\s*[^，,；;。!?？]*", re.I)
NUMBERS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


@dataclass(frozen=True)
class Features:
    task_labels: tuple[str, ...]
    anchors: tuple[tuple[str, str], ...]
    contributions: tuple[int, int, int, int, int, int]  # hundredths T/C/J/D/L/M
    statuses: tuple[str, str, str, str, str, str]
    condition_count: int | None
    logic: str
    dependency_depth: int | None
    output_count: int | None
    estimated_input_tokens: int | None
    estimator: str

    def public(self) -> dict:
        return {"task_labels": list(self.task_labels),
                "task_anchors": [{"task": t, "sha256": hashlib.sha256(a.encode()).hexdigest()} for t, a in self.anchors],
                **{k: {"value": v / 100, "status": s} for k, v, s in zip("TCJDLM", self.contributions, self.statuses)},
                "condition_count": self.condition_count,
                "condition_count_basis": "LOWER_BOUND" if self.statuses[1] == "OBSERVED_LOWER_BOUND_UNKNOWN_REMAINDER" else "OBSERVED" if self.condition_count is not None else "UNKNOWN",
                "logic": self.logic,
                "dependency_depth": self.dependency_depth, "output_count": self.output_count,
                "estimated_input_tokens": self.estimated_input_tokens,
                "estimator": self.estimator, "token_basis": "not_provider_actual"}


def _number(value: str) -> int:
    return int(value) if value.isdigit() else NUMBERS[value]


def _arithmetic_depth(node: ast.AST) -> int:
    if isinstance(node, ast.Expression):
        return _arithmetic_depth(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return 0
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return _arithmetic_depth(node.operand)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        return 1 + max(_arithmetic_depth(node.left), _arithmetic_depth(node.right))
    raise ValueError("UNRECOGNIZED_FORMULA")


def extract_features(question: str, prompt: str, *, estimated_tokens: int | None = None,
                     estimator: str = "UNKNOWN") -> Features:
    """Caller supplies only q0 or already validated resolution, and the full prompt.

    Counts are conservative grammar observations, not a semantic parser. Raw
    anchors stay local; public metrics contain only their hashes and rule names.
    """
    if not isinstance(question, str) or not isinstance(prompt, str):
        raise ValueError("ROUTER_INPUT_INVALID")
    if estimated_tokens is not None and (type(estimated_tokens) is not int or estimated_tokens <= 0 or estimator == "UNKNOWN"):
        raise ValueError("TOKEN_ESTIMATE_INVALID")
    text = NEGATED.sub("", QUOTES.sub("", question))
    anchors = tuple((name, m.group()) for name, (_, regex) in TASK_RULES.items()
                    for m in re.finditer(regex, text, re.I))
    labels = tuple(dict.fromkeys(t for t, _ in anchors)) or ("unknown",)
    t = max((TASK_RULES[x][0] for x in labels if x in TASK_RULES), default=10)
    # Only an explicit conditional/filter frame licenses predicate counting.
    frame = re.search(r"(?:若|如果|条件为|筛选条件[：:]|满足以下条件[：:])(.+?)(?=(?:能否|能不能|是否可以|可否)|[？?]|$)", text)
    conditions = []
    partial_conditions = False
    body = ""
    if frame:
        # Exception boundaries are lexical, independent of comma/semicolon.
        # Retain recognized predicates even when another clause is unresolved.
        body = re.split(r"除非|(?:紧急)?例外|豁免", frame[1], maxsplit=1)[0]
        for clause in re.split(r"，|,|；|;|\bAND\b|\bOR\b|且|并且|或者|或", body, flags=re.I):
            clause = clause.strip(" （）()且并")
            if not clause:
                continue
            if not re.search(r"[≤≥<>=]|不超过|至少|已批准|审批通过|已完成|为真|有效|属于|等于", clause):
                partial_conditions = True
                continue
            conditions.append(re.sub(r"\s+", "", clause))
    count = len(set(conditions)) if conditions else None
    explicit_zero = bool(re.search(r"无(?:筛选)?条件|不附加条件", text))
    if explicit_zero:
        count = 0
    c = (0, 3, 7, 11, 15, 19)[min(count or 0, 5)]
    and_ = bool(re.search(r"且|并且|\bAND\b", body, re.I)) if count else False
    or_ = bool(re.search(r"或者|或|\bOR\b", body, re.I)) if count else False
    logic, j, js = "NONE_OR_UNPARSED", 0, "OBSERVED" if count is not None else UNKNOWN
    if count and count > 1:
        logic, j = ("MIXED", 6) if and_ and or_ else ("AND", 3) if and_ else ("OR", 3) if or_ else ("NONE_OR_UNPARSED", 0)
    exception = bool(re.search(r"(?:除非|豁免|例外).*(?:上述条件|以上条件|这些条件|前述条件)", text)) and count is not None
    if exception:
        nested = bool(frame and re.search(r"[（(].*(?:且|或).*[）)]", frame[1]))
        logic, j, js = ("NESTED_EXCEPTION", 13, "OBSERVED") if nested else ("SCOPED_EXCEPTION", 9, "OBSERVED")
    elif re.search(r"除非|豁免|例外", text):
        logic, js = "EXCEPTION_SCOPE_UNKNOWN", UNKNOWN
    depth = None
    # Mask numeric identities before recognizing expressions. A date or tagged
    # version/ID is not an arithmetic operation, even in a calculate request.
    numeric_text = re.sub(r"(?<![\d./])\d{4}(?:-\d{2}-\d{2}|\s*/\s*\d{2}\s*/\s*\d{2})(?![\d/])", " ", text)
    def annual_identity(match):
        prefix, suffix = numeric_text[:match.start()], numeric_text[match.end():]
        annual = (re.search(r"(?:年度范围|年度|财年|学年)[（(\s]*$", prefix)
                  or re.match(r"[）)\s]*(?:年度|财年|学年|年)", suffix))
        explicit_division = re.search(r"(?:计算|公式(?:为)?|表达式(?:为)?|[（(=])\s*$", prefix)
        # Four-digit year pairs default to temporal identities. An explicit
        # arithmetic frame without an annual label still licenses real division.
        return match[0] if explicit_division and not annual else " "
    numeric_text = re.sub(r"(?<![\d./])\d{4}\s*/\s*\d{4}(?![\d./])", annual_identity, numeric_text)
    numeric_text = re.sub(r"(?:版本(?:号)?|编号|型号|\bID\b)\s*(?:为|是|[:：])?\s*[A-Za-z0-9]+(?:[._\-][A-Za-z0-9]+)*|(?<![A-Za-z0-9])[vV]\d+(?:[.\-]\d+)+", " ", numeric_text)
    for formula in re.finditer(r"(?<![A-Za-z0-9_.])[\d(][\d\s()+*/.\-]{2,}(?![A-Za-z0-9_.])", numeric_text):
        expression = formula[0].strip()
        if not re.search(r"[+*/\-]", expression):
            continue
        # Bare subtraction without an arithmetic instruction may be an ID.
        if not re.search(r"[+*/()]", expression) and "calculate" not in labels:
            continue
        try:
            observed_depth = _arithmetic_depth(ast.parse(expression, mode="eval"))
            depth = max(depth or 0, observed_depth)
        except (SyntaxError, ValueError, RecursionError):
            pass
    if re.search(r"先.*(?:计算|算).*再.*(?:用|根据)(?:上述)?结果.*(?:算|计算)", text):
        depth = max(depth or 0, 2)
        if re.search(r"再.*结果.*(?:算|计算).*然后.*(?:结果|所得).*?(?:算|计算)", text):
            depth = 3
    if depth is None and "calculate" in labels:
        depth = 1 if re.search(r"计算(?:总和|差额|均值)|相加|相减", text) else None
    d = 0 if not depth else 3 if depth == 1 else 9 if depth == 2 else 15
    output = None
    n = r"([1-9]\d*|[一二两三四五六七八九十])"
    grid = re.search(r"(?:分别)?(?:输出|列出)" + n + r"个(?:城市|实体)(?:的)?" + n + r"个指标", text)
    items = re.search(r"(?:输出|列出)" + n + r"(?:个|项)(?:比例|结果|指标|交付项)", text)
    named_grid = re.search(r"分别列出([^，,；;。？?的]+)的([^，,；;。？?]+)", text)
    if grid:
        output = _number(grid[1]) * _number(grid[2])
    elif items:
        output = _number(items[1])
    elif named_grid:
        entities = re.split(r"、|和|与", named_grid[1])
        indicators = re.split(r"、|和|与", named_grid[2])
        # Both lists must be explicit. Vague quantifiers are not counts.
        if len(entities) > 1 and len(indicators) > 1 and all(
                re.fullmatch(r"[\w\u4e00-\u9fff]{1,32}", x) and not re.search(r"多个|若干|等等|等$", x)
                for x in entities + indicators):
            output = len(set(entities)) * len(set(indicators))
    elif re.search(r"最终输出一个比例|只输出一个结果", text):
        output = 1
    m = 0 if not output or output == 1 else 2 if output <= 3 else 4 if output <= 6 else 6
    l = 0 if estimated_tokens is None or estimated_tokens <= 2000 else 2 if estimated_tokens <= 6000 else 5 if estimated_tokens <= 12000 else 8
    return Features(labels, anchors, (t, c, j, d, l, m),
                    ("OBSERVED" if anchors else UNKNOWN,
                     "OBSERVED_LOWER_BOUND_UNKNOWN_REMAINDER" if count and partial_conditions else "OBSERVED" if count is not None else UNKNOWN,
                     js, "OBSERVED" if depth is not None else UNKNOWN,
                     "ESTIMATED" if estimated_tokens is not None else "UNKNOWN",
                     "OBSERVED" if output is not None else UNKNOWN),
                    count, logic, depth, output, estimated_tokens, estimator)
