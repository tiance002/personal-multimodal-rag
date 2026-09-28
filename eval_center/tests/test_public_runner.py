from eval_center.public_data import PublicDocument
from eval_center.public_runner import _expected_normalized_text, _source_content


def test_upload_binding_text_matches_production_text_parser_newline_rules():
    document = PublicDocument("doc-1", "Title", "First line\r\nSecond line\rThird line")

    content = _source_content(document)

    assert content == "# Title\n\nFirst line\r\nSecond line\rThird line"
    assert _expected_normalized_text(content) == "# Title\n\nFirst line\nSecond line\nThird line"
