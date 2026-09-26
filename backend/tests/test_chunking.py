from backend.app.domain.chunking import chunk_document
from backend.app.domain.models import DocumentSection, NormalizedDocument


def test_chunking_preserves_exact_source_spans_and_heading_path():
    content = "# 事务\n提交记录\n\n## 回滚\n回滚记录和恢复步骤"
    document = NormalizedDocument(
        document_id="doc-1",
        version_id="ver-1",
        title="测试文档",
        media_type="text/plain",
        markdown_content=content,
        sections=[
            DocumentSection(
                section_id="sec-1",
                heading="事务",
                heading_path=("事务",),
                level=1,
                start=0,
                end=len(content),
            )
        ],
        assets=[],
        source_locators=[],
        content_sha256="hash",
        parser_version="text/v1",
    )

    chunks = chunk_document(document, max_chars=12, overlap=2)

    assert chunks
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert content[chunk.start:chunk.end] == chunk.content
        assert chunk.heading_path == ("事务",)
