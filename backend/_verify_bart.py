"""Throwaway: download the official Aug 10 2026 BART timetable PDFs and dump
late-night rows for Downtown Berkeley / 19th St / Embarcadero verification."""
import io
import urllib.request

from pypdf import PdfReader

PDFS = {
    "orange_wday": "https://www.bart.gov/sites/default/files/2026-07/August%2010%2C%20%202026%20%20WDAY%20Service%20for%20Berryessa%5FN%20San%20Jose%E2%95%A0%C3%BC%20to%20Richmond%20%28Orange%29%20Line.pdf",
    "yellow_wday": "https://www.bart.gov/sites/default/files/2026-07/August%2010%2C%20%202026%20%20WDAY%20Service%20for%20Antioch%5FSFO%20%28Yellow%29%20Line.pdf",
    "orange_wend": "https://www.bart.gov/sites/default/files/2026-07/August%2010%2C%20%202026%20%20Sat%5FSun%20Service%20for%20Berryessa%20N%20San%20Jose%20to%20Richmond%20%28Orange%29%20Line.pdf",
    "yellow_wend": "https://www.bart.gov/sites/default/files/2026-07/August%2010%2C%20%202026%20%20Sat%5FSun%20Service%20for%20Antioch%20to%20SFO%20%28Yellow%29%20Line.pdf",
}

for name, url in PDFS.items():
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    out = f"_bart_{name}.txt"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(name, "pages", len(reader.pages), "->", out, len(text), "chars")
