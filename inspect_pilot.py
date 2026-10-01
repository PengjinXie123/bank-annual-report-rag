from pathlib import Path
import json
import pdfplumber
import pypdfium2 as pdfium
from process_pilot_reports import REPORTS, REPORT_DIR, ROOT

out = ROOT / 'data' / 'processed' / 'pilot' / 'qa' / 'images'
out.mkdir(parents=True, exist_ok=True)
targets = {'601398': [14, 16, 193, 205], '601939': [14, 15, 167, 187]}
for spec in REPORTS:
    doc = pdfium.PdfDocument(str(REPORT_DIR / spec.filename))
    with pdfplumber.open(REPORT_DIR / spec.filename) as pdf:
        for number in targets[spec.code]:
            page = pdf.pages[number-1]
            words = page.extract_words(x_tolerance=2, y_tolerance=3)
            (out / f'{spec.code}_{number}_words.json').write_text(json.dumps(words,ensure_ascii=False,indent=2),encoding='utf-8')
            (out / f'{spec.code}_{number}.txt').write_text(page.extract_text() or '',encoding='utf-8')
            bitmap = doc[number-1].render(scale=1.6)
            bitmap.to_pil().save(out / f'{spec.code}_{number}.png')
            print(spec.code, number, page.width, page.height)
