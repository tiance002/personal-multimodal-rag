# Locked source Gold acceptance set

Version: `stable-gold-v1` / `trust-docs-v1`.33 questions from two pre-existing project documents;30 answerable,3 no-answer. It tests project-document retrieval, not general-domain performance. All cases are locked before real A/B results. Legacy fixtures remain the separate deterministic development data.

`corpus/` contains normalized frozen source snapshots. `sources.json` records original file hashes, source hashes, normalization, origin and locked dataset hash. `locked.jsonl` records exact source version and half-open normalized text offsets plus inspected quotes, never experiment chunk IDs. SHA checks and exact quote readback are performed by `eval_center.dataset.load_reviewed_dataset`.

Review means Agent inspection of original source anchors before experiments. Human review is explicitly NOT_RUN. Deterministic answer points contain alternative literal terms; they do not prove semantic faithfulness.

The generator refuses an in-place change after freezing. Create a separately versioned dataset for an approved correction; never tune locked questions using test results. Keep all source, question, answer and quote text local. Only strict anonymized sufficient statistics may be exported.

The four legacy fields retain their names/types. `expected_chunk_ids=[]` is deliberate under the separately versioned stable Gold loader; it is not a legacy core-v1 fixture and must not be run through the old chunk-ID evaluator. `gold_evidence` is authoritative.

Reproducible inspection command:

```powershell
python -m pytest -q eval_center/tests/test_reviewed_dataset.py -p no:cacheprovider
```
