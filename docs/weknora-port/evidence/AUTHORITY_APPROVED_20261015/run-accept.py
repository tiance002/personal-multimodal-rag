"""Scoped E-only pytest acceptance launcher for task 2.

Reconciles the approved isolated identity via WEKNORA_PORT_TEST_REDIS_RUN_ID,
redirects fixed repo evidence writes into this run's OUT, and never records
DSN/credentials. Fails instead of overwriting prior evidence labels.
"""
from pathlib import Path
import os, sys, json, re, subprocess
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
label, *args = sys.argv[1:]
assert re.fullmatch('[a-z0-9-]+', label), 'LABEL_INVALID'
assert not (OUT / (label + '.exitcode.txt')).exists(), 'REFUSE_EVIDENCE_OVERWRITE'

env = dict(os.environ)
env.update(PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
    PYTHONPATH=os.pathsep.join([str(ROOT), r'E:\WK\deps\reader',
        str(ROOT / 'var/weknora-port/runtime-deps'), str(OUT)]))
work = Path(r'E:\WK\run\authority-integration-20261015')
(work / 'tmp').mkdir(parents=True, exist_ok=True)
env['TMP'] = env['TEMP'] = str(work / 'tmp')

if '--pg-env' in args:
    args.remove('--pg-env')
    text = (ROOT / 'docs/weknora-port/AUTOMATIC_MEMORY_CHECKPOINT_20261013.md').read_text(encoding='utf-8-sig')
    candidates = re.findall(r'postgres(?:ql)?://[^\s\x27\x22`]+', text)
    env['WEKNORA_PORT_TEST_DSN'] = next(v for v in candidates if urlsplit(v).hostname == '127.0.0.1'
        and urlsplit(v).port == 56423 and urlsplit(v).path == '/weknora_python_port_20261013')
    info = subprocess.run(['docker', 'exec', 'eviroute-rag-redis', 'redis-cli', 'INFO', 'server'],
        capture_output=True, text=True, encoding='utf-8', check=True)
    run_id = re.search(r'run_id:([a-f0-9]{40})', info.stdout)[1]
    env['WEKNORA_SEQ_IMAGE_REDIS_ID'] = run_id
    env['WEKNORA_SEQ_IMAGE_RECEIPT'] = str(OUT / (label + '.pg.json'))
    env['WEKNORA_PORT_TEST_REDIS_RUN_ID'] = run_id
    env['WP_EVIDENCE_OUT'] = str(OUT / (label + '.redirects'))
    (OUT / (label + '.redirects')).mkdir(parents=True, exist_ok=True)
    (OUT / (label + '.redis-identity.json')).write_text(
        json.dumps({'container': 'eviroute-rag-redis', 'run_id': run_id, 'db': 15}, indent=2) + '\n', encoding='utf-8')

cmd = [sys.executable, '-B', '-m', 'pytest', '--noconftest', '-q', *args, '--tb=short',
    '-p', 'no:cacheprovider', '-p', 'redirect_plugin',
    '--basetemp=' + str(work / ('pytest-' + label)),
    '--junitxml=' + str(OUT / (label + '.junit.xml'))]
(OUT / (label + '.command.json')).write_text(json.dumps(
    {'argv': cmd, 'cwd': str(ROOT),
     'env_paths': {k: env[k] for k in ['PYTHONPATH', 'TMP', 'TEMP', 'PYTHONDONTWRITEBYTECODE']}}, indent=2) + '\n',
    encoding='utf-8')
with (OUT / (label + '.stdout.txt')).open('wb') as out, (OUT / (label + '.stderr.txt')).open('wb') as err:
    result = subprocess.run(cmd, cwd=ROOT, env=env, stdout=out, stderr=err)
(OUT / (label + '.exitcode.txt')).write_text(str(result.returncode) + '\n', encoding='utf-8')
print((OUT / (label + '.stdout.txt')).read_text(encoding='utf-8', errors='replace')[-7000:])
print('COMMAND_EXIT', result.returncode)
sys.exit(result.returncode)
