"""Export human-reviewed judgments alongside immutable original model answers."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export(directory):
    manifest = load(directory / 'run_manifest.json')
    if manifest['status'] != 'finished':
        raise ValueError('正式运行尚未完成。')
    for filename, key in [('reference_answers.json', 'reference_sha256'), ('questions.json', 'questions_sha256')]:
        if sha(directory / filename) != manifest[key]:
            raise ValueError(f'冻结文件发生变化：{filename}')
    grades = load(directory / 'assessments.json')
    questions = load(directory / 'questions.json')
    if {q['id'] for q in questions} != {g['id'] for g in grades}:
        raise ValueError('题目和人工判定不完整。')
    grade_map = {g['id']: g for g in grades}
    counts = Counter(g['judgment'] for g in grades)
    rows, chunks, details, records = [], [], [], {}
    prompt_tokens = completion_tokens = 0
    elapsed = 0
    for q in questions:
        qid = q['id']
        record_path = directory / f'{qid}.json'
        record = load(record_path)
        records[qid] = record
        grade = grade_map[qid]
        if sha(record_path) != grade['record_sha256']:
            raise ValueError(f'{qid} 原始回答与判定时的指纹不一致。')
        if record['query'] != q['query'] or record['retrieval']['corpus_sha256'] != manifest['index_manifest']['corpus_sha256']:
            raise ValueError(f'{qid} 题目或语料不一致。')
        hits = record['retrieval']['hits']
        recall = sorted({hit['company'] for hit in hits})
        usage = record.get('usage', {})
        prompt_tokens += usage.get('prompt_tokens', 0)
        completion_tokens += usage.get('completion_tokens', 0)
        elapsed += record.get('elapsed_seconds', 0)
        rows.append({'题号': qid, '类型': q['type'], '问题': q['query'], '筛选银行': q['company_filter'] or '全部10家',
                     '判定': grade['judgment'], '模型原始回答': record.get('answer', ''),
                     '错误或不足': grade['reason'], '原因归类': '、'.join(grade['failure_categories']),
                     '召回银行': '、'.join(recall), '召回块': '；'.join(hit['chunk_id'] for hit in hits),
                     '有效回答银行': '、'.join(grade['effective_answer_banks']),
                     '引用检查': '编号格式通过' if record['citation_check']['passed'] else '编号格式未通过',
                     '参考依据': grade['reference'], '总耗时秒': record['elapsed_seconds'],
                     '原始记录': f'{qid}.json', '原始记录SHA256': grade['record_sha256']})
        for hit in hits:
            chunks.append({'题号': qid, '排名': hit['rank'], '块ID': hit['chunk_id'], '银行': hit['company'],
                           'PDF起始页': hit['pdf_page_start'], 'PDF结束页': hit['pdf_page_end'],
                           '印刷页': hit.get('printed_page'), '章节': hit.get('section'),
                           '表名': hit.get('table_title'), '单位': hit.get('unit'), '口径元数据': hit.get('scope'),
                           '质量标记': hit.get('quality_status'), 'RRF分数': hit.get('rrf_score'),
                           '向量分数': hit['retrieval_scores'].get('vector'), 'BM25分数': hit['retrieval_scores'].get('bm25'),
                           '完整召回原文': hit['content']})
        recalled_text = '\n'.join(f"- 第{h['rank']}块 `{h['chunk_id']}`：{h['company']}，PDF第{h['pdf_page_start']}–{h['pdf_page_end']}页。" for h in hits)
        details.append(f"## {qid}：{q['type']} — {grade['judgment']}\n\n{q['query']}\n\n"
                       f"**第一次模型回答（原样保留）**\n\n{record.get('answer', record.get('error', ''))}\n\n"
                       f"**核对判定**：{grade['reason']}\n\n**参照依据**：{grade['reference']}\n\n"
                       f"**召回情况**：{len(recall)}家（{'、'.join(recall)}）；有效回答银行：{'、'.join(grade['effective_answer_banks']) or '无'}。\n\n"
                       f"{recalled_text}\n\n原始响应、提示词与全部召回原文见 [{qid}.json]({qid}.json)；可读原始记录见 [{qid}.md]({qid}.md)。\n")
    for filename, items in [('逐题评测记录.csv', rows), ('召回块明细.csv', chunks)]:
        with (directory / filename).open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(items[0]))
            writer.writeheader()
            writer.writerows(items)
    metrics = {'counts': dict(counts), 'strict_accuracy': counts['正确'] / len(questions),
               'question_count': len(questions), 'technical_completed_count': sum(r['status'] == 'completed' for r in records.values()),
               'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens,
               'total_tokens': prompt_tokens + completion_tokens, 'summed_question_elapsed_seconds': round(elapsed, 3),
               'coverage': {qid: {'recall_bank_count': len({h['company'] for h in records[qid]['retrieval']['hits']}),
                                'effective_answer_bank_count': len(grade_map[qid]['effective_answer_banks']), 'expected_bank_count': 10} for qid in ['Q9', 'Q10']}}
    (directory / 'evaluation_summary.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    table = '\n'.join(f"| {r['题号']} | {r['类型']} | {r['判定']} | {grade_map[r['题号']]['short_reason']} |" for r in rows)
    report = f"""# 银行业年报RAG：10题正式评测

