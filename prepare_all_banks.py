"""Extend the course corpus to all ten banks, preserving the original pilot chunks."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import time

import pdfplumber
from pypdf import PdfReader
from bank_catalog import BANKS, ROOT
from pilot_layouts import extract_reading_order
from prepare_course_chunks import (common, embed_text, split_prose, generic_table, make_table_chunks,
                                   infer_statement, read_jsonl, unit_from_text)
from process_pilot_reports import TABLE_SETTINGS, clean_table, is_table_candidate, write_jsonl

OUT = ROOT / 'data/processed/course_all'
PILOT = ROOT / 'data/processed/course_pilot'
MAIN = re.compile(r'(?:合并(?:及(?:公司|银行))?|本行|银行|母公司)(?:资产负债表|利润表|现金流量表)')
HEADINGS = ('重要提示', '释义', '财务摘要', '财务概要', '主要会计数据和财务指标', '公司简介',
            '公司基本情况', '董事长致辞', '行长致辞', '管理层讨论与分析', '经营情况讨论与分析',
            '财务报告', '财务报表附注', '审计报告', '风险管理', '资本管理', '公司治理',
            '重要事项', '股份变动及股东情况', '利润分配', '环境和社会责任')


def outline_sections(path):
    reader = PdfReader(path)
    sections = []
    def visit(items):
        for item in items:
            if isinstance(item, list):
                visit(item)
            else:
                try:
                    title = re.sub(r'\s+', ' ', item.title).strip()
                    destination = reader.get_destination_page_number(item)
                    if title and isinstance(destination, int) and destination >= 0:
                        sections.append((destination + 1, title))
                except (AttributeError, ValueError, KeyError):
                    continue
    try:
        visit(reader.outline)
    except (ValueError, KeyError):
        pass
    return sorted(sections, key=lambda row: row[0])


def page_text(page):
    raw, reading_order = extract_reading_order(page, '601939')
    lines = [re.sub(r'[ \t\u3000]+', ' ', line).strip() for line in raw.splitlines()]
    lines = [line for line in lines if line]
    printed = None
    # Only accept an actual standalone footer number; do not infer an offset.
    footer = page.crop((0, max(0, page.height - 38), page.width, page.height)).extract_text() or ''
    for line in footer.splitlines():
        match = re.fullmatch(r'\s*[-—]?\s*(\d{1,3})\s*[-—]?\s*', line)
        if match:
            printed = int(match[1])
    cleaned = []
    for i, line in enumerate(lines):
        if printed is not None and i >= len(lines) - 3 and line.strip('-— ') == str(printed):
            continue
        if (i < 2 or i >= len(lines) - 2) and len(line) < 65 and '2025' in line and '年度报告' in line:
            continue
        cleaned.append(line)
    return '\n'.join(cleaned), raw, printed, reading_order


def detect_section(text, previous):
    for line in text.splitlines()[:7]:
        compact = line.replace(' ', '')
        if len(compact) < 48:
            if re.match(r'^第[一二三四五六七八九十\d]+[章节]', compact):
                return line
            if compact in HEADINGS:
                return compact
            for heading in HEADINGS:
                if re.fullmatch(r'[一二三四五六七八九十\d]+[、.．]?'+heading, compact):
                    return line
    return previous or '封面与导读／章节未可靠识别'


def extract_bank(bank):
    started = time.perf_counter()
    destination = OUT / 'banks' / f"{bank['code']}_{bank['bank']}"
    destination.mkdir(parents=True, exist_ok=True)
    summary_path = destination / 'summary.json'
    source = ROOT / 'data/annual_reports/2025' / bank['local_file']
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_hash != bank['sha256']:
        raise RuntimeError(f"{bank['bank']} PDF与下载清单的哈希不一致。")
    if summary_path.is_file():
        existing = json.loads(summary_path.read_text(encoding='utf-8'))
        files = ('pages.jsonl', 'chunks.jsonl', 'tables.jsonl', 'processing_issues.jsonl')
        if existing.get('parser_version') == 'course-all-v1' and existing.get('pdf_sha256') == source_hash and all((destination / name).is_file() for name in files):
            print(f"{bank['bank']}：复用已完成文档处理", flush=True)
            return existing
    sections = outline_sections(source)
    pages, tables, chunks, issues = [], [], [], []
    current_section = ''
    with pdfplumber.open(source) as pdf:
        total = len(pdf.pages)
        for number, page in enumerate(pdf.pages, 1):
            try:
                text, raw, printed, reading_order = page_text(page)
            except Exception as error:
                issues.append({'company': bank['bank'], 'pdf_page': number, 'kind': 'page_text_error', 'reason': type(error).__name__})
                text, raw, printed, reading_order = '', '', None, 'extraction_failed'
            outline = next((title for start, title in reversed(sections) if start <= number), None)
            current_section = outline or detect_section(text, current_section)
            head = '\n'.join(text.splitlines()[:8])
            main_title = MAIN.search(head.replace(' ', ''))
            if main_title:
                section = (outline or '财务报告') + '／' + main_title[0]
            else:
                section = current_section
            meta = dict(company=bank['bank'], stock_code=bank['code'], report_year=int(bank['report_year']),
                        source_file=bank['local_file'], source_url=bank['pdf_url'], pdf_page=number,
                        printed_page=printed, section=section,
                        section_method='pdf_outline' if outline else 'page_heading_best_effort',
                        page_heading=next((line for line in text.splitlines()[:8] if len(line) <= 48), ''),
                        text=text, raw_text=raw, reading_order=reading_order)
            pages.append(meta)
            if not text:
                issues.append({'company': bank['bank'], 'pdf_page': number, 'kind': 'empty_page', 'reason': 'no_extractable_text'})
            elif text.count('\ufffd') > max(3, len(text) * .01):
                issues.append({'company': bank['bank'], 'pdf_page': number, 'kind': 'text_excluded', 'reason': 'replacement_characters'})
            else:
                for sequence, content in enumerate(split_prose(text), 1):
                    chunk = common(meta) | dict(chunk_id=f"{bank['code']}-p{number:04d}-text-{sequence:03d}",
                        chunk_type='text', table_title=main_title[0] if main_title else '',
                        unit=unit_from_text(text), quality_status='page_text_unreviewed', content=content,
                        chunk_sequence=sequence, length_exception=False, section_method=meta['section_method'])
                    chunk['embedding_text'] = embed_text(chunk)
                    chunks.append(chunk)
                page_tables = []
                if main_title:
                    try:
                        table, reason = infer_statement(page, meta)
                        if table:
                            table.update(title=main_title[0], table_id=f"{bank['code']}-p{number:04d}-auto-statement")
                            page_tables.append(table)
                        else:
                            issues.append({'company': bank['bank'], 'pdf_page': number, 'kind': 'statement_layout', 'reason': reason})
                    except Exception as error:
                        issues.append({'company': bank['bank'], 'pdf_page': number, 'kind': 'statement_layout', 'reason': type(error).__name__})
                if not page_tables and number > 4 and is_table_candidate(text):
                    try:
                        # Ruled grids first; use the original pilot text strategy for borderless tables.
                        found = page.find_tables()
                        candidates = []
                        for item in found:
                            raw_rows, _, numeric_rows = clean_table(item.extract())
                            if numeric_rows >= 3:
                                candidates.append((item, raw_rows))
                        if not candidates:
                            for item in page.find_tables(TABLE_SETTINGS):
                                raw_rows, _, numeric_rows = clean_table(item.extract())
                                if numeric_rows >= 3:
                                    candidates.append((item, raw_rows))
                        for sequence, (item, raw_rows) in enumerate(candidates, 1):
                            table_id = f"{bank['code']}-p{number:04d}-t{sequence:02d}"
                            old = dict(raw_rows=raw_rows, bbox=list(item.bbox), table_id=table_id)
                            table, reason = generic_table(old, meta)
                            if table:
                                table['table_id'] = table_id
                                table['header_context'] = ' | '.join(' / '.join(row) for row in raw_rows[:2])[:220]
                                page_tables.append(table)
                            else:
                                issues.append({'company': bank['bank'], 'pdf_page': number, 'kind': 'candidate_excluded', 'reason': reason})
                    except Exception as error:
                        issues.append({'company': bank['bank'], 'pdf_page': number, 'kind': 'table_extraction', 'reason': type(error).__name__})
                for table in page_tables:
                    tables.append(common(meta) | table)
                    chunks.extend(make_table_chunks(table, meta))
            if number % 25 == 0 or number == total:
                print(f"{bank['bank']}：{number}/{total}页，{len(chunks)}块", flush=True)
            page.close()
    for filename, rows in (('pages.jsonl', pages), ('tables.jsonl', tables), ('chunks.jsonl', chunks), ('processing_issues.jsonl', issues)):
        write_jsonl(destination / filename, rows)
    summary = dict(parser_version='course-all-v1', company=bank['bank'], stock_code=bank['code'],
                   pdf_sha256=source_hash, pdf_pages=len(pages), pages_with_text=sum(bool(row['text']) for row in pages),
                   pages_with_printed_page=sum(row['printed_page'] is not None for row in pages),
                   text_chunks=sum(row['chunk_type'] == 'text' for row in chunks),
                   table_chunks=sum(row['chunk_type'] == 'table' for row in chunks), tables=len(tables),
                   automatic_primary_statements=sum(row['quality_status'] == 'auto_layout_unreviewed' for row in tables),
                   issues=len(issues), outline_sections=len(sections), elapsed_seconds=round(time.perf_counter()-started, 2))
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


def main(workers):
    OUT.mkdir(parents=True, exist_ok=True)
    pilot_summary = json.loads((PILOT / 'summary.json').read_text(encoding='utf-8'))
    chunks = read_jsonl(PILOT / 'chunks.jsonl')
    tables = read_jsonl(PILOT / 'tables.jsonl')
    issues = read_jsonl(PILOT / 'processing_issues.jsonl')
    pilot_codes = {row['stock_code'] for row in chunks}
    remaining = [bank for bank in BANKS if bank['code'] not in pilot_codes]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(extract_bank, bank) for bank in remaining]
        for future in as_completed(futures):
            print(json.dumps(future.result(), ensure_ascii=False), flush=True)
    summaries = list(pilot_summary['companies'])
    for bank in remaining:
        directory = OUT / 'banks' / f"{bank['code']}_{bank['bank']}"
        chunks.extend(read_jsonl(directory / 'chunks.jsonl'))
        tables.extend(read_jsonl(directory / 'tables.jsonl'))
        issues.extend(read_jsonl(directory / 'processing_issues.jsonl'))
        summaries.append(json.loads((directory / 'summary.json').read_text(encoding='utf-8')))
    assert len({row['chunk_id'] for row in chunks}) == len(chunks)
    assert {row['stock_code'] for row in chunks} == {bank['code'] for bank in BANKS}
    write_jsonl(OUT / 'chunks.jsonl', chunks)
    write_jsonl(OUT / 'tables.jsonl', tables)
    write_jsonl(OUT / 'processing_issues.jsonl', issues)
    summary = dict(version='course-all-v1', generated_at=datetime.now().astimezone().isoformat(),
                   company_count=len(summaries), chunk_count=len(chunks), chunk_size=800, overlap=120,
                   companies=summaries, quality_counts=dict(Counter(row['quality_status'] for row in chunks)),
                   issues=len(issues), reused_pilot_chunks=pilot_summary['chunk_count'],
                   chunks_sha256=hashlib.sha256((OUT/'chunks.jsonl').read_bytes()).hexdigest(),
                   long_complete_row_chunks=sum(row.get('length_exception', False) for row in chunks),
                   policy='Best-effort course corpus; unreviewed content is explicitly flagged.')
    (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=2, choices=range(1, 5))
    main(parser.parse_args().workers)
