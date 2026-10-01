"""Check the running local server without invoking any paid model call."""
import json
import math
import unittest
import urllib.error
import urllib.request

from course_config import setting

BASE = 'http://127.0.0.1:8765'


def request(path, payload=None, headers=None):
    body = json.dumps(payload).encode() if payload is not None else None
    options = {'Content-Type': 'application/json'} if body else {}
    options.update(headers or {})
    req = urllib.request.Request(BASE + path, data=body, headers=options)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


class WebChecks(unittest.TestCase):
    def test_status_and_history_do_not_expose_keys(self):
        for path in ('/api/status', '/api/history'):
            status, _, body = request(path)
            self.assertEqual(status, 200)
            for name in ('DEEPSEEK_API_KEY', 'DASHSCOPE_API_KEY'):
                key = setting(name)
                if key:
                    self.assertNotIn(key.encode(), body)

    def test_private_files_and_path_traversal_are_not_served(self):
        for path in ('/.env', '/course_config.py', '/data/indexes/course_pilot/vectors.npy',
                     '/api/record?id=../../../../.env', '/%2e%2e/.env'):
            self.assertEqual(request(path)[0], 404)

    def test_invalid_questions_are_rejected_without_model_calls(self):
        for payload in ({'query': ''}, {'query': '问题', 'company': '未知银行'},
                        {'query': '问题', 'mode': 'unknown'}, {'query': '问题', 'top_k': 500}):
            self.assertEqual(request('/api/jobs', payload)[0], 400)

    def test_other_origins_are_rejected(self):
        self.assertEqual(request('/api/jobs', {'query': '问题'},
                                 {'Origin': 'https://example.com'})[0], 403)

    def test_record_export_contains_original_evidence(self):
        status, _, body = request('/api/export?id=formal%2F20261001-10questions%2FQ1.json')
        row = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(row['answer'])
        self.assertEqual(len(row['evidence']), 6)
        self.assertTrue(row['citation_check']['passed'])

    def test_formal_evaluation_history_ignores_reference_arrays(self):
        from retrieval_core import ROOT
        directory = ROOT / 'data/processed/course_pilot/answers/formal/20261001-10questions'
        if not directory.is_dir():
            self.skipTest('Formal evaluation has not been run yet.')
        status, _, body = request('/api/history')
        self.assertEqual(status, 200)
        ids = {row['record_id'] for row in json.loads(body)['records']}
        self.assertFalse(any('questions.json' in item or 'assessments.json' in item for item in ids))
        # History is a bounded recent list; later questions can displace formal records.
        for number in range(1, 11):
            record_id = f'formal%2F20261001-10questions%2FQ{number}.json'
            self.assertEqual(request('/api/record?id=' + record_id)[0], 200)

    def test_report_range_support(self):
        status, headers, body = request('/reports/601939.pdf', headers={'Range': 'bytes=0-15'})
        self.assertEqual(status, 206)
        self.assertEqual(len(body), 16)
        self.assertTrue(body.startswith(b'%PDF-'))
        self.assertTrue(headers['Content-Range'].startswith('bytes 0-15/'))

    def test_sky_points_match_current_corpus(self):
        from retrieval_core import ROOT, active_index, read_chunks
        chunks = read_chunks(active_index() / 'chunks.jsonl')
        status, _, body = request('/api/sky')
        sky = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(sky['count'], len(chunks))
        self.assertEqual([p['id'] for p in sky['points']], [p['chunk_id'] for p in chunks])
        for point, chunk in zip(sky['points'], chunks):
            self.assertEqual(sky['companies'][point['bank']], chunk['company'])
            self.assertEqual(point['page'], chunk['pdf_page_start'])
            self.assertTrue(all(math.isfinite(point[key]) and abs(point[key]) <= 1 for key in ('x', 'y')))
        self.assertGreater(sum(sky['explained_variance_ratio']), 0)
        self.assertLess(sum(sky['explained_variance_ratio']), 1)

    def test_star_selection_returns_original_chunk(self):
        from retrieval_core import active_index, read_chunks
        for chunk in (read_chunks(active_index() / 'chunks.jsonl')[0],
                      read_chunks(active_index() / 'chunks.jsonl')[-1]):
            status, _, body = request('/api/chunk?id=' + chunk['chunk_id'])
            row = json.loads(body)
            self.assertEqual(status, 200)
            self.assertEqual(row['content'], chunk['content'])
            self.assertEqual(row['source_url'], chunk['source_url'])
            self.assertNotIn('embedding_text', row)
        self.assertEqual(request('/api/chunk?id=../../.env')[0], 404)


if __name__ == '__main__':
    unittest.main()
