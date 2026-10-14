"""Build an immutable raw-Git snapshot at a short new E-drive artifact path."""
import argparse,hashlib,json,subprocess,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def run(args):
    commit=subprocess.check_output(['git','rev-parse',args.ref+'^{commit}'],cwd=ROOT,text=True).strip()
    target=Path(args.output).resolve()
    allowed=Path('E:/WK/pkg').resolve()
    if not target.is_relative_to(allowed) or target==allowed:raise ValueError('NEW_SHORT_ARTIFACT_PATH_REQUIRED')
    if target.exists():raise ValueError('IMMUTABLE_ARTIFACT_ALREADY_EXISTS')
    target.mkdir(parents=True);source=target/'src';source.mkdir();dist=target/'dist';dist.mkdir()
    names=subprocess.check_output(['git','ls-tree','-r','--name-only',commit,'backend','eval_center','pyproject.toml'],cwd=ROOT,text=True).splitlines()
    batch=''.join(commit+':'+name+'\n' for name in names).encode()
    output=subprocess.check_output(['git','cat-file','--batch'],input=batch,cwd=ROOT)
    position=0;hashes={}
    for name in names:
        end=output.index(b'\n',position);header=output[position:end].split();size=int(header[2]);position=end+1
        assert header[1]==b'blob'
        raw=output[position:position+size];position+=size+1
        path=(source/name).resolve()
        if not path.is_relative_to(source.resolve()):raise ValueError('SOURCE_PATH_ESCAPE')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);hashes[name]=hashlib.sha256(raw).hexdigest()
    buildpython=ROOT/'var/weknora-port/p8-build/venv/Scripts/python.exe'
    command=[str(buildpython),'-I','-B','-c',
        'import os;os.chdir('+repr(str(source))+');from setuptools.build_meta import build_wheel;print(build_wheel('+repr(str(dist))+'))']
    completed=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (target/'build.stdout.txt').write_bytes(completed.stdout);(target/'build.exitcode.txt').write_text(str(completed.returncode)+'\n')
    if completed.returncode:raise RuntimeError('ISOLATED_WHEEL_BUILD_FAILED')
    wheels=list(dist.glob('*.whl'));assert len(wheels)==1
    wheel=wheels[0];verified=[]
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.startswith(('backend/weknora_port/','eval_center/weknora')) and not name.endswith('/'):
                assert name in hashes and hashlib.sha256(archive.read(name)).hexdigest()==hashes[name],name
                verified.append(name)
    receipt={'kind':'RAW_GIT_SNAPSHOT_WHEEL_BYTE_CHECK','source_commit':commit,'source_files':len(names),'verified_native_files':len(verified),
        'wheel':str(wheel),'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'bytes':wheel.stat().st_size,'source_sha256':hashes,
        'build_exit':completed.returncode,'real_provider_calls':0,'installed_import':'NOT_RUN','standalone_dependencies':'NOT_CLAIMED','cutover':'NOT_RUN'}
    (target/'manifest.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in receipt.items() if k!='source_sha256'},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--ref',required=True);parser.add_argument('--output',required=True)
    run(parser.parse_args())
