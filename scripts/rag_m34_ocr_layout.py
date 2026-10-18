"""Offline, conservative OCR-position observations. Not a matrix/table parser.

No expected values, matrix dimensions, page coordinates, cloud calls, OCR calls,
or database writes. Shared line boxes are never divided into invented cell boxes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median
import sys

INTEGER = re.compile(r'[+-]?\d+', re.ASCII)
NUMERIC_LIKE = re.compile(r'[\d\s+\-−–—一.]+', re.ASCII)


def inspect_layout(text, layout, size):
    """Return only positioned lexical values and ambiguous original lines."""
    errors, checked, covered = [], [], set()
    width, height = size
    if layout.get('coordinate_basis') != 'image-pixels-top-left':
        errors.append('UNSUPPORTED_COORDINATE_BASIS')
    for index, line in enumerate(layout.get('lines', [])):
        start, end, box, value = (line.get(k) for k in ('start', 'end', 'bbox', 'text'))
        aligned = (type(start) is int and type(end) is int and
                   isinstance(value, str) and 0 <= start < end <= len(text) and text[start:end] == value)
        geometry = (isinstance(box, list) and len(box) == 4 and
                    all(type(x) in (int, float) and math.isfinite(x) for x in box))
        if geometry:
            geometry = 0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height
        if not aligned or not geometry:
            errors.append({'line': index, 'text_aligned': aligned, 'geometry_valid': geometry})
            continue
        checked.append({'line_index': index, 'raw_text': value, 'start': start, 'end': end, 'bbox': box})
        covered.update(range(start, end))
    missing = [i for i, c in enumerate(text) if not c.isspace() and i not in covered]
    if missing:
        errors.append({'unmapped_nonspace_offsets': missing})
    if not checked:
        errors.append('NO_ALIGNED_LINES')
    result = {'mapping_valid': not errors, 'errors': errors, 'aligned_lines': len(checked),
              'spatial_rows': [], 'unresolved_lines': [], 'matrix_row_column_assignment': 'UNKNOWN'}
    if errors:
        return result
    typical_height = median(x['bbox'][3] - x['bbox'][1] for x in checked)
    result['median_line_height'] = typical_height
    candidates = []
    for line in checked:
        value, box = line['raw_text'], line['bbox']
        compact = box[3] - box[1] <= typical_height * 1.5
        if INTEGER.fullmatch(value) and compact:
            candidates.append({**line, 'value': value, 'status': 'POSITIONED_LITERAL',
                               'reason': 'One literal integer occupies this compact OCR line box.'})
        elif NUMERIC_LIKE.fullmatch(value) and compact:
            # Preserve damaged signs / merged tokens as UNKNOWN, even when an
            # apparent expected integer could be guessed from the characters.
            candidates.append({**line, 'value': 'UNKNOWN', 'status': 'UNKNOWN',
                               'reason': 'Not one unambiguous literal integer; no sign/token normalization.'})
        elif re.search(r'\d', value):
            result['unresolved_lines'].append({**line, 'status': 'UNKNOWN_POSITION',
                'reason': 'Coarse/tall shared line box or mixed text; no individual cell coordinates.'})
    # Only cluster compact observations by measured y-centres. Row labels here
    # are spatial bands, never inferred matrix row numbers or matrix identities.
    groups = []
    for line in sorted(candidates, key=lambda x: ((x['bbox'][1] + x['bbox'][3]) / 2, x['bbox'][0])):
        cy = (line['bbox'][1] + line['bbox'][3]) / 2
        matching = [g for g in groups if abs(cy - median(g['centres'])) <= typical_height * .35]
        if len(matching) > 1:
            result['unresolved_lines'].append({**line, 'reason': 'Ambiguous spatial band'})
        elif matching:
            matching[0]['observations'].append(line)
            matching[0]['centres'].append(cy)
        else:
            groups.append({'observations': [line], 'centres': [cy]})
    result['spatial_rows'] = [{'band_index': i, 'centre_y': median(g['centres']),
        'observations': sorted(g['observations'], key=lambda x: x['bbox'][0]),
        'matrix_identity': 'UNKNOWN', 'matrix_row_number': 'UNKNOWN'} for i, g in enumerate(groups)]
    return result


def analyze(snapshot):
    from backend.weknora_port.image_multimodal import sanitize_ocr
    info, image, chunk = snapshot['image_info'], snapshot['source_image'], snapshot['chunk']
    text = info['ocr_text']
    result = inspect_layout(text, info.get('ocr_layout', {}), image['size_pixels'])
    result.update(schema='m34-layout-observations/v1', raw_ocr_sha256=hashlib.sha256(text.encode()).hexdigest(),
        source={'chunk_id': chunk['id'], 'document_id': chunk['knowledge_id'],
                'knowledge_base_id': chunk['knowledge_base_id'], 'source_version': chunk['metadata']['source_version'],
                'image_sha256': image['sha256'], 'image_size_pixels': image['size_pixels'],
                'ocr_adapter_record': info['ocr_status'], 'source_offsets': [chunk['start_at'], chunk['end_at']]},
        sanitize_ocr_changed_text=sanitize_ocr(text) != text,
        task_layout_equals_chunk_layout=all(t['outcomes']['0'].get('ocr_layout') == info.get('ocr_layout') for t in snapshot['image_task_rows']),
        task_text_equals_chunk_text=all(t['outcomes']['0'].get('ocr_result') == text for t in snapshot['image_task_rows']),
        local_ocr_calls=0, cloud_calls=0, matrix_values_completed_by_inference=False)
    has_values = any(o['status'] == 'POSITIONED_LITERAL' for row in result['spatial_rows'] for o in row['observations'])
    # This diagnostic has no cell/word geometry or matrix segmentation proof;
    # spatial bands alone therefore cannot certify a complete matrix.
    result['status'] = 'LAYOUT_PARTIAL' if result['mapping_valid'] and has_values else 'LAYOUT_UNAVAILABLE'
    result['full_matrix_reconstruction'] = 'UNKNOWN_NO_CELL_GEOMETRY_OR_MATRIX_BOUNDARY_PROOF'
    return result


def layout_text(result):
    lines = ['DERIVED SPATIAL OCR OBSERVATIONS — not original OCR, not a completed matrix',
             'Source: ' + json.dumps(result['source'], ensure_ascii=False),
             'Matrix identity, matrix row/column numbers, missing values and ambiguous signs remain UNKNOWN.']
    for row in result['spatial_rows']:
        lines.append(f"Spatial band {row['band_index']} (y-centre={row['centre_y']:.3f} px):")
        for o in row['observations']:
            lines.append(f"  {o['value']} | raw={o['raw_text']!r} | line={o['line_index']} | text[{o['start']}:{o['end']}] | bbox={o['bbox']}")
    lines.append('Unresolved original lines (shared/coarse boxes; no cell assignment):')
    lines.extend(f"  UNKNOWN | raw={o['raw_text']!r} | line={o['line_index']} | text[{o['start']}:{o['end']}] | bbox={o['bbox']}" for o in result['unresolved_lines'])
    return '\n'.join(lines) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--text-output', type=Path, required=True)
    args = p.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    result = analyze(json.loads(args.snapshot.read_text(encoding='utf-8')))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    args.text_output.write_text(layout_text(result), encoding='utf-8')
    print(result['status'], 'aligned_lines=', result['aligned_lines'], 'spatial_bands=', len(result['spatial_rows']))


if __name__ == '__main__':
    main()
