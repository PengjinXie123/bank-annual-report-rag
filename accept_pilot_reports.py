"""Independent golden-value and provenance checks for the two-bank pilot.

Golden rows were transcribed from rendered original pages, not parser output.
This is sample acceptance, not certification of every table in 776 PDF pages.
"""
from __future__ import annotations
import csv
import hashlib
import html
import json
from datetime import datetime
from pathlib import Path
from process_pilot_reports import REPORTS, REPORT_DIR, DEFAULT_OUTPUT_DIR, write_jsonl

ROOT=DEFAULT_OUTPUT_DIR
GOLDEN=[
 ('601398',14,1,'营业收入',['838,270','821,803','843,070']),
 ('601398',14,1,'净利润',['370,766','366,946','365,116']),
 ('601398',14,1,'资产总额',['53,477,773','48,821,746','44,697,079']),
 ('601398',14,1,'业务及管理费',['235,173','230,460','227,266']),
 ('601398',14,1,'客户贷款及垫款总额',['30,506,114','28,372,229','26,086,482']),
 ('601398',16,1,'不良贷款率',['1.31','1.34','1.36']),
 ('601398',16,1,'拨备覆盖率',['213.60','214.91','213.97']),
 ('601398',16,1,'核心一级资本充足率',['13.57','14.10','13.72']),
 ('601398',16,1,'资本充足率',['18.76','19.39','19.10']),
 ('601398',205,1,'利息净收入',['30','635,126','637,405','610,775','608,131']),
 ('601398',205,1,'利息支出',['','(696,705)','(790,543)','(644,766)','(731,240)']),
 ('601398',205,1,'公允价值变动净收益/（损失）',['33','511','12,220','(3,970)','4,133']),
 ('601398',205,1,'业务及管理费',['37','(235,173)','(230,460)','(214,834)','(211,043)']),
 ('601398',205,1,'母公司股东',['','368,562','365,863','','']),
 ('601398',205,1,'净利润',['','370,766','366,946','353,754','346,707']),
 ('601939',14,1,'营业收入',['761,049','746,970','1.88','769,736','783,760','824,246']),
 ('601939',14,1,'业务及管理费',['(215,678)','(214,312)','0.64','(210,088)','(210,896)','(209,864)']),
 ('601939',14,1,'净利润',['339,790','336,282','1.04','332,460','324,863','303,928']),
 ('601939',14,1,'资产总额',['45,631,818','40,571,149','12.47','38,324,826','34,600,711','30,253,979']),
 ('601939',14,1,'基本和稀释每股收益',['1.30','1.31','(0.76)','1.31','1.28','1.19']),
 ('601939',15,1,'不良贷款率',['1.31','1.34','(0.03)','1.37','1.38','1.42']),
 ('601939',15,1,'拨备覆盖率',['233.15','233.60','(0.45)','239.85','241.53','239.96']),
 ('601939',15,1,'核心一级资本充足率',['14.63','14.48','0.15','13.15','13.69','13.59']),
 ('601939',15,1,'资本充足率',['19.69','19.69','–','17.95','18.42','17.85']),
 ('601939',15,2,'营业收入',['190,070','204,203','179,429','187,347','199,929','184,239','182,530','180,272']),
 ('601939',15,2,'扣除非经常性损益后归属于本行股东的净利润',['83,273','78,325','95,130','82,055','86,888','77,446','91,231','79,758']),
 ('601939',15,2,'经营活动产生/(所用)的现金流量净额',['721,067','535,547','560,766','283,862','209,335','81,470','664,064','(616,846)']),
 ('601939',187,1,'一、营业收入',['','761,049','746,970','707,467','696,543']),
 ('601939',187,1,'利息支出',['','(580,488)','(651,675)','(565,051)','(631,426)']),
 ('601939',187,1,'业务及管理费',['47','(215,678)','(214,312)','(205,002)','(202,724)']),
 ('601939',187,1,'五、净利润',['','339,790','336,282','324,911','322,901']),
 ('601939',187,1,'少数股东损益',['','884','705','–','–']),
]

