"""Local PCA display coordinates for existing embeddings; never used for retrieval."""
from datetime import datetime

from retrieval_core import np, read_json, write_json

VERSION = 'pca-all-v1'


def sky_map(retriever):
    path = retriever.directory / 'sky_map.json'
    signature = {'version': VERSION, 'corpus_sha256': retriever.manifest['corpus_sha256'],
                 'vectors_sha256': retriever.manifest['vectors_sha256']}
    if path.is_file():
        saved = read_json(path)
        if all(saved.get(key) == value for key, value in signature.items()):
            return saved
    # Fit every real document vector, without fabricated positions or bank-separated layouts.
    values = retriever.vectors.astype(np.float64)
    values -= values.mean(axis=0)
    covariance = values.T @ values / (len(values) - 1)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    basis = eigenvectors[:, -2:][:, ::-1].copy()
    for column in range(2):
        if basis[np.argmax(np.abs(basis[:, column])), column] < 0:
            basis[:, column] *= -1
    coordinates = values @ basis
    scale = float(np.max(np.abs(coordinates))) or 1.0
    coordinates /= scale
    companies = retriever.manifest['companies']
    points = [{'id': row['chunk_id'], 'x': round(float(xy[0]), 6),
               'y': round(float(xy[1]), 6), 'bank': companies.index(row['company']),
               'page': row['pdf_page_start'], 'type': row['chunk_type']}
              for row, xy in zip(retriever.chunks, coordinates)]
    result = {**signature, 'created_at': datetime.now().astimezone().isoformat(),
              'method': 'PCA', 'companies': companies, 'count': len(points),
              'explained_variance_ratio': (eigenvalues[-2:][::-1] / eigenvalues.sum()).tolist(),
              'points': points}
    write_json(path, result)
    return result


if __name__ == '__main__':
    from search_course import CourseRetriever
    result = sky_map(CourseRetriever())
    print(f"PCA星空图：{result['count']}个真实切块，{len(result['companies'])}家银行。")
    print(f"二维保留方差比例：{sum(result['explained_variance_ratio']):.2%}；仅用于展示。")
