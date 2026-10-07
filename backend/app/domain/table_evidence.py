from backend.app.domain.models import DocumentTable, TableCell


MAX_NATIVE_ROW_PROJECTIONS = 50000

def key_value_row(table: DocumentTable, cells: list[TableCell]) -> str:
    """WeKnora-style row text; original cells remain untouched as proof."""
    origins = {c.coordinate: c for c in table.cells}
    values = []
    for cell in cells:
        origin = origins.get(cell.merged_anchor, cell)
        value = origin.cached_value if origin.formula else origin.value
        if value is None or value == '':
            continue
        # Native XLS keeps merged origins/spans as proof. Expand only the
        # retrieval representation, just as XLSX fills covered coordinates.
        for column in range(cell.column, cell.column + cell.column_span):
            labels = cell.column_headers if column == cell.column else ()
            if table.source_format == 'xls' and table.header_rows:
                header = next((c for c in table.cells if c.row in table.header_rows
                               and c.column <= column < c.column + c.column_span), None)
                if header is not None:
                    labels = header.column_headers
            if labels:
                label = labels[-1]
            else:
                number, label = column, ""
                while number:
                    number, digit = divmod(number - 1, 26)
                    label = chr(65 + digit) + label
            values.append(f"{label}: {value}")
    return f"Sheet: {table.sheet}\n" + ",".join(values) + "\n" if values else ""

def table_row_cells(table: DocumentTable) -> list[tuple[int, list[TableCell]]]:
    """Project existing native origins onto covered rows without copying raw cells."""
    if not table.source_format:
        return [(row, [c for c in table.cells if c.row == row])
                for row in sorted({c.row for c in table.cells})]
    work = 0
    for cell in table.cells:
        work += cell.row_span * cell.column_span
        if (cell.row + cell.row_span - 1 > 1000
                or cell.column + cell.column_span - 1 > 200
                or work > MAX_NATIVE_ROW_PROJECTIONS):
            raise ValueError("NATIVE_TABLE_ROW_PROJECTION_LIMIT")
    rows: dict[int, list[TableCell]] = {}
    for cell in table.cells:
        for row in range(cell.row, cell.row + cell.row_span):
            rows.setdefault(row, []).append(cell)
    return [(row, sorted(cells, key=lambda c: c.column)) for row, cells in sorted(rows.items())]

def table_row_range(table: DocumentTable, row: int, cells: list[TableCell]) -> str:
    if table.source_format:
        last_column = max(c.column + c.column_span - 1 for c in cells)
        return f"R{row}C{cells[0].column}:R{row}C{last_column}"
    return f"{cells[0].coordinate}:{cells[-1].coordinate}"

def table_row_texts(table:DocumentTable)->list[tuple[int,str]]:
    """Each source row is complete and independently carries its column headers."""
    result=[];total=0
    for row, cells in table_row_cells(table):
        if table.row_representation == "key_value":
            if row in table.header_rows:
                continue
            content = key_value_row(table, cells)
            if content:
                total += len(content)
                if total > 16 * 1024 * 1024:
                    raise ValueError('TABLE_RENDER_LIMIT')
                result.append((row, content))
            continue
        row_range = table_row_range(table, row, cells)
        if table.source_format:
            source=f'Document format: {table.source_format}; table: {table.table_id}; row: {row}; range: {row_range}; headers: {table.header_detection}\n'
            if table.page is not None: source+=f'Page: {table.page}; table bbox: {table.bbox}; source: derived native table\n'
            if table.caption: source+=f'Caption: {table.caption}\n'
        else:
            source=f'Sheet: {table.sheet or "UNSPECIFIED"}; table: {table.table_id}; range: {row_range}\n'
        values=[]
        for cell in cells:
            label=' > '.join(cell.column_headers) or 'HEADER_UNAVAILABLE'
            if table.source_format:
                import json
                values.append(f'{cell.coordinate} [{label}]={json.dumps(cell.display, ensure_ascii=False)}; span={cell.row_span}x{cell.column_span}')
                continue
            values.append(f'{cell.coordinate} [{label}]={cell.display}; format={cell.number_format}'+(f'; merged_anchor={cell.merged_anchor}' if cell.merged_anchor else ''))
        content=source+' | '.join(values)+'\n'
        total+=len(content)
        if total>16*1024*1024:raise ValueError('TABLE_RENDER_LIMIT')
        result.append((row,content))
    return result
