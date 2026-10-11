# P7 V6 Initial Development Cohort Freeze

Status: `FROZEN_FOR_PREPARATION` (data identity/sample selection only; no ECS transfer, model run, or quality result).

## Inputs and cohort

- Source root: `D:\\RAG-Public-Bench\\prepared\\adapters` (public prepared adapters).
- Full prepared corpora are retained as each dataset candidate pool so negative and unjudged background documents remain available. No answer-only index is used.
- Cohort: 24 unique Development QIDs, 8 each for SciFact, MIRACL-ZH, and LongBench-ZH; selected by ascending SHA-256 of `dataset_version + NUL + P7-DEV24-V1 + NUL + qid`. LongBench is stratified 4 each from `dureader` and `multifieldqa_zh`.
- All three manifests, corpora, and Development case files pass their recorded byte/hash checks. Each selected QID is outside that dataset's locked holdout.
- The nine original manifest/corpus/locked-holdout files match the historical P7 full-file size/hash whitelist. The locked files were read only to confirm zero QID overlap; no holdout item was selected or uploaded.

## Scoring and limits

- SciFact and MIRACL-ZH have document-level relevance qrels but no answer-reference text in these Development adapters. Their defensible scores are retrieval/context qrels metrics; QA answer quality must be marked unavailable unless independently verified public answer Gold is added.
- LongBench-ZH has one answer reference per case and paired-task-context diagnostic qrels. Label its retrieval scores diagnostic, not official qrels.
- Full candidate pools are 5,183 SciFact documents, 6,000 MIRACL-ZH documents, and 400 LongBench-ZH documents. MIRACL includes explicit grade-zero judgments and unjudged background documents; SciFact and LongBench have unjudged background documents.
- Corpus/index/chunking/embedding/model/prompt/route/runner revisions remain `NOT_FROZEN`; no indexing or model call is authorized by this freeze alone.
- OmniDocBench v1.6 Gold conflict, MMLongBench-Doc, and HotpotQA are outside this main cohort and remain independently blocked/unfrozen.

## Artifacts

- `var/reports/v6-first-evaluation-20261010/source_manifest.json`: dataset identity, bytes and hashes, qrels/pool identity, selected QIDs, and readiness gates.
- `var/reports/v6-first-evaluation-20261010/cohort_dev24.jsonl`: the 24 original public Development rows with qrels and available answer references preserved.
- The source manifest SHA-256 is recorded in the execution report when computed.
