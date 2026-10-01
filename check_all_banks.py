"""Corpus integrity and one real mixed-retrieval question per bank; no generated answers."""
from collections import Counter
from datetime import datetime
from pathlib import Path

from bank_catalog import BANKS, BANK_NAMES
from retrieval_core import ROOT, np, read_json, read_chunks, sha256_file, write_json
from search_course import CourseRetriever


def main():
    output = ROOT / 'data/processed/course_all/checks'
    output.mkdir(parents=True, exist_ok=True)
    retriever = CourseRetriever(ROOT / 'data/indexes/course_all')
    chunks = retriever.chunks
    pilot = read_chunks(ROOT / 'data/processed/course_pilot/chunks.jsonl')
    original = {row['chunk_id']: row for row in pilot}
    assert len(chunks) == len({row['chunk_id'] for row in chunks})
    assert {row['company'] for row in chunks} == set(BANK_NAMES)
    assert all(row == original[row['chunk_id']] for row in chunks if row['chunk_id'] in original)
    assert all(row.get('content') and row.get('section') and row.get('source_url') for row in chunks)
    assert all(len(row['content']) <= 800 or row.get('length_exception') for row in chunks)
    assert all(row['complete_rows'] for row in chunks if row['chunk_type'] == 'table')
    assert np.isfinite(retriever.vectors).all()
    assert np.allclose(np.linalg.norm(retriever.vectors, axis=1), 1, atol=1e-5)
    reused_vectors = np.load(ROOT / 'data/indexes/course_pilot/vectors.npy', allow_pickle=False)
    assert np.array_equal(retriever.vectors[:len(pilot)], reused_vectors)
    records = []
    for bank in BANKS:
        question = f"{bank['bank']}2025年不良贷款率是多少？"
        result = retriever.search(question, company=bank['bank'], mode='hybrid', report_year=2025)
        assert result['hits'] and all(row['company'] == bank['bank'] for row in result['hits'])
        relevant = [row['chunk_id'] for row in result['hits'] if '不良贷款率' in row['content']]
        record = dict(company=bank['bank'], question=question, hits=len(result['hits']),
                      chunks_with_query_term=relevant, first_page=result['hits'][0]['pdf_page_start'])
        records.append(record)
        write_json(output / f"{bank['code']}_retrieval.json", result)
        print(f"{bank['bank']}：6块召回，含问题科目的块{len(relevant)}个", flush=True)
    summary = dict(created_at=datetime.now().astimezone().isoformat(), integrity='passed',
                   company_count=len(BANK_NAMES), chunk_count=len(chunks), vector_shape=list(retriever.vectors.shape),
                   reused_pilot_vectors=len(pilot), company_chunk_counts=dict(Counter(row['company'] for row in chunks)),
                   retrieval_checks=records,
                   scope='Infrastructure smoke checks; not the final ten-question homework evaluation.')
    write_json(output / 'summary.json', summary)


if __name__ == '__main__':
    main()
