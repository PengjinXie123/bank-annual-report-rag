"""Source-specific layouts verified against rendered 2025 A-share reports.

Coordinates are PDF points, not answer values. Numerical values are always
extracted from the source PDF. Layouts are deliberately limited to QA samples.
"""
from __future__ import annotations
import re
from bisect import bisect_right

YEARS = ['2025', '2024', '2023']
ANNUAL = ['2025', '2024', '变化(%)', '2023', '2022', '2021']
PROFIT = ['附注', '本集团·2025', '本集团·2024', '本行·2025', '本行·2024']

LAYOUTS = {
    ('601398', 14): [dict(title='财务概要—财务数据', y=(480, 757), edges=[82,320,390,457,515], columns=YEARS, unit='人民币百万元', groups=['全年经营成果', '于报告期末'])],
    ('601398', 16): [dict(title='财务概要—财务指标', y=(134,443), edges=[87,320,385,452,510], columns=YEARS, unit='%', groups=['盈利能力指标', '资产质量指标', '资本充足率指标'])],
    ('601398', 205): [dict(title='合并及公司利润表', y=(188,638), edges=[89,215,246,309,376,443,510], columns=PROFIT, unit='人民币百万元', groups=['净利润归属于'])],
    ('601939', 14): [dict(title='财务摘要', y=(148,621), edges=[42,252,298,343,388,434,479,526], columns=ANNUAL, unit='人民币百万元（每股数据为人民币元；变化列为%）', groups=['全年业绩', '于12月31日', '每股计'])],
    ('601939', 15): [
        dict(title='财务摘要—财务指标', y=(132,388), edges=[70,280,327,373,418,464,509,554], columns=['2025','2024','变化(+/-)','2023','2022','2021'], unit='%（变化列为百分点）', groups=['盈利能力指标','资本充足指标','资产质量指标']),
        dict(title='财务摘要—季度指标', y=(559,622), edges=[70,213,255,298,340,383,425,468,510,554], columns=[f'{y}·第{q}季度' for y in [2025,2024] for q in ['一','二','三','四']], unit='人民币百万元', groups=[]),
    ],
    ('601939', 187): [dict(title='合并及银行利润表', y=(142,528), edges=[70,258,295,357,423,488,554], columns=PROFIT, unit='人民币百万元', groups=[])],
}

NUMBER = re.compile(r'^(?:[+-]?\d[\d,]*(?:\.\d+)?%?|\(\d[\d,]*(?:\.\d+)?\)|[-–—])$')

def extract_layout(page, layout):
    x0, x1 = layout['edges'][0], layout['edges'][-1]
    top, bottom = layout['y']
    chars = [c for c in page.chars if x0 <= (c['x0']+c['x1'])/2 <= x1 and top <= (c['top']+c['bottom'])/2 <= bottom]
    # Exclude small superscript footnote markers from labels; complete footnotes
    # remain available in the page text and table source_context.
    sizes = sorted(c['size'] for c in chars if c['text'].strip())
    median = sizes[len(sizes)//2]
    chars = [c for c in chars if c['size'] >= median * .8]
    lines = []
    for c in sorted(chars, key=lambda c: (c['top'],c['x0'])):
        cy = (c['top']+c['bottom'])/2
        if not lines or abs(cy-lines[-1][0]) > 3:
            lines.append([cy, []])
        lines[-1][1].append(c)
    rows, pending, current_group, errors = [], [], '', []
    for cy, line in lines:
        cells = [''] * (len(layout['edges'])-1)
        for c in sorted(line,key=lambda c:c['x0']):
            col = bisect_right(layout['edges'], (c['x0']+c['x1'])/2)-1
            if 0 <= col < len(cells): cells[col] += c['text']
        cells = [re.sub(r'\s+','',x) for x in cells]
        label, values = cells[0], cells[1:]
        if any(label.startswith(g) for g in layout['groups']):
            current_group=label
            pending=[]
            continue
        # An isolated note number belongs to the wrapped label on the next line.
        data_values = values[1:] if layout['columns'][0]=='附注' else values
        if not any(data_values):
            if label: pending.append(label)
            if values and values[0]: pending.append('@note:'+values[0])
            continue
        note = next((x[6:] for x in pending if x.startswith('@note:')), '')
        label = ''.join(x for x in pending if not x.startswith('@note:')) + label
        pending=[]
        if note and layout['columns'][0]=='附注' and not values[0]: values[0]=note
        if not label or any(v and not NUMBER.fullmatch(v) for v in values):
            errors.append({'y':round(cy,2),'cells':[label]+values})
        rows.append({'label':label,'group':current_group,'values':values,'y':round(cy,2)})
    if pending: errors.append({'unattached_label':pending})
    return rows, errors

def extract_reading_order(page, code):
    raw = page.extract_text(x_tolerance=3,y_tolerance=3) or ''
    if code != '601939': return raw, 'single_or_unclassified'
    # A genuine 2-column page has many long text lines on both sides and a
    # clear gutter. Numeric tables do not meet the long-Chinese-line condition.
    mid=page.width/2+15
    body=page.crop((0,95,page.width,page.height-65))
    words=body.extract_words(x_tolerance=2,y_tolerance=3)
    left=[w for w in words if w['x1'] < mid-8 and len(re.findall(r'[\u4e00-\u9fff]',w['text']))>=10]
    right=[w for w in words if w['x0'] > mid+5 and len(re.findall(r'[\u4e00-\u9fff]',w['text']))>=10]
    crossing=[c for c in body.chars if c['x0'] < mid+4 and c['x1'] > mid-4 and c['text'].strip()]
    if len(left)>=6 and len(right)>=6 and len(crossing)<=2:
        parts=[page.crop((0,0,page.width,95)),page.crop((0,95,mid,page.height-65)),page.crop((mid,95,page.width,page.height-65)),page.crop((0,page.height-65,page.width,page.height))]
        return '\n\n'.join(p.extract_text(x_tolerance=3,y_tolerance=3) or '' for p in parts), 'two_columns'
    return raw,'single_or_unclassified'
