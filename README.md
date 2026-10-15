# Personal RAG — WeKnora Python development candidate

The development default is Python/FastAPI `backend.weknora_port`, its image/memory worker, and React `WeKnoraPythonApp`. Existing containers and data are not changed by this source update. This is not production acceptance.

```powershell
python -m pip install '.[weknora-port]'
# Set the explicit WEKNORA_PORT_* variables described below, then:
weknora-python-api --host 127.0.0.1 --port 8000
weknora-python-worker
npm --prefix frontend run dev -- --host 127.0.0.1
```

See [development configuration and responsibilities](docs/WEKNORA_DEVELOPMENT.md). Empty or unapproved model-send authorities fail closed. No model server starts or model downloads occur automatically. Schema initialization is an explicit separate operation on a verified new database; API startup does not run the old Alembic migration.

[Legacy entrypoints](docs/LEGACY_ENTRYPOINTS.md) are explicit source-checkout tools and are excluded from the default wheel. [Previous README](docs/history/README_pre_weknora_default_20261015.md) and [old runtime facts](docs/RUNNING.md) remain historical evidence, not the new development startup instructions.