def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]

def main():
    checks=[]
    def check(name,actual,expected):
        checks.append(dict(name=name,passed=actual==expected,actual=actual,expected=expected))
    sources={row['code']:row for row in csv.DictReader((REPORT_DIR/'sources.csv').open(encoding='utf-8-sig'))}
    summaries=[]
    all_tables={}
    loaded={}
    for spec in REPORTS:
        d=ROOT/f'{spec.code}_{spec.company}'
        pages=read_jsonl(d/'pages.jsonl'); tables=read_jsonl(d/'tables.jsonl'); chunks=read_jsonl(d/'chunks.jsonl')
        summary=json.loads((d/'summary.json').read_text(encoding='utf-8'))
        summaries.append(summary)
        loaded[spec.code]=(d,pages,tables,chunks)
        all_tables.update({t['table_id']:t for t in tables})
        check(f'{spec.company}:原PDF SHA256',hashlib.sha256((REPORT_DIR/spec.filename).read_bytes()).hexdigest(),sources[spec.code]['sha256'])
        expected=408 if spec.code=='601398' else 368
        check(f'{spec.company}:每页均有记录',[p['pdf_page'] for p in pages],list(range(1,expected+1)))
        check(f'{spec.company}:无重复chunk_id',len({c['chunk_id'] for c in chunks}),len(chunks))
        check(f'{spec.company}:替代字符数',sum(p['text'].count('\ufffd') for p in pages),0)
        check(f'{spec.company}:来源元数据',all(c['company']==spec.company and c['source_url']==spec.source_url and c['source_file']==spec.filename and c['section'] and 1<=c['pdf_page']<=expected for c in chunks),True)
        check(f'{spec.company}:未复核表格不得进入就绪索引',all(c['chunk_type']!='table' or all_tables[c['table_id']]['validation_status']=='sample_layout_checked' for c in read_jsonl(d/'index_ready_chunks.jsonl')),True)
        check(f'{spec.company}:切块长度上限',max(len(c['content']) for c in chunks)<=1050,True)
        for t in tables:
            if t.get('validation_status')!='sample_layout_checked': continue
            check(t['table_id']+':行列宽度',all(len(r['values'])==len(t['columns']) for r in t['rows']),True)
            check(t['table_id']+':括号完整',all(v.count('(')==v.count(')') for r in t['rows'] for v in r['values']),True)
            check(t['table_id']+':单位与原文保留',bool(t['unit'] and t['source_context']),True)
        for n,label in ([(14,13),(16,15),(193,1),(205,13),(408,216)] if spec.code=='601398' else [(4,None),(14,12),(15,13),(167,165),(187,185),(199,197),(366,364)]):
            check(f'{spec.company}:PDF{n}报告页码',pages[n-1]['printed_page'],label)
        for n in ([193,205,215,408] if spec.code=='601398' else [180,187,195,366]):
            check(f'{spec.company}:PDF{n}附件章节','审计报告及财务报告' in pages[n-1]['section'],True)
        if spec.code=='601939':
            prose=pages[166]['text']
            check('建设银行:双栏阅读顺序',prose.index('债券发行')<prose.index('股票挂钩协议'),True)
            check('建设银行:捐款句未穿插右栏','本集团2025年对外捐赠总额为1.36亿元。' in prose,True)

    for code,page,number,label,expected in GOLDEN:
        table=all_tables[f'{code}-p{page:04d}-verified-{number:02d}']
        rows=[r for r in table['rows'] if r['label']==label]
        check(f'{code} PDF{page} 表{number} {label}',rows[0]['values'] if len(rows)==1 else None,expected)
    # Same metric repeated in the financial summary and the consolidated P&L.
    for code,profit_page,profit_label in [('601398',205,'净利润'),('601939',187,'五、净利润')]:
        a=all_tables[f'{code}-p0014-verified-01']
        b=all_tables[f'{code}-p{profit_page:04d}-verified-01']
        av=next(r['values'][0] for r in a['rows'] if r['label']=='净利润')
        bv=next(r['values'][1] for r in b['rows'] if r['label']==profit_label)
        check(f'{code}:摘要与利润表集团净利润交叉核对',av,bv)
    failed=[c for c in checks if not c['passed']]
    # Release only the verified tables and the two visually checked prose pages.
    for code,(d,pages,tables,chunks) in loaded.items():
        for c in chunks:
            if c['chunk_type']=='text' and (code,c['pdf_page']) in {('601398',193),('601939',167)}:
                c['validation_status']='sample_prose_checked'
                c['index_eligible']=not failed
        write_jsonl(d/'chunks.jsonl',chunks)
        write_jsonl(d/'index_ready_chunks.jsonl',[c for c in chunks if c.get('index_eligible') and not failed])
    qa=ROOT/'qa'; qa.mkdir(exist_ok=True)
    result=dict(generated_at=datetime.now().astimezone().isoformat(),verdict='SAMPLE_PASS_FULL_CORPUS_NOT_ACCEPTED' if not failed else 'FAIL',checks=len(checks),passed=len(checks)-len(failed),failed=failed,golden_rows=len(GOLDEN),golden_cells=sum(len(row[-1]) for row in GOLDEN),checks_detail=checks)
    (qa/'acceptance_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 工商银行与建设银行：文档处理验收','',f"验收时间：{result['generated_at']}",'',
           '**结论：关键样本通过；全量语料暂不通过入库验收。**' if not failed else '**结论：存在检查失败，禁止进入索引。**','',
           f"已对两份原始 A 股年报共 776 页做结构检查，并视觉抽查8页。自动检查 {len(checks)} 项，通过 {len(checks)-len(failed)} 项；对照原 PDF 图片建立 {len(GOLDEN)} 行、{result['golden_cells']} 个单元格的固定核对值。该通过率只适用于列明的样本，不能推断全书准确率。",'',
           '## 处理结果','', '|银行|PDF页数|有文本页|已检查样本表|待复核候选表|就绪切块|','|---|---:|---:|---:|---:|---:|']
    for s in summaries:
        d=loaded[s['stock_code']][0]
        lines.append(f"|{s['company']}|{s['pdf_pages']}|{s['pages_with_text']}|{s['verified_sample_tables']}|{s['unreviewed_table_candidates']}|{len(read_jsonl(d/'index_ready_chunks.jsonl'))}|")
    lines += ['', '所有页的原始提取文本都保留在 pages.jsonl；候选表原始单元格保留在 tables.jsonl 的 raw_rows。未复核内容的 index_eligible=false，不进入 index_ready_chunks.jsonl。就绪切块仅供下一阶段试验，不能代表全量年报。','',
              '## 已修复并核对','',
              '- 关键样本采用 PDF 字符坐标恢复行列，保留括号负数、空白、破折号、附注编号、多级表头与跨行名称。',
              '- 建设银行第15页的年度指标和季度指标分别成表；每股数据与金额数据的单位保留。',
              '- PDF页码与报告页码分开存储。工商银行PDF第193页是附件报告第1页；PDF第205页是附件第13页。',
              '- 建设银行使用PDF书签定位章节；工商银行使用目录定位主章节，审计附件单独定位。目录中的数字不再被误当页脚。',
              '- 建设银行双栏正文按左栏后右栏提取；已视觉核对PDF第167页。检测到的其他双栏页仍待人工抽查。',
              '- 样本表每个切块重复公司、年份、单位、报告页码及明确列名，避免切块后丢失列含义。',
              '- 两份原PDF的SHA256与下载清单一致，原文件未修改。','',
              '## 视觉抽查范围','',
              '|银行|PDF页 / 报告页|内容|','|---|---|---|',
              '|工商银行|14 / 13、16 / 15、205 / 13|财务数据、风险与资本指标、合并及公司利润表|',
              '|工商银行|193 / 1|附件页码及审计正文|',
              '|建设银行|14 / 12、15 / 13、187 / 185|财务摘要、年度与季度指标、合并及银行利润表|',
              '|建设银行|167 / 165|双栏董事会报告正文|','',
              '## 尚未通过的项目与下一步','',
              '1. 其余复杂表格只有候选提取结果，尚未恢复和验证全部层级表头、跨页续表及空单元格。必须继续增加布局处理或采用更强的版面解析，并抽查后再放行；不能直接把候选表用于回答数值问题。',
              '2. 图表、图片中的信息没有完成OCR或视觉结构化；正文提取覆盖页数不等于信息完整覆盖。',
              '3. 全量正文的双栏、跨页段落和脚注尚未逐项验收；仅审计正文与双栏样本放入就绪切块。',
              '4. 缺失的印刷页码保持null，不猜测。PDF页码与source_file/source_url仍可定位原文。',
              '5. 两家银行的示例布局具有报告特异性，不能原样推广到其余8家。先补齐文档处理，再开展全量embedding、BM25和问答评测。可以用已通过的样本单独试验检索。','',
              '口径提醒：建设银行2025年报说明对仓储业务列报进行了调整并相应调整2024年相关数据；引用时应保留同页脚注。两行“贷款总额”和“贷款净额”不能当作同一指标直接比较。', '',
              '## 页码与版面待核查清单','']
    for s in summaries:
        lines += [f"- {s['company']}：预期有印刷页码但未可靠提取（或页码冲突）的PDF页：{s['missing_or_conflicting_printed_pages']}。",f"- {s['company']}：自动判定双栏的PDF页：{s['two_column_pages']}。"]
    lines += ['', '## 复现','',
              '使用已安装 pdfplumber、pypdf、pypdfium2 的 Python，依次运行：','',
              '```text','python process_pilot_reports.py','python inspect_pilot.py','python accept_pilot_reports.py','```','',
              'acceptance_results.json 保存每项实际值、预期值及判断。golden行只用于验收，不用于生成或填充解析结果。','']
    if failed: lines += ['## 检查失败','',json.dumps(failed,ensure_ascii=False,indent=2)]
    (qa/'验收报告.md').write_text('\n'.join(lines),encoding='utf-8')
    # Standalone review page, including source images and the full accepted rows.
    body=['<h1>两家银行文档处理验收</h1><p>关键样本通过；全量语料尚待复核。详细范围见验收报告。</p>']
    for t in all_tables.values():
        if t.get('validation_status')!='sample_layout_checked':continue
        body.append(f"<h2>{html.escape(t['company']+' · '+t['title'])}</h2><p>PDF {t['pdf_page']} / 报告 {t['printed_page']}；{html.escape(t['unit'])}</p>")
        body.append('<div class="pair"><img src="images/'+t['stock_code']+'_'+str(t['pdf_page'])+'.png"><div><table><tr><th>项目 / 分组</th>'+''.join('<th>'+html.escape(c)+'</th>' for c in t['columns'])+'</tr>')
        for row in t['rows']:
            body.append('<tr><td>'+html.escape(row['group']+'／'+row['label'])+'</td>'+''.join('<td>'+html.escape(v if v else '〔空白〕')+'</td>' for v in row['values'])+'</tr>')
        body.append('</table><details><summary>同页原文与脚注</summary><pre>'+html.escape(t['source_context'])+'</pre></details></div></div>')
    (qa/'原文对照.html').write_text('<!doctype html><meta charset="utf-8"><title>文档验收原文对照</title><style>body{font:15px system-ui;margin:28px;color:#123}h2{margin-top:48px}.pair{display:grid;grid-template-columns:minmax(400px,1fr) minmax(600px,1.3fr);gap:20px;align-items:start}img{width:100%}table{border-collapse:collapse;font-size:12px}td,th{border:1px solid #cbd5e1;padding:7px}th{background:#e0f2fe}pre{white-space:pre-wrap}</style>'+''.join(body),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='checks_detail'},ensure_ascii=False,indent=2))
    if failed: raise SystemExit(1)

if __name__=='__main__': main()
