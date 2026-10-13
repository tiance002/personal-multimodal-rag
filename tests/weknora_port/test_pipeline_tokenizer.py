"""Pinned source fixtures, not a claim of direct Go execution."""
import hashlib,json
from pathlib import Path
import pytest
from backend.weknora_port.retrieval.query import default_segmenter,CppJiebaSegmenter
FIXTURES = [('小明硕士毕业于中国科学院计算所，后在日本京都大学深造', ['小明', '硕士', '毕业', '于', '中国', '科学', '学院', '科学院', '中国科学院', '计算', '计算所', '，', '后', '在', '日本', '京都', '大学', '日本京都大学', '深造'])]
@pytest.mark.parametrize('query,expected',FIXTURES)
def test_pinned_gojieba_source_fixture(query,expected):
    assert default_segmenter()(query)==expected

def test_exact_assets_and_cached_instance():
    directory=Path(__file__).parents[2]/'backend/weknora_port/retrieval/tokenizer_assets'
    manifest=json.loads((directory/'manifest.json').read_text())
    assert manifest['version']=='v1.4.7' and manifest['go_sum']=='h1:2YkXELcYLTE0SJetq6xv4MjpEikWga6VpFn4jIFFQ/k='
    for filename,expected in manifest['files'].items():
        data=(directory/filename).read_bytes()
        assert hashlib.sha256(data).hexdigest()==expected['sha256'] and len(data)==expected['bytes']
    assert default_segmenter() is default_segmenter()

@pytest.mark.parametrize('query,expected',[
    ('ABC123 12.5',['ABC123',' ','12.5']),
    ('abc-def',['abc','-','def']),
    ('\u957f\u6c5f\u5927\u6865',['\u957f\u6c5f','\u5927\u6865','\u957f\u6c5f\u5927\u6865']),
    ('\u4ed6\u6765\u5230\u4e86\u7f51\u6613\u676d\u7814\u5927\u53a6',['\u4ed6','\u6765\u5230','\u4e86','\u7f51\u6613','\u676d\u7814','\u5927\u53a6']),
])
def test_mp_mix_hmm_query_branches(query,expected):assert default_segmenter()(query)==expected
