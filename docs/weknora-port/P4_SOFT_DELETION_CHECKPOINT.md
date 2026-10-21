# Soft-deletion parity checkpoint

Fixed source `internal/types/message.go:447` uses `gorm.DeletedAt`; the fixed message repository queries use scoped GORM model reads. The plain Python SQL adapter must spell out that implicit exclusion. The three history/extraction reads in `storage/memory.py` now exclude deleted messages. The generation lifecycle also excludes soft-deleted sessions and assistant messages before claiming an attempt.

Actual isolated PG command:

```powershell
$env:WEKNORA_PORT_TEST_DSN='postgresql://port:isolated-port-fixture@127.0.0.1:56413/weknora_python_port_20261013'
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest tests/weknora_port/test_soft_deletion_pg.py -q -p no:cacheprovider
```

Exit **0**, **1 passed in 0.44s**. Actual physical identity was verified before any fixture write. Recent history, after-cursor and before-time reads returned the live message and excluded the deleted message. A deleted session was denied before a generation claim. `evidence/soft-deletion-pg-20261013.json` records actual fixture identities and assertions; only these new synthetic message/session/tenant rows were cleaned. No model call or old business data/UNKNOWN change occurred.

Associated scripted/offline command:

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest tests/weknora_port/test_generation_storage.py tests/weknora_port/test_memory_storage.py tests/weknora_port/test_model_configuration.py -q -p no:cacheprovider
```

Exit **0**, **51 passed, 1 skipped in 0.43s**. That skip was the separately opt-in PG case without an explicit DSN, not a passing real database test.

The configuration store also permits a wholly unconfigured optional role to remain empty, rather than forcing all five roles to contain fabricated IDs. Partially supplied role identities remain subject to the actual model-catalogue validator. API and full-chain integration are still pending.
