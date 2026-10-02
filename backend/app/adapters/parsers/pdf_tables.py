"""Map mature PyMuPDF tables; do not reconstruct missing PDF table structure."""
from __future__ import annotations

import math

import fitz

from backend.app.domain.models import DocumentTable, TableCell
from backend.app.domain.table_evidence import table_row_texts


def snapshot(table, strategy: str) -> dict[str, object]:
    """Detach all API evidence while its page/document is still alive."""
    return {
        'strategy': strategy, 'parser': f'PyMuPDF/{fitz.VersionBind}',
        'coordinate_system': 'unrotated PDF points, top-left; page 1-based',
        'bbox': list(table.bbox), 'row_count': table.row_count,
        'column_count': table.col_count, 'rows': table.extract(),
        'row_cells': [[list(c) if c is not None else None for c in r.cells] for r in table.rows],
        'header': {'names': list(table.header.names), 'external': table.header.external,
                   'bbox': list(table.header.bbox),
                   'cells': [list(c) if c is not None else None for c in table.header.cells]},
    }


def map_table(raw: dict, page: fitz.Page, page_no: int, index: int) -> DocumentTable:
    """Accept only API cells that exactly cover the API's own rectangular grid."""
    grid, values = raw['row_cells'], raw['rows']
    nr, nc = raw['row_count'], raw['column_count']
    if not 0 < nr <= 1000 or not 0 < nc <= 200 or nr * nc > 50000:
        raise ValueError('PDF_TABLE_LIMIT')
    if len(grid) != nr or len(values) != nr or any(len(r) != nc for r in grid + values):
        raise ValueError('PDF_TABLE_SPAN_UNCONFIRMED')
    rects = [c for row in grid for c in row if c is not None]
    if any(not all(math.isfinite(v) for v in c) or c[0] >= c[2] or c[1] >= c[3] for c in rects):
        raise ValueError('PDF_TABLE_SPAN_UNCONFIRMED')
    xs = sorted({v for c in rects for v in (c[0], c[2])})
    ys = sorted({v for c in rects for v in (c[1], c[3])})
    if len(xs) != nc + 1 or len(ys) != nr + 1 or [xs[0],ys[0],xs[-1],ys[-1]] != raw['bbox']:
        raise ValueError('PDF_TABLE_SPAN_UNCONFIRMED')
    occupied = set()
    cells = []
    header = raw['header']
    internal_header = not header['external'] and header['cells'] == grid[0]
    for r, row in enumerate(grid):
        for c, box in enumerate(row):
            if box is None:
                continue
            r0,r1 = ys.index(box[1]),ys.index(box[3])
            c0,c1 = xs.index(box[0]),xs.index(box[2])
            if (r0,c0) != (r,c):
                raise ValueError('PDF_TABLE_SPAN_UNCONFIRMED')
            for rr in range(r0,r1):
                for cc in range(c0,c1):
                    if (rr,cc) in occupied or ((rr,cc) != (r,c) and (grid[rr][cc] is not None or values[rr][cc] is not None)):
                        raise ValueError('PDF_TABLE_SPAN_UNCONFIRMED')
                    occupied.add((rr,cc))
            text = values[r][c]
            if not isinstance(text,str) or len(text) > 4096:
                raise ValueError('PDF_TABLE_TEXT_UNCONFIRMED')
            labels = tuple(f'INFERRED: {name}' for name in header['names'][c0:c1] if name)
            coord = f'R{r+1}C{c+1}'
            cells.append(TableCell(coordinate=coord,row=r+1,column=c+1,
                value=text,value_type='text' if text else 'empty',display=text,number_format='source_text',
                row_span=r1-r0,column_span=c1-c0,bbox=tuple(box),column_headers=labels,
                column_header=None,row_header=None,
                header_evidence={'status':'UNKNOWN','method':'PyMuPDF heuristic','header':header},
                native_locator={'page':page_no,'bbox':box,'table_bbox':raw['bbox'],
                    'raw_text':page.get_textbox(fitz.Rect(box)), 'extracted_text':text,
                    'strategy':raw['strategy'],'parser':raw['parser'],
                    'coordinate_system':raw['coordinate_system']}))
    if len(occupied) != nr*nc:
        raise ValueError('PDF_TABLE_SPAN_UNCONFIRMED')
    merges = tuple(f'{cell.coordinate}:R{cell.row+cell.row_span-1}C{cell.column+cell.column_span-1}'
                   for cell in cells if cell.row_span > 1 or cell.column_span > 1)
    return DocumentTable(table_id=f'pdf-p{page_no}-t{index}',source_format='pdf',page=page_no,
        bbox=tuple(raw['bbox']),raw_evidence=raw,cell_range=f'R1C1:R{nr}C{nc}',
        header_rows=(1,) if internal_header else (),header_detection='pymupdf-inferred-semantic-UNKNOWN',
        cells=cells,merged_ranges=merges,start=0,end=0)


