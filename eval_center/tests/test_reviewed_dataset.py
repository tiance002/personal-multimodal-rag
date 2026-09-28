import shutil
from pathlib import Path

import pytest

from eval_center.dataset import load_reviewed_dataset
from eval_center.build_reviewed_dataset import build, SOURCES


DATASET=Path(__file__).resolve().parents[2]/'evaluations/trust_v1'


def test_reviewed_dataset_has_30_plus_questions_and_real_readable_source_spans():
    manifest,cases,sources=load_reviewed_dataset(DATASET)
    assert manifest['sample_count']>=30
    assert len(sources)==2
    assert {'no_answer','cross_document','multi_evidence','scope','version','terminology'} <= {case['category'] for case in cases}
    assert all(case['metadata']['human_review']=='NOT_RUN' for case in cases)


@pytest.mark.parametrize('part',['source','locked'])
def test_frozen_sources_and_locked_questions_are_immutable(tmp_path,part):
    destination=tmp_path/'dataset'
    shutil.copytree(DATASET,destination)
    path=destination/('corpus/design.md' if part=='source' else 'locked.jsonl')
    path.write_bytes(path.read_bytes()+b' changed')
    with pytest.raises(ValueError): load_reviewed_dataset(destination)


def test_builder_is_idempotent_and_refuses_changed_sources_before_any_write(tmp_path):
    root=DATASET.parents[1]
    for source_path in SOURCES.values():
        destination=tmp_path/source_path
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(root/source_path,destination)
    build(tmp_path)
    build(tmp_path)
    dataset=tmp_path/'evaluations/trust_v1'
    before={path.relative_to(dataset):path.read_bytes() for path in dataset.rglob('*') if path.is_file()}
    readme=tmp_path/'README.md'
    readme.write_bytes(readme.read_bytes()+b'\nNew content\n')
    with pytest.raises(ValueError,match='frozen dataset already exists'): build(tmp_path)
    assert {path.relative_to(dataset):path.read_bytes() for path in dataset.rglob('*') if path.is_file()}==before
