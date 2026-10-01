"""Save limited PDF layout samples and matching extracted excerpts for the eight new banks."""
from bank_catalog import BANKS
from retrieval_core import ROOT, read_chunks, write_json
import pypdfium2 as pdfium


def main():
    output = ROOT / 'data/processed/course_all/samples'
    output.mkdir(parents=True, exist_ok=True)
    chunks = read_chunks(ROOT / 'data/processed/course_all/chunks.jsonl')
    records = []
    lines = ['# 新增8家银行的课程版抽查样本', '', '用于抽查PDF版面与提取内容；不代表全文人工验收。', '']
    for bank in BANKS:
        if bank['code'] in ('601398', '601939'):
            continue
        rows = [row for row in chunks if row['stock_code'] == bank['code']]
        # Early summary evidence, rather than a late note with similar words.
        selected = next((row for row in rows if row['chunk_type'] == 'text' and '不良贷款率' in row['content']
                         and row['pdf_page_start'] > 4), rows[0])
        page_number = selected['pdf_page_start']
        source = ROOT / 'data/annual_reports/2025' / bank['local_file']
        document = pdfium.PdfDocument(str(source))
        page = document[page_number - 1]
        bitmap = page.render(scale=1.2)
        bitmap.to_pil().save(output / f"{bank['code']}-p{page_number:04d}.png")
        bitmap.close(); page.close(); document.close()
        related = [row for row in rows if row['chunk_type'] == 'table' and row['pdf_page_start'] == page_number]
        records.append(dict(company=bank['bank'], stock_code=bank['code'], pdf_page=page_number,
                            text_chunk=selected['chunk_id'], table_chunks=[row['chunk_id'] for row in related],
                            image=f"{bank['code']}-p{page_number:04d}.png"))
        lines += [f"## {bank['bank']} · PDF第{page_number}页", '',
                  '```text', selected['content'], '```', '']
        if related:
            lines += ['同页表格首块（未人工认证）：', '', '```text', related[0]['content'], '```', '']
    write_json(output / 'samples.json', records)
    (output / '抽查样本.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
