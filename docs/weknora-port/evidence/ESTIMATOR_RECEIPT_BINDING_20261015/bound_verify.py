"""Run only the affected tokenizer check against one archived, hash-bound source form."""
from pathlib import Path
import sys,json,hashlib,importlib.util,importlib.machinery
import backend.weknora_port.generation
import pytest
folder=Path(__file__).resolve().parent
variant=sys.argv[1];assert variant in ('receipt','git')
source=folder/('estimator.'+variant+'.raw')
name='backend.weknora_port.generation.estimator'
loader=importlib.machinery.SourceFileLoader(name,str(source));spec=importlib.util.spec_from_loader(name,loader)
module=importlib.util.module_from_spec(spec);sys.modules[name]=module;loader.exec_module(module)
root=folder.parents[3]
code=pytest.main(['--noconftest',str(root/'tests/weknora_port/test_estimator.py'),'-q','-p','no:cacheprovider','--tb=short'])
(folder/(variant+'.test-binding.json')).write_text(json.dumps({'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'imported_file':module.__file__,'test':str(root/'tests/weknora_port/test_estimator.py'),'test_sha256':hashlib.sha256((root/'tests/weknora_port/test_estimator.py').read_bytes()).hexdigest(),'pytest_exit':code,'real_provider_calls':0},indent=2)+'\n',encoding='utf-8')
raise SystemExit(code)
