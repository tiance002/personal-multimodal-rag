"""Reproduce pinned native renderer dependency; all business rules stay Python."""
import argparse,hashlib,json,os,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def build(output):
    target=Path(output).resolve();allowed=Path('E:/WK/deps').resolve()
    if not target.is_relative_to(allowed) or target==allowed or target.exists():raise ValueError('NEW_NATIVE_DEPENDENCY_PATH_REQUIRED')
    target.mkdir(parents=True);work=target/'build';work.mkdir()
    source=ROOT/'backend/weknora_port/ingestion/html_codec_sources'
    for name in ['main.go','go.mod','go.sum']:(work/name).write_bytes((source/name).read_bytes())
    env=dict(os.environ,GOMODCACHE='D:/WK/go/modules',GOCACHE='E:/WK/go/cache',GOPROXY='off',GOSUMDB='off',GOTOOLCHAIN='local',CGO_ENABLED='0',GOTELEMETRY='off')
    executable=target/'htmlcodec.exe'
    result=subprocess.run(['E:/RAG-reference/toolchains/go1.26.0/go/bin/go.exe','build','-trimpath','-buildvcs=false','-o',str(executable),'.'],cwd=work,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (target/'build.stdout.txt').write_bytes(result.stdout);(target/'build.exitcode.txt').write_text(str(result.returncode))
    if result.returncode:raise RuntimeError('PINNED_HTML_LIBRARY_BUILD_FAILED')
    manifest={'path':str(executable),'sha256':hashlib.sha256(executable.read_bytes()).hexdigest(),'bytes':executable.stat().st_size,'library':'html-to-markdown/v2 v2.5.1','xnet':'v0.56.0',
        'source_sha256':{name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in ['main.go','go.mod','go.sum']},'scope':'Primitive renderer only; no WeKnora business/DB/network/Agent/arbitrary commands'}
    (target/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8');print(json.dumps(manifest,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);build(parser.parse_args().output)
