"""Read-only interpretation of explicit MinerU bmatrix syntax, never Gold.

Positions below are original span character ranges plus the original block bbox,
not invented cell-level pixel boxes. This diagnostic is not a production parser.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re


def matrices(middle, derived_text):
    output=[]
    for page_index,page in enumerate(middle['pages']):
        for block_index,block in enumerate(page['blocks']):
            for span_index,span in enumerate(block.get('content',[])):
                raw=span.get('content','')
                if span.get('type') not in ('equation_inline','equation') or not isinstance(raw,str):
                    continue
                for match in re.finditer(r'\\begin\{bmatrix\}(.*?)\\end\{bmatrix\}',raw,re.S):
                    rows=[];cursor=match.start(1)
                    for row_number,row in enumerate(re.split(r'\\\\',match[1])):
                        cells=[];column_cursor=cursor
                        for column_number,cell in enumerate(row.split('&')):
                            literal=cell.strip()
                            # Whitespace removal only; broken signs and any
                            # nonnumeric symbols remain explicitly UNKNOWN.
                            value=re.sub(r'\s+','',literal)
                            if not re.fullmatch(r'[+-]?\d+',value,re.ASCII):value='UNKNOWN'
                            start=column_cursor+len(cell)-len(cell.lstrip())
                            end=column_cursor+len(cell.rstrip())
                            cells.append({'row':row_number,'column':column_number,'raw':literal,'value':value,
                                          'span_start':start,'span_end':end})
                            column_cursor+=len(cell)+1
                        rows.append(cells);cursor+=len(row)+2
                    occurrence=derived_text.find(raw)
                    unique=occurrence>=0 and derived_text.find(raw,occurrence+1)<0
                    output.append({'middle_pointer':f'/pages/{page_index}/blocks/{block_index}/content/{span_index}',
                        'page_idx':page['page_idx'],'block_index':block['index'],'block_bbox':block['bbox'],
                        'bbox_basis':'normalized_page_block_not_cell', 'raw_span':raw,
                        'raw_span_sha256':hashlib.sha256(raw.encode()).hexdigest(),
                        'preceding_label':block['content'][span_index-1].get('content','') if span_index else '',
                        'derived_span_start':occurrence if unique else None,'derived_span_unique':unique,
                        'rows':rows,'complete_literals':all(c['value']!='UNKNOWN' for row in rows for c in row),
                        'column_counts':[len(row) for row in rows]})
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--middle',type=Path,required=True)
    p.add_argument('--derived-text',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    middle=json.loads(args.middle.read_text(encoding='utf-8'))
    text=args.derived_text.read_text(encoding='utf-8')
    result={'schema':'m34-mineru-matrix-diagnostic/v1','rule':'Only explicit bmatrix row/column delimiters and literal integers; no expected dimensions or values.',
        'middle_sha256':hashlib.sha256(args.middle.read_bytes()).hexdigest(),
        'derived_text_sha256':hashlib.sha256(text.encode()).hexdigest(),'matrices':matrices(middle,text),
        'producer':middle['metadata']['producer'],'mode':middle['extensions']['mineru'],
        'cloud_calls':0,'ocr_calls':0,'data_origin':'New local MinerU output, not original PDF/image text or Gold.'}
    for matrix in result['matrices']:
        for row in matrix['rows']:
            for cell in row:
                assert matrix['raw_span'][cell['span_start']:cell['span_end']]==cell['raw']
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('MATRIX_STRUCTURE_FROM_MINERU',[(m['column_counts'],m['complete_literals']) for m in result['matrices']])


if __name__=='__main__':main()
