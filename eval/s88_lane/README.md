# eval/s88_lane — 55 题评测与路由闸门工具

这套脚本在 ECS 的评测目录 `/opt/rag-s8/state/executions/s88-lane` 里运行（容器内挂载为 `/work`）。
仓库只放代码，**不放题目、gold、模型答案**（仓库是公开的，评测数据属于私有件）。

## 文件

| 文件 | 作用 |
|---|---|
| `collect_lane.py` | 调评测通道采集答案。原子写、checkpoint、资源采样；UNKNOWN 不自动重发；503/402 单独分类 |
| `slim_export.py` | 把每轮 16–19 MB 的原始帧压成约 60 KB 的 `_answers.jsonl`，只留题号+答案 |
| `prep_judge_data.py` | 从私有 QA 上下文抽 gold，叠 `gold-fixups.json` 修订层，产出 `gold55.json` 与候选答案表 |
| `judge_answers.py` | 机器判分：gold 命中 + 答案卫生两栏合成，输出逐题 JSONL |

## 运行顺序

```bash
# 1) 采集（容器内）
docker exec s88-lane-compact python /work/collect_lane.py \
  --cases /work/inference-inputs.private.json --out /work/out-compact \
  --tag lane-xing --retries 2 --retry-sleep 15

# 2) 瘦身导出
python3 slim_export.py out-compact

# 3) 备好判分输入（含 gold 修订）
python3 prep_judge_data.py

# 4) 判分
docker exec s88-lane-compact python /work/judge_answers.py cand-en v5
```

## gold 修订层

`gold-fixups.json`（私有件，不入库）用于修正经评审确认存在歧义的标注，结构见
`gold-fixups.schema.json`。规则：

- 只改有争议的题，原始 gold 保留在 `gold_original`，可随时回退；
- `also_accept` 里放同样成立的等价答案，判分时与 gold 同等对待；
- 每条必须写 `note`（判定依据）与 `evidence`，便于复核。

## 判分口径

- 比意思不比字面：同义改写、翻译、调序、更详细都算对；
- 末尾 `Sources: xxx.md` 是系统要求格式，判分前剔除；
- 与 gold 关键值/实体/日期矛盾、核心要点缺失、答成另一题、无据弃答、结论自相矛盾 → WRONG；
- 「并列展示互相矛盾的来源」不算自相矛盾（提示词本就要求这样做）。

## 已知限制

- 机器判分与人工裁定的一致率约 86%（44/51），仍有约 4 例误杀；
- 误杀集中在「核心对、子项漏一两个」的清单题，需要「要点覆盖表 + 矛盾列表」的两栏结构才能进一步压下去；
- 闸门漏判比误杀更危险：漏判会让错题进不了贵模型。当前漏判 3/51。
