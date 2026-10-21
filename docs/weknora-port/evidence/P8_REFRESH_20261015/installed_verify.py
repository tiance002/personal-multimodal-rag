"""Refreshed installed wheel proof; test harness comes from the checkout, production from the wheel."""
from pathlib import Path
import sys,json,hashlib
ROOT=Path(r'E:\RAG-Worktrees\weknora-python-port-20261013')
ARTIFACT=Path('E:/WK/pkg/p8-refresh-ee5919f-20261015')
SITE=(ARTIFACT/'installed/Lib/site-packages').resolve()
import backend
assert Path(backend.__file__).resolve().is_relative_to(SITE),backend.__file__
from backend.weknora_port import development,entrypoint,worker,initialize,authority
from backend.weknora_port.generation.context import Evidence
assert 'start_at' in Evidence.__dataclass_fields__
import backend.weknora_port.document_profile as profile
assert "summary_status='failed'" in Path(profile.__file__).read_text(encoding='utf-8')
assert hasattr(authority,'ConfiguredTokenAuthority')
sys.path.append(str(ROOT))
import pytest
names=['test_development_entry_pg.py','test_reference_metadata_pg.py','test_summary_configuration_pg.py']
args=['--noconftest',*[str(ROOT/'tests/weknora_port'/name) for name in names],'-q','-p','no:cacheprovider','--tb=short']
code=pytest.main(args)
production={name:str(Path(module.__file__).resolve()) for name,module in tuple(sys.modules.items()) if name.startswith('backend') and getattr(module,'__file__',None)}
assert all(Path(p).is_relative_to(SITE) for p in production.values()),production
m=json.loads((ARTIFACT/'manifest.json').read_text(encoding='utf-8'))
rows={}
for name,path in production.items():
 rel=str(Path(path).relative_to(SITE)).replace('\\','/')
 if rel in m['source_sha256']:
  actual=hashlib.sha256(Path(path).read_bytes()).hexdigest();assert actual==m['source_sha256'][rel];rows[rel]=actual
receipt={'kind':'REFRESHED_INSTALLED_WHEEL_ACTUAL_HTTP_ASGI_PG_REDIS_WORKER_MOCK_MODEL','source_commit':m['source_commit'],'wheel_sha256':m['sha256'],'pytest_exit':code,'production_module_paths':production,'verified_production_sha256':rows,'source_backend_imports':0,'test_sha256':{name:hashlib.sha256((ROOT/'tests/weknora_port'/name).read_bytes()).hexdigest() for name in names},'real_provider_calls':0,'standalone_dependency_resolution':'NOT_CLAIMED; explicit existing read-only third-party dependencies','cutover':'NOT_RUN'}
(ROOT/'docs/weknora-port/evidence/P8_REFRESH_20261015/installed.receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
print('INSTALLED_PRODUCTION_MODULES',len(production),'BYTE_VERIFIED',len(rows),'SOURCE_BACKEND_IMPORTS',0)
raise SystemExit(code)
