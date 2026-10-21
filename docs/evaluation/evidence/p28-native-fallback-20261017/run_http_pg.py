import os,json
from pathlib import Path
import pytest
from scripts.weknora_port_mvp_real import dsn
out=Path('E:/WK/run/p28-20261017')
os.environ['WEKNORA_PORT_TEST_DSN']=dsn()
os.environ['WEKNORA_PORT_TEST_REDIS_RUN_ID']=json.loads((out/'identity.json').read_text())['redis']['run_id']
os.environ['P28_EVIDENCE_DIR']=str(out/'http-pg')
os.environ['P21_EVIDENCE_DIR']=str(out/'http-pg')
(out/'http-pg').mkdir(exist_ok=True)
raise SystemExit(pytest.main(['tests/weknora_port/test_p21_http_pg.py','tests/weknora_port/test_qa_followup_fallback_pg.py','tests/weknora_port/test_fallback_output_pg.py','-q','-p','no:cacheprovider','--basetemp=E:/WK/run/p28-20261017/pytest-pg']))
