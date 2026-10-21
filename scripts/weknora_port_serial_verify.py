"""Serial targeted integration checks; redirects only test evidence destinations."""
from pathlib import Path
import sys,json,shutil,hashlib,os
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import pytest
OUT=ROOT/'docs/weknora-port/evidence/SERIAL_INTEGRATION_20261015'
PHASES={
'offline':['tests/weknora_port/'+n for n in ('test_local_ollama.py','test_local_controlled.py','test_generation.py','test_generation_protocol.py','test_generation_wire.py','test_provider_stream.py','test_stream_error_continuation.py','test_service.py','test_fallback_output.py','test_document_profile.py','test_conversation_configuration.py','test_send_gate.py')],
'mq':['tests/weknora_port/test_memory_queue_recovery.py','tests/weknora_port/test_memory_queue.py'],
'http':['tests/weknora_port/test_local_runtime_pg.py','tests/weknora_port/test_qa_followup_fallback_pg.py','tests/weknora_port/test_fallback_output_pg.py','tests/weknora_port/test_qa_caption_causal_pg.py']}
class RedirectEvidence:
    def __init__(self):self.redirects=[]
    def pytest_collection_modifyitems(self,items):
        modules={item.module for item in items}
        # Only these accepted test modules emit fixed/history evidence paths.
        names={'test_local_runtime_pg.py','test_qa_followup_fallback_pg.py','test_fallback_output_pg.py','test_qa_caption_causal_pg.py'}
        for module in modules:
            if Path(module.__file__).name not in names:continue
            if Path(module.__file__).name=='test_local_runtime_pg.py':
                module.OUT=OUT/'local';module.OUT.mkdir(exist_ok=True)
                receipt=ROOT/'docs/weknora-port/evidence/LOCAL_TAKEOVER_20261015/redis-identity.json'
                value=json.loads(receipt.read_text(encoding='utf-8'))
                assert value['run_id']==os.environ['WEKNORA_PORT_TEST_REDIS_RUN_ID']
                target=module.OUT/'redis-identity.json'
                if target.exists():assert target.read_bytes()==receipt.read_bytes()
                else:shutil.copy2(receipt,target)
            def evidence_path(value='.',*args):
                path=Path(value,*args)
                prefix=Path('docs/weknora-port/evidence')
                if not path.is_absolute() and path.is_relative_to(prefix):
                    target=OUT/'case-files'/path.relative_to(prefix)
                    target.parent.mkdir(parents=True,exist_ok=True)
                    self.redirects.append({'original':str(path),'actual':str(target)})
                    return target
                return path
            module.Path=evidence_path
phase=sys.argv[1];assert phase in PHASES
assert not (OUT/(phase+'.junit.xml')).exists(),'Use a new run label; preserve previous outputs'
selected=PHASES[phase]
inputs={'phase':phase,'files':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in selected},
    'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    'redis_identity_expected':os.environ.get('WEKNORA_PORT_TEST_REDIS_RUN_ID'),'real_provider_calls':0}
(OUT/(phase+'.inputs.json')).write_text(json.dumps(inputs,indent=2)+'\n',encoding='utf-8')
plugin=RedirectEvidence()
result=pytest.main(['--noconftest',*selected,'-q','-p','no:cacheprovider','--tb=short','--junitxml='+str(OUT/(phase+'.junit.xml'))],plugins=[plugin])
(OUT/(phase+'.redirects.json')).write_text(json.dumps(plugin.redirects,indent=2)+'\n',encoding='utf-8')
(OUT/(phase+'.exitcode.txt')).write_text(str(result)+'\n',encoding='utf-8')
raise SystemExit(result)
