"""Local-only course QA page; credentials and model calls remain server-side."""
from __future__ import annotations

import argparse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
from urllib.parse import parse_qs, unquote, urlsplit
import uuid

from answer_course import answer_question
from course_config import answer_config, redact, setting
from retrieval_core import ROOT, read_json
from search_course import CourseRetriever
from sky_projection import sky_map

WEB = ROOT / 'web'
ANSWERS = ROOT / 'data/processed/course_pilot/answers'
REPORTS = ROOT / 'data/annual_reports/2025'
PUBLIC_FIELDS = ('status', 'created_at', 'query', 'answer', 'insufficient_evidence', 'error',
                 'elapsed_seconds', 'answer_model', 'retrieval', 'evidence', 'sources',
                 'citation_check', 'usage', 'finish_reason')


def public_result(result, record_id):
    return redact({key: result[key] for key in PUBLIC_FIELDS if key in result} | {'record_id': record_id})


class Application:
    def __init__(self, index=None):
        self.retriever = CourseRetriever(index)
        self.sky = sky_map(self.retriever)
        self.chunks_by_id = {row['chunk_id']: row for row in self.retriever.chunks}
        self.jobs = {}
        self.lock = threading.Lock()
        self.reports = {}
        for row in self.retriever.chunks:
            path = REPORTS / Path(row['source_file']).name
            if path.is_file():
                self.reports[row['stock_code']] = path

    def status(self):
        try:
            config = answer_config()
            model = config['model']
            answer_ready = True
        except RuntimeError:
            model = '未配置'
            answer_ready = False
        return {'ready': True, 'answer_ready': answer_ready,
                'embedding_ready': bool(setting('DASHSCOPE_API_KEY')),
                'chunk_count': len(self.retriever.chunks), 'companies': self.retriever.manifest['companies'],
                'report_year': 2025, 'answer_model': model, 'report_codes': sorted(self.reports)}

    def record_path(self, record_id):
        path = (ANSWERS / record_id).resolve()
        if not path.is_relative_to(ANSWERS.resolve()) or path.suffix != '.json' or not path.is_file():
            raise FileNotFoundError
        return path

    def history(self):
        records = []
        for path in sorted(ANSWERS.rglob('*.json'), key=lambda path: path.stat().st_mtime, reverse=True):
            try:
                row = read_json(path)
            except (ValueError, OSError):
                continue
            if not isinstance(row, dict):
                continue
            if row.get('query') and row.get('status') and row.get('created_at'):
                records.append({'record_id': path.relative_to(ANSWERS).as_posix(), 'query': row['query'],
                                'created_at': row['created_at'], 'status': row['status']})
            if len(records) == 20:
                break
        return redact(records)

    def start(self, payload):
        query = payload.get('query', '')
        mode = payload.get('mode', 'hybrid')
        company = payload.get('company') or None
        top_k = payload.get('top_k', 6)
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise ValueError('请填写问题，长度不超过2000字。')
        if mode not in ('hybrid', 'vector', 'bm25') or company not in (None, *self.retriever.manifest['companies']):
            raise ValueError('检索模式或银行筛选不正确。')
        if type(top_k) is not int or not 3 <= top_k <= 12:
            raise ValueError('召回数量须为3至12之间的整数。')
        answer_config()
        with self.lock:
            if any(job['status'] == 'running' for job in self.jobs.values()):
                raise BlockingIOError('已有问题正在处理，请等待完成。')
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = {'status': 'running', 'message': '正在检索原文并等待模型回答。'}
        record_id = 'web/' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + job_id[:8] + '.json'

        def work():
            try:
                result = answer_question(query.strip(), retriever=self.retriever, mode=mode, company=company,
                                         top_k=top_k, output=ANSWERS / record_id)
                job = {'status': 'done', 'result': public_result(result, record_id)}
            except Exception as error:
                # Never expose exception objects, request headers, or configuration values.
                job = {'status': 'failed', 'message': f'服务运行失败（{type(error).__name__}），请检查本地运行环境。'}
            with self.lock:
                self.jobs[job_id] = job
                completed = [key for key, value in self.jobs.items() if value['status'] != 'running']
                for key in completed[:-50]:
                    self.jobs.pop(key, None)

        threading.Thread(target=work, daemon=True).start()
        return job_id


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_bytes(self, data, mime, status=200, headers=None):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def json(self, value, status=200):
        self.send_bytes(json.dumps(redact(value), ensure_ascii=False).encode('utf-8'),
                        'application/json; charset=utf-8', status)

    def trusted_host(self):
        port = self.server.server_port
        return self.headers.get('Host') in (f'127.0.0.1:{port}', f'localhost:{port}')

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.trusted_host():
            return self.json({'error': '仅接受本机地址。'}, 403)
        url = urlsplit(self.path)
        route = unquote(url.path)
        app = self.server.app
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/sky.js': ('sky.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if route in assets:
            filename, mime = assets[route]
            return self.send_bytes((WEB / filename).read_bytes(), mime)
        if route == '/api/status':
            return self.json(app.status())
        if route == '/api/sky':
            return self.json(app.sky)
        if route == '/api/chunk':
            chunk_id = parse_qs(url.query).get('id', [''])[0]
            row = app.chunks_by_id.get(chunk_id)
            if row is None:
                return self.json({'error': '原文块不存在。'}, 404)
            fields = ('chunk_id', 'company', 'stock_code', 'section', 'pdf_page_start',
                      'source_url', 'content', 'quality_status', 'chunk_type', 'report_year')
            return self.json({key: row.get(key) for key in fields})
        if route == '/api/history':
            return self.json({'records': app.history()})
        if route in ('/api/record', '/api/export'):
            record_id = parse_qs(url.query).get('id', [''])[0]
            try:
                result = read_json(app.record_path(record_id))
                if not result.get('query'):
                    raise FileNotFoundError
            except (FileNotFoundError, ValueError):
                return self.json({'error': '问答记录不存在。'}, 404)
            if route == '/api/export':
                return self.send_bytes(json.dumps(redact(result), ensure_ascii=False, indent=2).encode('utf-8'),
                                       'application/json; charset=utf-8', headers={
                                           'Content-Disposition': 'attachment; filename="answer-record.json"'})
            return self.json(public_result(result, record_id))
        if re.fullmatch(r'/api/jobs/[0-9a-f]{32}', route):
            with app.lock:
                job = app.jobs.get(route.rsplit('/', 1)[-1])
            return self.json(job) if job else self.json({'error': '任务不存在。'}, 404)
        report = re.fullmatch(r'/reports/(\d{6})\.pdf', route)
        if report and report[1] in app.reports:
            return self.send_report(app.reports[report[1]], report[1])
        return self.json({'error': '页面或文件不存在。'}, 404)

    def send_report(self, path, code):
        size = path.stat().st_size
        start, end, partial = 0, size - 1, False
        requested = self.headers.get('Range')
        if requested:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
            if not match or not any(match.groups()):
                return self.send_bytes(b'', 'application/pdf', 416, {'Content-Range': f'bytes */{size}'})
            if match[1]:
                start, end = int(match[1]), min(int(match[2]), size - 1) if match[2] else size - 1
            else:
                start = max(0, size - int(match[2]))
            if start >= size or start > end:
                return self.send_bytes(b'', 'application/pdf', 416, {'Content-Range': f'bytes */{size}'})
            partial = True
        headers = {'Accept-Ranges': 'bytes', 'Content-Disposition': f'inline; filename="{code}.pdf"'}
        if partial:
            headers['Content-Range'] = f'bytes {start}-{end}/{size}'
        with path.open('rb') as handle:
            handle.seek(start)
            data = handle.read(end - start + 1)
        self.send_bytes(data, 'application/pdf', 206 if partial else 200, headers)

    def do_POST(self):
        if not self.trusted_host():
            return self.json({'error': '仅接受本机地址。'}, 403)
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',
                                      f'http://localhost:{self.server.server_port}'):
            return self.json({'error': '请求来源不正确。'}, 403)
        if self.path != '/api/jobs':
            return self.json({'error': '接口不存在。'}, 404)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 12000 or self.headers.get_content_type() != 'application/json':
                raise ValueError('请求须为有效JSON。')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('请求须为JSON对象。')
            job_id = self.server.app.start(payload)
        except (ValueError, UnicodeDecodeError):
            return self.json({'error': '问题、检索模式、银行或召回数量不正确。'}, 400)
        except BlockingIOError:
            return self.json({'error': '已有问题正在处理，请等待完成。'}, 409)
        except RuntimeError as error:
            return self.json({'error': redact(str(error))}, 503)
        return self.json({'job_id': job_id}, 202)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--index', type=Path, help='默认优先使用已完成的10家索引。')
    args = parser.parse_args()
    app = Application(args.index)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.app = app
    print(f'银行年报问答页面：http://127.0.0.1:{args.port}\n按 Ctrl+C 停止服务。', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
