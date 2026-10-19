"""Scope/bundle serialization contracts only; no database or Provider."""
from pathlib import Path
import pytest
from scripts.s8_migration_bundle import assert_scope,validate_closure,scope_tail,imported_row,portable_relative,collect_assets,native,sha


def test_scope_rejects_foreign_tenant_subject_and_kb():
    assert_scope({'models':[{'tenant_id':332,'subject_id':'existing'}]},['kb'],'existing')
    for table,row in [('models',{'tenant_id':1,'subject_id':'existing'}),('models',{'tenant_id':332,'subject_id':'other'}),
                      ('chunks',{'tenant_id':332,'knowledge_base_id':'foreign'})]:
        with pytest.raises(ValueError):assert_scope({table:[row]},['kb'],'existing')
    with pytest.raises(ValueError):assert_scope({'tenants':[{'id':1}]},['kb'],'existing')
    with pytest.raises(ValueError):assert_scope({'messages':[{'tenant_id':332,'subject_id':'existing',
        'knowledge_base_scope':['other-kb']}]},['kb'],'existing')


def test_forward_fk_closure_rejects_missing_parent_without_synthesizing_rows():
    fk=[{'table_name':'port_graph_nodes','referenced_table':'port_graph_evidence','columns':['evidence_id'],'referenced_columns':['id']}]
    rows={'port_graph_nodes':[{'id':'n','evidence_id':'proof'}],'port_graph_evidence':[{'id':'proof'}]}
    validate_closure(rows,fk)
    rows['port_graph_evidence']=[]
    with pytest.raises(ValueError,match='FK_CLOSURE'):validate_closure(rows,fk)


def test_physical_mapping_preserves_original_text_offsets_hashes_and_cipher():
    mapping={'E:\\old\\image.png':'/var/lib/eviroute/storage/332/kb/doc/v/assets/image.png'}
    row={'content':'E:\\old\\image.png is literal OCR text','source_content':'original','start_at':4,'end_at':12,
         'metadata':{'path':'E:\\old\\image.png','logical_path':'E:\\old\\image.png','cipher':'do-not-rewrite'},
         'image_info':'{"image_url":"E:\\\\old\\\\image.png","ocr_text":"original"}'}
    mapped=imported_row('chunks',row,mapping)
    assert mapped['content']==row['content'] and mapped['source_content']=='original'
    assert (mapped['start_at'],mapped['end_at'])==(4,12)
    assert mapped['metadata']['logical_path']=='E:\\old\\image.png'
    assert mapped['metadata']['path'].startswith('/var/lib/eviroute/storage/')
    assert imported_row('models',{'parameters':{'api_key':'stored-ciphertext'}},mapping)=={'parameters':{'api_key':'stored-ciphertext'}}


def test_storage_tail_is_only_established_by_authorized_document_path():
    doc={'knowledge_base_id':'kb','id':'doc'}
    assert scope_tail('E:/old/assets/332/kb/doc/v/original/source.pdf',doc).as_posix()=='332/kb/doc/v/original/source.pdf'
    assert scope_tail('E:/old/assets/1/kb/doc/v/original/source.pdf',doc) is None
    with pytest.raises(ValueError,match='ESCAPE'):scope_tail('E:/old/332/kb/doc/../outside',doc)


def test_long_unicode_physical_filename_is_portable_without_changing_source_bytes(tmp_path):
    name='资料'*90+'.png';relative='332/kb/doc/v/original/'+name
    portable=portable_relative(relative)
    assert portable.parts[:-1]==tuple(relative.split('/')[:-1])
    assert portable.suffix=='.png' and all(len(part.encode('utf-8'))<=240 for part in portable.parts)
    original=tmp_path/('base'*25)/Path(relative)
    native(original).parent.mkdir(parents=True,exist_ok=True);native(original).write_bytes(b'original-source-bytes')
    document={'id':'doc','knowledge_base_id':'kb','file_path':str(original),'file_hash':sha(b'original-source-bytes'),'metadata':{}}
    assets,missing,mapping=collect_assets({'knowledges':[document]},tmp_path/'bundle','/var/lib/eviroute/storage')
    assert missing==[] and len(assets)==1 and assets[0]['original_file'] is True
    assert native(tmp_path/'bundle'/'assets'/assets[0]['relative_path']).read_bytes()==b'original-source-bytes'
    mapped=imported_row('knowledges',document,mapping)
    assert mapped['file_path'].endswith(portable.name) and document['file_path']==str(original)


def test_import_requires_explicit_apply_before_any_database_connection():
    import asyncio
    from types import SimpleNamespace
    from scripts.s8_migration_bundle import import_bundle
    with pytest.raises(ValueError,match='EXPLICIT_TARGET_APPLY'):
        asyncio.run(import_bundle(SimpleNamespace(apply=False,target_system_id=None)))
