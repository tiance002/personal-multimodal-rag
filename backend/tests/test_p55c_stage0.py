"""SIMULATED composition-root regression; no model or business database."""
from pathlib import Path
from unittest.mock import Mock
import uuid
from backend.app.bootstrap import build_container
from backend.app.config import Settings
from backend.app.domain.scope import Scope

def test_default_composition_auto_does_not_claim_history(tmp_path):
    c=build_container(Settings(database_url='sqlite+pysqlite:///:memory:',storage_root=tmp_path,
        memory_local_principal=str(uuid.uuid4())),model=None)
    service=c.memory_service
    service.repository.settings=Mock(return_value={'write_mode':'auto'})
    service.repository.schedule=Mock(return_value='UNKNOWN_JOB')
    assert service.after_completed('conversation',Scope.from_ids(['kb'])) is None
    service.repository.schedule.assert_not_called()
    assert service.availability()['automatic_extraction']=='MODEL_IDENTITY_NOT_CONFIGURED'
    c.engine.dispose()

def test_memory_panel_uses_actual_conversation_document_scope():
    root=Path(__file__).resolve().parents[2]
    panel=(root/'frontend/src/components/MemoryPanel.tsx').read_text(encoding='utf8')
    app=(root/'frontend/src/app/App.tsx').read_text(encoding='utf8')
    assert 'document_scope: [...documentScope]' in panel
    assert 'documentScope={state.documentScope}' in app
    assert '[kbId, scopeKey]' in panel
    client=(root/'frontend/src/api/client.ts').read_text(encoding='utf8')
    assert 'JSON.stringify({read_enabled: value.read_enabled, write_mode: value.write_mode})' in client
