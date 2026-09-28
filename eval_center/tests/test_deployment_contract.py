from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_systemd_service_is_unprivileged_private_and_uses_dedicated_sqlite():
    service = (ROOT / "deploy/eval_center/rag-eval.service").read_text(encoding="utf-8")

    assert "User=rag-eval" in service
    assert "Group=rag-eval" in service
    assert "--host 127.0.0.1" in service
    assert "--port 8787" in service
    assert "EVAL_CENTER_DB=/srv/rag-eval/experiments/registry.sqlite3" in service
    assert "NoNewPrivileges=yes" in service
    assert "ProtectSystem=strict" in service
    assert "ReadWritePaths=/srv/rag-eval/experiments" in service


def test_installer_is_repeatable_and_does_not_delete_or_replace_registry_data():
    installer = (ROOT / "deploy/eval_center/install.sh").read_text(encoding="utf-8")

    assert "install -d" in installer
    assert "getent passwd rag-eval" in installer
    assert "source.backup(target)" in installer
    assert "PRAGMA integrity_check" in installer
    assert "rm -rf" not in installer
    assert "systemctl restart rag-eval.service" in installer
    assert "find eval_center" in installer
    assert "sha256sum CODE_SHA" in installer
    assert "runtime.env" in installer


def test_payload_contains_v2_dependency_closure_and_committed_version():
    helper = (ROOT / 'scripts/deploy_eval_center.ps1').read_text(encoding='utf-8')
    for module in ('contracts_v2.py','verification.py','gold.py','metrics.py','quality.py','source_metrics.py','telemetry.py'):
        assert module in helper
    assert 'git status --porcelain' in helper
    assert 'CODE_SHA' in helper
    uploader=(ROOT/'scripts/upload_eval_bundle.ps1').read_text(encoding='utf-8')
    assert 'EVAL_CENTER_CODE_SHA' in uploader


def test_deployment_helper_requires_an_explicit_apply_switch_and_strict_known_hosts():
    helper = (ROOT / "scripts/deploy_eval_center.ps1").read_text(encoding="utf-8")

    assert "[switch]$Apply" in helper
    assert "StrictHostKeyChecking=yes" in helper
    assert "UserKnownHostsFile" in helper
    assert "IdentityFile" in helper
    assert "[int]$Port" in helper
    assert "[string]$HostName" in helper
    assert "120.55.115.162" not in helper
