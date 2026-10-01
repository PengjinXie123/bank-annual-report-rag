"""Build a best-effort, source-preserving course corpus from both pilot reports."""
from __future__ import annotations
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
import pdfplumber
from process_pilot_reports import REPORTS, REPORT_DIR, DEFAULT_OUTPUT_DIR, write_jsonl
from pilot_layouts import extract_layout, NUMBER

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'data'/'processed'/'course_pilot'
MAIN_TITLE=re.compile(r'合并及(?:公司|银行)(?:资产负债表|利润表|现金流量表)')

def read_jsonl(path):
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()]

def split_prose(text, size=800, overlap=120):
    """Hard cap, prefer punctuation/newline; overlap is actual source text."""
    start=0
    while start<len(text):
        end=min(start+size,len(text))
        if end<len(text):
            candidates=[text.rfind(c,start+size//2,end) for c in ['\n','。','；']]
            boundary=max(candidates)
            if boundary>=0:end=boundary+1
        content=text[start:end].strip()
        if content:yield content
        if end==len(text):break
        start=end-overlap

def table_title(page):
    head='\n'.join(page['text'].splitlines()[:9])
    match=MAIN_TITLE.search(head)
    if match:return match[0]
    candidates=[line for line in page['text'].splitlines()[:9] if '表' in line and len(line)<45]
    return candidates[0] if candidates else (page.get('page_heading') or page['section'])+'／候选表'

def unit_from_text(text):
    match=re.search(r'(?:人民币)?(?:百万元|亿元|万元)|单位[：:]\s*[^\n]{1,15}',text[:650])
    return match[0] if match else '未可靠识别，见原文'

def scope_from_text(text):
    head=text[:650]
    if '本集团' in head and ('本行' in head or '本公司' in head):return '本集团与本行并列，必须按列区分'
    return '见原表或正文，未独立核定'

def infer_statement(page, meta):
    """Infer aligned numerical columns from repeated year header positions.

    Used only for the primary statements. Best effort, never marked audited.
    """
    words=page.extract_words(x_tolerance=2,y_tolerance=3)
    years=[w for w in words if re.fullmatch(r'202[45]年?',w['text']) and w['top']<page.height*.35]
    groups=[]
    for w in sorted(years,key=lambda w:(w['top'],w['x0'])):
        if not groups or abs(w['top']-groups[-1][0]['top'])>4:groups.append([])
        groups[-1].append(w)
    header=next((g for g in groups if len(g)==4 and [re.sub(r'\D','',w['text']) for w in sorted(g,key=lambda w:w['x0'])]==['2025','2024','2025','2024']),None)
    if not header:return None,'four-column year header not found'
    header.sort(key=lambda w:w['x0'])
    anchors=[w['x1'] for w in header]
    header_bottom=max(w['bottom'] for w in header)
    footer=page.height-95
    numbers=[w for w in words if header_bottom<w['top']<footer and NUMBER.fullmatch(w['text'])]
    bins=[[] for _ in anchors]
    for w in numbers:
        k=min(range(4),key=lambda i:abs(w['x1']-anchors[i]))
        if abs(w['x1']-anchors[k])<12:bins[k].append(w)
    if min(map(len,bins))<3:return None,'not enough aligned numerical rows'
    starts=[min(w['x0'] for w in b) for b in bins]
    ends=[max(w['x1'] for w in b) for b in bins]
    note_words=[w for w in numbers if w['x1']<starts[0]-5 and re.fullmatch(r'\d{1,2}',w['text'])]
    left=min(w['x0'] for w in words if header_bottom<w['top']<footer)
    data_edge=starts[0]-3
    if len(note_words)>=3:
        note_start=min(w['x0'] for w in note_words)
        note_end=max(w['x1'] for w in note_words)
        edges=[left-2,note_start-5,(note_end+starts[0])/2]
        columns=['附注','本集团·2025','本集团·2024','本行·2025','本行·2024']
    else:
        edges=[left-2,data_edge]
        columns=['本集团·2025','本集团·2024','本行·2025','本行·2024']
    edges.extend((ends[i]+starts[i+1])/2 for i in range(3))
    edges.append(max(ends[-1],anchors[-1])+4)
    # Place the bottom boundary just below the last aligned row to exclude notes.
    bottom=max(w['bottom'] for b in bins for w in b)+4
    layout=dict(title=table_title(meta),y=(header_bottom+1,bottom),edges=edges,columns=columns,
                unit=unit_from_text(meta['text']),groups=['资产：','负债：','股东权益：','净利润归属于：',
                '一、经营活动','二、投资活动','三、筹资活动','经营活动产生的现金流量：'])
    rows,errors=extract_layout(page,layout)
    valid=[r for r in rows if r['label'] and any(r['values']) and all(not v or NUMBER.fullmatch(v) for v in r['values'])]
    if len(valid)<3 or len(errors)>max(2,len(rows)*.2):return None,f'layout rejected: {len(rows)} rows, {len(errors)} issues'
    return dict(title=layout['title'],columns=columns,unit=layout['unit'],rows=valid,
                bbox=[edges[0],layout['y'][0],edges[-1],bottom],quality_status='auto_layout_unreviewed',
                extraction_issues=errors),None

def generic_table(table, page):
    raw=table.get('raw_rows',[])
    if '目录' in page['section'] or page['pdf_page']<=4:return None,'directory_or_front_matter'
    if not raw:return None,'no_raw_rows'
    cells=[c for r in raw for c in r if c]
    if any(len(c)>120 for c in cells):return None,'prose_detected_in_cells'
    numeric=[c for c in cells if re.search(r'\d',c) and len(c)<35]
    bad=[c for c in numeric if c.count('(')!=c.count(')') or c.count('（')!=c.count('）')]
    if numeric and len(bad)/len(numeric)>.12:return None,'many_broken_parentheses'
    width=max(map(len,raw))
    if width<2:return None,'single_column'
    if sum(bool(NUMBER.fullmatch(c)) for c in cells)<6:return None,'not_a_numeric_table'
    rows=[{'label':f'原表行{i+1}','group':'','values':r+['']*(width-len(r))} for i,r in enumerate(raw)]
    return dict(title=table_title(page),columns=[f'原始列{i+1}' for i in range(width)],
                unit=unit_from_text(page['text']),rows=rows,bbox=table['bbox'],
                quality_status='raw_grid_unreviewed',extraction_issues=['保留原始单元格；年份/表头未可靠对应各列']),None

def common(page):
    return {key:page[key] for key in ['company','stock_code','report_year','section','source_file','source_url']} | {
        'report_type':'年度报告','pdf_page_start':page['pdf_page'],'pdf_page_end':page['pdf_page'],
        'printed_page':page['printed_page'],'scope':scope_from_text(page['text'])}

def embed_text(chunk):
    prefix=f"{chunk['company']}（{chunk['stock_code']}）{chunk['report_year']}年年度报告；章节：{chunk['section']}；PDF第{chunk['pdf_page_start']}页"
    if chunk['table_title']:prefix+=f"；表格：{chunk['table_title']}；单位：{chunk['unit']}；口径：{chunk['scope']}"
    return prefix+'\n'+chunk['content']

def make_table_chunks(table,page):
    prefix=f"表名：{table['title']}；单位：{table['unit']}；列头："+' | '.join(table['columns'])+'。空白表示原表未列示。'
    if table['quality_status']=='raw_grid_unreviewed':prefix+='本候选表未经人工核对，列名未可靠还原，请回原文确认。'
    if table.get('header_context'):prefix+='原表表头候选（待核对）：'+table['header_context']+'。'
    batches=[]; current=[]; size=0
    for row in table['rows']:
        line=(row['group']+'／' if row['group'] else '')+row['label']+'：'+'；'.join(f'{c}={v or "〔空白〕"}' for c,v in zip(table['columns'],row['values']))
        if current and len(prefix)+size+len(line)+1>800:
            batches.append(current);current=[];size=0
        current.append(line);size+=len(line)+1
    if current:batches.append(current)
    for i,batch in enumerate(batches,1):
        chunk=common(page)|dict(chunk_id=table['table_id']+f'-c{i:03d}',chunk_type='table',table_id=table['table_id'],
            table_title=table['title'],unit=table['unit'],quality_status=table['quality_status'],
            content=prefix+'\n'+'\n'.join(batch),scope='按列头区分；原始列则以同页原文为准',chunk_sequence=i,
            complete_rows=True,length_exception=len(prefix+'\n'+'\n'.join(batch))>800)
        chunk['embedding_text']=embed_text(chunk)
        yield chunk

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    chunks=[];tables_out=[];issues=[];summaries=[]
    for spec in REPORTS:
        path=DEFAULT_OUTPUT_DIR/f'{spec.code}_{spec.company}'
        pages=read_jsonl(path/'pages.jsonl'); old_tables=read_jsonl(path/'tables.jsonl')
        accepted={t['pdf_page']:[] for t in old_tables if t['validation_status']=='sample_layout_checked'}
        for t in old_tables:
            if t['pdf_page'] in accepted and t['validation_status']=='sample_layout_checked':accepted[t['pdf_page']].append(t)
        auto={}
        with pdfplumber.open(REPORT_DIR/spec.filename) as pdf:
            for page in pages:
                n=page['pdf_page']
                if n not in accepted and MAIN_TITLE.search('\n'.join(page['text'].splitlines()[:6])):
                    table,error=infer_statement(pdf.pages[n-1],page)
                    if table:auto[n]=table
                    else:issues.append(dict(company=spec.company,pdf_page=n,reason=error,kind='statement_layout'))
        for page in pages:
            n=page['pdf_page'];text=page['text']
            if text.count('\ufffd')>max(3,len(text)*.01):
                issues.append(dict(company=spec.company,pdf_page=n,reason='replacement_characters',kind='text_excluded'));continue
            for i,content in enumerate(split_prose(text),1):
                chunk=common(page)|dict(chunk_id=f'{spec.code}-p{n:04d}-text-{i:03d}',chunk_type='text',
                    table_title=table_title(page) if MAIN_TITLE.search(text[:600]) else '',unit=unit_from_text(text),
                    quality_status='page_text_unreviewed',content=content,chunk_sequence=i,length_exception=False)
                chunk['embedding_text']=embed_text(chunk);chunks.append(chunk)
            page_tables=[]
            if n in accepted:
                for old in accepted[n]:
                    page_tables.append(old|dict(title=old['title'],quality_status='sample_layout_checked'))
            elif n in auto:
                page_tables.append(auto[n]|dict(table_id=f'{spec.code}-p{n:04d}-auto-statement'))
            else:
                for old in (t for t in old_tables if t['pdf_page']==n):
                    table,error=generic_table(old,page)
                    if table:page_tables.append(table|dict(table_id=old['table_id']))
                    else:issues.append(dict(company=spec.company,pdf_page=n,table_id=old['table_id'],reason=error,kind='candidate_excluded'))
            for table in page_tables:
                tables_out.append(common(page)|table)
                chunks.extend(make_table_chunks(table,page))
        company_chunks=[c for c in chunks if c['stock_code']==spec.code]
        summaries.append(dict(company=spec.company,pdf_pages=len(pages),pages_with_text=sum(bool(p['text']) for p in pages),
            text_chunks=sum(c['chunk_type']=='text' for c in company_chunks),table_chunks=sum(c['chunk_type']=='table' for c in company_chunks),
            tables=sum(t['stock_code']==spec.code for t in tables_out),automatic_primary_statements=len(auto)))
        print(json.dumps(summaries[-1],ensure_ascii=False),flush=True)
    assert len({c['chunk_id'] for c in chunks})==len(chunks)
    write_jsonl(OUT/'chunks.jsonl',chunks);write_jsonl(OUT/'tables.jsonl',tables_out);write_jsonl(OUT/'processing_issues.jsonl',issues)
    sha=hashlib.sha256((OUT/'chunks.jsonl').read_bytes()).hexdigest()
    summary=dict(version='course-pilot-v1',generated_at=datetime.now().astimezone().isoformat(),chunk_size=800,overlap=120,
        chunk_count=len(chunks),chunks_sha256=sha,companies=summaries,quality_counts=dict(Counter(c['quality_status'] for c in chunks)),
        long_complete_row_chunks=sum(c['length_exception'] for c in chunks),issues=len(issues),
        policy='best-effort course corpus; unreviewed content is included with flags; no whole-corpus accuracy claim')
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    sample=[c for c in chunks if (c['stock_code'],c['pdf_page_start']) in [('601398',14),('601398',205),('601939',15),('601939',185)] and c['chunk_type']=='table']
    (OUT/'切块样本.md').write_text('# 课程版切块样本\n\n'+'\n\n'.join(f"## {c['chunk_id']}\n\n质量标记：{c['quality_status']}\n\n```text\n{c['embedding_text']}\n```" for c in sample),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
