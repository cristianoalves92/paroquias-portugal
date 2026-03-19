#!/usr/bin/env python3
import csv
import html
import re
import time
from pathlib import Path

import requests

BASE = "https://www.anuariocatolicoportugal.net/lista_paroquias.asp"
OUT = Path("data/paroquias_portugal_anuario_catolico.csv")
RAW = Path("data/raw/anuario_pages")
RAW.mkdir(parents=True, exist_ok=True)

ROW_RE = re.compile(
    r'<tr\s+bgcolor="#FFFFFF">\s*'
    r'<td[^>]*>\s*<strong>\s*<A\s+HREF="ficha_paroquia_padre\.asp\?paroquiaid=(\d+)">(.*?)</A>\s*</strong>\s*</td>\s*'
    r'<td[^>]*>\s*<div[^>]*>(.*?)</div>\s*</td>\s*'
    r'<td[^>]*>\s*<div[^>]*>(.*?)</div>\s*</td>\s*'
    r'<td[^>]*>\s*<div[^>]*>(.*?)</div>\s*</td>\s*'
    r'</tr>',
    re.IGNORECASE | re.DOTALL,
)

COUNT_RE = re.compile(r'Par\&oacute;quias</strong>\s*\d+\s*a\s*\d+\s*de\s*\((\d+)\)', re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean(s: str) -> str:
    s = TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = s.replace("\xa0", " ")
    s = WS_RE.sub(" ", s).strip()
    return s


def fetch(session: requests.Session, offset: int) -> str:
    url = BASE if offset == 0 else f"{BASE}?offset={offset}"
    r = session.get(url, timeout=30)
    r.raise_for_status()
    r.encoding = "iso-8859-1"
    text = r.text
    (RAW / f"lista_paroquias_offset_{offset}.html").write_text(text, encoding="utf-8")
    return text


def parse(text: str):
    rows = []
    for pid, nome, orago, arciprestado, diocese in ROW_RE.findall(text):
        rows.append(
            {
                "paroquia_id": int(pid),
                "nome": clean(nome),
                "orago": clean(orago),
                "arciprestado": clean(arciprestado),
                "diocese": clean(diocese),
                "url_ficha": f"https://www.anuariocatolicoportugal.net/ficha_paroquia_padre.asp?paroquiaid={pid}",
            }
        )
    return rows


def main() -> None:
    session = requests.Session()

    first = fetch(session, 0)
    m = COUNT_RE.search(first)
    total = int(m.group(1)) if m else None

    all_rows = []
    seen = set()

    offset = 0
    while True:
        text = first if offset == 0 else fetch(session, offset)
        page_rows = parse(text)

        if not page_rows:
            break

        for row in page_rows:
            key = row["paroquia_id"]
            if key in seen:
                continue
            seen.add(key)
            all_rows.append(row)

        offset += 50
        time.sleep(0.1)

        if total is not None and len(all_rows) >= total:
            break

    all_rows.sort(key=lambda r: (r["nome"].lower(), r["paroquia_id"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["paroquia_id", "nome", "orago", "arciprestado", "diocese", "url_ficha"],
        )
        w.writeheader()
        w.writerows(all_rows)

    print(f"total_extraido={len(all_rows)}")
    if total is not None:
        print(f"total_indicado_site={total}")


if __name__ == "__main__":
    main()
