# Parser application packaging checkpoint

Added the explicit `weknora-port` application extra in `pyproject.toml` for verified HTTPX/httpcore, PDFium and MarkItDown format dependencies. Existing pinned base dependencies remain available. New direct parser pins match fixed-source lock versions already installed/imported in the isolated parser venv. Actual MarkItDown metadata lists `docx`, `pptx` and `xlsx` extras; the new extra requests those verified names. No shared old environment was upgraded.

Added package-data rules for source SQL/manifests, fixed tokenizer dictionaries/HMM/user dictionary and applicable notices/licenses. The wheel contains Python modules and these data resources; it does not require a Go compiler/runtime. Temporary `build/` output is now ignored, preserving generated files without committing build residue.

Actual command:

`& '.\var\weknora-port\p2-venv\Scripts\python.exe' -m pip wheel . --no-deps --wheel-dir 'var/weknora-port/wheels'`

Exit **0**, built `personal_rag-0.1.0-py3-none-any.whl`, 3,576,948 bytes, SHA256 `b5161fc6c51d3fdd0cc516f9b30a32d5acb0ae142a5895d671dae5af1f9cc157`. Build isolation supplied setuptools (it was absent from the shared old interpreter). This wheel is an intermediate code-tree packaging check, not a release/cutover image or complete runtime acceptance.

ZIP membership check via the prescribed old interpreter: exit **0**, `missing=[]`, `forbidden=[]`, 521 files. Required source schema, manifests, all three tokenizer resources and `LICENSE-THIRD-PARTY-WeKnora-MIT.txt` are present. No `.go`, Go executable or environment credential file is present. The first checklist used the nonexistent ingestion `NOTICE` filename and failed; corrected the checklist to the actual license filename rather than altering the attribution file. A following Git-status command masked that first script's process exit, so the first check is not reported PASS.

Dependency extras metadata/import check: exit **0**, MarkItDown **0.1.3** with the named extras. Installed parser/source comparisons remain those recorded in `P2_INGEST_PARITY.md`; this command did not rerun parsing or install the candidate into production.

All package checks are **OFFLINE/BUILD PASS** only. PostgreSQL/Redis execution, real providers, original-question replay, deployment, Push/Tag/Release are **NOT RUN**. Initial schema and source assets may still change during this active Goal; final packaging must be rebuilt after their actual final inputs are committed.
