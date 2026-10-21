# Personal RAG — WeKnora Python development candidate

The development default is Python/FastAPI `backend.weknora_port`, its image/memory worker, and React `WeKnoraPythonApp`. Existing containers and data are not changed by this source update. This is not production acceptance.

```powershell
# Explicit local configuration; no old .env or legacy Compose is loaded.
.\wk.ps1 check  -Config E:\WK\run\p81-retirement-20261018\local.json
.\wk.ps1 start  -Config E:\WK\run\p81-retirement-20261018\local.json
.\wk.ps1 status -Config E:\WK\run\p81-retirement-20261018\local.json
.\wk.ps1 stop   -Config E:\WK\run\p81-retirement-20261018\local.json
```

See [development configuration and responsibilities](docs/WEKNORA_DEVELOPMENT.md) and the [configuration template](docs/local-runtime.example.json). The verified local configuration above currently disables cloud sends. MinerU Basic uses local ONNX models; Tesseract has no default or fallback execution path. No model server or download starts automatically. Schema initialization is a separate operation; startup never runs legacy migrations.

[Legacy entrypoints](docs/LEGACY_ENTRYPOINTS.md) are explicit source-checkout tools and are excluded from the default wheel. [Previous README](docs/history/README_pre_weknora_default_20261015.md) and [old runtime facts](docs/RUNNING.md) remain historical evidence, not the new development startup instructions.
