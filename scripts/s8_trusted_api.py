"""Trusted S8 loopback API; zero-cloud by default, no Worker or grant creation."""
from __future__ import annotations
import argparse, asyncio, os, socket, time, uuid
from contextlib import asynccontextmanager
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.s8_evaluation_controller import ARMS, arm_environment, canonical, read, sha, validate_authorization, validate_identity, validate_plan
from scripts.s7_graph_api_check import overlay_cheap
from scripts.s7_graph_generation import QWEN, QWEN_ID, require, write_new


def resource_identity(actual):
    return {key:actual[key] for key in ('postgres_system_id','database','redis_run_id','redis_db')}


def verify_identity(plan,actual,principal,storage):
    resource=resource_identity(actual)
    identity={'tenant_id':principal.tenant_id,'subject_sha256':sha(principal.subject_id),'storage_root':str(Path(storage).resolve())}
    require(resource==plan['resource_identity'] and sha(canonical(resource))==plan['runtime_identity_sha256'],'S8_RESOURCE_IDENTITY_MISMATCH')
    require(identity==plan['principal'],'S8_TRUSTED_PRINCIPAL_MISMATCH')
    return identity


def binding_snapshot(configured):
    return {'configured_roles':configured,'effective_roles':{**configured,'cheap':{'provider':'siliconflow','model':QWEN,'model_id':QWEN_ID}},
            'query_understand_model_id':QWEN_ID,'database_role_write':False}


def execution_mode(args):
    require(args.cloud is False or args.authorization is not None and args.ledger is not None,'S8_NEW_CLOUD_AUTHORIZATION_REQUIRED')
    require(not args.cloud or bool(args.case_id),'S8_CLOUD_CASE_ID_REQUIRED')
    require(args.cloud or args.ledger is None and args.authorization is None,'S8_READONLY_MODE_HAS_NO_LEDGER')
    return args.cloud


def authorize_case(parent,ledger,plan,identity,case_id,arm):
    selected=[case for case in plan['cases'] if case['case_id']==case_id and arm in case.get('eligible_arms',['A'])]
    require(len(selected)==1,'S8_APPROVED_ARM_CASE_REQUIRED')
    validate_authorization(parent,ledger,plan,identity,selected[0],arm)
    return selected[0]['case_id']


async def serve(args):
    import uvicorn
    from scripts.local_runtime import load_config,environment,verify_resources
    from scripts.s2_generation_quality import identity_for
    from scripts.m4_tenant332_api import BearerTokenGuard
    from backend.weknora_port.development import DevelopmentSettings,assemble
    from backend.weknora_port.entrypoint import create_app
    from backend.weknora_port.storage.configuration import PostgresModelRoleStore
    cloud=execution_mode(args); plan=validate_plan(read(args.plan)); cfg=load_config(args.config)
    actual=await verify_resources(cfg)
    env,principal=identity_for(332,environment(cfg,actual['redis_run_id']),read(args.principal_state))
    for key in ('WEKNORA_PORT_EXECUTION_FILE','WEKNORA_PORT_EVIDENCE_DIR'):env.pop(key,None)
    env.update(arm_environment(args.arm),WEKNORA_PORT_CLOUD_ALLOWED=str(cloud).lower(),
               WEKNORA_PORT_QUERY_UNDERSTAND_MODEL_ID=QWEN_ID,WEKNORA_PORT_REDIS_PREFIX='weknora-python-s8-'+uuid.uuid4().hex)
    if cloud:env['WEKNORA_PORT_EXECUTION_FILE']=str(args.ledger.resolve())
    settings=DevelopmentSettings.from_environment(env)
    principal_identity=verify_identity(plan,actual,principal,settings.storage)
    require(settings.router_mode=='DYNAMIC','S8_ORIGINAL_DYNAMIC_REQUIRED')
    token=os.environ.get('S8_API_BEARER_TOKEN',''); require(len(token)>=32,'S8_EPHEMERAL_BEARER_REQUIRED')
    if not args.port:
        with socket.socket() as listener:listener.bind(('127.0.0.1',0)); args.port=listener.getsockname()[1]
    identity={**principal_identity,'runtime_identity_sha256':sha(canonical(resource_identity(actual))),
              'graph_enabled':ARMS[args.arm]['graph'],'rerank_top1_floorless':ARMS[args.arm]['floorless'],
              'base_url':f'http://127.0.0.1:{args.port}','model_binding_snapshot':plan['model_binding_snapshot']}
    if cloud:
        parent=read(args.authorization)
        identity['authorized_case_id']=authorize_case(parent,args.ledger,plan,identity,args.case_id,args.arm)
    args.output.mkdir(parents=True,exist_ok=True)

    @asynccontextmanager
    async def factory(active):
        async with assemble(active,principal=principal) as runtime:
            # Strict read + archived resolve, not native _snapshot/persist: startup has zero model-row writes.
            async with runtime.leases.lease() as guard:
                _,roles=await PostgresModelRoleStore(guard,principal,runtime.catalogue,cipher=runtime.cipher).read()
            configured={}
            for role in roles:
                if role.role not in ('cheap','expensive','embedding','rerank'):continue
                model_id=runtime.models._identity(role)[0]; resolved=await runtime.models.resolve(model_id)
                require((role.provider,role.model)==(resolved.provider,resolved.model),'S8_ARCHIVED_ROLE_MISMATCH')
                configured[role.role]={'provider':role.provider,'model':role.model,'model_id':model_id}
            identity['model_binding_snapshot']=binding_snapshot(configured)
            validate_identity(plan,identity,args.arm)
            await overlay_cheap(runtime)
            write_new(args.output/'startup.json',{**identity,'cloud_allowed':cloud,'resource_identity':resource_identity(actual),
                'redis_prefix':settings.redis_prefix,'worker_started':False,'database_roles_changed':False})
            yield runtime

    app=BearerTokenGuard(create_app(settings=settings,runtime_factory=factory),token,float(os.environ.get('S8_API_TOKEN_EXPIRES',time.time()+2400)))
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=args.port,log_level='warning',access_log=False))
    async def stop():
        while not (args.output/'stop.request').exists():await asyncio.sleep(.2)
        if cloud and any(row['status']=='INFLIGHT' for row in read(args.ledger)['entries']):return
        server.should_exit=True
    watcher=asyncio.create_task(stop())
    try:await server.serve()
    finally:watcher.cancel(); await asyncio.gather(watcher,return_exceptions=True)


def arguments(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('plan','config','principal-state','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--arm',choices=tuple(ARMS),required=True);parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--cloud',action='store_true');parser.add_argument('--authorization',type=Path);parser.add_argument('--ledger',type=Path)
    parser.add_argument('--case-id')
    return parser.parse_args(argv)


if __name__=='__main__':
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:runner.run(serve(arguments()))
