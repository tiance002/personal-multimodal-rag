# Offline machine-only neighbor constructor. Standard library; no provider/HTTP/DB entry.
import json, hashlib, difflib, re

def normalized(value):
    # Comparison key only. Payload characters are never normalized or padded.
    return ' '.join(value.split())

def metadata(row):
    value=row['metadata']
    return json.loads(value) if isinstance(value,str) else value

def prepare_sources(rows, document_id, version):
    eligible=[]
    for r in rows:
        m=metadata(r)
        if r['knowledge_id']!=document_id or m.get('source_version')!=version: continue
        if r['chunk_type']!='text' or not r['is_enabled'] or r.get('deleted_at') is not None: continue
        if not m.get('mineru_source') or m.get('origin')=='generated' or r.get('image_info'): continue
        if len(r['content'])!=r['end_at']-r['start_at']: raise AssertionError('SOURCE_LENGTH')
        eligible.append(r)
    eligible.sort(key=lambda r:(r['chunk_index'],r['start_at'],r['id']))
    assert len({r['chunk_index'] for r in eligible})==len(eligible)
    chars={};owners={};locs={}
    for r in eligible:
        for offset,ch in enumerate(r['content'],r['start_at']):
            if offset in chars: assert chars[offset]==ch,'OVERLAP_CONFLICT'
            chars[offset]=ch;owners.setdefault(offset,[]).append(r['id'])
        for loc in metadata(r).get('source_locators',[]):
            locs[(loc['start'],loc['end'],loc.get('raw_text',''))]=loc
    return {'rows':eligible,'chars':chars,'owners':owners,'locators':list(locs.values()),'document_id':document_id,'source_version':version}

def source_units(source, rows, excluded_span=None):
    positions=set()
    for r in rows:positions.update(range(r['start_at'],r['end_at']))
    if excluded_span:
        positions.difference_update(range(*excluded_span))
    return [(i,source['chars'][i]) for i in sorted(positions)]

def runs(units):
    groups=[]
    for pos,ch in units:
        if not groups or pos!=groups[-1][-1][0]+1:groups.append([])
        groups[-1].append((pos,ch))
    return groups

def deduplicate(units, anchor_text, seen_lines=None, seen_blocks=None):
    # Full anchor wins; blocks are source paragraphs or whole contiguous runs.
    # Whitespace-normalized paragraph keys also catch equivalent line wrapping.
    paragraphs=lambda text:[x for x in re.split(r'\n[\t \r]*\n',text) if normalized(x)]
    seen_lines=set(normalized(x) for x in anchor_text.splitlines() if normalized(x)) if seen_lines is None else seen_lines
    seen_blocks=({normalized(anchor_text)} | {normalized(x) for x in paragraphs(anchor_text)}) if seen_blocks is None else seen_blocks
    kept=[];dropped=[]
    for block in runs(units):
        text=''.join(ch for _,ch in block);whole=normalized(text)
        if whole and whole in seen_blocks:
            dropped.append({'reason':'DUPLICATE_NORMALIZED_BLOCK','start':block[0][0],'end':block[-1][0]+1,'text':text});continue
        boundaries=[0]+[m.end() for m in re.finditer(r'\n[\t \r]*\n',text)]+[len(text)]
        for left,right in zip(boundaries,boundaries[1:]):
            paragraph=block[left:right]
            if not paragraph:continue
            value=text[left:right];key=normalized(value)
            if key and key in seen_blocks:
                dropped.append({'reason':'DUPLICATE_NORMALIZED_PARAGRAPH','start':paragraph[0][0],'end':paragraph[-1][0]+1,'text':value});continue
            if key:seen_blocks.add(key)
            cursor=0
            for line in value.splitlines(keepends=True):
                part=paragraph[cursor:cursor+len(line)];cursor+=len(line);key=normalized(line)
                if key and key in seen_lines:dropped.append({'reason':'DUPLICATE_NORMALIZED_LINE','start':part[0][0],'end':part[-1][0]+1,'text':line});continue
                if key:seen_lines.add(key)
                kept.extend(part)
    return kept,dropped,seen_lines,seen_blocks

def fragments(source, units, wire_start):
    result=[];cursor=wire_start
    for group in runs(units):
        text=''.join(ch for _,ch in group);start,end=group[0][0],group[-1][0]+1
        locs=[loc for loc in source['locators'] if loc['start']<end and loc['end']>start]
        result.append({'kind':'SOURCE_SUBSTRING_IDENTITY','source_start':start,'source_end':end,'raw_text':text,'cleaned_wire_text':text,'wire_start':cursor,'wire_end':cursor+len(text),'chunk_ids':sorted(set(x for pos,_ in group for x in source['owners'][pos])),'source_locators':locs})
        cursor+=len(text)
    return result

def anchor_mapping(source, anchor, wire, wire_start):
    raw=anchor['content'];out=[]
    for op,a,b,c,d in difflib.SequenceMatcher(None,raw,wire,autojunk=False).get_opcodes():
        out.append({'kind':'FROZEN_P13_CLEANING_'+op.upper(),'source_start':anchor['start_at']+a,'source_end':anchor['start_at']+b,'raw_text':raw[a:b],'cleaned_wire_text':wire[c:d],'wire_start':wire_start+c,'wire_end':wire_start+d,'chunk_ids':[anchor['id']]})
    return out

