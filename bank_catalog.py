"""The downloaded-report manifest is the single source for bank identities."""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
with (ROOT / 'data/annual_reports/2025/sources.csv').open(encoding='utf-8-sig', newline='') as handle:
    BANKS = tuple(csv.DictReader(handle))
BANK_NAMES = tuple(bank['bank'] for bank in BANKS)
