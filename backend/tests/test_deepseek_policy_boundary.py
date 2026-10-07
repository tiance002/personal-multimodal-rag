import json
import pytest
from backend.tests.test_deepseek_safety import gateway
from backend.app.adapters.models.deepseek import DeepSeekGateway
from backend.app.ports.providers import ProviderRequestNotSent

def test_unwired_gate_types_fail_closed_without_reservation(tmp_path,monkeypatch):
    configured,path,requests=gateway(tmp_path,monkeypatch)
    model=DeepSeekGateway(api_key='SIMULATED',cloud_enabled=True,attempt_gate=configured.attempt_gate)
    with pytest.raises(ProviderRequestNotSent,match='DEEPSEEK_GATE_POLICY_MISSING'):
        model.answer_with_budget('synthetic',1,512,cloud_authorized=True)
    assert requests==[]
    assert json.loads(path.read_text())['consumed_attempts']==0
