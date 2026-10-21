"""Offline h1 ZIP checks for already downloaded target modules, no graph expansion."""
from pathlib import Path
import hashlib,base64,json,zipfile
ROOT=Path('E:/RAG-reference/WeKnora-3e8b0bfc')
CACHE=Path('D:/WK/go/modules/cache/download')
sums={}
for line in (ROOT/'go.sum').read_text(encoding='utf-8').splitlines():
    module,version,digest=line.split();sums[(module,version)]=digest
rows=[];excluded=[]
for file in sorted(CACHE.rglob('*.ziphash')):
    relative=file.relative_to(CACHE).as_posix();escaped,filename=relative.rsplit('/@v/',1)
    def unescape(value):
        import re
        return re.sub(r'!([a-z])',lambda m:m[1].upper(),value)
    module=unescape(escaped);version=unescape(filename[:-8])
    expected=sums.get((module,version))
    if expected is None:
        excluded.append({'module':module,'version':version,'reason':'not in fixed upstream go.sum; existing cache preserved'})
        continue
    archive=file.with_suffix('.zip');h=hashlib.sha256()
    with zipfile.ZipFile(archive) as z:
        for name in sorted(z.namelist()):
            if name.endswith('/'):continue
            if '\n' in name:raise ValueError('ZIP_NAME_NEWLINE')
            digest=hashlib.sha256(z.read(name)).hexdigest()
            h.update((digest+'  '+name+'\n').encode())
    actual='h1:'+base64.b64encode(h.digest()).decode()
    recorded=file.read_text(encoding='utf-8').strip()
    assert actual==expected==recorded,(module,version,actual,expected,recorded)
    rows.append({'module':module,'version':version,'h1':actual,'zip':str(archive)})
assert rows
algorithm=Path('E:/RAG-reference/toolchains/go1.26.0/go/src/cmd/vendor/golang.org/x/mod/sumdb/dirhash/hash.go')
result={'scope':'Already downloaded target module ZIP bytes only; not full graph go mod verify or extracted-directory integrity',
    'upstream':'3e8b0bfc80b845b2d4b2ed683994748741450a97','cache':str(CACHE),
    'go_sum_sha256':hashlib.sha256((ROOT/'go.sum').read_bytes()).hexdigest(),
    'algorithm_source':str(algorithm),'algorithm_source_sha256':hashlib.sha256(algorithm.read_bytes()).hexdigest(),
    'count':len(rows),'modules':rows,'excluded':excluded,'status':'PASS'}
output=Path('docs/weknora-port/evidence/QA_ENTRY_20261015/go.cached-zip-h1.json')
assert not output.exists()
output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'cached_zip_h1':'PASS','count':len(rows)}))
