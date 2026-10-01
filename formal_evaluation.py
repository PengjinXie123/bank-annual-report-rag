"""Freeze source references, then run the ten course questions once without answer rewriting."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path

from bank_catalog import BANKS, ROOT

QUESTIONS = [
    ('Q1', '单公司数值', '工商银行', '工商银行2025年集团口径的营业收入和净利润分别是多少？请注明单位和出处。'),
    ('Q2', '表格与计算', '建设银行', '建设银行2025年末资产总额是多少？相比2024年末增加了多少，增长率是多少？请列出计算依据和出处。'),
    ('Q3', '风险指标', '农业银行', '农业银行2025年末的不良贷款率和拨备覆盖率分别是多少？相比2024年末各变化了多少个百分点？'),
    ('Q4', '资本指标', '中国银行', '中国银行2025年末集团口径的核心一级资本充足率和资本充足率分别是多少？请给出出处。'),
    ('Q5', '正文解释', '招商银行', '招商银行2025年的净利息收益率相比2024年如何变化？年报如何解释这种变化？'),
    ('Q6', '业务信息归纳', '兴业银行', '兴业银行2025年报披露了哪些绿色金融业务举措？请列举至少3项，并给出相关业务数据及出处；证据不足时明确说明。'),
    ('Q7', '跨两家公司比较', None, '比较交通银行与中信银行2025年的营业收入和归属于母公司股东的净利润：分别是多少、同比如何变化？请保持单位和统计口径一致，并分别引用两家年报。'),
    ('Q8', '证据不足检验', '平安银行', '仅依据本知识库中的2025年度报告，能否得知平安银行2026年第三季度的实际净利润？请说明依据，不要用预测值代替实际值。'),
    ('Q9', '跨公司全景数值', None, '汇总本知识库中10家银行2025年末的不良贷款率，按从低到高排序，指出最高和最低的银行。逐家注明统计口径、报告页码；没有找到可靠证据的银行单独列出，不要补造数据。'),
    ('Q10', '跨公司全景分析', None, '综合10家银行2025年报，各家如何描述净息差或净利息收益率的变化及其原因？采取了哪些应对措施？先逐家列出证据，再归纳共同点和差异，并明确实际覆盖及缺少证据的银行。'),
]

# These pages are checked directly against the downloaded PDF files, not against generated answers.
SOURCE_PAGES = {
    '工商银行': [14, 16, 21, 27], '建设银行': [14, 15, 25],
    '农业银行': [10, 11, 25, 28], '中国银行': [8, 9, 17],
    '交通银行': [11, 12, 39], '招商银行': [16, 46],
    '兴业银行': [16, 28, 29, 30, 40, 60, 61, 78, 79, 91],
    '浦发银行': [28, 29, 58, 72], '中信银行': [17, 18, 52, 443],
    '平安银行': [7, 17, 25, 26],
}

FACTS = {
    'Q1': {'银行': '工商银行', 'PDF页': [14], '单位': '人民币百万元', '口径': '集团',
           '营业收入': 838270, '净利润': 370766, '注意': '归母净利润368562不是题目要求的集团净利润。'},
    'Q2': {'银行': '建设银行', 'PDF页': [14], '单位': '人民币百万元', '口径': '集团',
           '2025资产': 45631818, '2024资产': 40571149, '增加额': 5060669,
           '增长率%': round((45631818 / 40571149 - 1) * 100, 2), '公式': '(2025资产-2024资产)/2024资产×100%'},
    'Q3': {'银行': '农业银行', 'PDF页': [11], '2025不良贷款率%': 1.27, '2024不良贷款率%': 1.30,
           '不良变动百分点': -0.03, '2025拨备覆盖率%': 292.55, '2024拨备覆盖率%': 299.61, '拨备变动百分点': -7.06},
    'Q4': {'银行': '中国银行', 'PDF页': [8, 9], '口径': '集团资本监管口径', '核心一级资本充足率%': 12.53, '资本充足率%': 18.85},
    'Q5': {'银行': '招商银行', 'PDF页': [16, 46], '集团2025%': 1.87, '集团2024%': 1.98, '变动百分点': -0.11,
           '本公司2025%': 1.92, '本公司变动基点': -12,
           '原因': ['存量房贷利率及LPR下调、有效信贷需求不足影响贷款定价', '市场利率下行影响债券、同业、票据等收益率', '低成本活期增长承压、存款定期化削弱负债成本下降效果'],
           '判定': '题目未限定集团，准确区分本公司和集团均可；至少覆盖资产与负债两端原因。'},
    'Q6': {'银行': '兴业银行', 'PDF页': [60, 61],
           '可接受举措示例': ['绿色与转型金融：表内外绿色融资2.46万亿元，人行口径绿色贷款11075.54亿元',
                             '绿色与科技融合：科技类企业绿色贷款4197.73亿元',
                             '区域协作：京津冀、长三角、粤港澳等区域绿色贷款5322亿元；西部873.82亿元',
                             '碳金融创新：创新业务1228笔、1013.87亿元；企业碳账户5.15万户、个人778.42万户',
                             '集团综合绿色服务：绿色租赁、信托、基金1498.31亿元，ESG及绿色理财累计发行3939.04亿元'],
           '判定': '至少3个不同举措，数据要与对应举措、时点、单位一致；例子不是穷尽清单。'},
    'Q7': {'单位': '人民币百万元', '口径': '合并营业收入与归母净利润',
           '交通银行': {'PDF页': [11], '营业收入': 265071, '营业收入同比%': 2.02, '归母净利润': 95622, '归母同比%': 2.18},
           '中信银行': {'PDF页': [17, 443], '营业收入': 212475, '营业收入同比%': -0.55, '归母净利润': 70618, '归母同比%': 2.98},
           '注意': '中信表中“归属于本行股东”70618与PDF443的归母合并净利润对应。'},
    'Q8': {'银行': '平安银行', 'PDF页': [17, 25], '预期': '明确无法仅从2025年报得到2026年第三季度实际净利润，不捏造，不将预测或2025季度数据替代实际值。',
           '判定': '这是预设资料范围之外的问题；正确拒答算正确。不能把其它题的检索漏召回拒答同样算正确。'},
    'Q9': {'最低': ['招商银行'], '最高并列': ['工商银行', '建设银行'],
           '数据%': {'工商银行': 1.31, '建设银行': 1.31, '农业银行': 1.27, '中国银行': 1.23,
                     '交通银行': 1.28, '招商银行': 0.94, '兴业银行': 1.08, '浦发银行': 1.26, '中信银行': 1.15, '平安银行': 1.05},
           'PDF页': {'工商银行': 16, '建设银行': 15, '农业银行': 11, '中国银行': 8, '交通银行': 12,
                     '招商银行': 16, '兴业银行': 91, '浦发银行': 29, '中信银行': 18, '平安银行': 17},
           '口径注意': '逐家沿用原报告口径。交行标注集团监管口径；兴业原文公司口径须保留，不强行统一为集团。其他报告中明确集团的指标标注集团；农业结合财务摘要说明，不能把行业1.50%误作本行指标。',
           '判定': '完整正确须覆盖10家、升序及并列极值正确、引用有支持；资料存在而未召回属于系统覆盖失败，诚实列缺证只计部分正确。'},
    'Q10': {'逐行基准': {
        '工商银行': {'PDF页': [16, 21, 27], '2025%': 1.28, '2024%': 1.42, '要点': 'LPR及存款期限结构影响；改善存款成本、推动综合服务。'},
        '建设银行': {'PDF页': [15, 25], '2025%': 1.34, '2024%': 1.51, '要点': 'LPR和低市场利率压低收益；调资产负债结构、加强定价。'},
        '农业银行': {'PDF页': [11, 25, 28], '2025%': 1.28, '2024%': 1.42, '要点': 'LPR及低市场利率压低资产收益；落实存款利率调整机制、优化存款结构。'},
        '中国银行': {'PDF页': [8, 17], '2025%': 1.26, '2024%': 1.40, '要点': '境内LPR及外币市场利率下降；量价协同、存款成本管控。'},
        '交通银行': {'PDF页': [12, 39], '2025%': 1.20, '2024%': 1.27, '要点': 'LPR、行业竞争及证券投资收益下行；业务结构调整、动态定价。'},
        '招商银行': {'PDF页': [16, 46], '2025%': 1.87, '2024%': 1.98, '要点': '房贷及LPR下调、需求不足、存款定期化；有效资产组织、低成本存款、高成本负债压降。'},
        '兴业银行': {'PDF页': [29, 40, 78, 79], '2025%': 1.71, '2024%': 1.82, '要点': '生息资产收益3.72%至3.24%，负债成本2.17%至1.74%；资产结构优化、结算存款与灵活定价。'},
        '浦发银行': {'PDF页': [28, 58, 72], '2025%': 1.42, '2024%': 1.42, '要点': '资产收益与负债成本同降46基点，息差持平；主动资产负债管理、提高一般贷款及存款占比。'},
        '中信银行': {'PDF页': [17, 52], '2025%': 1.63, '2024%': 1.77, '要点': 'LPR、按揭重定价、需求不足；增高减低资产结构、压票据、负债结构优化。'},
        '平安银行': {'PDF页': [7, 17, 25, 26], '2025%': 1.78, '2024%': 1.87, '要点': '贷款利率下行与业务结构调整；低成本及活期存款吸收、负债成本下降、降本增效。'},
    }, '判定': '完整正确需逐家覆盖变化、原因、措施与来源，并有共同点及差异；仅覆盖少数银行算部分或错误，不能推广到10家。'},
}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(directory):
    from pypdf import PdfReader
    import pdfplumber
    directory.mkdir(parents=True, exist_ok=False)
    snapshots = []
    for bank in BANKS:
        source = ROOT / 'data/annual_reports/2025' / bank['local_file']
        actual_sha = sha(source)
        if actual_sha != bank['sha256']:
            raise RuntimeError(f"源PDF指纹变化：{bank['bank']}")
        reader = PdfReader(source)
        plumber = pdfplumber.open(source) if bank['code'] == '601939' else None
        for page_number in SOURCE_PAGES[bank['bank']]:
            text = (plumber.pages[page_number - 1].extract_text(x_tolerance=3, y_tolerance=3)
                    if plumber else reader.pages[page_number - 1].extract_text()) or ''
            path = directory / 'reference_pages' / f"{bank['code']}-p{page_number:04}.txt"
            path.parent.mkdir(exist_ok=True)
            path.write_text(text, encoding='utf-8')
            snapshots.append({'company': bank['bank'], 'source_file': bank['local_file'], 'source_url': bank['pdf_url'],
                              'source_sha256': actual_sha, 'pdf_page': page_number,
                              'text_extractor': 'pdfplumber' if plumber else 'pypdf',
                              'text_file': str(path.relative_to(directory)), 'text_sha256': sha(path)})
        if plumber:
            plumber.close()
        print(f"原PDF依据已保存：{bank['bank']}", flush=True)
    save(directory / 'questions.json', [{'id': qid, 'type': kind, 'company_filter': company, 'query': query}
                                      for qid, kind, company, query in QUESTIONS])
    save(directory / 'reference_answers.json', {'prepared_at': datetime.now().astimezone().isoformat(),
         'method': '从下载的原始年报PDF提取指定页并人工核对；未使用被评测模型生成参照答案。',
         'grading': {'正确': '所要求的关键事实、单位、口径、计算和来源均成立，覆盖足够；Q8的正确拒答也算正确。',
                     '部分正确': '至少部分关键事实正确，但遗漏、覆盖不足或解释/口径/引用有问题。',
                     '错误': '关键事实错误、无有效完成、凭空补造，或对资料内问题仅拒答而无有效答案。'},
         'facts': FACTS, 'source_snapshots': snapshots})
    print(f'参考依据准备完成：{directory}', flush=True)


def run(directory):
    from answer_course import answer_question, PARAMETERS
    from search_course import CourseRetriever
    references = directory / 'reference_answers.json'
    questions_file = directory / 'questions.json'
    questions = json.loads(questions_file.read_text(encoding='utf-8'))
    if (directory / 'run_manifest.json').exists() or any((directory / f"Q{i}.json").exists() for i in range(1, 11)):
        raise RuntimeError('该目录已有正式运行记录，请使用新的目录，避免覆盖第一次回答。')
    ref_sha, question_sha = sha(references), sha(questions_file)
    retriever = CourseRetriever(ROOT / 'data/indexes/course_all')
    manifest = {'started_at': datetime.now().astimezone().isoformat(), 'status': 'running',
                'reference_sha256': ref_sha, 'questions_sha256': question_sha,
                'index_manifest': retriever.manifest, 'mode': 'hybrid', 'top_k': 6, 'candidate_k': 10,
                'rrf_constant': 60, 'report_year': 2025, 'answer_parameters': PARAMETERS,
                'method': '逐题一次正式生成；仅服务层临时网络错误按既有客户端重试。不给模型参照答案，不改写原回答。'}
    save(directory / 'run_manifest.json', manifest)
    rows = []
    for q in questions:
        if sha(references) != ref_sha or sha(questions_file) != question_sha:
            raise RuntimeError('评测过程中题目或参照答案发生变化。')
        print(f"开始 {q['id']}：{q['query']}", flush=True)
        record = answer_question(q['query'], retriever=retriever, mode='hybrid', company=q['company_filter'],
                                 top_k=6, report_year=2025, output=directory / f"{q['id']}.json")
        hits = record.get('retrieval', {}).get('hits', [])
        rows.append({'id': q['id'], 'query': q['query'], 'status': record['status'],
                     'recall_companies': sorted({hit['company'] for hit in hits}),
                     'record': f"{q['id']}.json", 'elapsed_seconds': record.get('elapsed_seconds'),
                     'usage': record.get('usage'), 'answer_model': record.get('answer_model')})
        save(directory / 'run_progress.json', rows)
        print(f"{q['id']} 已保存：{record['status']}，召回银行 {rows[-1]['recall_companies']}", flush=True)
    manifest.update(status='finished', finished_at=datetime.now().astimezone().isoformat(), results=rows)
    save(directory / 'run_manifest.json', manifest)
    print(f'正式10题运行完成：{directory}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run'])
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    (prepare if args.action == 'prepare' else run)(args.directory)
