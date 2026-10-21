from scripts.rag_m34_ocr_layout import inspect_layout


def _layout(text, lines):
    return {
        'schema': 'image-ocr-layout-v1',
        'coordinate_basis': 'image-pixels-top-left',
        'lines': [
            {'start': start, 'end': end, 'text': value, 'bbox': bbox}
            for start, end, value, bbox in lines
        ],
    }


def _line(text, value, bbox):
    start = text.index(value)
    return (start, start + len(value), value, bbox)


def test_inspect_layout_fails_closed_on_misaligned_offsets_and_out_of_bounds_boxes():
    text = '37'
    misaligned = _layout(text, [(0, 2, '38', [10, 10, 20, 20])])
    outside = _layout(text, [_line(text, '37', [10, 10, 1001, 20])])

    for layout in (misaligned, outside):
        result = inspect_layout(text, layout, (1000, 1000))
        assert result['mapping_valid'] is False
        assert result['spatial_rows'] == []
        assert result['aligned_lines'] == 0


def test_inspect_layout_preserves_single_ascii_negative_literal():
    text = '-7'
    result = inspect_layout(text, _layout(text, [_line(text, '-7', [23, 40, 35, 51])]), (800, 600))

    assert result['mapping_valid'] is True
    observation = result['spatial_rows'][0]['observations'][0]
    assert observation['raw_text'] == '-7'
    assert observation['value'] == '-7'
    assert observation['status'] == 'POSITIONED_LITERAL'


def test_inspect_layout_keeps_damaged_sign_sequence_unknown():
    text = '-—-7'
    result = inspect_layout(text, _layout(text, [_line(text, text, [140, 80, 190, 95])]), (800, 600))

    assert result['mapping_valid'] is True
    observation = result['spatial_rows'][0]['observations'][0]
    assert observation['raw_text'] == '-—-7'
    assert observation['value'] == 'UNKNOWN'
    assert observation['status'] == 'UNKNOWN'


def test_inspect_layout_does_not_invent_cells_for_coarse_or_labeled_lines():
    text = '5\nA=10 B=20\n12 19\n9'
    lines = [
        _line(text, '5', [20, 10, 30, 20]),
        _line(text, 'A=10 B=20', [40, 35, 180, 45]),
        _line(text, '12 19', [10, 60, 190, 120]),
        (text.rindex('9'), text.rindex('9') + 1, '9', [20, 140, 30, 150]),
    ]
    result = inspect_layout(text, _layout(text, lines), (300, 300))

    assert result['mapping_valid'] is True
    positioned = [o for row in result['spatial_rows'] for o in row['observations']]
    assert [o['raw_text'] for o in positioned] == ['5', '9']
    unresolved = {o['raw_text']: o for o in result['unresolved_lines']}
    assert unresolved['A=10 B=20']['bbox'] == [40, 35, 180, 45]
    assert unresolved['12 19']['bbox'] == [10, 60, 190, 120]
    assert all('cell_bbox' not in item and 'cells' not in item for item in result['unresolved_lines'])


def test_horizontal_observations_are_only_spatial_bands():
    text = '4\n11'
    lines = [
        _line(text, '4', [30, 50, 40, 60]),
        _line(text, '11', [200, 51, 215, 61]),
    ]
    result = inspect_layout(text, _layout(text, lines), (500, 500))

    assert result['mapping_valid'] is True
    assert len(result['spatial_rows']) == 1
    band = result['spatial_rows'][0]
    assert band['band_index'] == 0
    assert band['matrix_identity'] == 'UNKNOWN'
    assert band['matrix_row_number'] == 'UNKNOWN'
    assert result['matrix_row_column_assignment'] == 'UNKNOWN'


def test_inspect_layout_rejects_offsets_after_three_blank_lines_are_sanitized():
    from backend.weknora_port.image_multimodal import sanitize_ocr

    original = 'source\n\n\n-7'
    sanitized = sanitize_ocr(original)
    old_layout = _layout(original, [_line(original, '-7', [30, 20, 45, 32])])

    assert sanitized == 'source\n\n-7'
    result = inspect_layout(sanitized, old_layout, (200, 100))
    assert result['mapping_valid'] is False
    assert result['spatial_rows'] == []


def test_mineru_matrix_diagnostic_uses_delimiters_and_preserves_raw_span_ranges():
    from scripts.rag_m34_mineru_matrix import matrices
    raw=r'\begin{bmatrix} - 17 & 6 \\ 12 & - 3 \end{bmatrix}'
    middle={'pages':[{'page_idx':0,'blocks':[{'type':'text','index':4,'bbox':[.1,.2,.7,.9],
        'content':[{'type':'equation_inline','content':raw}]}]}]}
    result=matrices(middle,raw)
    assert [[c['value'] for c in row] for row in result[0]['rows']]==[['-17','6'],['12','-3']]
    for row in result[0]['rows']:
        for cell in row:
            assert raw[cell['span_start']:cell['span_end']]==cell['raw']
    assert result[0]['bbox_basis']=='normalized_page_block_not_cell'


def test_mineru_matrix_diagnostic_never_repairs_a_damaged_sign():
    from scripts.rag_m34_mineru_matrix import matrices
    raw=r'\begin{bmatrix} -—-17 \end{bmatrix}'
    middle={'pages':[{'page_idx':0,'blocks':[{'type':'text','index':0,'bbox':[0,0,1,1],
        'content':[{'type':'equation_inline','content':raw}]}]}]}
    result=matrices(middle,raw)[0]
    assert result['rows'][0][0]['value']=='UNKNOWN' and not result['complete_literals']
