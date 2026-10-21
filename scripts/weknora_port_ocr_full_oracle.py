"""Fixed Go source full OCR oracle; never an application/parser backend."""
import argparse,hashlib,json,os,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];UP=Path('E:/RAG-reference/WeKnora-3e8b0bfc');PIN='3e8b0bfc80b845b2d4b2ed683994748741450a97';WORK=Path('E:/WK/oracle/ocr-html')
GO='E:/RAG-reference/toolchains/go1.26.0/go/bin/go.exe'


def source(name):return subprocess.check_output(['git','show',PIN+':'+name],cwd=UP).decode('utf-8')


def prepare():
    WORK.mkdir(parents=True,exist_ok=True)
    for original,target in [('internal/application/service/ocr_sanitizer.go','ocr.go'),('internal/infrastructure/docparser/html_table_normalizer.go','table.go')]:
        text=source(original).replace('package service','package main',1).replace('package docparser','package main',1)
        text=text.replace('\n\t"github.com/Tencent/WeKnora/internal/infrastructure/docparser"','').replace('docparser.NormalizeHTMLTables','NormalizeHTMLTables')
        (WORK/target).write_text(text,encoding='utf-8')
    mineru=source('internal/infrastructure/docparser/mineru_converter.go');start=mineru.index('func unescapeMarkdownImageSyntax(');end=mineru.index('\n}',start)+2
    variable=next(line.strip() for line in mineru.splitlines() if line.startswith('var escapedImageSyntaxPattern = regexp.MustCompile'))
    (WORK/'image_unescape.go').write_text('package main\nimport "regexp"\n'+variable+'\n'+mineru[start:end]+'\n',encoding='utf-8')
    # go.mod/go.sum already frozen and officially checksummed in the initial module receipt.
    assert 'html-to-markdown/v2 v2.5.1' in (WORK/'go.mod').read_text()
    env=dict(os.environ,GOMODCACHE='D:/WK/go/modules',GOCACHE='E:/WK/go/cache',GOPROXY='off',GOSUMDB='off',GOTOOLCHAIN='local',CGO_ENABLED='0',GOTELEMETRY='off')
    built=subprocess.run([GO,'build','-trimpath','-o',str(WORK/'oracle.exe'),'.'],cwd=WORK,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    if built.returncode:raise RuntimeError('FIXED_SOURCE_ORACLE_BUILD_FAILED:'+built.stdout.decode(errors='replace'))


def emit():
    cases=['','   ','<html><body><img/></body></html>','No text content.','```html\n<p>这是一段内容</p>\n```',
        '<html><body><h1>标题</h1><p>这是一段很长的正文内容。</p></body></html>',
        'This is mostly text with a <b>bold</b> word.',
        '<p>A substantial paragraph with <strong>strong text</strong>, <em>italic text</em> and <a href="https://example.test">link</a>.</p>',
        '<div><h2>报告摘要</h2><p>本季度营收同比增长15%。</p><table><tr><th>指标</th><th>数值</th></tr><tr><td>营收</td><td>10亿</td></tr></table></div>',
        '# Report\n\n'+'A long surrounding paragraph. '*10+'\n\n<table style="color:red"><tr><th>Metric</th><th>Value</th></tr><tr><td>Revenue</td><td>10</td></tr></table>\n\nEnd.',
        '# Table example\n\n```html\n<table><tr><td>Do not change fenced code</td></tr></table>\n```',
        '# Spans\n\n'+'Plain context. '*15+'<table class="layout"><tr><td rowspan="2">Merged cell</td><td>One</td></tr><tr><td>Two</td></tr></table>',
        '<ul><li>First substantial item</li><li>Second substantial item</li></ul>',
        '<blockquote><p>A meaningful quotation</p></blockquote>',
        '<pre><code>print("substantial content")</code></pre>',
        '<p>Line one<br/>Line two substantial text</p>']
    result=subprocess.run([str(WORK/'oracle.exe')],input=json.dumps(cases,ensure_ascii=False).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    assert result.returncode==0,result.stderr.decode(errors='replace')
    rows=json.loads(result.stdout);assert len(rows)==len(cases)
    report={'kind':'FIXED_SOURCE_FULL_OCR_HTML_TABLE_CONVERTER_ORACLE','upstream':PIN,'html_library':'html-to-markdown/v2 v2.5.1','xnet':'v0.56.0','binary_sha256':hashlib.sha256((WORK/'oracle.exe').read_bytes()).hexdigest(),
        'cases':[{'input':text,'output':output} for text,output in zip(cases,rows)],'exit_code':result.returncode,'real_provider_calls':0,'python_implementation':'NOT_IMPLEMENTED_FULL_CONVERTER','scope':'Oracle only, no runtime/nativehelper/backend integration'}
    (ROOT/'tests/weknora_port/fixtures/ocr_full_go_oracle.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'docs/weknora-port/evidence/P2_OCR_FULL_ORACLE_20261014.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'exit_code':0,'cases':len(cases),'binary_sha256':report['binary_sha256'],'real_provider_calls':0,'python_full_converter':'NOT_IMPLEMENTED'}))


if __name__=='__main__':prepare();emit()