def page_tables(page: fitz.Page, page_no: int, raw_text: str):
    """Return retrieval pieces, detached page evidence, and explicit limitations.

    Entire native text blocks must fit the confirmed table bbox. Any intersecting
    block that crosses it prevents admission, so prose is never silently clipped.
    Text-strategy guesses remain raw diagnostic evidence, outside table retrieval.
    """
    evidence = {'table_status':'not_detected','table_candidates':[]}
    warnings = []
    suffix = f':page={page_no}'
    if not raw_text.strip():
        evidence['table_status'] = 'unsupported'
        return [(raw_text,None)], evidence, ['PDF_TABLE_STRUCTURE_UNSUPPORTED'+suffix]
    if page.rotation:
        evidence['table_status'] = 'unsupported'
        return [(raw_text,None)], evidence, ['PDF_TABLE_ROTATION_UNSUPPORTED'+suffix]
    try:
        candidates = [snapshot(t,'lines') for t in page.find_tables(strategy='lines').tables]
        if not candidates:
            candidates = [snapshot(t,'text') for t in page.find_tables(strategy='text').tables]
        evidence['table_candidates'] = candidates
    except Exception:
        evidence['table_status'] = 'partial'
        return [(raw_text,None)], evidence, ['PDF_TABLE_EXTRACTION_FAILED'+suffix]
    if not candidates:
        return [(raw_text,None)], evidence, warnings
    if candidates[0]['strategy'] == 'text':
        evidence['table_status'] = 'partial'
        return [(raw_text,None)], evidence, ['PDF_BORDERLESS_TABLE_UNCONFIRMED'+suffix]
    admitted = []
    blocks = [b for b in page.get_text('blocks') if b[6] == 0]
    for index, raw in enumerate(candidates):
        try:
            table = map_table(raw,page,page_no,index)
            box = fitz.Rect(table.bbox)
            if any(box.intersects(fitz.Rect(b[:4])) and not box.contains(fitz.Rect(b[:4])) for b in blocks):
                raise ValueError('PDF_TABLE_TEXT_OVERLAP_UNCONFIRMED')
            if any(box.intersects(fitz.Rect(other['bbox'])) for other in candidates if other is not raw):
                raise ValueError('PDF_TABLE_OVERLAP_UNCONFIRMED')
            # Apply the existing renderer admission bound before committing a projection.
            table_row_texts(table)
            admitted.append(table)
        except ValueError as exc:
            warnings.append(str(exc)+suffix)
    if not admitted:
        evidence['table_status'] = 'partial'
        return [(raw_text,None)], evidence, warnings
    warnings.append('PDF_HEADERS_INFERRED_UNKNOWN'+suffix)
    evidence['table_status'] = 'partial'  # Heuristic header semantics are not confirmed.
    evidence['admitted_table_ids'] = [t.table_id for t in admitted]
    pieces = []
    emitted = set()
    suppressed = []
    for block in blocks:
        table = next((t for t in admitted if fitz.Rect(t.bbox).contains(fitz.Rect(block[:4]))),None)
        if table is None:
            pieces.append((block[4],None))
        else:
            suppressed.append({'block_number':block[5],'bbox':list(block[:4]),'raw_text':block[4],
                               'table_id':table.table_id,'rule':'full-native-text-block-contained-in-table-bbox'})
            if table.table_id not in emitted:
                pieces.append(('',table))
                emitted.add(table.table_id)
    for table in admitted:
        if table.table_id not in emitted:
            pieces.append(('',table))
    evidence['suppressed_text_blocks'] = suppressed
    return pieces, evidence, warnings
