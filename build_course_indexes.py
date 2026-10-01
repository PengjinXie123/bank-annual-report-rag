"""Build persisted cosine and BM25 indexes, with resumable embedding batches."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
import shutil
import time

from retrieval_core import (ROOT, MODEL, DIMENSION, ENDPOINT, TOKENIZER_VERSION, np,
                            read_chunks, read_json, write_json, digest, sha256_file,
                            save_array, embed, build_bm25)


def build(args):
    source = args.chunks.resolve()
    chunks = read_chunks(source)
    if not chunks or len({row['chunk_id'] for row in chunks}) != len(chunks):
        raise RuntimeError('切块为空或 chunk_id 重复。')
    for row in chunks:
        for field in ('content', 'embedding_text', 'company', 'section', 'pdf_page_start', 'source_url'):
            if not row.get(field):
                raise RuntimeError(f"切块 {row['chunk_id']} 缺少 {field}。")
    corpus_hash = sha256_file(source)
    config = {'corpus_sha256': corpus_hash, 'model': MODEL, 'dimension': DIMENSION,
              'text_type': 'document', 'endpoint': ENDPOINT, 'tokenizer': TOKENIZER_VERSION}
    reusable = {}
    if args.reuse_index:
        seed = args.reuse_index.resolve()
        seed_manifest = read_json(seed / 'manifest.json')
        if seed_manifest['status'] != 'ready' or any(seed_manifest.get(key) != config[key]
                    for key in ('model', 'dimension', 'text_type', 'endpoint')):
            raise RuntimeError('待复用索引的模型、维度或文档设置不一致。')
        for filename, field in [('chunks.jsonl', 'corpus_sha256'), ('vectors.npy', 'vectors_sha256')]:
            if sha256_file(seed / filename) != seed_manifest[field]:
                raise RuntimeError('待复用索引文件哈希不一致。')
        seed_chunks = read_chunks(seed / 'chunks.jsonl')
        seed_vectors = np.load(seed / 'vectors.npy', allow_pickle=False)
        if (seed_vectors.shape != (len(seed_chunks), DIMENSION) or not np.isfinite(seed_vectors).all()
                or not np.allclose(np.linalg.norm(seed_vectors, axis=1), 1, atol=1e-5)):
            raise RuntimeError('待复用向量完整性检查失败。')
        reusable = {digest(row['embedding_text']): seed_vectors[i] for i, row in enumerate(seed_chunks)}
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    cache = directory / 'embedding_batches' / digest(config)[:16]
    cache.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    manifest = {'status': 'building', 'created_at': datetime.now().astimezone().isoformat(),
                **config, 'chunk_count': len(chunks), 'batch_size': 10,
                'vector_metric': 'cosine; L2-normalized float32 dot product',
              'bm25': {'library': 'rank-bm25==0.2.2', 'k1': 1.5, 'b': 0.75, 'epsilon': 0.25}}
    manifest['seed_index'] = str(args.reuse_index.resolve()) if args.reuse_index else None
    write_json(directory / 'manifest.json', manifest)
    shutil.copyfile(source, directory / 'chunks.jsonl')
    write_json(directory / 'bm25.json', build_bm25(chunks))
    print(f'BM25 完成：{len(chunks)} 块。开始 embedding，每批 10 条。', flush=True)

    def run_batch(batch_number, texts):
        path = cache / f'batch-{batch_number:04d}.npy'
        meta = cache / f'batch-{batch_number:04d}.json'
        texts_hash = digest(texts)
        if path.is_file() and meta.is_file():
            information = read_json(meta)
            values = np.load(path, allow_pickle=False)
            if (information.get('texts_sha256') == texts_hash
                    and information.get('vectors_sha256') == sha256_file(path)
                    and values.shape == (len(texts), DIMENSION)
                    and np.isfinite(values).all()
                    and np.allclose(np.linalg.norm(values, axis=1), 1, atol=1e-5)):
                return batch_number, values, information['tokens'], True, information.get('seeded_vectors', 0), False
        values = np.empty((len(texts), DIMENSION), dtype=np.float32)
        missing = []
        for i, text in enumerate(texts):
            existing = reusable.get(digest(text))
            if existing is None:
                missing.append(i)
            else:
                values[i] = existing
        tokens = 0
        if missing:
            fresh, tokens = embed([texts[i] for i in missing], 'document')
            values[missing] = fresh
        seeded = len(texts) - len(missing)
        save_array(path, values)
        write_json(meta, {'texts_sha256': texts_hash, 'vectors_sha256': sha256_file(path),
                          'tokens': tokens, 'count': len(texts), 'seeded_vectors': seeded})
        return batch_number, values, tokens, False, seeded, bool(missing)

    batches = [chunks[start:start + 10] for start in range(0, len(chunks), 10)]
    results = [None] * len(batches)
    cached = tokens_total = tokens_new = seeded_total = requested_batches = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(run_batch, i, [row['embedding_text'] for row in batch])
                   for i, batch in enumerate(batches)]
        for count, future in enumerate(as_completed(futures), 1):
            i, vectors, tokens, reused, seeded, requested = future.result()
            results[i] = vectors
            cached += int(reused)
            tokens_total += tokens
            tokens_new += tokens if requested else 0
            seeded_total += seeded
            requested_batches += int(requested)
            if count % 20 == 0 or count == len(batches):
                print(f'embedding {count}/{len(batches)} 批；复用 {cached} 批；'
                      f'耗时 {time.perf_counter() - started:.1f}s', flush=True)
    vectors = np.concatenate(results, axis=0)
    if vectors.shape != (len(chunks), DIMENSION) or not np.isfinite(vectors).all():
        raise RuntimeError('向量索引完整性检查失败。')
    save_array(directory / 'vectors.npy', vectors)
    manifest.update(status='ready', finished_at=datetime.now().astimezone().isoformat(),
        elapsed_seconds=round(time.perf_counter() - started, 2), total_batches=len(batches),
        cached_batches=cached, new_batches=requested_batches, seeded_vectors=seeded_total,
        document_tokens=tokens_total, newly_requested_document_tokens=tokens_new,
        document_tokens_scope='API token counts for newly encoded documents, including cached batches; excludes seeded vectors',
        vector_shape=list(vectors.shape), vectors_sha256=sha256_file(directory / 'vectors.npy'),
        bm25_sha256=sha256_file(directory / 'bm25.json'),
        companies=sorted({row['company'] for row in chunks}))
    write_json(directory / 'manifest.json', manifest)
    print(f'索引就绪：{directory}\n向量形状 {vectors.shape}；文档 token {tokens_total}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    expanded = (ROOT / 'data/processed/course_all/chunks.jsonl').is_file()
    parser.add_argument('--chunks', type=Path,
                        default=ROOT / f'data/processed/{"course_all" if expanded else "course_pilot"}/chunks.jsonl')
    parser.add_argument('--output', type=Path,
                        default=ROOT / f'data/indexes/{"course_all" if expanded else "course_pilot"}')
    parser.add_argument('--workers', type=int, default=3, choices=range(1, 5))
    parser.add_argument('--reuse-index', type=Path,
                        default=ROOT / 'data/indexes/course_pilot'
                        if expanded and (ROOT / 'data/indexes/course_pilot/manifest.json').is_file() else None,
                        help='复用相同模型与完全相同embedding文本的已有向量。')
    build(parser.parse_args())
