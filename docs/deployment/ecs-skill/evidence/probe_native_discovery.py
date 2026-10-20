"""Query the installed app-server's local skills/list; never start a model turn."""
from pathlib import Path
import json, os, queue, subprocess, threading, time, hashlib

R=Path(__file__).resolve().parent
repo=Path(r'E:\RAG-Worktrees\ecs-incremental-deploy-skill-20261020')
exe=Path(r'C:\Users\22088\AppData\Roaming\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe')
home=R/'native-discovery-home';home.mkdir(exist_ok=False)
env=os.environ.copy();env['CODEX_HOME']=str(home)
env['TMP']=str(home);env['TEMP']=str(home)
cmd=[str(exe),'app-server','--stdio','-c','analytics.enabled=false']
p=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,encoding='utf-8',env=env,cwd=repo)
q=queue.Queue();errs=[]
def stdout():
    for line in p.stdout:
        try:q.put(json.loads(line))
        except ValueError:q.put({'non_json':line[:300]})
def stderr():
    for line in p.stderr:errs.append(line)
threading.Thread(target=stdout,daemon=True).start();threading.Thread(target=stderr,daemon=True).start()
def send(d):p.stdin.write(json.dumps(d)+'\n');p.stdin.flush()
def receive(ident):
    end=time.monotonic()+30
    while time.monotonic()<end:
        try:d=q.get(timeout=1)
        except queue.Empty:continue
        if d.get('id')==ident:return d
    raise TimeoutError('LOCAL_SKILLS_LIST_RESPONSE_NOT_RECEIVED')
try:
    send({'id':1,'method':'initialize','params':{'clientInfo':{'name':'ecs-skill-offline-discovery','version':'1.0.0'},'capabilities':{'experimentalApi':True}}})
    init=receive(1);assert 'result' in init,init
    send({'method':'initialized'})
    send({'id':2,'method':'skills/list','params':{'cwds':[str(repo)],'forceReload':True}})
    listed=receive(2)
    (R/'native-discovery-response.json').write_text(json.dumps(listed,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    assert 'result' in listed,listed
    # Full metadata can include system/user skill paths; retain only project match in delivery.
    matches=[]
    def walk(v):
        if isinstance(v,dict):
            if v.get('name')=='ecs-incremental-deploy':matches.append(v)
            for x in v.values():walk(x)
        elif isinstance(v,list):
            for x in v:walk(x)
    walk(listed)
    assert len(matches)==1, {'matches':len(matches)}
    result={'status':'PASS','type':'NATIVE_LOCAL_SKILLS_LIST','command':cmd,'cwd':str(repo),'isolated_codex_home':str(home),'model_turns':0,'ssh_calls':0,'docker_calls':0,'global_config_or_skills_modified':False,'project_skill':matches[0],'init_response':init,'skill_sha256':hashlib.sha256((repo/'.agents/skills/ecs-incremental-deploy/SKILL.md').read_bytes()).hexdigest()}
    (R/'native-discovery.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'PASS','name':matches[0]['name'],'path':matches[0].get('path'),'scope':matches[0].get('scope'),'model_turns':0}))
finally:
    if p.poll() is None:
        p.stdin.close()
        try:p.wait(timeout=5)
        except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=5)
    (R/'native-discovery.stderr.log').write_text(''.join(errs),encoding='utf-8')
