#!/usr/bin/env python3
import argparse
import csv
import html
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse

import requests

BING_SEARCH = "https://www.bing.com/search?q="
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"}

RESULT_RE = re.compile(
    r'<li class="b_algo".*?<h2><a href="([^"]+)"[^>]*>(.*?)</a></h2>',
    re.IGNORECASE | re.DOTALL,
)
HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
PAGE_WEB_CELL_RE = re.compile(
    r"P&aacute;gina Web\s*</span>\s*</td>\s*<td[^>]*>(.*?)</td>",
    re.IGNORECASE | re.DOTALL,
)
TEXT_URL_RE = re.compile(r"(https?://[^\s<]+|www\.[^\s<]+)", re.IGNORECASE)

STOPWORDS = {
    "paroquia",
    "paroquia",
    "paróquia",
    "paroquial",
    "igreja",
    "de",
    "da",
    "do",
    "das",
    "dos",
    "e",
}


def clean_html_text(s: str) -> str:
    s = TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = s.replace("\xa0", " ")
    s = WS_RE.sub(" ", s).strip()
    return s


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def normalize_url(url: str) -> str:
    u = url.strip()
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    elif not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u


def is_valid_public_url(url: str) -> bool:
    if not url:
        return False
    p = urlparse(url)
    host = p.netloc.strip().lower()
    if not host:
        return False
    if host in {"http", "https", "www", "localhost"}:
        return False
    if "." not in host:
        return False
    return True


