"""Pilot annual-report parser for ICBC and CCB.

Outputs page text, structured tables, retrieval chunks, a processing summary,
and a human-readable QA sample. Every record keeps company, section, PDF page,
and printed report page metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pdfplumber
from pypdf import PdfReader
from pilot_layouts import LAYOUTS, extract_layout, extract_reading_order


ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "data" / "annual_reports" / "2025"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "processed" / "pilot"


@dataclass(frozen=True)
class ReportSpec:
    code: str
    company: str
    filename: str
    source_url: str
    header_pattern: str
    numbered_sections: bool


REPORTS = (
    ReportSpec(
        code="601398",
        company="工商银行",
        filename="601398_工商银行_2025年年度报告.pdf",
        source_url="https://static.cninfo.com.cn/finalpage/2026-03-28/1225047240.PDF",
        header_pattern=r"^中国工商银行股份有限公司\s*2025年度报告（A股）\s*(\d+)$",
        numbered_sections=True,
    ),
    ReportSpec(
        code="601939",
        company="建设银行",
        filename="601939_建设银行_2025年年度报告.pdf",
        source_url="https://static.cninfo.com.cn/finalpage/2026-03-28/1225046718.PDF",
        header_pattern=r"^中国建设银行股份有限公司\s*\\\s*2025年度报告$",
        numbered_sections=False,
    ),
)


TABLE_SETTINGS = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "snap_tolerance": 4,
    "join_tolerance": 4,
    "intersection_tolerance": 6,
    "text_tolerance": 3,
    "min_words_vertical": 2,
    "min_words_horizontal": 1,
}

CCB_SECTIONS = (
    "释义",
    "公司简介",
    "公司基本情况",
    "排名和奖项",
    "财务摘要",
    "董事长致辞",
    "行长致辞",
    "管理层讨论与分析",
    "公司治理",
    "董事会报告",
    "重要事项",
    "组织架构图",
    "分支机构及子公司",
    "审计报告",
    "财务报表",
    "财务报表附注",
    "未经审计补充财务资料",
)

ICBC_SECTIONS = (
    "1. 释义",
    "2. 2025 年主要排名与奖项",
    "3. 重要提示",
    "4. 公司基本情况简介",
    "5. 财务概要",
    "6. 董事长致辞",
    "7. 行长致辞",
    "8. 讨论与分析",
    "9. 股本变动及主要股东持股情况",
    "10. 公司治理、环境和社会",
    "11. 董事会报告",
    "12. 重要事项",
    "13. 组织机构图",
    "14. 审计报告及财务报告",
    "15. 董事、高级管理人员关于2025年度报告的确认意见",
    "16. 备查文件目录",
    "17. 境内外机构名录",
)


def normalize_space(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[ \t\u3000]+", " ", value.replace("\r", "")).strip()


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def detect_printed_page(lines: list[str], spec: ReportSpec) -> int | None:
    header_regex = re.compile(spec.header_pattern)
    for line in lines[:3] + lines[-3:]:
        match = header_regex.match(normalize_space(line))
        if match and match.groups():
            return int(match.group(1))

    # CCB places the report page number on a line by itself at the bottom.
    for line in reversed(lines[-4:]):
        value = normalize_space(line)
        if re.fullmatch(r"\d{1,3}", value):
            return int(value)
    return None


def clean_page_text(raw_text: str, spec: ReportSpec) -> tuple[str, int | None]:
    lines = [normalize_space(line) for line in raw_text.splitlines()]
    lines = [line for line in lines if line]
    printed_page = detect_printed_page(lines, spec)
    header_regex = re.compile(spec.header_pattern)

    cleaned: list[str] = []
    for index, line in enumerate(lines):
        if header_regex.match(line):
            continue
        if (
            spec.code == "601939"
            and printed_page is not None
            and index >= len(lines) - 3
            and line == str(printed_page)
        ):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip(), printed_page


def find_section(text: str, previous: str, spec: ReportSpec) -> tuple[str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    page_heading = ""
    for line in lines[:8]:
        if len(line) <= 40 and not re.fullmatch(r"[\d\s.,()%（）/+\-]+", line):
            page_heading = line
            break

    first_block = "\n".join(lines[:8])
    if spec.code == "601398" and any(
        marker in first_block
        for marker in ("审计报告", "合并及公司资产负债表", "合并及公司利润表", "财务报表附注")
    ):
        return "14. 审计报告及财务报告", page_heading
    if spec.code == "601939" and any(
        marker in first_block
        for marker in ("外部审计师审计报告及财务报告", "审计报告", "合并及银行资产负债表", "合并及银行利润表", "财务报表附注")
    ):
        return "审计报告及财务报告", page_heading

    if spec.numbered_sections:
        for line in lines[:8]:
            compact = line.replace(" ", "")
            for section in ICBC_SECTIONS:
                if compact == section.replace(" ", ""):
                    return section, page_heading
    else:
        for line in lines[:8]:
            candidate = line.replace(" ", "")
            for section in CCB_SECTIONS:
                if candidate == section or candidate.startswith(section):
                    return section, page_heading
    return previous or "未识别章节", page_heading


def numeric_token_count(line: str) -> int:
    return len(re.findall(r"(?<![\u4e00-\u9fff])\(?-?\d[\d,.]*(?:%|个百分点)?\)?", line))


def is_table_candidate(text: str) -> bool:
    lines = text.splitlines()
    numeric_rows = sum(numeric_token_count(line) >= 2 for line in lines)
    return numeric_rows >= 3


def clean_table(raw_table: list[list[str | None]]) -> tuple[list[list[str]], list[list[str]], int]:
    raw_rows: list[list[str]] = []
    normalized_rows: list[list[str]] = []
    numeric_rows = 0
    numeric_cell = re.compile(r"^[（(]?[-+]?\d[\d,.]*(?:%|个百分点)?[)）]?$|^[-—]+$")

    for row in raw_table:
        cells = [normalize_space(cell).replace("\n", " ") for cell in row]
        if not any(cells):
            continue
        raw_rows.append(cells)
        nonempty = [cell for cell in cells if cell]
        numeric_indexes = [index for index, cell in enumerate(cells) if cell and numeric_cell.match(cell)]
        if len(numeric_indexes) >= 2:
            numeric_rows += 1
        if len(numeric_indexes) >= 2 and numeric_indexes[0] > 0:
            first_number = numeric_indexes[0]
            label = "".join(cell for cell in cells[:first_number] if cell)
            normalized = ([label] if label else []) + [cell for cell in cells[first_number:] if cell]
        else:
            normalized = ["".join(nonempty)] if nonempty else []
        if normalized:
            normalized_rows.append(normalized)
    return raw_rows, normalized_rows, numeric_rows


def split_text(text: str, max_chars: int = 900, overlap_chars: int = 120) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    for line in lines:
        extra = len(line) + (1 if current else 0)
        if current and current_length + extra > max_chars:
            chunk = "\n".join(current)
            chunks.append(chunk)
            overlap = chunk[-overlap_chars:] if overlap_chars else ""
            current = [overlap, line] if overlap else [line]
            current_length = sum(len(item) for item in current) + len(current) - 1
        else:
            current.append(line)
            current_length += extra
    if current:
        chunks.append("\n".join(current))
    return chunks


def table_chunks(table: dict[str, Any], max_chars: int = 900) -> list[str]:
    if table.get('validation_status') != 'sample_layout_checked':
        return []
    if 'columns' in table:
        prefix = (f"{table['company']}2025年年度报告；{table['section']}；"
                  f"{table['title']}；PDF第{table['pdf_page']}页；报告第{table['printed_page']}页；"
                  f"单位：{table['unit']}。空白表示原表未列示；破折号保留原义；括号表示负数。")
        result, current = [], prefix
        for row in table['rows']:
            line = '\n' + row['group'] + '／' + row['label'] + '：' + '；'.join(
                f"{col}={value or '（空白）'}" for col, value in zip(table['columns'], row['values']))
            if len(current)+len(line)>max_chars and current!=prefix:
                result.append(current)
                current=prefix
            current+=line
        if current!=prefix: result.append(current)
        return result
    rows = table["normalized_rows"]
    if not rows:
        return []
    prefix = (
        f"表格来源：{table['company']}2025年年度报告；"
        f"章节：{table['section']}；PDF第{table['pdf_page']}页；"
        f"报告第{table['printed_page'] or '未标注'}页。"
    )
    result: list[str] = []
    current = prefix
    header = " | ".join(rows[0])
    for index, row in enumerate(rows):
        line = " | ".join(row)
        if len(current) + len(line) + 1 > max_chars and current != prefix:
            result.append(current)
            current = prefix + "\n" + header if index > 0 else prefix
        current += "\n" + line
    if current != prefix:
        result.append(current)
    return result


def quality_metrics(text: str) -> dict[str, float | int]:
    visible = [char for char in text if not char.isspace()]
    if not visible:
        return {"characters": 0, "cjk_ratio": 0.0, "replacement_chars": 0}
    cjk = sum("\u4e00" <= char <= "\u9fff" for char in visible)
    return {
        "characters": len(visible),
        "cjk_ratio": round(cjk / len(visible), 4),
        "replacement_chars": text.count("\ufffd"),
    }


def process_report(spec: ReportSpec, output_root: Path) -> dict[str, Any]:
    input_path = REPORT_DIR / spec.filename
    if not input_path.exists():
        raise FileNotFoundError(input_path)

    report_output = output_root / f"{spec.code}_{spec.company}"
    report_output.mkdir(parents=True, exist_ok=True)
    pages: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    section = "未识别章节"
    started = time.time()
    section_starts = [(1, '封面与导读')]
    if spec.code == '601939':
        reader = PdfReader(input_path)
        def walk(items):
            for item in items:
                if isinstance(item, list):
                    walk(item)
                else:
                    section_starts.append((reader.get_destination_page_number(item)+1, item.title))
        walk(reader.outline)

    with pdfplumber.open(input_path) as pdf:
        if spec.code == '601398':
            toc = pdf.pages[4].extract_text() or ''
            section_starts.append((5, '目录'))
            for line in toc.splitlines():
                match = re.match(r'^(\d+\.\s+.+?)\s*\.{3,}\s*(\d+)\s*$', line)
                if match:
                    section_starts.append((int(match[2])+1, match[1].strip()))
        section_starts.sort(key=lambda item:item[0])
        for page_index, page in enumerate(pdf.pages, start=1):
            raw_text, reading_order = extract_reading_order(page, spec.code)
            text = '\n'.join(normalize_space(line) for line in raw_text.splitlines()
                             if normalize_space(line) and not re.match(spec.header_pattern,normalize_space(line)))
            footer = page.crop((0,page.height-65,page.width,page.height)).extract_text() or ''
            printed_page = detect_printed_page(footer.splitlines(), spec)
            expected = (page_index-1 if 2<=page_index<=190 else page_index-192 if page_index>=193 else None) if spec.code=='601398' else (page_index-2 if 5<=page_index<=366 else None)
            # On dense pages the footer can share a baseline with body text.
            # Verify the actual footer word; never synthesize a page number.
            if expected is not None and printed_page != expected:
                footer_words=page.crop((0,page.height-50,page.width,page.height)).extract_words()
                if any(w['text']==str(expected) and (w['x0']<80 or w['x1']>page.width-80 or abs((w['x0']+w['x1'])/2-page.width/2)<20) for w in footer_words):
                    printed_page=expected
            page_number_issue = printed_page != expected
            if expected is None or page_number_issue: printed_page=None
            if printed_page is not None:
                text=re.sub(r'\s'+str(printed_page)+r'\s*$','',text)
            section = next((s for n,s in reversed(section_starts) if n<=page_index),'封面与导读')
            if spec.code=='601398' and page_index>=191:
                section='14. 审计报告及财务报告／附件'
            if spec.code=='601939' and 180<=page_index<=366:
                section='附录／审计报告及财务报告'
            if (spec.code=='601398' and page_index>=193) or (spec.code=='601939' and 180<=page_index<=366):
                head='\n'.join(text.splitlines()[:5])
                for marker in ['未经审计财务报表补充资料','财务报表补充资料','财务报表附注','合并及公司利润表','合并及银行利润表','合并及公司资产负债表','合并及银行资产负债表','审计报告']:
                    if marker in head:
                        section += '／'+marker
                        break
            page_heading = next((l for l in text.splitlines()[:8] if len(l)<=40),'')
            page_tables: list[dict[str, Any]] = []

            layouts=LAYOUTS.get((spec.code,page_index),[])
            for table_index, layout in enumerate(layouts,start=1):
                rows, errors = extract_layout(page,layout)
                if errors: raise ValueError(f'{spec.code} p{page_index} invalid layout: {errors}')
                table_id=f'{spec.code}-p{page_index:04d}-verified-{table_index:02d}'
                record=dict(table_id=table_id,company=spec.company,stock_code=spec.code,report_year=2025,
                    report_title=f'{spec.company}2025年年度报告',source_file=spec.filename,source_url=spec.source_url,
                    pdf_page=page_index,printed_page=printed_page,section=section,page_heading=page_heading,
                    bbox=[layout['edges'][0],layout['y'][0],layout['edges'][-1],layout['y'][1]],
                    title=layout['title'],columns=layout['columns'],unit=layout['unit'],rows=rows,
                    normalized_rows=[['项目']+layout['columns']]+[[r['group']+'／'+r['label']]+r['values'] for r in rows],
                    source_context=text,numeric_rows=len(rows),validation_status='sample_layout_checked')
                tables.append(record)
                page_tables.append({'table_id':table_id,'rows':len(rows),'validation_status':'sample_layout_checked'})

            if not layouts and is_table_candidate(text):
                for table_index, found_table in enumerate(page.find_tables(TABLE_SETTINGS), start=1):
                    raw_rows, normalized_rows, numeric_rows = clean_table(found_table.extract())
                    if len(normalized_rows) < 3 or numeric_rows < 3:
                        continue
                    table_id = f"{spec.code}-p{page_index:04d}-t{table_index:02d}"
                    record = {
                        "table_id": table_id,
                        "company": spec.company,
                        "stock_code": spec.code,
                        "report_year": 2025,
                        "report_title": f"{spec.company}2025年年度报告",
                        "source_file": spec.filename,
                        "source_url": spec.source_url,
                        "pdf_page": page_index,
                        "printed_page": printed_page,
                        "section": section,
                        "page_heading": page_heading,
                        "bbox": [round(value, 2) for value in found_table.bbox],
                        "raw_rows": raw_rows,
                        "normalized_rows": normalized_rows,
                        "numeric_rows": numeric_rows,
                        "validation_status": "needs_review",
                        "index_eligible": False,
                    }
                    tables.append(record)
                    page_tables.append({"table_id": table_id, "rows": len(normalized_rows)})

            page_record = {
                "page_id": f"{spec.code}-p{page_index:04d}",
                "company": spec.company,
                "stock_code": spec.code,
                "report_year": 2025,
                "report_title": f"{spec.company}2025年年度报告",
                "source_file": spec.filename,
                "source_url": spec.source_url,
                "pdf_page": page_index,
                "printed_page": printed_page,
                "section": section,
                "page_heading": page_heading,
                "text": text,
                "raw_text": raw_text,
                "quality": quality_metrics(text),
                "tables": page_tables,
                "reading_order": reading_order,
                "page_number_issue": page_number_issue,
                "page_number_method": "footer_text_verified_against_report_sequence" if printed_page is not None else "unavailable_or_not_printed",
            }
            pages.append(page_record)

            for chunk_index, content in enumerate(split_text(text), start=1):
                chunks.append(
                    {
                        "chunk_id": f"{spec.code}-p{page_index:04d}-text-{chunk_index:02d}",
                        "chunk_type": "text",
                        "company": spec.company,
                        "stock_code": spec.code,
                        "report_year": 2025,
                        "source_file": spec.filename,
                        "source_url": spec.source_url,
                        "pdf_page": page_index,
                        "printed_page": printed_page,
                        "section": section,
                        "page_heading": page_heading,
                        "content": content,
                        "validation_status": "needs_review",
                        "index_eligible": False,
                    }
                )

            if page_index % 25 == 0 or page_index == len(pdf.pages):
                print(f"{spec.company}: {page_index}/{len(pdf.pages)} 页", flush=True)

    for table in tables:
        for index, content in enumerate(table_chunks(table), start=1):
            chunks.append(
                {
                    "chunk_id": f"{table['table_id']}-chunk-{index:02d}",
                    "chunk_type": "table",
                    "company": table["company"],
                    "stock_code": table["stock_code"],
                    "report_year": 2025,
                    "source_file": table["source_file"],
                    "source_url": table["source_url"],
                    "pdf_page": table["pdf_page"],
                    "printed_page": table["printed_page"],
                    "section": table["section"],
                    "page_heading": table["page_heading"],
                    "table_id": table["table_id"],
                    "content": content,
                    "validation_status": table['validation_status'],
                    "index_eligible": True,
                }
            )

    write_jsonl(report_output / "pages.jsonl", pages)
    write_jsonl(report_output / "tables.jsonl", tables)
    write_jsonl(report_output / "chunks.jsonl", chunks)
    write_jsonl(report_output / "index_ready_chunks.jsonl", [c for c in chunks if c.get('index_eligible')])

    summary = {
        "company": spec.company,
        "stock_code": spec.code,
        "report_year": 2025,
        "source_file": spec.filename,
        "source_url": spec.source_url,
        "pdf_pages": len(pages),
        "pages_with_text": sum(bool(page["text"]) for page in pages),
        "pages_with_printed_page": sum(page["printed_page"] is not None for page in pages),
        "pages_with_tables": sum(bool(page["tables"]) for page in pages),
        "tables": len(tables),
        "text_chunks": sum(chunk["chunk_type"] == "text" for chunk in chunks),
        "table_chunks": sum(chunk["chunk_type"] == "table" for chunk in chunks),
        "characters": sum(page["quality"]["characters"] for page in pages),
        "replacement_chars": sum(page["quality"]["replacement_chars"] for page in pages),
        "sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "verified_sample_tables": sum(t.get('validation_status')=='sample_layout_checked' for t in tables),
        "unreviewed_table_candidates": sum(t.get('validation_status')=='needs_review' for t in tables),
        "two_column_pages": [p['pdf_page'] for p in pages if p['reading_order']=='two_columns'],
        "missing_or_conflicting_printed_pages": [p['pdf_page'] for p in pages if p['page_number_issue']],
        "acceptance_scope": "sample_only; unreviewed text and tables excluded from index_ready_chunks",
        "elapsed_seconds": round(time.time() - started, 2),
    }
    (report_output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    build_qa_sample(report_output / "qa_sample.md", pages, tables, spec)
    return summary


def build_qa_sample(path: Path, pages: list[dict[str, Any]], tables: list[dict[str, Any]], spec: ReportSpec) -> None:
    terms = ("财务概要", "财务摘要", "不良贷款率", "资本充足率", "合并利润表")
    selected: list[tuple[str, dict[str, Any]]] = []
    used_pages: set[int] = set()
    for term in terms:
        aliases = (term,)
        if term == "合并利润表":
            aliases = ("合并及公司利润表", "合并及银行利润表")
        candidates = [
            page
            for page in pages
            if page["pdf_page"] not in used_pages
            and (any(alias in page["text"] for alias in aliases) or term in page["section"])
        ]
        def sample_score(page: dict[str, Any]) -> int:
            first_text = page["text"][:240]
            first_line = page["text"].splitlines()[0] if page["text"] else ""
            score = 0
            score += 120 if any(alias in first_text for alias in aliases) else 0
            score += 200 if any(alias in first_line for alias in aliases) else 0
            score += 80 if term in page["section"] else 0
            if term in ("不良贷款率", "资本充足率") and page["section"] in ("5. 财务概要", "财务摘要"):
                score += 220
            score += 30 if len(page["text"]) > 300 else 0
            score -= 250 if len(page["text"]) < 100 else 0
            score -= 200 if page["page_heading"] == "目录" else 0
            return score
        match = max(candidates, key=sample_score, default=None)
        if match:
            selected.append((term, match))
            used_pages.add(match["pdf_page"])

    lines = [
        f"# {spec.company}文档处理抽查",
        "",
        "本文件用于人工核对正文、报告页码、章节和表格行列。",
        "",
    ]
    for term, page in selected:
        lines.extend(
            [
                f"## 关键词：{term}",
                "",
                f"- PDF页码：{page['pdf_page']}",
                f"- 报告页码：{page['printed_page']}",
                f"- 章节：{page['section']}",
                "",
                "```text",
                page["text"][:1800],
                "```",
                "",
            ]
        )
        page_tables = [table for table in tables if table["pdf_page"] == page["pdf_page"]][:2]
        for table in page_tables:
            lines.extend([f"表格 `{table['table_id']}`：", ""])
            for row in table["normalized_rows"][:12]:
                lines.append(" | ".join(row))
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    summaries = [process_report(spec, args.output_dir) for spec in REPORTS]
    (args.output_dir / "pilot_summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
