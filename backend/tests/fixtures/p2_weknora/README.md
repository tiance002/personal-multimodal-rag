# P2 offline XLS fixture

`ragged.xls` is an unmodified genuine BIFF workbook from python-excel/xlrd tag
`2.0.2`, `tests/samples/ragged.xls`:
https://github.com/python-excel/xlrd/blob/2.0.2/tests/samples/ragged.xls

SHA-256: `a144c284163641c2cb7dfc17d0116006aeffc3dff2b4878f039d4dbab6e2b07e`.
Upstream license is preserved in `xlrd-LICENSE`. Tests read the committed fixture
offline. Sheet1 has five ragged rows and four columns; Sheet2/3 are empty.
This proves real XLS decoding, not original formula/cache provenance.
