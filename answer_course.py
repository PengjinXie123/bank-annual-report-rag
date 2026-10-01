"""Retrieve actual evidence and answer with DeepSeek; persist auditable citations."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

from course_config import answer_config, credential_source, redact
from retrieval_core import ROOT, digest, write_json
from search_course import CourseRetriever
from bank_catalog import BANK_NAMES

PROMPT_PATH = ROOT / 'prompts/answer_system.txt'
PARAMETERS = {'thinking': {'type': 'disabled'}, 'temperature': 0.1,
              'max_tokens': 1800, 'response_format': {'type': 'json_object'}, 'stream': False}


def call_deepseek(messages, config):
    payload = {'model': config['model'], 'messages': messages, **PARAMETERS}
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    for attempt in range(3):
        request = urllib.request.Request(config['base_url'] + '/chat/completions', data=body,
            headers={'Authorization': 'Bearer ' + config['api_key'], 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                result = json.load(response)
            if not result.get('choices'):
                raise RuntimeError('DeepSeek 未返回回答选项。')
            return redact(result), attempt + 1
        except urllib.error.HTTPError as error:
            if error.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                hints = {401: '请检查 .env 的 DEEPSEEK_API_KEY', 402: '请检查API账户余额',
                         400: '请检查模型名称或请求参数', 404: '请检查服务地址和模型名称'}
                raise RuntimeError(f'DeepSeek HTTP {error.code}；{hints.get(error.code, "请求失败")}。') from None
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise RuntimeError('DeepSeek 网络请求失败，已尝试3次。') from None
        except json.JSONDecodeError:
            raise RuntimeError('DeepSeek 返回内容不是有效JSON。') from None
        time.sleep(2 ** (attempt + 1))
    raise RuntimeError('DeepSeek 请求未完成。')


def make_evidence(hits):
    return [{'evidence_id': f'E{i}', **hit} for i, hit in enumerate(hits, 1)]


def messages_for(query, evidence):
    system = PROMPT_PATH.read_text(encoding='utf-8')
    blocks = []
    for row in evidence:
        blocks.append(json.dumps({key: row.get(key) for key in
            ('evidence_id', 'chunk_id', 'company', 'report_year', 'report_type', 'section',
             'pdf_page_start', 'pdf_page_end', 'printed_page', 'table_title', 'unit', 'scope',
             'quality_status', 'content')}, ensure_ascii=False))
    user = '问题：' + query + '\n\n以下每行JSON是一块实际召回证据，按编号引用：\n' + '\n'.join(blocks)
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]


def check_citations(answer, evidence, insufficient=False):
    available = {row['evidence_id']: row for row in evidence}
    mentioned = list(dict.fromkeys(re.findall(r'\[(E\d+)\]', answer)))
    unknown = [item for item in mentioned if item not in available]
    malformed = [token for token in re.findall(r'\[E[^\]]*\]', answer)
                 if not re.fullmatch(r'\[E\d+\]', token)]
    missing = not mentioned and not insufficient
    return {'passed': not (unknown or malformed or missing), 'referenced_ids': mentioned,
            'unknown_ids': unknown, 'malformed_citations': malformed,
            'missing_citations_for_answer': missing,
            'scope': '引用编号存在性与格式检查；不证明结论与证据语义一致或数值正确。'}


def resolved_sources(evidence, mentioned):
    return [{'evidence_id': row['evidence_id'], 'chunk_id': row['chunk_id'],
             'company': row['company'], 'section': row['section'],
             'pdf_page_start': row['pdf_page_start'], 'pdf_page_end': row['pdf_page_end'],
             'printed_page': row['printed_page'], 'quality_status': row['quality_status'],
             'source_file': row['source_file'], 'source_url': row['source_url'],
             'source_page_url': row['source_url'].split('#')[0] + f"#page={row['pdf_page_start']}"}
            for row in evidence if row['evidence_id'] in mentioned]


def answer_question(query, retriever=None, mode='hybrid', company=None, top_k=6, report_year=2025,
                    output=None):
    started = time.perf_counter()
    result = {'status': 'running', 'created_at': datetime.now().astimezone().isoformat(),
              'query': query, 'answer': None, 'insufficient_evidence': None}
    try:
        config = answer_config()
        result['answer_model'] = {'requested_model': config['model'], 'base_url': config['base_url'],
                                 'credential_source': credential_source('DEEPSEEK_API_KEY')}
        retriever = retriever or CourseRetriever()
        retrieval = retriever.search(query, mode=mode, top_k=top_k, company=company, report_year=report_year)
        evidence = make_evidence(retrieval['hits'])
        result.update(retrieval=retrieval, evidence=evidence,
                      embedding_credential_source=credential_source('DASHSCOPE_API_KEY'))
        if not evidence:
            result.update(status='no_evidence', answer='没有检索到可用证据，无法回答。', insufficient_evidence=True)
        else:
            messages = messages_for(query, evidence)
            result.update(prompt_sha256=digest(messages), messages=messages, request_parameters=PARAMETERS)
            # Save the exact retrieval and prompt before the network request.
            if output:
                write_json(output, redact(result))
            model_started = time.perf_counter()
            raw, attempts = call_deepseek(messages, config)
            result.update(raw_response=raw, model_elapsed_seconds=round(time.perf_counter() - model_started, 3),
                          request_attempts=attempts)
            choice = raw['choices'][0]
            content = choice.get('message', {}).get('content')
            result.update(raw_answer_content=content, finish_reason=choice.get('finish_reason'))
            result['answer_model']['returned_model'] = raw.get('model')
            result['usage'] = raw.get('usage', {})
            try:
                parsed = json.loads(content) if isinstance(content, str) else None
            except json.JSONDecodeError:
                parsed = None
            if (not isinstance(parsed, dict) or not isinstance(parsed.get('answer'), str)
                    or not parsed['answer'].strip() or type(parsed.get('insufficient_evidence')) is not bool):
                result.update(status='invalid_model_output', error='回答JSON格式或字段不完整；原始输出已保留。')
            else:
                result.update(answer=parsed['answer'], insufficient_evidence=parsed['insufficient_evidence'])
                check = check_citations(result['answer'], evidence, result['insufficient_evidence'])
                result.update(citation_check=check, sources=resolved_sources(evidence, check['referenced_ids']),
                              status='completed' if check['passed'] else 'citation_error')
                if result['finish_reason'] != 'stop':
                    result.update(status='incomplete_model_output', error='模型未正常完成，原始输出已保留。')
    except RuntimeError as error:
        result.update(status='runtime_error', error=redact(str(error)))
    result['elapsed_seconds'] = round(time.perf_counter() - started, 3)
    result = redact(result)
    if output:
        write_json(output, result)
        Path(output).with_suffix('.md').write_text(render_markdown(result), encoding='utf-8')
    return result


def render_markdown(result):
    model = result.get('answer_model', {})
    lines = ['# 银行年报问答记录', '', f"问题：{result['query']}", '',
             f"状态：{result['status']}；耗时：{result.get('elapsed_seconds', '运行中')}秒", '',
             f"请求模型：{model.get('requested_model', '未配置')}；返回模型字段：{model.get('returned_model', '未返回')}", '',
             '## 模型原始回答', '', result.get('answer') or result.get('raw_answer_content') or '未生成回答。', '',
             '## 程序解析的引用来源', '']
    for source in result.get('sources', []):
        lines.append(f"- [{source['evidence_id']}] {source['company']}；{source['section']}；"
                     f"PDF第{source['pdf_page_start']}页；块ID：{source['chunk_id']}；"
                     f"[报告来源]({source['source_page_url']})")
    lines += ['', '## 引用检查', '', json.dumps(result.get('citation_check', {}), ensure_ascii=False), '']
    if result.get('error'):
        lines += ['错误：' + result['error'], '']
    lines += ['## 本轮实际召回原文', '']
    for row in result.get('evidence', []):
        lines += [f"### [{row['evidence_id']}] {row['company']} · PDF第{row['pdf_page_start']}页", '',
                  f"章节：{row['section']}；块ID：{row['chunk_id']}；质量：{row['quality_status']}", '',
                  '```text', row['content'], '```', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', required=True)
    parser.add_argument('--company', choices=BANK_NAMES)
    parser.add_argument('--mode', choices=['vector', 'bm25', 'hybrid'], default='hybrid')
    parser.add_argument('--top-k', type=int, default=6)
    parser.add_argument('--year', type=int, default=2025)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / 'data/processed/course_pilot/answers' / (
        datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + digest(args.query)[:8] + '.json')
    result = answer_question(args.query, mode=args.mode, company=args.company, top_k=args.top_k,
                             report_year=args.year, output=output)
    print(result.get('answer') or result.get('error') or result['status'])
    for source in result.get('sources', []):
        print(f"[{source['evidence_id']}] {source['company']}；{source['section']}；"
              f"PDF第{source['pdf_page_start']}页；{source['chunk_id']}")
    print(f"\n状态：{result['status']}；记录：{output}")
    raise SystemExit(0 if result['status'] == 'completed' else 1)
