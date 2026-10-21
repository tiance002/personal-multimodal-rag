# P24 — Remaining Dataset Wiring and Valid Gold Audit

**Status:** P24_PARTIAL_OFFLINE_AUDIT_COMPLETE_REAL_CHEAP_NOT_RUN
**Date:** 2026-10-17 (Asia/Shanghai)
**Baseline:** 9fe6f853d8dad30e97314b83984633f621940a90

## Outcome

P9/P19 historical records cover all frozen QIDs for SciFact, MIRACL-ZH, LongBench-ZH and HotpotQA (8/8 each), but their scores remain phase- and revision-specific. MMLongBench-Doc has 8 frozen QIDs: row-0687 was previously scored in P9; the remaining 7 were audited below, with **0 new P24 Cheap Only runs**. The current P22 selected KB scope has 2 KBs/287 fingerprinted vectors and no MML source-name hits. The isolated PG and Redis containers were stopped, and the last saved finite SiliconFlow fence has zero headroom. No service was started, no source uploaded/indexed and no model request was sent.

For OmniDocBench, all 24 saved source bindings are valid. Three pages meet the existing unique full-text alignment requirements for the project NED diagnostic. This is not an official OmniDocBench score; OmniDocBench has no QA Gold.

## MMLongBench-Doc — 7 remaining frozen QIDs

All seven QID rows and source-document identities exist in the frozen manifests. Annotation page indices are not assumed to be physical PDF pages unless independently checked.

| QID | Frozen Gold | Source / evidence audit | P24 disposition |
|---|---|---|---|
| row-0430 | 0 | Cat-vs-dog source SHA matches the frozen manifest. Gold requires counting tiger images over the full deck; parsed text has image placeholders and P10 did not validate species/count. | NOT RUN — visual counting capability not established; Gold not independently confirmed. |
| row-0431 | 5 | Same deck; annotated pages 29, 30, 32 have unknown physical-page convention. Saved text/crops do not establish network layer count. | NOT RUN — diagram interpretation required. |
| row-0434 | Cat vs Dog dataset | MinerU Basic text on physical page 34 says “Kaggle’s Cat vs Dog dataset (25k dog/cat pictures).” Existing P16 OCR saw “Coding time!” on physical page 33, but P15/P16 did not establish a machine section boundary linking those pages. | NOT RUN — answer text exists only in a separate MinerU condition; section relation/current index unestablished. |
| row-0684 | men | Original Pew PDF physical page 10 chart visually confirms men 81% vs women 78% answering yes. Existing text parser/index omitted those values. | Gold source-confirmed; NOT RUN — current text index lacks chart values. |
| row-0685 | Not answerable | Gold’s negative claim lacks a complete 45-page exclusion audit and the frozen evidence page is unspecified. One chart page cannot prove whole-document absence. | NOT RUN — Gold unanswerability not verified. |
| row-0958 | shiny object; mirror | B3 source SHA matches. Existing OCR wording “shiny surface” does not establish both examples; evidence is a diagram. | NOT RUN — visual interpretation required. |
| row-0959 | radiowaves; microwaves | MinerU Basic text on physical page 17 supports both terms. P11 MinerU-B reached Context and a real Cheap answer whose core matched Gold. This is a separate historical parser/index condition, not the P9/P24 baseline. | NOT RUN — no current matching active index and no fence headroom. |

Frozen source SHA-256 values, verified against the P10 manifest:

- Cat-vs-dog PDF: 40a74d3fc5e73c81465206a4043c19b850daf66fc5617756f5894d25ad054b4b
- Pew PDF: 8f49f93f1665fd5ff604e66ffed9649d8b18ce7f114ab43463aeac46cd3d00cb
- B3 PDF: 43be9592854c7da10bf011335faec017b726ff2e23bb4ccae7e8fe88d7801f22

Historical index snapshots are not interchangeable: P9 native had 23 Cat-vs-dog chunks and 162 Pew chunks; P11 native had 23 Cat-vs-dog and 6 B3 chunks; P11 MinerU Basic had 52 Cat-vs-dog and 34 B3 chunks. Current P22 scope is separate and has no MML source hit.

Historical row distinctions:

- row-0434: P11 native search had no real rerank. P11 MinerU search/rerank ran, but Rerank dropped the candidate before useful Context; there was no final Cheap answer. P12–P16 investigated; no reliable section association was found.
- row-0959: P11 MinerU-B completed a real RAG answer with the core matching Gold. Keep it as separate historical evidence and do not merge into P9/P24 scoring.

## OmniDocBench — 24 parse pages

The current original archive SHA matches the frozen P10 manifest: 111d1e8eff5fa7e743fec9adf72b67538981fadd72fac303a43c344d3f2d1c06. All 24 source bindings are valid; only three pages have a unique usable text/Gold alignment for project page-text normalized edit distance (NED):

| Page | P9 Tesseract NED | P10 MinerU Basic NED |
|---|---:|---:|
| PPT_EnglishtoAmericanTransition_page_012.png | 0.120000 | 0.133333 |
| PPT_lecture1_page_007.png | 0.588235 | 0.132075 |
| yanbaopptmerge_yanbaoPPT_4185.jpg | 0.602151 | 0.053763 |
| **Mean, same 3 pages only** | **0.436795** | **0.106391** |

