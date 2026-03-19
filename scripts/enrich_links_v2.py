#!/usr/bin/env python3
import argparse
import csv
import html
import json
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse

import requests

BING_SEARCH = "https://www.bing.com/search?q="
UA = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122 Safari/537.36"
    )
}

RESULT_RE = re.compile(
    r'<li class="b_algo".*?<h2><a href="([^"]+)"[^>]*>(.*?)</a></h2>',
    re.IGNORECASE | re.DOTALL,
)
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
PAGE_WEB_CELL_RE = re.compile(
    r"P&aacute;gina Web\s*</span>\s*</td>\s*<td[^>]*>(.*?)</td>",
    re.IGNORECASE | re.DOTALL,
)
TEXT_URL_RE = re.compile(r"(https?://[^\s<]+|www\.[^\s<]+)", re.IGNORECASE)

STOPWORDS = {
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

ABBR_MAP = {
    "stª": "santa",
    "sta.": "santa",
    "sta ": "santa ",
    "stº": "santo",
    "sto.": "santo",
    "sto ": "santo ",
    "st.": "santo",
    "s.": "sao",
    "s ": "sao ",
    "nª srª": "nossa senhora",
    "nª sra": "nossa senhora",
    "n srª": "nossa senhora",
    "n sra": "nossa senhora",
    "srª": "senhora",
    "sra": "senhora",
}


def clean_html_text(s: str) -> str:
    s = TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = s.replace("\xa0", " ")
    return WS_RE.sub(" ", s).strip()


def normalize_text(s: str) -> str:
    s = s.lower().strip()
    for src, dst in ABBR_MAP.items():
        s = s.replace(src, dst)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def slugify(s: str) -> str:
    return normalize_text(s).replace(" ", "-")


def normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    elif not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u.strip()


def is_valid_public_url(url: str) -> bool:
    if not url:
        return False
    p = urlparse(url)
    host = p.netloc.strip().lower()
    if not host or host in {"http", "https", "www", "localhost"}:
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


def is_generic_social(url: str) -> bool:
    p = urlparse(url)
    host = p.netloc.lower()
    path = p.path.strip("/").lower()
    if is_facebook(url):
        if path in {"", "pages", "pg"}:
            return True
        if "googleworkspace" in path:
            return True
    if is_instagram(url):
        if path in {"", "accounts"}:
            return True
        if "googleworkspace" in path:
            return True
    return host in {"facebook.com", "www.facebook.com", "instagram.com", "www.instagram.com"}


def is_bad_site(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    bad_hosts = {
        "anuariocatolicoportugal.net",
        "google.com",
        "youtube.com",
        "linkedin.com",
        "wikipedia.org",
    }
    return any(h in host for h in bad_hosts)


def fetch(session: requests.Session, url: str, timeout: int = 12) -> str:
    r = session.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
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


def extract_tokens(row: dict) -> tuple[list[str], str, list[str]]:
    nome_tokens = [
        t
        for t in normalize_text(row.get("nome", "")).split()
        if len(t) >= 4 and t not in STOPWORDS
    ]
    diocese_token = normalize_text(row.get("diocese", "")).split()
    diocese = diocese_token[0] if diocese_token else ""
    orago_tokens = [
        t
        for t in normalize_text(row.get("orago", "")).split()
        if len(t) >= 4 and t not in STOPWORDS
    ]
    return nome_tokens[:5], diocese, orago_tokens[:4]


def score_candidate(row: dict, url: str, title: str, rank: int, platform: str) -> float:
    text = normalize_text(f"{url} {title}")
    nome_tokens, diocese, orago_tokens = extract_tokens(row)
    if not text:
        return 0.0

    name_match = 0.0
    if nome_tokens:
        hits = sum(1 for t in nome_tokens if t in text)
        name_match = hits / len(nome_tokens)

    score = 0.30 + 0.42 * name_match
    if diocese and diocese in text:
        score += 0.10
    if "paroquia" in text or "paroquial" in text:
        score += 0.08
    if any(t in text for t in orago_tokens):
        score += 0.08
    score += max(0.0, 0.08 - 0.02 * rank)

    if platform == "facebook" and not is_facebook(url):
        score -= 0.35
    if platform == "instagram" and not is_instagram(url):
        score -= 0.35
    if platform == "site" and (is_social(url) or is_bad_site(url)):
        score -= 0.40

    return round(max(0.0, min(0.95, score)), 3)


def web_search(session: requests.Session, query: str) -> list[tuple[str, str]]:
    r = session.get(BING_SEARCH + quote_plus(query), headers=UA, timeout=12)
    r.raise_for_status()
    results = []
    for href, raw_title in RESULT_RE.findall(r.text):
        href = normalize_url(html.unescape(href))
        title = clean_html_text(raw_title)
        if is_valid_public_url(href):
            results.append((href, title))
    return results


def parse_api_name(name: str) -> tuple[str, str]:
    m = re.match(r"^(.*?)\s*\((.*?)\)\s*$", name or "")
    if m:
        return m.group(1), m.group(2)
    return name or "", ""


class OfficialDirectories:
    def __init__(self, session: requests.Session):
        self.session = session
        self._porto = None
        self._setubal = None
        self._santarem = None
        self._leiria = None
        self._angra = None
        self._guarda = None
        self._viseu = None

    def _load_porto(self):
        if self._porto is not None:
            return self._porto
        r = self.session.post(
            "https://www.diocese-porto.pt/umbraco/Surface/Data/AjaxRequest",
            data={
                "url": "https://www.aparoquia.com/apo/webservice/porto/listar/paroquias",
                "data": "ecfd1e3a7c22352e63ea9tert5299ae6",
            },
            headers=UA,
            timeout=20,
        )
        items = json.loads(json.loads(r.text))
        index = {}
        for item in items:
            nome, orago = parse_api_name(item.get("paroquia", ""))
            index[(normalize_text(nome), normalize_text(orago))] = item
        self._porto = index
        return index

    def _load_setubal(self):
        if self._setubal is not None:
            return self._setubal
        items = self.session.post(
            "https://aparoquia.com/apo/webservice/v2/listar/paroquias/idDiocese/17",
            data={"authCode": "ecfd1e3a7c22352e63ea9acda5299ae6"},
            headers=UA,
            timeout=20,
        ).json()
        index = {}
        for item in items:
            nome, orago = parse_api_name(item.get("nome", ""))
            index[(normalize_text(nome), normalize_text(orago))] = item
        self._setubal = index
        return index

    def _load_santarem(self):
        if self._santarem is not None:
            return self._santarem
        items = self.session.post(
            "https://aparoquia.com/apo/webservice/vSantarem/listar/paroquias/idDiocese/16",
            data={"authCode": "ecfd1e3a7c22352e63ea9acda5299ae6"},
            headers=UA,
            timeout=20,
        ).json()
        index = {}
        for item in items:
            nome, orago = parse_api_name(item.get("nome", ""))
            index[(normalize_text(nome), normalize_text(orago))] = item
        self._santarem = index
        return index

    def _load_leiria(self):
        if self._leiria is not None:
            return self._leiria
        text = fetch(self.session, "https://www.leiria-fatima.pt/organica/paroquias/", timeout=20)
        rows = re.findall(r'<tr[^>]*class="ninja_table_row_.*?</tr>', text, re.IGNORECASE | re.DOTALL)
        index = {}
        for row in rows:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.IGNORECASE | re.DOTALL)
            values = [clean_html_text(cell) for cell in cells]
            if len(values) < 17:
                continue
            name = values[0]
            site = normalize_url(values[14])
            facebook = normalize_url(values[16])
            index[normalize_text(name)] = {"site": site, "facebook": facebook}
        self._leiria = index
        return index

    def _load_angra(self):
        if self._angra is not None:
            return self._angra
        items = self.session.post(
            "https://aparoquia.com/apo/webservice/v2/listar/paroquias/idDiocese/2",
            data={"authCode": "ecfd1e3a7c22352e63ea9acda5299ae6"},
            headers=UA,
            timeout=20,
        ).json()
        index = {}
        for item in items:
            nome, orago = parse_api_name(item.get("nome", ""))
            index[(normalize_text(nome), normalize_text(orago))] = item
        self._angra = index
        return index

    def _load_guarda(self):
        if self._guarda is not None:
            return self._guarda
        items = self.session.post(
            "https://aparoquia.com/apo/webservice/v2/listar/paroquias/idDiocese/10",
            data={"authCode": "ecfd1e3a7c22352e63ea9acda5299ae6"},
            headers=UA,
            timeout=20,
        ).json()
        index = {}
        for item in items:
            nome, orago = parse_api_name(item.get("nome", ""))
            index[(normalize_text(nome), normalize_text(orago))] = item
        self._guarda = index
        return index

    def _load_viseu(self):
        if self._viseu is not None:
            return self._viseu
        items = self.session.post(
            "https://aparoquia.com/apo/webservice/v2/listar/paroquias/idDiocese/20",
            data={"authCode": "ecfd1e3a7c22352e63ea9acda5299ae6"},
            headers=UA,
            timeout=20,
        ).json()
        index = {}
        for item in items:
            nome, orago = parse_api_name(item.get("nome", ""))
            index[(normalize_text(nome), normalize_text(orago))] = item
        self._viseu = index
        return index

    def lookup(self, row: dict) -> dict:
        key = (normalize_text(row.get("nome", "")), normalize_text(row.get("orago", "")))
        diocese = row.get("diocese", "")
        if diocese == "Porto":
            return self._porto_lookup(key)
        if diocese == "Setúbal":
            return self._setubal_lookup(key)
        if diocese == "Santarém":
            return self._santarem_lookup(key)
        if diocese == "Leiria-Fátima":
            return self._leiria_lookup(row, key)
        if diocese == "Angra":
            return self._angra_lookup(row, key)
        if diocese == "Guarda":
            return self._guarda_lookup(key)
        if diocese == "Viseu":
            return self._viseu_lookup(key)
        if diocese == "Braga":
            return self._braga_lookup(row)
        return {}

    def _porto_lookup(self, key: tuple[str, str]) -> dict:
        item = self._load_porto().get(key)
        if not item:
            return {}
        website = normalize_url(item.get("website") or "")
        result = {}
        if is_valid_public_url(website):
            if is_facebook(website):
                result["facebook"] = website
            elif is_instagram(website):
                result["instagram"] = website
            else:
                result["site"] = website
        return result

    def _setubal_lookup(self, key: tuple[str, str]) -> dict:
        item = self._load_setubal().get(key)
        if not item:
            return {}
        website = normalize_url(item.get("website") or "")
        result = {}
        if is_valid_public_url(website):
            if is_facebook(website):
                result["facebook"] = website
            elif is_instagram(website):
                result["instagram"] = website
            else:
                result["site"] = website
        return result

    def _santarem_lookup(self, key: tuple[str, str]) -> dict:
        item = self._load_santarem().get(key)
        if not item:
            return {}
        website = normalize_url(item.get("website") or "")
        return {"site": website} if is_valid_public_url(website) else {}

    def _leiria_lookup(self, row: dict, key: tuple[str, str]) -> dict:
        entries = self._load_leiria()
        result = entries.get(key[0], {})
        if not result and normalize_text(row.get("arciprestado", "")) == "ourem":
            composite = f"{row.get('nome','')} (Ourém)"
            result = entries.get(normalize_text(composite), {})
        clean = {}
        site = normalize_url(result.get("site", ""))
        facebook = normalize_url(result.get("facebook", ""))
        if is_valid_public_url(site):
            clean["site"] = site
        if is_valid_public_url(facebook):
            clean["facebook"] = facebook
        return clean

    def _angra_lookup(self, row: dict, key: tuple[str, str]) -> dict:
        entries = self._load_angra()
        item = entries.get(key)
        if not item and key[0] == normalize_text("Santa Cruz"):
            item = entries.get((normalize_text("Santa Cruz - Lagoa"), key[1]))
        website = normalize_url((item or {}).get("website") or "")
        if not is_valid_public_url(website):
            return {}
        if is_facebook(website):
            return {"facebook": website}
        if is_instagram(website):
            return {"instagram": website}
        return {"site": website}

    def _guarda_lookup(self, key: tuple[str, str]) -> dict:
        item = self._load_guarda().get(key)
        if not item:
            return {}
        website = normalize_url(item.get("website") or "")
        if not is_valid_public_url(website):
            return {}
        if is_facebook(website):
            return {"facebook": website}
        if is_instagram(website):
            return {"instagram": website}
        return {"site": website}

    def _viseu_lookup(self, key: tuple[str, str]) -> dict:
        item = self._load_viseu().get(key)
        if not item:
            return {}
        website = normalize_url(item.get("website") or "")
        if not is_valid_public_url(website):
            return {}
        if is_facebook(website):
            return {"facebook": website}
        if is_instagram(website):
            return {"instagram": website}
        return {"site": website}

    def _braga_lookup(self, row: dict) -> dict:
        url = f"https://arquidiocese-braga.pt/local/{slugify(row.get('nome',''))}-{slugify(row.get('orago',''))}"
        try:
            html_text = fetch(self.session, url, timeout=8)
        except Exception:
            return {}
        title = clean_html_text(TITLE_RE.search(html_text).group(1)) if TITLE_RE.search(html_text) else ""
        expected = normalize_text(f"{row.get('nome','')} ({row.get('orago','')})")
        if expected and expected in normalize_text(title):
            return {"site": url}
        return {}


def apply_official_candidates(row: dict, candidates: dict) -> None:
    for key in ("site", "facebook", "instagram"):
        value = normalize_url(candidates.get(key, ""))
        if value and is_valid_public_url(value) and not row.get(key, "").strip():
            row[key] = value
            row[f"{key}_confidence"] = "1.0"


def scan_site_for_socials(session: requests.Session, row: dict) -> None:
    site = normalize_url(row.get("site", ""))
    if not site or (row.get("facebook") and row.get("instagram")):
        return
    try:
        html_text = fetch(session, site, timeout=10)
    except Exception:
        return
    hrefs = [normalize_url(unquote(h)) for h in HREF_RE.findall(html_text)]
    for h in hrefs:
        if not row.get("facebook") and is_valid_public_url(h) and is_facebook(h) and not is_generic_social(h):
            row["facebook"] = h
            row["facebook_confidence"] = row.get("site_confidence") or "0.9"
        if not row.get("instagram") and is_valid_public_url(h) and is_instagram(h) and not is_generic_social(h):
            row["instagram"] = h
            row["instagram_confidence"] = row.get("site_confidence") or "0.9"
        if row.get("facebook") and row.get("instagram"):
            break


def enrich_row(
    session: requests.Session,
    row: dict,
    directories: OfficialDirectories,
    delay_s: float,
    min_score: float,
    official_only: bool,
    scan_site_socials: bool,
) -> None:
    for key in ("site", "facebook", "instagram"):
        if not is_valid_public_url(normalize_url(row.get(key, ""))):
            row[key] = ""
            row[f"{key}_confidence"] = ""
    for c in ("site_confidence", "facebook_confidence", "instagram_confidence"):
        row.setdefault(c, "")

    ficha = row.get("url_ficha", "").strip()
    if ficha:
        try:
            direct = extract_from_ficha(fetch(session, ficha))
            for key in ("site", "facebook", "instagram"):
                if direct[key]:
                    row[key] = direct[key]
                    row[f"{key}_confidence"] = "1.0"
        except Exception:
            pass

    apply_official_candidates(row, directories.lookup(row))

    if scan_site_socials and row.get("site"):
        scan_site_for_socials(session, row)

    missing = [k for k in ("site", "facebook", "instagram") if not row.get(k, "").strip()]
    if not missing or official_only:
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

    for rank, (url, title) in enumerate(results[:10]):
        if not row.get("facebook") and is_facebook(url):
            score = score_candidate(row, url, title, rank, "facebook")
            if score >= min_score:
                row["facebook"] = url
                row["facebook_confidence"] = str(score)
        if not row.get("instagram") and is_instagram(url):
            score = score_candidate(row, url, title, rank, "instagram")
            if score >= min_score:
                row["instagram"] = url
                row["instagram_confidence"] = str(score)
        if not row.get("site") and not is_social(url) and not is_bad_site(url):
            score = score_candidate(row, url, title, rank, "site")
            if score >= min_score:
                row["site"] = url
                row["site_confidence"] = str(score)
        if row.get("site") and row.get("facebook") and row.get("instagram"):
            break

    if scan_site_socials and row.get("site"):
        scan_site_for_socials(session, row)

    time.sleep(delay_s)


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/paroquias_portugal_anuario_catolico.csv")
    ap.add_argument("--output", default="")
    ap.add_argument("--delay", type=float, default=0.1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--checkpoint-every", type=int, default=25)
    ap.add_argument("--diocese", action="append", default=[])
    ap.add_argument("--official-only", action="store_true")
    ap.add_argument("--scan-site-socials", action="store_true")
    ap.add_argument("--min-score", type=float, default=0.62)
    args = ap.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output) if args.output else in_path

    with in_path.open("r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        fieldnames = list(r.fieldnames or [])
        rows = list(r)

    session = requests.Session()
    directories = OfficialDirectories(session)
    dioceses = set(args.diocese)
    selected = [
        idx
        for idx, row in enumerate(rows)
        if (not dioceses or row.get("diocese") in dioceses)
    ]
    if args.limit > 0:
        selected = selected[: args.limit]

    for pos, idx in enumerate(selected, start=1):
        enrich_row(
            session,
            rows[idx],
            directories,
            args.delay,
            args.min_score,
            args.official_only,
            args.scan_site_socials,
        )
        if pos % args.checkpoint_every == 0:
            write_csv(out_path, rows, fieldnames)
            print(f"checkpoint={pos}/{len(selected)}")

    write_csv(out_path, rows, fieldnames)
    print(f"done={len(selected)} rows")


if __name__ == "__main__":
    main()
