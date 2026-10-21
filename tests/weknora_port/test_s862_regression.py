"""S8.6.2 source-slot regression; no Provider or database."""
from backend.weknora_port.retrieval.strategy import try_rule_compare


def test_cn_aspect_modifier_never_becomes_part_of_second_object():
    query = '比较A和B在安全性方面的区别'
    rows = try_rule_compare(query)
    assert rows is not None
    assert [row.covers for row in rows] == [('A', '安全性'), ('B', '安全性')]
    assert [query[slice(*row.object_span)] for row in rows] == ['A', 'B']
    assert all(query[slice(*row.dimension_span)] == '安全性' for row in rows)
