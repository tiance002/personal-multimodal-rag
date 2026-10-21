# S8.3-R delivery evidence

These are compact offline reviews, metrics, hashes, tests and diagnostic tools for the12 completed real API requests. Full immutable Provider/SSE/PG evidence remains in the archive identified by the main report; no sensitive headers or credential values are included here.

- `batch1-fact-review.json` / `batch2-fact-review.json`: per-answer facts/citations, exact target original membership and A/B comparability.
- `final-metrics.json`: actual request/response/timing hashes and paths, raw target rerank scores with index/content mapping, actualwire chunks, usage, SQL and persistence.
- `session-config/`: actual RED/GREEN logs. These tests are already committed with the implementation.
- `s8_3_r_ecs_acceptance.py`, `remote_s8_3_r_case_controller.py`, `test_tools_offline.py`: diagnostic helpers; final memory checkpoint fix was tested offline only AFTER all12 real cases. It did not recover historical memory measurements.
- `aggregate_final_metrics.py`: offline metric extraction from the original `downloaded-final` directory beside the script; no real model calls.
- tool/protection JSONs: actual verification results, not prospective test claims.

The current real-call authorization is CLOSED. Do not run controller/serve/model commands using this closed grant. Offline helper tests need no Provider; the toolkit is not a new public API or production model framework. Paths refer to the original evidence layout, not a self-contained deployment package. The complete runtime/source image is identified in the main report.