评测开始：{manifest['started_at']}；结束：{manifest['finished_at']}。

采用10家银行2025年报、{manifest['index_manifest']['chunk_count']}个块，向量与BM25混合检索（RRF=60、每路候选10、最终Top-6）。Q1–Q6及Q8明确筛选对应银行；Q7、Q9、Q10在全部银行中检索。请求回答模型为 `deepseek-v4-flash`，服务实际返回 `deepseek-flash`，温度0.1、关闭思考、最大输出1800 tokens。未进行逐银行分解、补召回或修改答案。

先从原PDF准备参照依据并冻结指纹，再逐题生成一次，最后由Codex逐题核对原文。判断没有调用另一个大模型裁判。首次回答的10个JSON及Markdown未作改写；`completed`和引用编号检查通过只代表技术流程完成，不代表答案正确。

## 结果

**正确{counts['正确']}题、部分正确{counts['部分正确']}题、错误{counts['错误']}题；严格正确率{metrics['strict_accuracy']:.0%}。** Q8是预设资料外问题，正确拒答算正确；Q7/Q9/Q10的資料内信息漏召回不算正确拒答。该比例仅描述这10道固定题，不代表系统普遍准确率。

| 题号 | 题型 | 判定 | 主要发现 |
|---|---|---|---|
{table}

全景题Q9召回4/10家，但只得到3/10家的可靠指标；Q10召回5/10家，实际完整回答净息差变化、原因与措施的银行为3/10家。不同题的相关性不同，因此“召回了一家银行”不等于“有效回答了这家银行”。

## 错误如何发生

1. **召回与切块限制**：全局Top-6没有保证银行覆盖。同一家银行多个近似块占用名额；Q7的两家利润表只召回了收入部分，归母净利润位于没有入选的后续块。Q10中中国银行信用风险及农业银行利率敏感性分析属于低相关召回。
2. **模型数值比较错误**：Q9虽然写对了3个数值，却称农业银行1.27%为最高；已经召回的1.31%明显更高。这是回答生成阶段的错误，不能全归因于检索。
3. **口径与时间限定不足**：Q6将不同绿色商行和集团协同业务数据一概写成集团口径；Q10把平安银行“本行将继续”的部分展望措施混入已采取措施。数据和引用看似充分时仍需核对限定词。

## 文件与可复核性

- `questions.json`：固定10道题；`reference_answers.json`及`reference_pages/`：生成答案前冻结的参照事实和原PDF摘录；`reference_contact_1.jpg`、`reference_contact_2.jpg`：原PDF渲染核对图。
- `Q1.json`至`Q10.json`：原始模型响应、回答、提示词、参数、召回块、分数、出处与用量；同名Markdown便于阅读。
- `assessments.json`：逐题人工核对判定与对应原始记录指纹；`audit_pages/`：评测后复核实际引用的原PDF页，未改变参照答案。
- `逐题评测记录.csv`：每题回答、对错、错误原因；`召回块明细.csv`：全部60个召回块的内容、页码与分数；`evaluation_summary.json`：汇总指标。
- `run_manifest.json`：语料和索引指纹、模型参数、题目与参照文件指纹。运行总计消耗{metrics['total_tokens']}个回答模型tokens，逐题总耗时约{elapsed:.1f}秒（不含准备与人工核对）。

原始记录保存在沿用的 `course_pilot/answers/formal/` 历史根目录，实际使用的是已完成的10家索引 `data/indexes/course_all`，不是两家试点索引；网页历史记录也能查看这一轮10题。

## 逐题核对

""" + '\n'.join(details)
    (directory / '正式评测报告.md').write_text(report.replace('資料', '资料'), encoding='utf-8')
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    export(parser.parse_args().directory)
