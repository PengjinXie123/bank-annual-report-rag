"""Search the course corpus with vector, BM25, or reciprocal-rank fusion."""
from __future__ import annotations

import argparse
from pathlib import Path
import time

from retrieval_core import (ROOT, ENDPOINT, TOKENIZER_VERSION, np, read_json, write_json,
                            read_chunks, sha256_file, digest, embed, save_array,
                            restore_bm25, tokenize, active_index)
from bank_catalog import BANK_NAMES


class CourseRetriever:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else active_index()
        self.manifest = read_json(self.directory / 'manifest.json')
        if self.manifest['status'] != 'ready':
            raise RuntimeError('索引尚未完成，请先运行 build_course_indexes.py。')
        if self.manifest['tokenizer'] != TOKENIZER_VERSION:
            raise RuntimeError('分词器版本不一致，请重新建索引。')
        for filename, hash_key in [('chunks.jsonl', 'corpus_sha256'), ('vectors.npy', 'vectors_sha256'),
                                   ('bm25.json', 'bm25_sha256')]:
            if sha256_file(self.directory / filename) != self.manifest[hash_key]:
                raise RuntimeError(f'{filename} 与索引清单不一致。')
        self.chunks = read_chunks(self.directory / 'chunks.jsonl')
        self.vectors = np.load(self.directory / 'vectors.npy', allow_pickle=False)
        self.bm25 = restore_bm25(read_json(self.directory / 'bm25.json'))
        if (len(self.chunks) != self.manifest['chunk_count']
                or self.bm25.corpus_size != len(self.chunks)
                or list(self.vectors.shape) != self.manifest['vector_shape']):
            raise RuntimeError('语料、向量与 BM25 行数不一致。')

    def query_vector(self, query):
        signature = digest({'query': query, 'model': self.manifest['model'],
                            'dimension': self.manifest['dimension'], 'text_type': 'query',
                            'endpoint': self.manifest.get('endpoint', ENDPOINT)})
        cache = self.directory / 'query_cache'
        cache.mkdir(exist_ok=True)
        path = cache / (signature + '.npy')
        if path.is_file():
            vector = np.load(path, allow_pickle=False)
            if (vector.shape == (1, self.manifest['dimension']) and np.isfinite(vector).all()
                    and np.allclose(np.linalg.norm(vector), 1, atol=1e-5)):
                return vector[0], 0, True
        values, tokens = embed([query], 'query', model=self.manifest['model'],
                               dimension=self.manifest['dimension'],
                               endpoint=self.manifest.get('endpoint', ENDPOINT))
        save_array(path, values)
        return values[0], tokens, False

    def search(self, query, mode='hybrid', top_k=6, company=None, report_year=None, candidate_k=10):
        if mode not in ('vector', 'bm25', 'hybrid') or top_k < 1 or candidate_k < 1:
            raise ValueError('检索参数不合法。')
        started = time.perf_counter()
        indices = [i for i, chunk in enumerate(self.chunks)
                   if (company is None or chunk['company'] == company)
                   and (report_year is None or int(chunk['report_year']) == report_year)]
        if not indices:
            raise ValueError('筛选条件下没有切块。')
        scores, ranks = {}, {}
        usage = {'query_tokens_new': 0, 'query_vector_cached': False}
        count = max(top_k, candidate_k)
        if mode in ('vector', 'hybrid'):
            query_embedding, tokens, cached = self.query_vector(query)
            usage.update(query_tokens_new=tokens, query_vector_cached=cached)
            values = self.vectors @ query_embedding
            scores['vector'] = values
            selected = sorted(indices, key=lambda i: (-float(values[i]), i))[:count]
            ranks['vector'] = {i: rank for rank, i in enumerate(selected, 1)}
        if mode in ('bm25', 'hybrid'):
            values = self.bm25.get_scores(tokenize(query))
            scores['bm25'] = values
            selected = sorted((i for i in indices if values[i] > 0),
                              key=lambda i: (-float(values[i]), i))[:count]
            ranks['bm25'] = {i: rank for rank, i in enumerate(selected, 1)}
        fused = {i: sum(1 / (60 + ranking[i]) for ranking in ranks.values() if i in ranking)
                 for ranking in ranks.values() for i in ranking}
        if mode == 'hybrid':
            selected = sorted(fused, key=lambda i: (-fused[i], i))[:top_k]
        else:
            selected = list(ranks[mode])[:top_k]
        hits = []
        for i in selected:
            hits.append({**self.chunks[i], 'rank': len(hits) + 1,
                         'retrieval_scores': {name: float(values[i]) for name, values in scores.items()},
                         'channel_ranks': {name: ranking[i] for name, ranking in ranks.items() if i in ranking},
                         'rrf_score': fused[i] if mode == 'hybrid' else None})
        return {'query': query, 'mode': mode, 'top_k': top_k, 'candidate_k': count,
                'filters': {'company': company, 'report_year': report_year},
                'corpus_sha256': self.manifest['corpus_sha256'], 'model': self.manifest['model'],
                'elapsed_seconds': round(time.perf_counter() - started, 3), **usage, 'hits': hits}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', required=True)
    parser.add_argument('--mode', choices=['vector', 'bm25', 'hybrid'], default='hybrid')
    parser.add_argument('--company', choices=BANK_NAMES)
    parser.add_argument('--year', type=int)
    parser.add_argument('--top-k', type=int, default=6)
    parser.add_argument('--index', type=Path, help='默认优先使用已完成的10家索引，否则使用试点索引。')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = CourseRetriever(args.index).search(args.query, args.mode, args.top_k, args.company, args.year)
    if args.output:
        write_json(args.output, result)
    for hit in result['hits']:
        print(f"{hit['rank']}. {hit['chunk_id']} | {hit['company']} | PDF第{hit['pdf_page_start']}页"
              f" | {hit['quality_status']}\n{hit['content'][:160]}\n")
    print(f"检索耗时 {result['elapsed_seconds']}s；模式 {result['mode']}")
