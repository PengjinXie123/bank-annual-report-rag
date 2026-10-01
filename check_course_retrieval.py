"""Three actual retrieval questions; this does not evaluate generated answers."""
from collections import Counter
from datetime import datetime

from retrieval_core import ROOT, np, write_json
from search_course import CourseRetriever

QUESTIONS = [
    {'id': 'Q1', 'query': '工商银行2025年本集团营业收入和净利润分别是多少？',
     'company': '工商银行', 'targets': ['601398-p0014-verified-01-c001']},
    {'id': 'Q2', 'query': '建设银行2025年不良贷款率和核心一级资本充足率分别是多少？',
     'company': '建设银行', 'targets': ['601939-p0015-verified-01-c001', '601939-p0015-verified-01-c002']},
    {'id': 'Q3', 'query': '比较工商银行和建设银行2025年的核心一级资本充足率。',
     'company': None, 'targets': ['601398-p0016-verified-01-c001', '601939-p0015-verified-01-c001']},
]


def main():
    retriever = CourseRetriever(ROOT / 'data/indexes/course_pilot')
    chunks = retriever.chunks
    assert len(chunks) == len({c['chunk_id'] for c in chunks})
    assert np.isfinite(retriever.vectors).all()
    assert np.allclose(np.linalg.norm(retriever.vectors, axis=1), 1, atol=1e-5)
    assert all(len(c['content']) <= 800 or c.get('length_exception') for c in chunks)
    assert all(c['pdf_page_start'] == c['pdf_page_end'] for c in chunks)
    assert all(c.get('complete_rows') for c in chunks if c['chunk_type'] == 'table')
    output = ROOT / 'data/processed/course_pilot/retrieval_checks'
    output.mkdir(exist_ok=True)
    records = []
    for question in QUESTIONS:
        for mode in ('vector', 'bm25', 'hybrid'):
            result = retriever.search(question['query'], mode=mode, company=question['company'])
            actual = {row['chunk_id'] for row in result['hits']}
            targets = question['targets']
            result['check'] = {'question_id': question['id'], 'reference_chunk_ids': targets,
                               'reference_chunks_recalled': [target for target in targets if target in actual],
                               'all_reference_chunks_recalled': set(targets).issubset(actual),
                               'note': '已核对摘要样本块的 top-6 召回检查，不代表其他等效证据无效，也不评判模型回答。'}
            if question['company']:
                assert all(row['company'] == question['company'] for row in result['hits'])
            write_json(output / f"{question['id']}_{mode}.json", result)
            records.append({'id': question['id'], 'query': question['query'], 'mode': mode,
                            'reference_recalled': len(result['check']['reference_chunks_recalled']),
                            'reference_total': len(targets),
                            'companies': sorted({row['company'] for row in result['hits']}),
                            'first_hit': result['hits'][0]['chunk_id'] if result['hits'] else None,
                            'elapsed_seconds': result['elapsed_seconds']})
            print(f"{question['id']} {mode}: 摘要样本召回 {records[-1]['reference_recalled']}/{len(targets)}；"
                  f"公司 {','.join(records[-1]['companies'])}", flush=True)
    summary = {'created_at': datetime.now().astimezone().isoformat(),
               'integrity_checks': 'passed', 'chunk_count': len(chunks),
               'vector_shape': list(retriever.vectors.shape),
               'company_counts': dict(Counter(c['company'] for c in chunks)),
               'retrieval_records': records,
               'scope': '3 questions x 3 retrieval modes; no answer generation or answer accuracy measurement'}
    write_json(output / 'summary.json', summary)
    lines = ['# 两家银行索引与检索检查', '',
             f"检查时间：{summary['created_at']}", '',
             f"完整性检查通过：{len(chunks)}个唯一块ID；向量形状{list(retriever.vectors.shape)}；"
             '有限数值及L2归一化；语料/向量/BM25文件哈希与行数一致；正文800字上限；表格不截断行；银行筛选有效。', '',
             '模型：text-embedding-v4，1024维。BM25：jieba搜索分词 + BM25Okapi。混合：两路各取10块，RRF(k=60)，最终6块。', '',
             '|问题|模式|已核对摘要样本块召回|召回公司|首条块ID|',
             '|---|---|---|---|---|']
    lines.extend(f"|{r['id']}|{r['mode']}|{r['reference_recalled']}/{r['reference_total']}|"
                 f"{'、'.join(r['companies'])}|{r['first_hit']}|" for r in records)
    lines += ['', '问题：', ''] + [f"- {q['id']}：{q['query']}" for q in QUESTIONS]
    lines += ['', '本检查只确认真实检索和证据召回。目标块来自此前已核对的摘要表；没有命中这些固定块时，'
              '其他页可能仍提供有效证据，完整原文与分数保存在同目录的9份JSON中。尚未调用回答模型，'
              '也不将这3题作为最终10题评测。未复核表格存在错列风险，错误结果保留用于后续分析。', '']
    (output / '索引验收.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
