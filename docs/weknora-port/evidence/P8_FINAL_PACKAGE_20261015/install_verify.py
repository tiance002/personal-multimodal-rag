from pathlib import Path
import json,subprocess,sys,os,zipfile,hashlib
root=Path.cwd();artifact=Path('E:/WK/pkg/p8-final-b5f396d-20261015');m=json.loads((artifact/'manifest.json').read_text(encoding='utf-8'));wheel=Path(m['wheel'])
with zipfile.ZipFile(wheel) as z:
 assert not any(n.startswith(('backend/app/','backend/tests/')) for n in z.namelist())
 for rel in ['backend/weknora_port/generation/context.py','backend/weknora_port/generation/coordinator.py','backend/weknora_port/document_profile.py']:
  assert hashlib.sha256(z.read(rel)).hexdigest()==m['source_sha256'][rel]
 assert b"'start_at':e.start_at" in z.read('backend/weknora_port/generation/coordinator.py')
 assert b"summary_status='failed'" in z.read('backend/weknora_port/document_profile.py')
venv=artifact/'installed';subprocess.run([sys.executable,'-B','-m','venv','--without-pip',str(venv)],check=True)
paths=[Path(r'E:\RAG quention\.venv\Lib\site-packages'),root/'var/weknora-port/runtime-deps',Path('E:/WK/deps/reader')];assert all(p.is_dir() for p in paths)
(venv/'Lib/site-packages/verified-third-party-dependencies.pth').write_text('\n'.join(str(p) for p in paths)+'\n',encoding='utf-8')
r=subprocess.run([sys.executable,'-B','-m','pip','--python',str(venv/'Scripts/python.exe'),'install','--no-index','--no-deps',str(wheel)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT);(artifact/'install.stdout.txt').write_bytes(r.stdout);assert r.returncode==0,r.stdout.decode(errors='replace')
env=os.environ.copy();env.pop('PYTHONPATH',None);env['PYTHONDONTWRITEBYTECODE']='1';env['PYTHONIOENCODING']='utf-8';results={}
for entry in ('api','worker','init'):
 r=subprocess.run([str(venv/('Scripts/weknora-python-'+entry+'.exe')),'--help'],cwd=artifact,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT);(artifact/(entry+'.help.txt')).write_bytes(r.stdout);assert r.returncode==0;results[entry]=r.returncode
receipt={'source_commit':m['source_commit'],'wheel':str(wheel),'sha256':m['sha256'],'install_exit':0,'console_help_exit':results,'legacy_backend_modules_in_wheel':False,'contains_reference_and_summary_fix':True,'third_party_dependency_paths':[str(p) for p in paths],'standalone_dependency_resolution':'NOT_CLAIMED; reused explicit read-only third-party dependencies','real_provider_calls':0}
(artifact/'installed-entry.receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8');print(json.dumps(receipt,indent=2))