def assemble(source, anchor, original_wire, before, after):
    pieces=[];mapping=[];cursor=0
    if before:
        text=''.join(ch for _,ch in before);pieces.append(text);mapping+=fragments(source,before,cursor);cursor+=len(text)
        pieces.append('\n\n');mapping.append({'kind':'FIXED_STRUCTURAL_SEPARATOR_NOT_PADDING','wire_start':cursor,'wire_end':cursor+2,'cleaned_wire_text':'\n\n'});cursor+=2
    pieces.append(original_wire);mapping+=anchor_mapping(source,anchor,original_wire,cursor);cursor+=len(original_wire)
    if after:
        pieces.append('\n\n');mapping.append({'kind':'FIXED_STRUCTURAL_SEPARATOR_NOT_PADDING','wire_start':cursor,'wire_end':cursor+2,'cleaned_wire_text':'\n\n'});cursor+=2
        text=''.join(ch for _,ch in after);pieces.append(text);mapping+=fragments(source,after,cursor);cursor+=len(text)
    return ''.join(pieces),mapping

def build_machine_candidate(source, anchor_id, original_wire, cap=1200, step=7):
    # No query, answer, Gold, title, page choice, model score, or semantic parameter.
    rows=source['rows'];ordinal=next(i for i,r in enumerate(rows) if r['id']==anchor_id);anchor=rows[ordinal]
    assert len(original_wire)<=cap and anchor['id']==anchor_id
    previous=rows[ordinal-1] if ordinal else None;following=rows[ordinal+1] if ordinal+1<len(rows) else None
    raw_before=source_units(source,[previous]) if previous else []
    raw_before=[u for u in raw_before if u[0]<anchor['start_at']]
    raw_after=source_units(source,[following]) if following else []
    raw_after=[u for u in raw_after if u[0]>=anchor['end_at']]
    before,drop1,seen_lines,seen_blocks=deduplicate(raw_before,original_wire)
    after,drop2,_,_=deduplicate(raw_after,original_wire,seen_lines,seen_blocks)
    sides=int(bool(before))+int(bool(after));budget=max(0,cap-len(original_wire)-2*sides)
    choices=[(anchor['start_at']-pos,pos,'before',ch) for pos,ch in before]+[(pos-anchor['end_at']+1,pos,'after',ch) for pos,ch in after]
    selected=sorted(choices,key=lambda x:(x[0],x[1]))[:budget]
    before_keep=sorted([(pos,ch) for _,pos,side,ch in selected if side=='before']);after_keep=sorted([(pos,ch) for _,pos,side,ch in selected if side=='after'])
    near,near_map=assemble(source,anchor,original_wire,before_keep,after_keep)
    needed=len(before_keep)+len(after_keep);seed=(ordinal+step)%len(rows);excluded={i for i in [ordinal-1,ordinal,ordinal+1] if 0<=i<len(rows)}
    picked=[];far_dropped=[];stream=[]
    for distance in range(len(rows)):
        ordinals=[seed] if distance==0 else [seed-distance,seed+distance]
        for candidate in ordinals:
            if 0<=candidate<len(rows) and candidate not in excluded and candidate not in picked:picked.append(candidate)
        far_rows=[rows[i] for i in sorted(picked)]
        units=source_units(source,far_rows)
        stream,far_dropped,_,_=deduplicate(units,original_wire)
        if len(stream)>=needed:break
    assert len(stream)>=needed,'REMOTE_CONTEXT_INSUFFICIENT'
    remote=stream[:needed];left=remote[:len(before_keep)];right=remote[len(before_keep):]
    length,length_map=assemble(source,anchor,original_wire,left,right)
    assert len(near)==len(length)<=cap
    assert near.count(original_wire)>=1 and length.count(original_wire)>=1
    assert all(abs(i-ordinal)>1 for i in picked)
    return {'anchor_id':anchor_id,'anchor_chunk_index':anchor['chunk_index'],'document_id':source['document_id'],'source_version':source['source_version'],'anchor_source_span':[anchor['start_at'],anchor['end_at']],'anchor_wire_chars':len(original_wire),'near_neighbor_chunk_ids':[r['id'] for r in [previous,following] if r],'near_neighbor_chunk_indices':[r['chunk_index'] for r in [previous,following] if r],'near_available_unique_chars_before_line_dedup':[len(raw_before),len(raw_after)],'near_kept_chars_before_after':[len(before_keep),len(after_keep)],'near_text':near,'near_chars':len(near),'near_mapping':near_map,'near_dedup_records':drop1+drop2,'nonadjacent_seed_chunk_index':rows[seed]['chunk_index'],'nonadjacent_sample_chunk_indices':[rows[i]['chunk_index'] for i in sorted(picked)],'length_text':length,'length_chars':len(length),'length_mapping':length_map,'nonadjacent_dedup_records':far_dropped,'normalization':'Unicode whitespace collapse, case-sensitive, comparison only; nonempty exact normalized block/line keys; anchor priority','coordinate_overlap_dedup':'One character per source Unicode coordinate, verify all overlaps equal; neighbor coordinates inside complete raw anchor span excluded.','anchor_position_near':len(before_keep)+2 if before_keep else 0,'anchor_position_length':len(left)+2 if left else 0}
