#!/usr/bin/env python3
"""
Build a high-recall directory of European funder websites.

The primary source is the latest CC0 Research Organization Registry (ROR)
data dump. Records must be active, have type "funder", be located in the
configured European scope, and have an official website. Output is
deduplicated by registrable domain and selected with country balancing.

Optional live checks add:
  * HTTP reachability/status
  * homepage language from HTML/HTTP metadata
  * website update date from Last-Modified or sitemap <lastmod>

No third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import email.utils
import hashlib
import html
import io
import json
import re
import shutil
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, Iterator


ZENODO_CONCEPT_API = "https://zenodo.org/api/records/6347574"
USER_AGENT = (
    "EU-Funding-Source-Directory/1.0 "
    "(research directory; contact: admin@tickerbell.biz)"
)

# EU/EEA, European microstates and territories, the UK, Switzerland,
# the Western Balkans, Eastern Partnership countries, Russia, Türkiye,
# and transcontinental Kazakhstan.
EUROPE_SCOPE = {
    "AD", "AL", "AM", "AT", "AZ", "BA", "BE", "BG", "BY", "CH", "CY",
    "CZ", "DE", "DK", "EE", "ES", "FI", "FO", "FR", "GB", "GE", "GG",
    "GR", "HR", "HU", "IE", "IM", "IS", "IT", "JE", "KZ", "LI", "LT",
    "LU", "LV", "MC", "MD", "ME", "MK", "MT", "NL", "NO", "PL", "PT",
    "RO", "RS", "RU", "SE", "SI", "SJ", "SK", "SM", "TR", "UA", "VA",
    "XK",
}

COUNTRY_NAMES = {
    "AD": "Andorra", "AL": "Albania", "AM": "Armenia", "AT": "Austria",
    "AZ": "Azerbaijan", "BA": "Bosnia and Herzegovina", "BE": "Belgium",
    "BG": "Bulgaria", "BY": "Belarus", "CH": "Switzerland", "CY": "Cyprus",
    "CZ": "Czechia", "DE": "Germany", "DK": "Denmark", "EE": "Estonia",
    "ES": "Spain", "FI": "Finland", "FO": "Faroe Islands", "FR": "France",
    "GB": "United Kingdom", "GE": "Georgia", "GG": "Guernsey",
    "GR": "Greece", "HR": "Croatia", "HU": "Hungary", "IE": "Ireland",
    "IM": "Isle of Man", "IS": "Iceland", "IT": "Italy", "JE": "Jersey",
    "KZ": "Kazakhstan", "LI": "Liechtenstein", "LT": "Lithuania",
    "LU": "Luxembourg", "LV": "Latvia", "MC": "Monaco", "MD": "Moldova",
    "ME": "Montenegro", "MK": "North Macedonia", "MT": "Malta",
    "NL": "Netherlands", "NO": "Norway", "PL": "Poland", "PT": "Portugal",
    "RO": "Romania", "RS": "Serbia", "RU": "Russia", "SE": "Sweden",
    "SI": "Slovenia", "SJ": "Svalbard and Jan Mayen", "SK": "Slovakia",
    "SM": "San Marino", "TR": "Türkiye", "UA": "Ukraine",
    "VA": "Vatican City", "XK": "Kosovo",
}

# Used only when a live homepage check cannot identify a language.
# Multiple official/national languages are separated by "|".
COUNTRY_LANGUAGES = {
    "AD": ("ca", "Catalan"),
    "AL": ("sq", "Albanian"),
    "AM": ("hy", "Armenian"),
    "AT": ("de", "German"),
    "AZ": ("az", "Azerbaijani"),
    "BA": ("bs|hr|sr", "Bosnian|Croatian|Serbian"),
    "BE": ("nl|fr|de", "Dutch|French|German"),
    "BG": ("bg", "Bulgarian"),
    "BY": ("be|ru", "Belarusian|Russian"),
    "CH": ("de|fr|it|rm", "German|French|Italian|Romansh"),
    "CY": ("el|tr", "Greek|Turkish"),
    "CZ": ("cs", "Czech"),
    "DE": ("de", "German"),
    "DK": ("da", "Danish"),
    "EE": ("et", "Estonian"),
    "ES": ("es|ca|eu|gl", "Spanish|Catalan|Basque|Galician"),
    "FI": ("fi|sv", "Finnish|Swedish"),
    "FO": ("fo", "Faroese"),
    "FR": ("fr", "French"),
    "GB": ("en|cy|gd", "English|Welsh|Scottish Gaelic"),
    "GE": ("ka", "Georgian"),
    "GG": ("en|fr", "English|French"),
    "GR": ("el", "Greek"),
    "HR": ("hr", "Croatian"),
    "HU": ("hu", "Hungarian"),
    "IE": ("en|ga", "English|Irish"),
    "IM": ("en|gv", "English|Manx"),
    "IS": ("is", "Icelandic"),
    "IT": ("it", "Italian"),
    "JE": ("en|fr", "English|French"),
    "KZ": ("kk|ru", "Kazakh|Russian"),
    "LI": ("de", "German"),
    "LT": ("lt", "Lithuanian"),
    "LU": ("lb|fr|de", "Luxembourgish|French|German"),
    "LV": ("lv", "Latvian"),
    "MC": ("fr", "French"),
    "MD": ("ro", "Romanian"),
    "ME": ("cnr|sr|bs|sq|hr", "Montenegrin|Serbian|Bosnian|Albanian|Croatian"),
    "MK": ("mk|sq", "Macedonian|Albanian"),
    "MT": ("mt|en", "Maltese|English"),
    "NL": ("nl", "Dutch"),
    "NO": ("nb|nn|se", "Norwegian Bokmål|Norwegian Nynorsk|Northern Sami"),
    "PL": ("pl", "Polish"),
    "PT": ("pt", "Portuguese"),
    "RO": ("ro", "Romanian"),
    "RS": ("sr", "Serbian"),
    "RU": ("ru", "Russian"),
    "SE": ("sv", "Swedish"),
    "SI": ("sl", "Slovenian"),
    "SJ": ("nb", "Norwegian Bokmål"),
    "SK": ("sk", "Slovak"),
    "SM": ("it", "Italian"),
    "TR": ("tr", "Turkish"),
    "UA": ("uk", "Ukrainian"),
    "VA": ("it|la", "Italian|Latin"),
    "XK": ("sq|sr", "Albanian|Serbian"),
}

LANGUAGE_NAMES = {
    "az": "Azerbaijani", "be": "Belarusian", "bg": "Bulgarian",
    "bs": "Bosnian", "ca": "Catalan", "cnr": "Montenegrin", "cs": "Czech",
    "cy": "Welsh", "da": "Danish", "de": "German", "el": "Greek",
    "en": "English", "es": "Spanish", "et": "Estonian", "eu": "Basque",
    "fi": "Finnish", "fo": "Faroese", "fr": "French", "ga": "Irish",
    "gd": "Scottish Gaelic", "gl": "Galician", "gv": "Manx",
    "hr": "Croatian", "hu": "Hungarian", "hy": "Armenian",
    "is": "Icelandic", "it": "Italian", "ka": "Georgian", "kk": "Kazakh",
    "la": "Latin", "lb": "Luxembourgish", "lt": "Lithuanian",
    "lv": "Latvian", "mk": "Macedonian", "mt": "Maltese", "nb": "Norwegian Bokmål",
    "nl": "Dutch", "nn": "Norwegian Nynorsk", "pl": "Polish",
    "pt": "Portuguese", "rm": "Romansh", "ro": "Romanian",
    "ru": "Russian", "se": "Northern Sami", "sk": "Slovak",
    "sl": "Slovenian", "sq": "Albanian", "sr": "Serbian",
    "sv": "Swedish", "tr": "Turkish", "uk": "Ukrainian",
}

# A compact set of official or well-established European funding/call portals.
# These also ensure coverage for countries with few or no ROR funder records.
SUPPLEMENTAL_SITES = [
    ("funding-tenders.ec.europa.eu", "https://funding-tenders.ec.europa.eu/", "European Union", "EU", "en", "English", "EU funding portal"),
    ("cascadefunding.eu", "https://cascadefunding.eu/", "European Union", "EU", "en", "English", "cascade funding portal"),
    ("eureka-network.org", "https://eureka-network.org/", "European Union", "EU", "en", "English", "innovation funding network"),
    ("interregeurope.eu", "https://www.interregeurope.eu/", "European Union", "EU", "en", "English", "territorial cooperation programme"),
    ("eeagrants.org", "https://eeagrants.org/", "European Economic Area", "EU", "en", "English", "EEA grants portal"),
    ("govern.ad", "https://www.govern.ad/", "Andorra", "AD", "ca", "Catalan", "government portal"),
    ("gov.gg", "https://www.gov.gg/", "Guernsey", "GG", "en", "English", "government portal"),
    ("fondzainovacije.me", "https://fondzainovacije.me/", "Montenegro", "ME", "cnr", "Montenegrin", "innovation fund"),
    ("fitr.mk", "https://fitr.mk/", "North Macedonia", "MK", "mk|sq", "Macedonian|Albanian", "innovation fund"),
    ("vatican.va", "https://www.vatican.va/", "Vatican City", "VA", "it|la", "Italian|Latin", "government portal"),
]

# Common multi-label public suffixes needed for European institutional domains.
# This is intentionally conservative; ROR's own "domains" value is preferred.
MULTI_LABEL_SUFFIXES = {
    "ac.at", "ac.be", "ac.cy", "ac.il", "ac.rs", "ac.uk",
    "co.at", "co.hu", "co.it", "co.me", "co.nl", "co.no", "co.rs", "co.uk",
    "edu.al", "edu.am", "edu.az", "edu.ba", "edu.ge", "edu.gr", "edu.it",
    "edu.mk", "edu.mt", "edu.pl", "edu.rs", "edu.tr", "edu.ua",
    "gov.al", "gov.am", "gov.az", "gov.ba", "gov.cy", "gov.ge", "gov.gr",
    "gov.hu", "gov.ie", "gov.it", "gov.me", "gov.md", "gov.mk", "gov.mt",
    "gov.pl", "gov.pt", "gov.ro", "gov.rs", "gov.si", "gov.sk", "gov.tr",
    "gov.ua", "gov.uk",
    "org.al", "org.ba", "org.cy", "org.es", "org.ge", "org.gr", "org.pl",
    "org.pt", "org.rs", "org.tr", "org.uk",
}

OUTPUT_FIELDS = [
    "domain", "country", "language", "last_update",
    "website_url", "organization", "country_code", "language_code",
    "last_update_basis",
    "ror_record_last_modified", "website_last_modified", "last_checked_utc",
    "http_status", "reachable", "source_category", "ror_id", "ror_types",
    "source_record", "relevance_note",
]


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr, flush=True)


def request(url: str, *, timeout: float, method: str = "GET") -> urllib.response.addinfourl:
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
            "Accept-Language": "en,*;q=0.5",
        },
    )
    context = ssl.create_default_context()
    return urllib.request.urlopen(req, timeout=timeout, context=context)


def download_latest_ror(cache_dir: Path, timeout: float) -> tuple[Path, dict]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    with request(ZENODO_CONCEPT_API, timeout=timeout) as response:
        metadata = json.load(response)

    files = metadata.get("files", [])
    zip_items = [item for item in files if str(item.get("key", "")).endswith(".zip")]
    if not zip_items:
        raise RuntimeError("Latest ROR Zenodo record does not contain a ZIP data dump")
    item = zip_items[0]
    filename = Path(item["key"]).name
    destination = cache_dir / filename
    expected_size = int(item.get("size") or 0)
    checksum = str(item.get("checksum") or "")
    if destination.exists() and (
        (expected_size and destination.stat().st_size == expected_size)
        and verify_checksum(destination, checksum)
    ):
        return destination, metadata

    url = item.get("links", {}).get("self") or item.get("links", {}).get("content")
    if not url:
        raise RuntimeError("ROR data dump download URL is missing")
    temporary = destination.with_suffix(destination.suffix + ".part")
    eprint(f"Downloading {filename} ...")
    with request(url, timeout=max(timeout, 120)) as response, temporary.open("wb") as out:
        shutil.copyfileobj(response, out, length=1024 * 1024)
    if expected_size and temporary.stat().st_size != expected_size:
        raise RuntimeError(
            f"Incomplete ROR download: got {temporary.stat().st_size}, expected {expected_size}"
        )
    if not verify_checksum(temporary, checksum):
        raise RuntimeError("ROR data dump checksum verification failed")
    temporary.replace(destination)
    return destination, metadata


def verify_checksum(path: Path, checksum: str) -> bool:
    if not checksum:
        return True
    if ":" in checksum:
        algorithm, expected = checksum.split(":", 1)
    else:
        algorithm, expected = "md5", checksum
    try:
        digest = hashlib.new(algorithm)
    except ValueError:
        return True
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().lower() == expected.lower()


def find_csv_member(archive: zipfile.ZipFile) -> str:
    members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
    if not members:
        raise RuntimeError("ROR archive contains no CSV file")
    return sorted(members)[-1]


def split_multi(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(";") if part.strip()]


def first_url(value: str) -> str:
    for candidate in split_multi(value):
        if urllib.parse.urlsplit(candidate).hostname:
            return candidate
    return ""


def normalize_host(value: str) -> str:
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    host = (urllib.parse.urlsplit(value).hostname or "").strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return host


def registrable_domain(host: str) -> str:
    host = normalize_host(host)
    if not host or re.fullmatch(r"\d+(?:\.\d+){3}", host):
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    suffix2 = ".".join(labels[-2:])
    if suffix2 in MULTI_LABEL_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return suffix2


def display_name(row: dict[str, str]) -> str:
    return (
        row.get("names.types.ror_display", "").strip()
        or row.get("names.types.label", "").split(";")[0].strip()
        or row.get("names.types.alias", "").split(";")[0].strip()
    )


def language_fallback(country_code: str, ror_language: str = "") -> tuple[str, str, str]:
    fallback_code, fallback_name = COUNTRY_LANGUAGES.get(
        country_code, ("en", "English")
    )
    return fallback_code, fallback_name, "country_official_languages"


def iter_ror_candidates(zip_path: Path) -> Iterator[dict[str, str]]:
    with zipfile.ZipFile(zip_path) as archive:
        member = find_csv_member(archive)
        with archive.open(member) as binary:
            text_stream = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
            for row in csv.DictReader(text_stream):
                country_code = row.get(
                    "locations.geonames_details.country_code", ""
                ).strip()
                if country_code not in EUROPE_SCOPE:
                    continue
                if row.get("status", "").strip().lower() != "active":
                    continue
                types = {x.strip().lower() for x in split_multi(row.get("types", ""))}
                if "funder" not in types:
                    continue
                website_url = first_url(row.get("links.type.website", ""))
                if not website_url:
                    continue

                ror_domains = split_multi(row.get("domains", ""))
                host = normalize_host(ror_domains[0] if ror_domains else website_url)
                domain = registrable_domain(host)
                if not domain:
                    continue
                language_code, language, language_basis = language_fallback(
                    country_code, row.get("ror_display_lang", "")
                )
                country = (
                    row.get("locations.geonames_details.country_name", "").strip()
                    or COUNTRY_NAMES.get(country_code, country_code)
                )
                record_modified = row.get("admin.last_modified.date", "").strip()
                yield {
                    "domain": domain,
                    "website_url": website_url,
                    "organization": display_name(row),
                    "country": country,
                    "country_code": country_code,
                    "language": language,
                    "language_code": language_code,
                    "language_basis": language_basis,
                    "last_update": record_modified,
                    "last_update_basis": "ror_record_last_modified",
                    "ror_record_last_modified": record_modified,
                    "website_last_modified": "",
                    "last_checked_utc": "",
                    "http_status": "",
                    "reachable": "not_checked",
                    "source_category": "research_funder",
                    "ror_id": row.get("id", "").strip(),
                    "ror_types": row.get("types", "").strip(),
                    "source_record": row.get("id", "").strip(),
                    "relevance_note": (
                        "Active organization classified as a funder in ROR; "
                        "verify each call's external eligibility before applying."
                    ),
                }


def supplemental_candidates(release_date: str) -> Iterator[dict[str, str]]:
    for domain, url, country, code, lang_code, language, category in SUPPLEMENTAL_SITES:
        yield {
            "domain": registrable_domain(domain),
            "website_url": url,
            "organization": domain,
            "country": country,
            "country_code": code,
            "language": language,
            "language_code": lang_code,
            "language_basis": "curated_portal_language",
            "last_update": release_date,
            "last_update_basis": "directory_release_date",
            "ror_record_last_modified": "",
            "website_last_modified": "",
            "last_checked_utc": "",
            "http_status": "",
            "reachable": "not_checked",
            "source_category": category,
            "ror_id": "",
            "ror_types": "",
            "source_record": "curated supplemental seed",
            "relevance_note": (
                "Curated European funding, challenge, programme, or government portal."
            ),
        }


def deduplicate(candidates: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    selected: dict[str, dict[str, str]] = {}
    for item in candidates:
        domain = item["domain"]
        existing = selected.get(domain)
        if existing is None:
            selected[domain] = item
            continue
        # Prefer curated portals, then the most recently updated ROR record.
        existing_curated = existing["source_record"] == "curated supplemental seed"
        item_curated = item["source_record"] == "curated supplemental seed"
        if item_curated and not existing_curated:
            selected[domain] = item
        elif item_curated == existing_curated and item["last_update"] > existing["last_update"]:
            selected[domain] = item
    return list(selected.values())


def select_balanced(
    candidates: list[dict[str, str]], target: int, max_per_country: int
) -> list[dict[str, str]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for item in candidates:
        groups[item["country_code"]].append(item)
    for items in groups.values():
        items.sort(
            key=lambda item: (
                item["last_update"],
                item["source_record"] == "curated supplemental seed",
                item["domain"],
            ),
            reverse=True,
        )

    order = sorted(
        groups,
        key=lambda code: (code != "EU", COUNTRY_NAMES.get(code, code), code),
    )
    output: list[dict[str, str]] = []
    used = Counter()
    level = 0
    while len(output) < target:
        added = False
        for code in order:
            if used[code] >= max_per_country:
                continue
            items = groups[code]
            if level < len(items):
                output.append(items[level])
                used[code] += 1
                added = True
                if len(output) == target:
                    break
        if not added:
            break
        level += 1
    if len(output) < target:
        raise RuntimeError(
            f"Only {len(output)} unique balanced domains available; "
            f"increase --max-per-country or reduce --target"
        )
    return output


def normalize_date(value: str) -> str:
    value = html.unescape((value or "").strip())
    if not value:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        return parsed.date().isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    match = re.search(r"(19|20)\d{2}-\d{2}-\d{2}", value)
    if match:
        return match.group(0)
    match = re.search(r"(19|20)\d{2}", value)
    return match.group(0) if match else ""


def normalize_language(value: str) -> tuple[str, str]:
    value = html.unescape((value or "").strip()).lower().replace("_", "-")
    if not value:
        return "", ""
    code = value.split(",", 1)[0].split(";", 1)[0].split("-", 1)[0].strip()
    if code in LANGUAGE_NAMES:
        return code, LANGUAGE_NAMES[code]
    return "", ""


HTML_LANG_RE = re.compile(
    r"<html\b[^>]*\blang\s*=\s*[\"']?\s*([A-Za-z]{2,3}(?:-[A-Za-z0-9]+)?)",
    re.IGNORECASE,
)
META_LANG_RE = re.compile(
    r"<meta\b[^>]*(?:http-equiv\s*=\s*[\"']?content-language[\"']?[^>]*"
    r"content\s*=\s*[\"']([^\"']+)|content\s*=\s*[\"']([^\"']+)[\"'][^>]*"
    r"http-equiv\s*=\s*[\"']?content-language)",
    re.IGNORECASE,
)
LASTMOD_RE = re.compile(r"<lastmod>\s*([^<]+)\s*</lastmod>", re.IGNORECASE)


def decode_html(data: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset=([A-Za-z0-9._-]+)", content_type or "", re.I)
    charsets = [charset_match.group(1)] if charset_match else []
    charsets.extend(["utf-8", "windows-1252"])
    for charset in charsets:
        try:
            return data.decode(charset)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def read_limited(response: urllib.response.addinfourl, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        block = response.read(min(65536, remaining))
        if not block:
            break
        chunks.append(block)
        remaining -= len(block)
    return b"".join(chunks)


def site_check(item: dict[str, str], timeout: float) -> dict[str, str]:
    result = dict(item)
    checked = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    result["last_checked_utc"] = checked
    url = item["website_url"]
    final_url = url
    homepage_text = ""
    content_language = ""
    http_last_modified = ""

    try:
        with request(url, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            result["http_status"] = str(status)
            result["reachable"] = "yes" if status < 500 else "no"
            final_url = response.geturl() or url
            result["website_url"] = final_url
            content_language = response.headers.get("Content-Language", "")
            http_last_modified = normalize_date(
                response.headers.get("Last-Modified", "")
            )
            data = read_limited(response, 512 * 1024)
            homepage_text = decode_html(
                data, response.headers.get("Content-Type", "")
            )
    except urllib.error.HTTPError as exc:
        result["http_status"] = str(exc.code)
        # Any HTTP response proves that the host was reachable, even when the
        # requested homepage rejects the checker or is temporarily unavailable.
        result["reachable"] = "yes"
        content_language = exc.headers.get("Content-Language", "")
        http_last_modified = normalize_date(exc.headers.get("Last-Modified", ""))
    except Exception as exc:  # network errors are recorded, not fatal
        # A failed check is not evidence that the public site is down; corporate
        # proxies, DNS policy, TLS policy, and temporary routing can all cause it.
        result["reachable"] = "unknown"
        result["http_status"] = f"{type(exc).__name__}"

    detected_code, detected_name = normalize_language(content_language)
    if not detected_code and homepage_text:
        match = HTML_LANG_RE.search(homepage_text)
        if match:
            detected_code, detected_name = normalize_language(match.group(1))
        if not detected_code:
            match = META_LANG_RE.search(homepage_text)
            if match:
                detected_code, detected_name = normalize_language(
                    match.group(1) or match.group(2)
                )
    if detected_code:
        result["language_code"] = detected_code
        result["language"] = detected_name
        result["language_basis"] = "website_html_or_http"

    website_date = http_last_modified
    basis = "http_last_modified" if website_date else ""
    if not website_date and result["reachable"] == "yes":
        split = urllib.parse.urlsplit(final_url)
        sitemap_url = urllib.parse.urlunsplit(
            (split.scheme or "https", split.netloc, "/sitemap.xml", "", "")
        )
        try:
            with request(sitemap_url, timeout=timeout) as response:
                xml = decode_html(
                    read_limited(response, 2 * 1024 * 1024),
                    response.headers.get("Content-Type", ""),
                )
            dates = [normalize_date(x) for x in LASTMOD_RE.findall(xml)]
            dates = [x for x in dates if x]
            if dates:
                website_date = max(dates)
                basis = "sitemap_lastmod"
        except Exception:
            pass

    if website_date:
        result["website_last_modified"] = website_date
        result["last_update"] = website_date
        result["last_update_basis"] = basis
    return result


def enrich_sites(
    rows: list[dict[str, str]], workers: int, timeout: float
) -> list[dict[str, str]]:
    eprint(f"Checking {len(rows)} websites with {workers} workers ...")
    output: list[dict[str, str] | None] = [None] * len(rows)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(site_check, row, timeout): index
            for index, row in enumerate(rows)
        }
        completed = 0
        for future in as_completed(future_map):
            index = future_map[future]
            try:
                output[index] = future.result()
            except Exception as exc:
                failed = dict(rows[index])
                failed["reachable"] = "unknown"
                failed["http_status"] = type(exc).__name__
                failed["last_checked_utc"] = (
                    dt.datetime.now(dt.timezone.utc)
                    .replace(microsecond=0)
                    .isoformat()
                )
                output[index] = failed
            completed += 1
            if completed % 100 == 0 or completed == len(rows):
                eprint(f"  checked {completed}/{len(rows)}")
    return [row for row in output if row is not None]


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def validate_output(rows: list[dict[str, str]], target: int) -> dict:
    domains = [row["domain"] for row in rows]
    countries = Counter(row["country_code"] for row in rows)
    languages = Counter()
    for row in rows:
        languages.update(x for x in row["language_code"].split("|") if x)
    invalid_domains = [
        domain
        for domain in domains
        if not domain
        or " " in domain
        or "." not in domain
        or domain != domain.lower()
    ]
    report = {
        "rows": len(rows),
        "unique_domains": len(set(domains)),
        "countries_or_regions": len(countries),
        "country_codes": dict(sorted(countries.items())),
        "language_codes": dict(sorted(languages.items())),
        "reachability": dict(sorted(Counter(row["reachable"] for row in rows).items())),
        "last_update_basis": dict(
            sorted(Counter(row["last_update_basis"] for row in rows).items())
        ),
        "website_last_modified_dates": sum(
            bool(row["website_last_modified"]) for row in rows
        ),
        "invalid_domains": invalid_domains,
        "duplicate_domains": len(domains) - len(set(domains)),
    }
    if report["rows"] < target:
        raise RuntimeError(f"Expected at least {target} rows, got {report['rows']}")
    if report["duplicate_domains"]:
        raise RuntimeError("Output contains duplicate domains")
    if invalid_domains:
        raise RuntimeError(f"Output contains {len(invalid_domains)} invalid domains")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a CSV of unique European funder websites from ROR."
    )
    parser.add_argument(
        "--output", type=Path, default=Path("eu_funding_sources_1200.csv")
    )
    parser.add_argument(
        "--report", type=Path, default=Path("eu_funding_sources_validation.json")
    )
    parser.add_argument("--ror-zip", type=Path, help="Use a local ROR ZIP data dump")
    parser.add_argument(
        "--cache-dir", type=Path, default=Path(".cache/ror"), help="Download cache"
    )
    parser.add_argument("--target", type=int, default=1200)
    parser.add_argument("--max-per-country", type=int, default=70)
    parser.add_argument(
        "--check-sites",
        action="store_true",
        help="Check live language, reachability, and website update metadata",
    )
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--timeout", type=float, default=8.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.target < 1000:
        eprint("Warning: target is below the requested 1,000-domain baseline")
    release_metadata: dict = {}
    if args.ror_zip:
        ror_zip = args.ror_zip
    else:
        ror_zip, release_metadata = download_latest_ror(args.cache_dir, args.timeout)
    if not ror_zip.is_file():
        raise FileNotFoundError(ror_zip)

    filename_match = re.search(
        r"(v\d+(?:\.\d+)?)-((?:19|20)\d{2}-\d{2}-\d{2})", ror_zip.name
    )
    inferred_version = filename_match.group(1) if filename_match else ""
    inferred_date = filename_match.group(2) if filename_match else ""
    publication_date = (
        release_metadata.get("metadata", {}).get("publication_date")
        or inferred_date
        or dt.date.today().isoformat()
    )
    candidates = deduplicate(
        list(iter_ror_candidates(ror_zip))
        + list(supplemental_candidates(publication_date))
    )
    eprint(f"Eligible unique domains before balancing: {len(candidates)}")
    rows = select_balanced(candidates, args.target, args.max_per_country)
    if args.check_sites:
        rows = enrich_sites(rows, max(1, args.workers), max(1.0, args.timeout))
    rows.sort(key=lambda row: (row["country"], row["organization"], row["domain"]))

    report = validate_output(rows, args.target)
    report.update(
        {
            "generated_at_utc": dt.datetime.now(dt.timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "ror_release": (
                release_metadata.get("metadata", {}).get("version", "")
                or inferred_version
            ),
            "ror_publication_date": publication_date,
            "source_archive": ror_zip.name,
            "site_checks_enabled": bool(args.check_sites),
        }
    )
    write_csv(args.output, rows)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    eprint(
        f"Wrote {report['rows']} rows / {report['unique_domains']} unique domains "
        f"across {report['countries_or_regions']} countries or regions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
