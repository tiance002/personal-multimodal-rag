"""Go JSON boundary cases through actual complete search, preserving raw child."""
import asyncio,json
from pathlib import Path
import pytest
from tests.weknora_port.test_search_image_completion import source,search
from backend.weknora_port.generation import Evidence

FIXTURE=json.loads((Path(__file__).parent/'fixtures/image_json_boundary_go_oracle.json').read_text(encoding='utf-8'))

@pytest.mark.parametrize('case',FIXTURE['cases'],ids=lambda c:c['name'])
def test_parent_aggregation_matches_go_json(case):
    rows,_,_=asyncio.run(search([source('parent'),source('child',parent='parent',kind='image_ocr',raw=case['raw'])],['parent','child']))
    assert json.loads(rows[0].image_info or '[]')==json.loads(case['expected_parent'] or '[]')
    assert rows[1].image_info==case['raw']
    assert Evidence.from_result(rows[0]).image_info==rows[0].image_info

@pytest.mark.parametrize('existing',['[]',' ','not JSON','[{"URL":"existing"}]'])
def test_boundary_child_cannot_overwrite_nonempty_parent(existing):
    case=FIXTURE['cases'][0]
    rows,_,_=asyncio.run(search([source('parent',raw=existing),source('child',parent='parent',kind='image_ocr',raw=case['raw'])],['parent','child']))
    assert rows[0].image_info==existing and rows[1].image_info==case['raw']