Lower NED is better. MinerU improved the 3-page mean; the first page regressed slightly. The other 21 pages were NOT RUN for this metric because annotation text order/alignment is ambiguous or completed page OCR is unavailable. Official layout/table/formula metrics were NOT RUN. There is no official Omni QA Gold. P12’s six real Omni RAG diagnostics remain diagnostics, not QA accuracy.

## Six-dataset historical coverage

Counts below are frozen QID records, not complete-corpus indexing. P9 and P19 phases/revisions are kept separate.

| Dataset | Frozen / historical coverage | Retrieval and valid answer/parse metrics |
|---|---|---|
| SciFact | 8 QIDs; P9 4 + P19 4 | No answer Gold. Subset source recall P9 Search/Rerank/Context 1.000/0.625/0.500; P19 1.000/1.000/1.000. Not official full-corpus recall. |
| MIRACL-ZH | 8; P9 5 + P19 3 | Passage qrels/retrieval only; no answer Gold. P9 and P19 selected-subset Search/Rerank/Context 1.000/1.000/1.000. |
| LongBench-ZH | 8; P9 3 + P19 5 | Task-specific only. Retrieval P9 1.000/1.000/1.000; P19 0.800/1.000/0.800. P9 DuReader ROUGE-L 0.277257 (n=2), MultiFieldQA F1 0.093750 (n=1); P19 DuReader ROUGE-L 0.337017 (n=2), MultiFieldQA F1 0.214269 (n=3). |
| HotpotQA | 8; P9 3 + P19 5 | P9 retrieval 1.000/0.833/0.833, answer EM 0, token F1 0.114268; P19 retrieval 1.000/0.900/0.900, EM 0, token F1 0.147185. No support-sentence/joint metric claimed. |
| MMLongBench-Doc | 8; P9 1, P24 audited 7 and ran 0 | P9 row-0687 EM 0, character F1 0.017834. P11/P12 MinerU records for 0434/0959 are separate configurations, not added to this score. |
| OmniDocBench | 24 parse pages | 24/24 source-bound; 3/24 project NED-scored. No official QA Gold. |

P9 API source revision: a2eaa8e44ca55b4d8b3856399c5c09350b6f4985. P19 runtime revision: bec7c282be4c1cc4b49979e786e949082392d49b. Do not combine their metrics as one same-build score.

## Execution, commands and protections

- HEAD was 9fe6f853d8dad30e97314b83984633f621940a90 and matched the requested baseline. Seven inherited dirty entries (one modified, six untracked) were preserved.
- Offline preflight command: E:\RAG quention\.venv\Scripts\python.exe -B E:\WK\run\p24-20261017\preflight.py — exit code 0. It audited baseline, dirty paths, frozen P9/P10/P11/P19/P22 records, source/archive hashes, current selected scope and container identities.
- Original Pew PDF physical page 10 is rendered at [pew-10.png](E:/WK/run/p24-20261017/pew-10.png). The exact render command was not retained in the P24 evidence folder, so it is not claimed as a reproducible command record.
- Report/JSON validation: E:\RAG quention\.venv\Scripts\python.exe -B E:\WK\run\p24-20261017\validate_reports.py — exit code 0; output/exit saved under E:\WK\run\p24-20261017\validation.*.
- Tracked diff check: git -C E:\RAG-Worktrees\weknora-python-port-20261013 diff --check -- progress.md — exit code 0.
- Full FastAPI/Worker/PG/Redis/Cheap execution: **NOT RUN**. New P24 calls/tokens/UNKNOWN/uploads/indexes: **0/0/0/0/0**.
- Last saved fence: closed limit 1,669,727, headroom 0; 61 historical UNKNOWN and 705 historical ledger rows preserved. No fence was expanded or rewritten.
- Isolated PG and Redis were both stopped at inspection. P22 selected scope was unchanged (2 KBs, 287 vectors) with no MML source-name hits. Services were not started and no data was modified.

## Next priority

Before a new live batch, establish a new bounded fence with positive headroom through the existing authority process, restore the approved isolated dependencies, and select a normal-upload KB scope containing the exact frozen sources. Then resolve the per-QID evidence gates: full-deck count for 0430; diagram for 0431/0958; section association for 0434; complete-document negative audit for 0685. 0684 has a confirmed Gold but needs chart evidence in the allowed index path. Preserve 0959’s P11 MinerU result separately unless a comparable condition is run.

No global Prompt, model, Rerank threshold, route or parser changes were made. P24 is partial and makes no official OmniDocBench or unified MML score claim.

## Evidence

- Machine audit: E:/WK/run/p24-20261017/audit-result.json
- Preflight output / exit: E:/WK/run/p24-20261017/preflight.stdout.txt and preflight.exitcode.txt
- Preflight script: E:/WK/run/p24-20261017/preflight.py
- Pew page render: E:/WK/run/p24-20261017/pew-10.png
- Frozen P9 Gold: E:/WK/run/p9-20261016/gold-all.json
- P10 MML manifest: E:/WK/run/p10-mineru/frozen/mml-pdfs-and-qid-pages.json
- P10 Omni manifest: E:/WK/run/p10-mineru/frozen/omni24-original-pages-and-gold.json
