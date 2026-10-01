"""Download the exact report versions recorded in sources.csv, validating SHA-256."""
import argparse
import hashlib
from pathlib import Path
import urllib.request

from bank_catalog import BANKS, ROOT


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(verify_only=False):
    directory = ROOT / 'data/annual_reports/2025'
    directory.mkdir(parents=True, exist_ok=True)
    for bank in BANKS:
        name = bank['local_file']
        if Path(name).name != name:
            raise ValueError('清单文件名必须是文件名，不能包含目录。')
        path = directory / name
        if path.is_file() and digest(path) == bank['sha256']:
            print(f"已校验：{bank['bank']}", flush=True)
            continue
        if verify_only:
            raise RuntimeError(f"报告缺失或指纹不同：{bank['bank']}")
        if path.exists():
            raise RuntimeError(f"已有文件与清单不同：{bank['bank']}。请先另行保留原文件。")
        request = urllib.request.Request(bank['pdf_url'], headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=90) as response:
            data = response.read()
        if not data.startswith(b'%PDF') or hashlib.sha256(data).hexdigest() != bank['sha256']:
            raise RuntimeError(f"下载内容与清单不一致：{bank['bank']}，未保存。")
        path.write_bytes(data)
        print(f"已下载并校验：{bank['bank']} → {name}", flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true', help='仅检查本地PDF，不联网下载。')
    main(parser.parse_args().verify_only)
