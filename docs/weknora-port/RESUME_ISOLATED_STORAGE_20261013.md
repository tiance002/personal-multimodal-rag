# Resume actual isolated storage evidence — 2026-10-13

Dedicated E branch codex/weknora-python-port-resume-20261013. New PG containera5464dff5efb, ParadeDBv0.22.6-pg17, port56423, independent volumeweknora-python-resume-pg-20261013. Databaseweknora_python_port_20261013 systemID7696088403504697382. Redis692b84d8fee0, redis7.4.2, port56424, volumeweknora-python-resume-redis-20261013, runIDc7d5aaff50b2743adea96a426718c5df3483c80f. Original fixture systemID7696038373595934758 and both old business DB identities are prohibited. Old services/config/keys/volumes not changed.

Schema command: E:/RAG quention/.venv/Scripts/python.exe -B scripts/weknora_port_isolated_schema.py --dsn <synthetic new56423 DSN> --expected-system-identifier 7696088403504697382 --prohibited-system-identifier 7691227493754040358 --prohibited-system-identifier 7689510505384681511 --prohibited-system-identifier 7696038373595934758 --report docs/weknora-port/evidence/RESUME_SCHEMA_20261013.json
Exit0. Actual001/002/003 receipts; pg_search0.22.6/vector0.8.1. Synthetic dimension3, no real Embedding.

pip install --disable-pip-version-check --target var/weknora-port/runtime-deps cryptography==46.0.3 tiktoken==0.14.0
Exit0. Only E isolated directory; original virtualenv not modified. Direct versions already pinned in imported pyproject. Import smoke and resolved-lock recorded separately.

Actual lifecycle defect: documents.py supplied a synchronous int tenant resolver to ScopedKnowledgeRepository's async resolver contract. Upload produced DOCUMENT_PROCESSING_FAILED. Corrected async resolver, same owner check. Imported actual-PG fixture also had incorrect constructor positional arguments and omitted image_processing_config; fixed explicit config without changing completion assertion. Windows long fixture paths require short E basetemp; product long-path support is not claimed.

Final targeted command, PYTHONPATH=E:/RAG-Worktrees/weknora-python-port-20261013/var/weknora-port/runtime-deps, explicit new56423 WEKNORA_PORT_TEST_DSN:
E:/RAG quention/.venv/Scripts/python.exe -B -m pytest --noconftest -q tests/weknora_port/test_documents.py tests/weknora_port/test_documents_pg.py tests/weknora_port/test_model_store_pg.py tests/weknora_port/test_turn_storage_pg.py tests/weknora_port/test_send_gate_pg.py tests/weknora_port/test_memory_storage_pg.py tests/weknora_port/test_generation_storage.py -p no:cacheprovider --basetemp=E:/RAG-Tests/w13a --tb=short
Exit0:41passed4.35s. There are3document doubles,25generation storage doubles and13actual PG cases (one generation case +12 explicit pg modules). Check collection if counts change; not all41are real DB tests.

Earlier attempts retained as FAIL:34pass/3cryptography dependency failures/1missing temp parent; after dependency install3modelpass/1constructor failure; next omitted KB image config failure; next DOCUMENT_PROCESSING_FAILED; async resolver fix39pass/2Windows long-path failures; short root then41pass. No assertion lowered and no provider call.

Status: ISOLATED_INTEGRATION_PASS for documentupload/source/chunk readback, owner/admission/replay/failure/UNKNOWN storage, monthly gate and memory CRUD mechanisms. Not full uploaded-document index/retrieval/generation HTTP E2E, direct upstream full-chain oracle, real provider or cutover PASS. Need concrete runtime binding and P7. Old copied evidence receipts restored unchanged; new readbacks live under evidence/RESUME_*.json. 原始UNKNOWN保留。未实现的命令不得报告通过。
