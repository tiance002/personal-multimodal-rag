# Latest frozen P5 timeout policy — 2026-10-13

Current authority: parent's explicit decision makes task 02/03 controlling. Explicit Cheap timeout does not trigger upgrade with provider_fallback=false. Historical P5-TIMEOUT authorization claims and old C worktree remain unchanged.

Actual modifications: generation/coordinator.py removes timeout from upgrade admission; storage/generation.py denies a second persistent attempt based on timeout. Tests preserve length/empty/invalid-handle upgrades, UNKNOWN retention, one attempt on timeout and no final failed-draft publication. The isolated real-PG two-attempt case now uses an approved truncation with unknown usage; it is NOT RUN in this execution.

Commands from E:/RAG-Worktrees/weknora-python-port-20261013, Python E:/RAG quention/.venv/Scripts/python.exe; PYTHONDONTWRITEBYTECODE=1:

RED: python -B -m pytest --noconftest -q tests/weknora_port/test_generation.py::test_latest_frozen_timeout_policy_preserves_unknown_without_upgrade tests/weknora_port/test_generation_wire.py::test_transport_read_timeout_does_not_upgrade_with_default_fallback_off -p no:cacheprovider --tb=short
Exit 1: 3 failed. Old coordinator suppressed timeout and MockTransport observed 2 requests.

GREEN: python -B -m pytest --noconftest -q tests/weknora_port/test_generation.py tests/weknora_port/test_generation_wire.py tests/weknora_port/test_generation_protocol.py tests/weknora_port/test_generation_storage.py tests/weknora_port/test_service.py -p no:cacheprovider --tb=short
Exit 0: 133 passed, 1 skipped (explicit PG DSN not supplied).

Result: IMPLEMENTED / OFFLINE_PASS for this policy and associated invariants only. SQL doubles and MockTransport are simulated. ISOLATED_INTEGRATION_PASS NOT RUN; REAL_PROVIDER_PASS/CUTOVER_PASS NOT RUN. No threshold, feature weights, q0/rewrite max, auxiliary model roles, source context, output tokens or old history changed.

Fixed upstream3e8b0bfc has no Cheap-to-Expensive policy: this is the approved P5 deviation, not a claim of direct Go timeout-upgrade parity. Continue concrete runtime binding. 未实现的命令不得报告通过。
