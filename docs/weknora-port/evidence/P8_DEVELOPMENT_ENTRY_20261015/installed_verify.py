"""Installed production-code verification; source test harness only, no source backend import."""
from pathlib import Path
import sys,json,hashlib,os
ROOT=Path(r'E:\RAG-Worktrees\weknora-python-port-20261013')
ARTIFACT=Path('E:/WK/pkg/p8-dev-entry-20261015')
SITE=(ARTIFACT/'installed/Lib/site-packages').resolve()
import backend
assert Path(backend.__file__).resolve().is_relative_to(SITE),backend.__file__
from backend.weknora_port import entrypoint,worker,initialize,development
sys.path.append(str(ROOT))
import pytest
code=pytest.main(['--noconftest',str(ROOT/'tests/weknora_port/test_development_entry_pg.py'),'-q','-p','no:cacheprovider','--tb=short'])
production={name:str(Path(module.__file__).resolve()) for name,module in tuple(sys.modules.items()) if name.startswith('backend') and getattr(module,'__file__',None)}
assert all(Path(p).is_relative_to(SITE) for p in production.values()),production
manifest=json.loads((ARTIFACT/'manifest.json').read_text(encoding='utf-8'))
receipt={'kind':'INSTALLED_WHEEL_ACTUAL_LOOPBACK_HTTP_PG_REDIS_WORKER_MOCK_MODEL','source_commit':manifest['source_commit'],'wheel_sha256':manifest['sha256'],'pytest_exit':code,'production_module_paths':production,'source_backend_imports':0,'test_harness':str(ROOT/'tests/weknora_port/test_development_entry_pg.py'),'test_sha256':hashlib.sha256((ROOT/'tests/weknora_port/test_development_entry_pg.py').read_bytes()).hexdigest(),'real_provider_calls':0,'standalone_dependency_resolution':'NOT_CLAIMED; reused explicit read-only third-party paths','cutover':'NOT_RUN'}
(ROOT/'docs/weknora-port/evidence/P8_DEVELOPMENT_ENTRY_20261015/installed-http.receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
print('INSTALLED_PRODUCTION_MODULES',len(production),'SOURCE_BACKEND_IMPORTS',0)
raise SystemExit(code)
