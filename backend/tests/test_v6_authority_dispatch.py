"""SIMULATED provider/lease/counter; real temporary files and native SQLite only."""
from dataclasses import replace
from pathlib import Path
import hashlib,json
import pytest
from backend.tests.test_v6_ledger_authority import setup
from backend.tests.test_v6_authority_durability import marker
from backend.tests.test_v6_free_native_binding import native_setup,invoke
from backend.tests.test_v6_free_model_mainpath import result_store
from backend.tests.test_v6_xing_generation_binding import no_live_sender
from backend.app.application.validation_usd_budget import V6OwnerActivityGate
from backend.app.ports.providers import ProviderRequestNotSent
from eval_center.paired_state import provider_send_claim_record


@pytest.mark.parametrize('fault',[None,'manifest','epoch','source_fence'])
def test_full_authority_rechecked_at_final_normal_native_lease(tmp_path,monkeypatch,fault):
    ledger,grant,manifest_path,manifest,load=setup(tmp_path,unknown=True)
    fence_path=tmp_path/'SIMULATED-source-fence.json'
    fence_path.write_text(json.dumps(marker(tmp_path/'SIMULATED-source.json')))
    fence_sha=hashlib.sha256(fence_path.read_bytes()).hexdigest()
    active_path=Path(str(ledger)+'.authority.json')
    active=marker(ledger,'ACTIVE_SINGLE_WRITER');active['source_fence_marker_sha256']=fence_sha
    active_path.write_text(json.dumps(active))
    manifest['v6_deepseek_authority'].update(authority_epoch=2,
        marker_sha256=hashlib.sha256(active_path.read_bytes()).hexdigest(),
        source_fence_marker_path=str(fence_path.resolve()),source_fence_marker_sha256=fence_sha)
    manifest_path.write_text(json.dumps(manifest))
    gate=V6OwnerActivityGate(ledger,grant=grant,authority=load())
    before=ledger.read_bytes();gate.check_authority_active()
    # This read-only common guard must not depend on a DeepSeek reserve/read.
    monkeypatch.setattr(gate,'_read',lambda:pytest.fail('dispatch guard read/wrote ledger'))
    case=native_setup(monkeypatch,tmp_path);lease_checks=[]
    def lease(lease_identity):
        assert lease_identity==case[4].lease_identity_sha256
        lease_checks.append(lease_identity)
        # Native claim admission succeeds. Drift occurs only at the final
        # transport callback, immediately before the actual sender boundary.
        if len(lease_checks)==2:
            if fault=='manifest':
                manifest['v6_deepseek_authority']['status']='RETIRED'
                manifest_path.write_text(json.dumps(manifest))
            elif fault=='epoch':
                active['epoch']=3;active_path.write_text(json.dumps(active))
            elif fault=='source_fence':fence_path.write_text('SIMULATED ALTERED FENCE')
        gate.check_authority_active()
    case[0].transport.send_claim_callback=replace(case[4],lease_validator=lease)
    if fault:
        with pytest.raises(ProviderRequestNotSent,match='SF_FREE_DURABLE_SEND_CHECK_DENIED'):invoke(case)
        assert not case[7]
        assert case[0].transport.attempts[case[2].request_id]['send_status']=='NOT_SENT'
        assert list(case[6].reservations.values())[-1]['state']=='released'
        row=provider_send_claim_record(case[5],request_id=case[2].request_id)
        assert row['status']=='blocked'
        with pytest.raises(ProviderRequestNotSent):invoke(case)
        assert not case[7]  # The native consumed slot is never replayed.
    else:
        invoke(case);assert len(case[7])==1
    assert len(lease_checks)==2 and ledger.read_bytes()==before
