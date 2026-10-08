"""Deterministic SIMULATED PDFs; actual decoding/extraction is never mocked."""
import fitz


def complex_span_pdf():
    """Two missing internal segments make an overlapping, nonrectangular merge.

    Design coordinates are independent of find_tables: the upper merged origin
    overlaps the lower-left cell, so it cannot prove a rectangular cell grid.
    """
    xs, ys = [50, 170, 290, 410], [80, 116, 152, 188]
    with fitz.open() as pdf:
        page = pdf.new_page(width=480, height=320)
        for row, y in enumerate(ys):
            for col in range(3):
                if (row, col) != (2, 1):
                    page.draw_line((xs[col], y), (xs[col + 1], y))
        for col, x in enumerate(xs):
            for row in range(3):
                if (row, col) != (1, 1):
                    page.draw_line((x, ys[row]), (x, ys[row + 1]))
        for row in range(3):
            for col in range(3):
                page.insert_text((xs[col] + 8, ys[row] + 22), f'{row + 1}{col + 1}')
        return pdf.tobytes(no_new_id=True)


def page_failure_pdf(mode, scan_image, *, with_text=False):
    """Blank or raster-only page, optionally followed by usable native text."""
    assert mode in ('empty', 'scan')
    with fitz.open() as pdf:
        page = pdf.new_page(width=480, height=320)
        if mode == 'scan':
            page.insert_image(page.rect, stream=scan_image)
        if with_text:
            page = pdf.new_page(width=480, height=320)
            page.insert_text((35, 50), 'Retained source amount: -7.25 on page two.')
        return pdf.tobytes(no_new_id=True)
