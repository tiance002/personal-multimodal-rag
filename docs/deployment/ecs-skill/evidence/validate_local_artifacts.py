"""Validate authored Skill resources and receipts without executing deployment commands."""
from pathlib import Path
import argparse,hashlib,importlib.util,json,re,subprocess,sys
import yaml

HERE=Path(__file__).resolve().parent
DEFAULT_REPO=Path(r'E:\RAG-Worktrees\ecs-incremental-deploy-skill-20261020')
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--repo',type=Path,default=DEFAULT_REPO);ap.add_argument('--output',type=Path,default=HERE/'artifact-validation.json');args=ap.parse_args()
    repo=args.repo.resolve();skill=repo/'.agents/skills/ecs-incremental-deploy';checks=[]
    v=Path(r'C:\Users\22088\.codex\skills\.system\skill-creator\scripts\quick_validate.py')
    p=subprocess.run([sys.executable,'-X','utf8',str(v),str(skill)],capture_output=True,text=True,encoding='utf-8')
    checks.append({'name':'installed_creator_frontmatter_validator','exit_code':p.returncode,'stdout':p.stdout.strip(),'pass':p.returncode==0})
    refs=[]
    for f in [skill/'SKILL.md',*(skill/'references').glob('*.md')]:
        text=f.read_text(encoding='utf-8')
        assert '\ufffd' not in text,'REPLACEMENT_CHARACTER_'+f.name
        for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)',text):
            if re.match(r'https?://',link):continue
            target=(f.parent/link.split('#')[0]).resolve()
            assert target.is_relative_to(repo),str(target)
            refs.append({'from':str(f.relative_to(repo)),'target':str(target.relative_to(repo)),'exists':target.exists()})
    checks.append({'name':'all_local_reference_links_within_repo_exist','pass':all(x['exists'] for x in refs),'links':refs})
    meta=yaml.safe_load((skill/'agents/openai.yaml').read_text(encoding='utf-8'))
    assert meta['policy']['allow_implicit_invocation'] is True
    assert '$ecs-incremental-deploy' in meta['interface']['default_prompt']
    assert '\ufffd' not in (skill/'agents/openai.yaml').read_text(encoding='utf-8')
    checks.append({'name':'native_metadata_valid_implicit_enabled','pass':True})
    prompts=json.loads((skill/'test-prompts.json').read_text(encoding='utf-8'))
    assert len(prompts)>=3 and len({x['id'] for x in prompts})==len(prompts)
    assert all(x['prompt'] and x['fixture'] and x['expected'] for x in prompts)
    checks.append({'name':'frozen_test_contract_complete','pass':True,'count':len(prompts)})
    native=json.loads((HERE/'native-discovery.json').read_text(encoding='utf-8'))
    assert native['project_skill']['name']=='ecs-incremental-deploy' and native['project_skill']['scope']=='repo' and native['project_skill']['enabled'] is True
    assert native['model_turns']==0 and native['ssh_calls']==0 and native['docker_calls']==0
    checks.append({'name':'actual_installed_native_project_discovery','pass':True,'record':str(HERE/'native-discovery.json'),'scope':'repo','probe_model_turns':0,'baseline_skill_sha256':native['skill_sha256'],'note':'Discovery probe was at baseline; final file revalidation independent from automatic UI selection.'})
    source_scope=subprocess.run(['git','-C',str(repo),'diff','86d42a7374f738c996e7804a067b0052291ec997','--name-only','--','backend','frontend','deploy','scripts'],capture_output=True,text=True)
    assert source_scope.returncode==0 and not source_scope.stdout.strip()
    checks.append({'name':'no_runtime_or_deployment_source_changes','pass':True,'git_diff_exit_code':source_scope.returncode})
    pat=[r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',r'sk-[A-Za-z0-9_-]{20,}',r'Bearer\s+[A-Za-z0-9._~-]{24,}',r'postgres(?:ql)?://[^\s:@/]+:[^\s@/]+@']
    for f in skill.rglob('*'):
        if f.is_file():
            s=f.read_text(encoding='utf-8');assert not any(re.search(x,s) for x in pat),'SENSITIVE_PATTERN_'+f.name
    checks.append({'name':'skill_credentials_pattern_scan','pass':True,'note':'Pattern scan only, not proof of all possible secret forms; source prose reviewed independently.'})
    result={'status':'PASS' if all(c['pass'] for c in checks) else 'FAIL','scope':'Local file/metadata/real native skills-list receipts only; noECS, noDocker, noRAGProvider','checks':checks,'skill_sha256':digest(skill/'SKILL.md'),'files':[{'path':str(f.relative_to(repo)),'sha256':digest(f),'bytes':f.stat().st_size} for f in skill.rglob('*') if f.is_file()]}
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'checks':len(checks),'references':len(refs),'files':len(result['files'])}));return 0 if result['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