def is_facebook(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return "facebook.com" in host or "fb.com" in host


def is_instagram(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return "instagram.com" in host


def is_social(url: str) -> bool:
    return is_facebook(url) or is_instagram(url)


def is_bad_site(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    if not host:
        return True
    bad_hosts = [
        "anuariocatolicoportugal.net",
        "google.com",
        "youtube.com",
        "linkedin.com",
        "wikipedia.org",
    ]
    return any(h in host for h in bad_hosts)


def extract_tokens(row: dict) -> tuple[list[str], str, list[str]]:
    nome_tokens = [t for t in normalize_text(row.get("nome", "")).split() if len(t) >= 4 and t not in STOPWORDS]
    diocese_token = normalize_text(row.get("diocese", "")).split()
    diocese = diocese_token[0] if diocese_token else ""
    orago_tokens = [t for t in normalize_text(row.get("orago", "")).split() if len(t) >= 5 and t not in STOPWORDS]
    return nome_tokens[:5], diocese, orago_tokens[:3]


def score_candidate(row: dict, url: str, title: str, rank: int, platform: str) -> float:
    text = normalize_text(f"{url} {title}")
    nome_tokens, diocese, orago_tokens = extract_tokens(row)

    if not text:
        return 0.0

    name_match = 0.0
    if nome_tokens:
        hits = sum(1 for t in nome_tokens if t in text)
        name_match = hits / len(nome_tokens)

    score = 0.30 + 0.40 * name_match
    if diocese and diocese in text:
        score += 0.12
    if "paroquia" in text or "paroquial" in text:
        score += 0.06
    if any(t in text for t in orago_tokens):
        score += 0.05
    score += max(0.0, 0.08 - 0.02 * rank)

    if platform == "facebook" and not is_facebook(url):
        score -= 0.30
    if platform == "instagram" and not is_instagram(url):
        score -= 0.30
    if platform == "site" and (is_social(url) or is_bad_site(url)):
        score -= 0.35

    score = max(0.0, min(0.89, score))
    return round(score, 3)


def fetch(session: requests.Session, url: str, timeout: int = 12) -> str:
    r = session.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    r.encoding = "iso-8859-1"
    return r.text


def extract_from_ficha(html_text: str) -> dict:
    result = {"site": "", "facebook": "", "instagram": ""}

    m = PAGE_WEB_CELL_RE.search(html_text)
    if m:
        cell = m.group(1)
        hrefs = HREF_RE.findall(cell)
        if hrefs:
            candidate = normalize_url(clean_html_text(hrefs[0]))
            if is_valid_public_url(candidate):
                result["site"] = candidate
        else:
            text = clean_html_text(cell)
            um = TEXT_URL_RE.search(text)
            if um:
                candidate = normalize_url(um.group(1))
                if is_valid_public_url(candidate):
                    result["site"] = candidate

    hrefs = [normalize_url(unquote(h)) for h in HREF_RE.findall(html_text)]
    for h in hrefs:
        if not result["facebook"] and is_valid_public_url(h) and is_facebook(h):
            result["facebook"] = h
        if not result["instagram"] and is_valid_public_url(h) and is_instagram(h):
            result["instagram"] = h
        if result["facebook"] and result["instagram"]:
            break

    return result


def web_search(session: requests.Session, query: str) -> list[tuple[str, str]]:
    url = BING_SEARCH + quote_plus(query)
    r = session.get(url, headers=UA, timeout=12)
    r.raise_for_status()
    txt = r.text
    results = []
    for href, raw_title in RESULT_RE.findall(txt):
        href = normalize_url(html.unescape(href))
        title = clean_html_text(raw_title)
        if is_valid_public_url(href):
            results.append((href, title))
    return results


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def enrich_row(session: requests.Session, row: dict, delay_s: float) -> None:
    for k in ("site", "facebook", "instagram"):
        if not is_valid_public_url(normalize_url(row.get(k, ""))):
            row[k] = ""
            row[f"{k}_confidence"] = ""

    for c in ("site_confidence", "facebook_confidence", "instagram_confidence"):
        row.setdefault(c, "")

    html_text = ""
    ficha = row.get("url_ficha", "").strip()
    if ficha:
        try:
            html_text = fetch(session, ficha)
            direct = extract_from_ficha(html_text)
            for k in ("site", "facebook", "instagram"):
                if direct[k]:
                    row[k] = direct[k]
                    row[f"{k}_confidence"] = "1.0"  # regra pedida: link ja existente = 100%
        except Exception:
            pass

    missing = [k for k in ("site", "facebook", "instagram") if not row.get(k, "").strip()]
    if not missing:
        return

    queries = [
        f'{row.get("orago","")} {row.get("nome","")} paroquia',
        f'{row.get("orago","")} {row.get("nome","")} {row.get("arciprestado","")} paroquia',
        f'{row.get("orago","")} {row.get("nome","")} {row.get("diocese","")} paroquia portugal',
    ]
    results: list[tuple[str, str]] = []
    for query in queries:
        try:
            results = web_search(session, query)
        except Exception:
            results = []
        if results:
            break

    if not results:
        time.sleep(delay_s)
        return

    for rank, (url, title) in enumerate(results[:10]):
        if not row.get("facebook") and is_facebook(url):
            row["facebook"] = url
            row["facebook_confidence"] = str(score_candidate(row, url, title, rank, "facebook"))
        if not row.get("instagram") and is_instagram(url):
            row["instagram"] = url
            row["instagram_confidence"] = str(score_candidate(row, url, title, rank, "instagram"))
        if not row.get("site") and not is_social(url) and not is_bad_site(url):
            row["site"] = url
            row["site_confidence"] = str(score_candidate(row, url, title, rank, "site"))
        if row.get("site") and row.get("facebook") and row.get("instagram"):
            break

    time.sleep(delay_s)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/paroquias_portugal_anuario_catolico.csv")
    ap.add_argument("--output", default="")
    ap.add_argument("--delay", type=float, default=0.15)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--checkpoint-every", type=int, default=25)
    args = ap.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output) if args.output else in_path

    rows: list[dict] = []
    with in_path.open("r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        fieldnames = list(r.fieldnames or [])
        required = [
            "paroquia_id",
            "nome",
            "orago",
            "arciprestado",
            "diocese",
            "url_ficha",
            "site",
            "facebook",
            "instagram",
            "site_confidence",
            "facebook_confidence",
            "instagram_confidence",
        ]
        for col in required:
            if col not in fieldnames:
                fieldnames.append(col)
        for row in r:
            for col in required:
                row.setdefault(col, "")
            rows.append(row)

    total = len(rows)
    target = min(total, args.limit) if args.limit > 0 else total
    session = requests.Session()

    for idx in range(target):
        enrich_row(session, rows[idx], args.delay)
        if (idx + 1) % args.checkpoint_every == 0:
            write_csv(out_path, rows, fieldnames)
            print(f"checkpoint={idx+1}/{target}")

    write_csv(out_path, rows, fieldnames)
    print(f"done={target}/{total}")


if __name__ == "__main__":
    main()
