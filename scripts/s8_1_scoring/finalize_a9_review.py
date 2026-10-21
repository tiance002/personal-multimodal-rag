"""Offline final A9 scoring narrative and assertions from audited receipts."""
import hashlib
import html
import json
from pathlib import Path
import re
import statistics
import s8_1_score as scoring

BASE = scoring.BASE
ROOT = BASE.parent / 'a-baseline-evidence'


def main():
    audit = scoring.read(BASE / 'a9-independent-audit-v2.json')
    first = scoring.read(BASE / 'first-case-score.results.json')
    facts = scoring.read(BASE / 'fact-criteria.json')
    sentence_reviews = scoring.read(BASE / 'a9-hotpot-exact-sentence-review.json')
    statements = {
        'scifact-1304': {'factual_status': 'FAIL_UNSUPPORTED_FALLBACK_DETAIL', 'earliest_loss': 'WEIGHTED_RRF_TOP30',
            'reason': 'Gold chunks raw vector57/BM25 4 -> fused57/92 -> absent Top30. Rerank max .0443106 removes all. Metadata fallback invents GFRα/IGF-1/heart-failure mechanisms absent from actual model inputs.',
            'official_answer': 'NOT_APPLICABLE_NO_ANSWER_OR_STANCE_GOLD'},
        'miracl-zh-5704915#0': {'factual_status': 'PASS_LOCATION_SOURCE_SUPPORTED', 'earliest_loss': 'NONE_NECESSARY_SOURCE_LOSS',
            'reason': 'Both positive sources arrive as derived summaries; original readback bytes independently state Haidian. Original retrieved Biyun Temple text also explicitly places it within Haidian Xiangshan Park. Do not call generated summaries original evidence.',
            'official_answer': 'NOT_APPLICABLE_NO_ANSWER_GOLD'},
        'multifieldqa_zh': {'factual_status': 'PASS_EXACT_REQUIREMENT_ORIGINAL_TEXT', 'earliest_loss': 'NONE_NECESSARY_FACT_LOSS',
            'reason': 'The actual wire contains the exact source requirement 星下点的分辨率应优于 5m from original text chunk e5cf40df...; answer preserves direction, quantity and unit. F1 lower due full response and Sources filename, not a wrong 5m fact.',
            'fact_points': [{'criterion': '影像星下点分辨率优于5m', 'context': 'PRESENT_ORIGINAL_TEXT', 'answer': 'PRESENT_CORRECT'}]},
        'dureader': {'factual_status': 'PARTIAL_CAUSE_COVERAGE_WITH_QUALIFIER_AND_RANGE_ERRORS', 'earliest_loss': 'GENERATION_QUALIFIER_AND_NORMAL_RANGE_HANDLING',
            'reason': 'All seven broad Gold cause categories exist in original text and answer. Answer omits infectious mononucleosis detail. It generalizes a chronic-viral-hepatitis-specific source treatment claim and calls ALT<=40 U/L mild elevation although actual source gives 0–40 as normal. Core cause retrieval succeeds; broad claim accuracy and source quality are separate.',
            'fact_points': [{'criterion': c, 'context': 'PRESENT_ORIGINAL_TEXT', 'answer': 'PRESENT'} for c in ['大量或长期饮酒','病毒性肝炎','肝硬化与肝癌','胆道疾病','药物/化学中毒','心脏疾病']]
                + [{'criterion': '其他感染（含肺炎、伤寒、结核、传染性单核细胞增多症）', 'context': 'PRESENT_ORIGINAL_TEXT', 'answer': 'PARTIAL_MONONUCLEOSIS_OMITTED'},
                   {'criterion': 'ALT正常范围0–40 U/L', 'context': 'PRESENT_ORIGINAL_TEXT', 'answer': 'MISSTATED_AS_MILD_ELEVATION'},
                   {'criterion': '2倍治疗主张限定慢性病毒性肝炎患者', 'context': 'PRESENT_QUALIFIER', 'answer': 'QUALIFIER_OMITTED'}]},
        '5a729fe55542991f9a20c53b': {'factual_status': 'PASS_TWO_HOP_CORE_ANSWER_SUPPORTED', 'earliest_loss': 'NONE_NECESSARY_FACT_LOSS',
            'reason': 'All 3 official required supporting sentences actually reach final model input, bound to raw source documents. Core answer Pieter van Musschenbroek correct, dates align supplied source. Full response EM=0 is formatting/extra explanation. Plain Sources line omits Tribometer file despite tribometer claim; list completeness issue is separate from Context fact support.',
            'plain_sources_issue': 'TRIBOMETER_SOURCE_FILENAME_OMITTED_FROM_FINAL_SOURCES_LINE'},
        '5a7631cd5542994ccc91870c': {'factual_status': 'PARTIAL_SAFE_ABSTENTION_SECOND_HOP_MISSING', 'earliest_loss': 'RERANK_SECOND_SOURCE_REMOVED',
            'reason': 'Frost Place source enters raw vector9/11 and fused19/23, so Top30 contains it. Actual Rerank scores .0006215679/.0038395789 remove both. Only 1 of 2 required source/sentences reaches model. Answer identifies seminar but explicitly lacks location; does not answer Gold Franconia, New Hampshire. No fabricated location.'},
        'row-0434': {'factual_status': 'FAIL_TASK_ANSWER_WITH_EVIDENCE_AWARE_REFUSAL', 'earliest_loss': 'RERANK_ALL_CANDIDATES_REMOVED; SECTION_LINK_ALREADY_UNVERIFIED',
            'reason': 'Correct original PDF retrieved through ECS same-byte doc alias4019..., but no Coding time string in 30 actual Rerank passages. Top score .0175781 is imports/pylearn2 code, not section/dataset relationship. All candidates below floor. Metadata-only fallback tentatively mentions Cat vs Dog but refuses exact section claim; not a grounded correct answer.',
            'typed_answer_result': 'NOT_CORRECT_FULL_RESPONSE; no official GPT judge'},
        'row-0687': {'factual_status': 'FAIL_MEDIAN_TASK_AND_ORIGINAL_AVERAGE_CONFLICT', 'earliest_loss': 'PARSER_CHART_STRUCTURE_NOISE_AND_GENERATION_REASONING',
            'reason': 'Actual wire has Clinton A/B43,C20,D16,F21 and Trump D15/F35 plus explicit average C−. It also contains noisy table flattened Trump C. Answer falsely says both averages C, claims no distribution, and declines median comparison. Cannot credit Gold merely because Clinton token exists.',
            'original_pdf_visual_review': {'pdf_sha256': '8f49f93f1665fd5ff604e66ffed9649d8b18ce7f114ab43463aeac46cd3d00cb',
                'reviewed_physical_page': 28, 'printed_page': 26, 'image': str(BASE / 'pew-poppler-28.png'),
                'Trump': {'A_or_B':30,'C':19,'D':15,'F':35,'average':'C−'},
                'Clinton': {'A_or_B':43,'C':20,'D':16,'F':21,'average':'C'},
                'distribution_interpretation': 'Clinton cumulative F+D=37%, then C=57%, places 50th percentile in C. Trump F+D=50% and upper grades sum49%; displayed rounded distribution and nonresponse require care at exact median boundary. It supports the intended comparison more than the answer admits; average is not median.',
                'scope': 'Single manually verified page; native Gold page_index_convention remains UNKNOWN, no official page-recall metric.'}},
        'row-0958': {'factual_status': 'FAIL_UNSUPPORTED_FALLBACK_AND_GOLD_LIST_MISMATCH', 'earliest_loss': 'RERANK_ALL_CANDIDATES_REMOVED',
            'reason': 'Gold physical-source identity is present in retrieval. Max .0493048 passage is microwave shiny surfaces, all below floor. Final metadata fallback sends no original reflection examples. Answer gives mirror and calm water, unlike native shiny object/mirror, and metal backing/general physics details lack actual input support. Refusal ability not demonstrated.'}
    }
    rows = []
    for row in audit['cases']:
        cid = row['case_id']
        tag = next(k for k in statements if k == cid or k in cid)
        review = dict(statements[tag])
        if cid == 'scifact-1304':
            metrics = {'retrieval': first['retrieval'], 'answer': None}
            tokens, latency = first['usage']['actual_tokens'], first['usage']['post_elapsed_seconds']
            complete, equal, closed, readbacks = True, first['consistency']['raw_provider_equals_sse_equals_pg_equals_history_equals_result'], first['usage']['child_status'], first['consistency']['source_http_readbacks']
        else:
            metrics = {'retrieval': row['retrieval'], 'answer': row['answer_metric']}
            tokens, latency = row['actual_tokens'], row['elapsed_seconds']
            complete, equal, closed, readbacks = row['completed'], row['raw_sse_pg_history_result_equal'], row['ledger_status'], row['readback_count']
            assert not row['readback_failures'] and row['same_run_identity'] and row['frozen_question_scope_match'] and row['no_gold_fields_in_post']
            assert all(c['wire_hash_matches_ledger_pg'] and c['scope_matches'] and c['settled'] and c['response_http200'] and c['protocol_complete'] for c in row['provider_calls'])
            if row['context']:
                assert row['context_bound_to_actual_wire']
                assert all(c['source_hash_matches_metadata'] and c['readback_identity_version_matches'] for c in row['context_chunks'])
            if cid.startswith('hotpot'):
                sentence = next(s for s in sentence_reviews if s['case_id'] == cid)
                context_ids = set(row['retrieval']['context']['ranked_source_document_ids'])
                gold = next(c for c in scoring.read(scoring.GOLD)['cases'] if c['identity']['case_id'] == cid)
                positives = {k for k,v in gold['gold']['qrels'].items() if v>0}
                review['hotpot_required_sources'] = {'at_least_one': bool(context_ids & positives), 'all': positives <= context_ids,
                    'required': len(positives), 'present': len(context_ids & positives)}
                review['hotpot_required_supporting_sentences'] = {'exact_text_coverage': sentence['exact_text_coverage'],
                    'required': len(sentence['required_sentences']), 'present': sum(s['exact_sentence_in_final_context'] and s['exact_sentence_in_every_generation_input_capture'] for s in sentence['required_sentences'])}
        assert complete and equal and closed == 'CLOSED'
        rows.append({'case_id': cid, 'arm': 'A', 'metrics': metrics, 'factual_review': review,
            'actual_tokens': tokens, 'post_seconds': latency, 'application_chain': 'PASS_REAL_API_MODEL_SSE_PG_HISTORY',
            'source_http_readbacks': readbacks, 'structured_reference_count': 0})
    manifest = scoring.read(ROOT / 'MANIFEST.json')
    bad = [name for name,meta in manifest.items() if not(ROOT/name).is_file() or scoring.sha(ROOT/name)!=meta['sha256'] or (ROOT/name).stat().st_size!=meta['bytes']]
    assert not bad
    prior_case = next((BASE.parent/'first-case-evidence/cases').iterdir())
    current_case = next(p for p in (ROOT/'cases').iterdir() if scoring.read(next(p.glob('api-cases/*/result.json')))['case_id']=='scifact-1304')
    first_reused = all(scoring.sha(next(prior_case.glob('api-cases/*/'+name))) == scoring.sha(next(current_case.glob('api-cases/*/'+name))) for name in ['question.json','response.sse','history.json','result.json'])
    assert first_reused
    calls = [c for r in audit['cases'] if 'provider_calls' in r for c in r['provider_calls']]
    tokens_by_role = {}
    for c in calls + first['usage']['calls']:
        role = 'answer' if '/answer/' in c['request_id'] else 'rewrite' if c['request_id'].endswith('query_rewrite') else c['capability']
        tokens_by_role[role] = tokens_by_role.get(role, 0) + c['usage']['total_tokens']
    report = {'schema':'s8-1-a9-independent-final/v1','scope':'FROZEN_PREPARED_CORPUS_SMALL_SAMPLE',
        'status':'A_BASELINE_COMPLETE_WITH_QUALITY_FAILURES','case_count':9,'cases':rows,
        'health':{'all_9_real_api_model_sse_pg_history':'PASS','normal_evidence_context_cases':6,'search_nothing_metadata_fallback_cases':3,
            'all_normal_context_chunk_text_bound_to_actual_wire':True,'identity_scope_settlement_stop_condition_found':False,
            'may_continue_B_C_given_own_preconditions':True,'public_structured_citation_click_chain':'NOT_AVAILABLE_STRUCTURED_REFERENCES_DISABLED',
            'source_file_readback':'PASS_SAVED_HTTP_AND_BYTES','plain_sources_accuracy':'SEPARATE_REVIEW_NOT_EQUIVALENT_TO_STRUCTURED_CITATIONS'},
        'usage':{'provider_calls':36,'actual_tokens':sum(r['actual_tokens'] for r in rows),'tokens_by_role':tokens_by_role,
            'all_settled':True,'all_children_closed':True,'new_unknown':0,'post_seconds':{
                'sum_sequential_http':sum(r['post_seconds'] for r in rows),'mean':statistics.mean(r['post_seconds'] for r in rows),
                'median':statistics.median(r['post_seconds'] for r in rows),'min':min(r['post_seconds'] for r in rows),'max':max(r['post_seconds'] for r in rows)}},
        'protection':{'all_manifest_files_checked':len(manifest),'manifest_failures':bad,'first_scifact_receipt_bytes_identical_reused':first_reused,
            'gold_labels_not_in_saved_post_or_request_bodies':True,'new_cloud_calls_by_review':0,'gold_files_modified':False},
        'omni':{'historical_project_ned_mapped_pages':3,'not_scorable_pages':21,'new_ocr':0,'new_embedding':0,'rag_question_denominator':0,'official_layout_table_formula_metrics':'NOT_RUN'},
        'methodology_corrections':[
            'v1 UUID-only scoring undercounted MML0434; v2 uses the actually read-back same-byte original SHA, KB and version as source alias; frozen Gold unchanged.',
            'Some whole-context checks initially differed because generation re-renders XML and escapes entities; actual per-chunk text matches after entity unescape/whitespace normalization.',
            'Full completed application response, including Sources filenames, remains answer metric input; no Gold string extraction, official GPT judge or whole-benchmark claim.',
            'Retrieval Recall@10 is per distinct source document; source presence anywhere in Top30 or full Context and sentence fact delivery reported separately.',
            'Pew median question and average grade require separate reasoning; original PDF chart manually inspected, not OCRed again.']}
    assert report['usage']['actual_tokens']==88080 and sum(tokens_by_role.values())==88080
    scoring.write_new(BASE/'a9-independent-final.results.json',report)
    print(json.dumps({'status':report['status'],'tokens':report['usage'],'manifest_files':len(manifest),'first_reused':first_reused,'new_calls':0},ensure_ascii=True))


if __name__=='__main__':
    main()
