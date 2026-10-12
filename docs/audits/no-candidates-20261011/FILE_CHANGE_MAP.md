# 实际修改文件与任务原因

范围：本任务修复工作树，基底 d13951af；非全仓重构。AGENTS历史附录在后续独立提交归档。

| 文件 | 对应需求 / 原因 |
| --- | --- |
| `.dockerignore` | F1/F6/F8 shared verified runtime dependencies/config; secrets and corpus excluded from image context |
| `backend/app/adapters/models/cloud.py` | F1/F6/F7/F8 ordinary assembly + owner-requested role configuration; preserve evaluation policies |
| `backend/app/adapters/models/factory.py` | F1/F6/F7/F8 ordinary assembly + owner-requested role configuration; preserve evaluation policies |
| `backend/app/adapters/models/siliconflow_xing_capacity.py` | F1/F6/F7/F8 ordinary assembly + owner-requested role configuration; preserve evaluation policies |
| `backend/app/adapters/postgres/knowledge_repository.py` | F2/F3/F4/F5 scoped ingestion/version/diagnostic/history compatibility |
| `backend/app/api/routes.py` | F5/F8 input/readiness/configuration and current API contract |
| `backend/app/application/budget.py` | F4/F5/F6/F7 protocol/usage/presentation and caption scope boundaries |
| `backend/app/application/quick_chain.py` | F4/F5/F6/F7 protocol/usage/presentation and caption scope boundaries |
| `backend/app/application/retrieval.py` | F4 safe channel diagnostics; existing ranking retained |
| `backend/app/application/rule_router_v1.py` | F4/F5/F6/F7 protocol/usage/presentation and caption scope boundaries |
| `backend/app/application/run_metrics.py` | F4/F5/F6/F7 protocol/usage/presentation and caption scope boundaries |
| `backend/app/bootstrap.py` | F1/F6/F7/F8 ordinary assembly + owner-requested role configuration; preserve evaluation policies |
| `backend/app/config.py` | F1/F6/F7/F8 ordinary assembly + owner-requested role configuration; preserve evaluation policies |
| `backend/app/domain/model_registry.py` | F1/F6/F7/F8 ordinary assembly + owner-requested role configuration; preserve evaluation policies |
| `backend/app/main.py` | F1/F6/F7/F8 ordinary assembly + owner-requested role configuration; preserve evaluation policies |
| `backend/app/ports/model_usage.py` | F4/F5/F6/F7 protocol/usage/presentation and caption scope boundaries |
| `backend/app/ports/providers.py` | F4/F5/F6/F7 protocol/usage/presentation and caption scope boundaries |
| `backend/app/workers/ingestion.py` | F1/F6/F7/F8 ordinary assembly + owner-requested role configuration; preserve evaluation policies |
| `backend/tests/test_context_pool_policy.py` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `backend/tests/test_model_provider_contracts.py` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `backend/tests/test_p55a_lifecycle_integration.py` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `backend/tests/test_p55d_retrieval_postgres.py` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `backend/tests/test_siliconflow_xing_capacity.py` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `contracts/openapi.json` | F5/F8 input/readiness/configuration and current API contract |
| `deploy/Dockerfile.api` | F1/F6/F8 shared verified runtime dependencies/config; secrets and corpus excluded from image context |
| `deploy/Dockerfile.worker` | F1/F6/F8 shared verified runtime dependencies/config; secrets and corpus excluded from image context |
| `deploy/compose.yml` | F1/F6/F8 shared verified runtime dependencies/config; secrets and corpus excluded from image context |
| `docs/SOURCE-STATE.md` | G0-G5 current evidence/report/handoff; preserve historical records |
| `frontend/src/api/client.ts` | F2/F3/F5/F8 visible consent/readiness/history + owner-requested five-role form and legible names |
| `frontend/src/app/App.tsx` | F2/F3/F5/F8 visible consent/readiness/history + owner-requested five-role form and legible names |
| `frontend/src/app/state.ts` | F2/F3/F5/F8 visible consent/readiness/history + owner-requested five-role form and legible names |
| `frontend/src/components/ChatPanel.tsx` | F2/F3/F5/F8 visible consent/readiness/history + owner-requested five-role form and legible names |
| `frontend/src/components/KnowledgeBasePanel.tsx` | F2/F3/F5/F8 visible consent/readiness/history + owner-requested five-role form and legible names |
| `frontend/src/components/MemoryPanel.tsx` | F2/F3/F5/F8 visible consent/readiness/history + owner-requested five-role form and legible names |
| `frontend/src/components/Sidebar.tsx` | F2/F3/F5/F8 visible consent/readiness/history + owner-requested five-role form and legible names |
| `frontend/src/styles.css` | F2/F3/F5/F8 visible consent/readiness/history + owner-requested five-role form and legible names |
| `frontend/tests/clarification-response.spec.ts` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `frontend/tests/p55b21-memory.spec.ts` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `frontend/tests/p55c-memory-scope.spec.ts` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `frontend/tests/scope-sync.spec.ts` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `frontend/tests/sse.spec.ts` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `progress.md` | G0-G5 current evidence/report/handoff; preserve historical records |
| `pyproject.toml` | F1/F6/F8 shared verified runtime dependencies/config; secrets and corpus excluded from image context |
| `scripts/verify_p55a_isolated.py` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `scripts/verify_p55b2_memory_isolated.py` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `scripts/verify_p55b_context_isolated.py` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `alembic/versions/0023_service_token_policy.py` | F7 persistent independent ordinary quota; no evaluation history changes |
| `backend/app/adapters/models/runtime_readiness.py` | F1/F6/F7/F8 ordinary assembly + owner-requested role configuration; preserve evaluation policies |
| `backend/app/adapters/models/service_capacity.py` | F1/F6/F7/F8 ordinary assembly + owner-requested role configuration; preserve evaluation policies |
| `backend/app/adapters/models/service_configuration.py` | F1/F6/F7/F8 ordinary assembly + owner-requested role configuration; preserve evaluation policies |
| `backend/app/adapters/models/service_deepseek_capacity.py` | F1/F6/F7/F8 ordinary assembly + owner-requested role configuration; preserve evaluation policies |
| `backend/app/adapters/models/user_settings.py` | F1/F6/F7/F8 ordinary assembly + owner-requested role configuration; preserve evaluation policies |
| `backend/app/adapters/postgres/service_usage.py` | F7 persistent independent ordinary quota; no evaluation history changes |
| `backend/app/application/service_caption.py` | F4/F5/F6/F7 protocol/usage/presentation and caption scope boundaries |
| `backend/app/application/service_usage.py` | F4/F5/F6/F7 protocol/usage/presentation and caption scope boundaries |
| `backend/app/ports/run_presentation.py` | F4/F5/F6/F7 protocol/usage/presentation and caption scope boundaries |
| `backend/app/ports/service_policy.py` | F4/F5/F6/F7 protocol/usage/presentation and caption scope boundaries |
| `backend/tests/isolated_target.py` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `backend/tests/test_run_presentation.py` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `backend/tests/test_runtime_readiness.py` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `backend/tests/test_service_caption.py` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `backend/tests/test_service_configuration.py` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `backend/tests/test_service_usage.py` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `backend/tests/test_user_model_settings.py` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `deploy/service-policy.example.json` | F1/F6/F8 shared verified runtime dependencies/config; secrets and corpus excluded from image context |
| `docs/RUNNING.md` | G0-G5 current evidence/report/handoff; preserve historical records |
| `docs/audits/no-candidates-20261011/LOCAL_RUNTIME_IDENTITY.md` | G0-G5 current evidence/report/handoff; preserve historical records |
| `docs/audits/no-candidates-20261011/MINIMAL_FIX_REPORT.md` | G0-G5 current evidence/report/handoff; preserve historical records |
| `docs/audits/no-candidates-20261011/NO_CANDIDATES_ROOT_CAUSE.md` | G0-G5 current evidence/report/handoff; preserve historical records |
| `docs/audits/no-candidates-20261011/PAUSE_HANDOFF.md` | G0-G5 current evidence/report/handoff; preserve historical records |
| `docs/audits/no-candidates-20261011/REDUNDANCY_DECISIONS.md` | G0-G5 current evidence/report/handoff; preserve historical records |
| `docs/audits/no-candidates-20261011/root-cause.json` | G0-G5 current evidence/report/handoff; preserve historical records |
| `frontend/src/components/ModelSettingsPanel.tsx` | F2/F3/F5/F8 visible consent/readiness/history + owner-requested five-role form and legible names |
| `frontend/tests/model-settings.spec.ts` | G4 affected contracts/invariants; verified fixture drift or directly related regression |
| `scripts/capture_current_runtime.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/deploy_no_candidates_service.ps1` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/refresh_isolated_target.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/replay_original_questions.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/restore_scoped_storage.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/smoke_normal_answer.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/smoke_normal_assembly.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/smoke_normal_upload.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/smoke_normal_web.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/smoke_service_capacity_linux.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/smoke_service_quota_postgres.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/upgrade_scoped_versions.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/verify_context_pool_baseline.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/verify_failure_history_web.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/verify_model_configuration_service.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/verify_model_settings_web.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/verify_normal_privacy.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/verify_scoped_version_preservation.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `scripts/verify_service_migration_restore.py` | G0/G3/G4 scoped recoverable real verification/deployment/evidence; no generic model runner |
| `README.md` | Current runtime/evidence/affected verification required by F1–F8 and G0–G5 |
| `docs/audits/no-candidates-20261011/EVIDENCE_INDEX_20261012.json` | Current runtime/evidence/affected verification required by F1–F8 and G0–G5 |
| `docs/audits/no-candidates-20261011/FILE_CHANGE_MAP.md` | Current runtime/evidence/affected verification required by F1–F8 and G0–G5 |
