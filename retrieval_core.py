"""Shared embedding client and reproducible Chinese BM25 tokenization."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
from course_config import credential

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / '.deps'))
import numpy as np

MODEL = 'text-embedding-v4'
DIMENSION = 1024
ENDPOINT = 'https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding'
TOKENIZER_VERSION = 'jieba-0.42.1-search-hmm-false-finance-v1'
FINANCE_WORDS = ('工商银行', '建设银行', '农业银行', '中国银行', '交通银行', '招商银行',
                 '兴业银行', '浦发银行', '中信银行', '平安银行', '营业收入', '净利润',
                 '不良贷款率', '核心一级资本充足率', '资本充足率', '净息差', '本集团',
                 '本行', '归属于母公司股东', '资产减值损失')
_tokenizer = None


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def save_array(path, values):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('wb') as handle:
        np.save(handle, values, allow_pickle=False)
    os.replace(temporary, path)


def load_key():
    return credential('DASHSCOPE_API_KEY')


def embed(texts, text_type, model=MODEL, dimension=DIMENSION, endpoint=ENDPOINT):
    if not 1 <= len(texts) <= 10 or text_type not in ('document', 'query'):
        raise ValueError('embedding 每批须为 1–10 条，类型须为 document/query。')
    key = load_key()
    payload = json.dumps({'model': model, 'input': {'texts': texts},
                          'parameters': {'dimension': dimension, 'text_type': text_type}}).encode('utf-8')
    for attempt in range(6):
        request = urllib.request.Request(endpoint, data=payload,
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.load(response)
            items = sorted(result['output']['embeddings'], key=lambda row: row['text_index'])
            if [row['text_index'] for row in items] != list(range(len(texts))):
                raise RuntimeError('embedding 返回的文本序号不完整。')
            vectors = np.asarray([row['embedding'] for row in items], dtype=np.float32)
            if vectors.shape != (len(texts), dimension) or not np.isfinite(vectors).all():
                raise RuntimeError('embedding 向量维度或数值异常。')
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if (norms <= 0).any():
                raise RuntimeError('embedding 返回零向量。')
            vectors /= norms
            return vectors, result.get('usage', {}).get('total_tokens', 0)
        except urllib.error.HTTPError as error:
            # Do not include response bodies, headers, or credentials in errors.
            if error.code not in (408, 429, 500, 502, 503, 504) or attempt == 5:
                raise RuntimeError(f'embedding 请求失败：HTTP {error.code}') from None
        except (urllib.error.URLError, TimeoutError):
            if attempt == 5:
                raise RuntimeError('embedding 网络请求失败，已重试 6 次。') from None
        time.sleep(min(2 ** attempt, 16))
    raise RuntimeError('embedding 请求未完成。')


def tokenize(text):
    global _tokenizer
    if _tokenizer is None:
        import jieba
        import logging
        jieba.setLogLevel(logging.WARNING)
        (ROOT / 'tmp').mkdir(exist_ok=True)
        _tokenizer = jieba.Tokenizer()
        _tokenizer.tmp_dir = str(ROOT / 'tmp')
        _tokenizer.cache_file = 'jieba-course.cache'
        _tokenizer.initialize()
        for word in FINANCE_WORDS:
            _tokenizer.add_word(word, freq=100000)
    return [token.lower() for token in _tokenizer.cut_for_search(text, HMM=False)
            if re.search(r'[\w\u4e00-\u9fff]', token)]


def read_chunks(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def active_index():
    expanded = ROOT / 'data/indexes/course_all'
    if (expanded / 'manifest.json').is_file():
        if read_json(expanded / 'manifest.json').get('status') == 'ready':
            return expanded
    return ROOT / 'data/indexes/course_pilot'


def build_bm25(chunks):
    from rank_bm25 import BM25Okapi
    tokens = [tokenize(chunk['embedding_text']) for chunk in chunks]
    bm25 = BM25Okapi(tokens, k1=1.5, b=0.75, epsilon=0.25)
    return {name: getattr(bm25, name) for name in
            ('corpus_size', 'avgdl', 'doc_freqs', 'idf', 'doc_len', 'k1', 'b', 'epsilon')}


def restore_bm25(data):
    from rank_bm25 import BM25Okapi
    bm25 = BM25Okapi.__new__(BM25Okapi)
    for key, value in data.items():
        setattr(bm25, key, value)
    return bm25
