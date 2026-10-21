# Knowledge base external processing permission

The selected library KB now exposes its persisted `cloud_allowed` permission and an explicit privacy switch. New KB default remains false. The explanation states that questions, retrieved passages and images may be sent to configured cloud models when enabled, and that disabling prohibits external processing. No internal principal/tokenizer/budget information is exposed. The control changes state only after successful persistence; errors retain the prior value and are shown inline.

Frontend mutation uses the existing runtime-identity gate, then `PATCH /api/v1/knowledge-bases/{knowledge_base_id}/external-processing` with exactly `{"cloud_allowed":true|false}`. API `KnowledgeBaseExternalProcessing` uses StrictBool and forbids extra fields. The existing Origin middleware already covers all PATCH writes; absent, null and cross-Origin requests fail before runtime mutation. The injected protocol is `update_knowledge_base_consent(principal, knowledge_base_id, cloud_allowed)` and returns the current KnowledgeBase DTO. Actual authorization/persistence is root-owned; this worker did not touch SQL or call a real API/model. Permission changes do not approve Goal model calls or trigger probes.

Changed files for this task:

- `backend/weknora_port/http_api.py`: only StrictBool DTO, runtime protocol method and PATCH route; the HTTP worker owns the rest of this shared file.
- `frontend/src/weknora_port/KnowledgeBaseConsent.tsx`, `api.ts`, library integration in `WeKnoraPythonApp.tsx`, and small styles in `styles.css`.
- `frontend/playwright.weknora.config.ts`, `frontend/tests/weknora-port/consent.spec.ts`, `tests/weknora_port/test_http_consent.py`.
- `API_CONTRACT.md` consent section and this evidence note.

Executed from the managed worktree:

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest -q tests/weknora_port/test_http_consent.py tests/weknora_port/test_http_api.py --tb=short -p no:cacheprovider
```

Exit0: **23 passed in0.56s**, in-process ASGI/fake runtime. Twelve consent cases cover exact true/false, coercion/extra-field denial and Origin rejection.

From `frontend`:

```powershell
$env:VITE_RAG_IMPLEMENTATION='weknora_python'; npm.cmd run build
$env:PLAYWRIGHT_CHROME_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'; npm.cmd exec playwright test -- --config playwright.weknora.config.ts consent.spec.ts
```

Build exit0, TypeScript check and Vite production adapter build completed. Browser exit0: **3 passed in7.4s**. Tests run a separate Vite port5189 with all `/api/v1` requests mocked; they verify enable/disable, rejected save and runtime-identity mismatch preventing PATCH. No provider endpoint is invoked.

Initial browser runs failed because a broad mock `/api/**` also intercepted legacy JavaScript modules under `src/api/`, producing module MIME errors. The mock was narrowed to `/api/v1/**`. One later case failed because Playwright `check()` demands immediate checkbox state while the controlled switch intentionally waits for server persistence; the test now clicks and waits for the persisted state. These failed executions are not counted as PASS. Final 3-case run above passed without changing that persistence behavior.

Scoped `git diff --check` exit0. Default legacy build also passed earlier; only the explicitly configured adapter build is used as adapter evidence. No dependency was installed. Real runtime/database permission persistence and final privacy-gate integration acceptance: NOT RUN by this worker, to be supplied by root. No worker commit/tag; no milestone acceptance claim.
