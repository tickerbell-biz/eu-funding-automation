#!/usr/bin/env python3
"""
Find funding opportunities that match a project description.

The crawler reads the European funding-source directory produced by
build_eu_funding_sources.py, discovers likely call pages through robots.txt,
sitemaps, and on-page links, and writes evidence-backed candidate matches.

It deliberately does not claim eligibility. Every result keeps the live URL,
evidence excerpt, discovery path, and check time so a human can verify it.
No third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import html
import io
import json
import re
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Iterator, Sequence

try:
    from pypdf import PdfReader  # type: ignore[import-not-found]
except ImportError:  # HTML crawling remains fully functional.
    PdfReader = None


USER_AGENT = (
    "TickerBell-EU-Funding-Finder/1.0 "
    "(public-interest grant discovery; contact: admin@tickerbell.biz)"
)
DEFAULT_TIMEOUT = 12.0
MAX_RESPONSE_BYTES = 4_000_000

# These terms are used only to discover likely call pages. The list covers the
# languages represented in the source directory; it is not used to infer
# eligibility.
FUNDING_TERMS = (
    "funding", "fund", "grant", "call for proposal", "call for projects",
    "open call", "fellowship", "scholarship", "award", "prize", "challenge",
    "competition", "subsidy", "tender",
    "financement", "appel à projets", "appel a projets", "subvention", "bourse",
    "concours",
    "förderung", "foerderung", "ausschreibung", "stipendium", "zuschuss",
    "wettbewerb",
    "finanziamento", "bando", "contributo", "borsa", "concorso",
    "financiación", "financiacion", "convocatoria", "subvención", "subvencion",
    "beca",
    "financiamento", "concurso", "aviso", "bolsa", "subsídio", "subsidio",
    "financiering", "subsidie", "oproep", "beurs",
    "finansowanie", "nabór", "nabor", "stypendium",
    "financování", "financovani", "výzva", "vyzva", "dotace",
    "financovanie", "dotácia", "dotacia", "štipendium",
    "pályázat", "palyazat", "támogatás", "tamogatas", "ösztöndíj",
    "finanțare", "finantare", "apel", "bursă", "bursa",
    "финансиране", "покана", "стипендия",
    "χρηματοδότηση", "πρόσκληση", "επιχορήγηση", "υποτροφία",
    "financiranje", "poziv", "natječaj", "natjecaj", "potpora", "stipendija",
    "финансирање", "позив", "конкурс", "razpis", "nepovratna sredstva",
    "finansiering", "utlysning", "bidrag", "tilskudd", "stipend",
    "opslag", "rahoitus", "hakukierros", "avustus", "apuraha",
    "rahastus", "taotlusvoor", "toetus",
    "finansējums", "finansejums", "konkurss",
    "finansavimas", "kvietimas", "dotacija",
    "maoiniú", "maoiniu", "deontas", "cyllid", "galwad",
    "finanzjament", "sejħa", "sejha", "għotja", "ghotja",
    "fjármögnun", "fjarmognun", "úthlutun", "uthlutun", "styrkur",
    "financim", "thirrje", "bursë", "burse",
    "финансирање", "повик", "грант", "стипендија",
    "фінансування", "конкурс", "стипендія",
    "finansman", "çağrı", "cagri", "hibe", "burs",
    "დაფინანსება", "კონკურსი", "გრანტი",
    "ֆինանսավորում", "դրամաշնորհ", "մրցույթ",
    "maliyyələşdirmə", "maliyyelesdirme", "çağırış", "cagiris", "qrant",
    "фінансаванне", "финансирование", "стипендия",
    "finançament", "financament", "convocatòria", "convocatoria",
    "dirulaguntza", "deialdia", "beka",
    "finanzéierung", "finanzeierung", "ausschreiwung", "subventioun",
    "fígging", "figging", "stuðul", "studul",
    "қаржыландыру", "байқау",
)

DEADLINE_LABELS = (
    "deadline", "closing date", "closes", "apply by", "submission date",
    "apply until", "open until",
    "date limite", "clôture", "cloture", "frist", "bewerbungsschluss",
    "scadenza", "fecha límite", "fecha limite", "prazo", "sluitingsdatum",
    "termin", "nabór do", "uzávěrka", "uzavierka", "határidő", "hatarido",
    "termen limită", "termen limita", "краен срок", "προθεσμία",
    "rok prijave", "rok za prijavu", "sista ansökningsdag", "søknadsfrist",
    "ansøgningsfrist", "hakuaika", "tähtaeg", "pieteikšanās termiņš",
    "paraiškų teikimo terminas", "spriocdháta", "dyddiad cau",
    "data tal-għeluq", "umsóknarfrestur", "afati", "краен рок",
    "кінцевий термін", "son başvuru", "son basvuru", "срок подачи",
)

OPEN_TERMS = (
    "open call", "applications open", "now open", "apply now", "accepting applications",
    "appel ouvert", "ouvert", "offen", "aperto", "aperta", "abierta", "abierto",
    "otwarty", "nyitott", "deschis", "отворен", "ανοιχτή", "otvoren",
)
CLOSED_TERMS = (
    "call closed", "applications closed", "closed for applications", "expired",
    "appel clos", "clôturé", "geschlossen", "chiuso", "cerrada", "cerrado",
    "zamknięty", "lezárt", "închis", "затворен", "κλειστή", "zatvoren",
)

MANPOWER_DIRECT_TERMS = (
    "postdoctoral fellowship", "post-doctoral fellowship", "postdoc fellowship",
    "doctoral fellowship", "doctoral candidate", "doctoral network",
    "phd scholarship", "research fellowship", "researcher salary",
    "salary and mobility allowance", "living allowance", "employment contract",
    "personnel costs", "staff costs", "hire researchers", "recruit researchers",
    "bourse postdoctorale", "bourse doctorale", "postdoktorandenstipendium",
    "doktorandenstipendium", "assegno di ricerca", "beca postdoctoral",
    "beca doctoral", "bolsa de pós-doutoramento", "bolsa de doutoramento",
    "stypendium podoktorskie", "stypendium doktoranckie",
    "posztdoktori ösztöndíj", "doktori ösztöndíj",
)
MANPOWER_MOBILITY_TERMS = (
    "staff exchange", "secondment", "research mobility", "mobility grant",
    "short-term scientific mission", "visiting researcher", "research stay",
    "exchange of staff", "mobility allowance", "detachment", "détachement",
)
TEAM_GRANT_TERMS = (
    "research team", "project team", "personnel costs eligible", "staff costs eligible",
    "salaries are eligible", "salary costs", "human resources costs",
)

SCHEME_WATCHLIST = (
    {
        "family": "MSCA Postdoctoral Fellowships",
        "aliases": ("msca postdoctoral", "marie skłodowska-curie postdoctoral"),
        "mechanism": "direct_salary_or_fellowship",
        "introduction": (
            "Funds a named postdoctoral researcher with a host, including training "
            "and international/inter-sectoral mobility."
        ),
    },
    {
        "family": "MSCA Doctoral Networks",
        "aliases": ("msca doctoral network", "marie skłodowska-curie doctoral network"),
        "mechanism": "doctoral_recruitment",
        "introduction": (
            "Funds consortia that recruit and train doctoral candidates through "
            "international and cross-sector doctoral programmes."
        ),
    },
    {
        "family": "MSCA Staff Exchanges",
        "aliases": ("msca staff exchange", "marie skłodowska-curie staff exchange"),
        "mechanism": "staff_mobility_or_secondment",
        "introduction": (
            "Funds short-term international and inter-sectoral exchanges of R&I staff."
        ),
    },
    {
        "family": "MSCA COFUND",
        "aliases": ("msca cofund", "co-fund doctoral", "cofund doctoral"),
        "mechanism": "cofunded_recruitment_programme",
        "introduction": (
            "Co-funds institutional doctoral or postdoctoral programmes that recruit researchers."
        ),
    },
    {
        "family": "ERA Fellowships",
        "aliases": ("era fellowship", "era fellowships"),
        "mechanism": "direct_salary_or_fellowship",
        "introduction": (
            "Supports eligible high-quality MSCA Postdoctoral Fellowship proposals "
            "hosted in Widening Countries."
        ),
    },
    {
        "family": "European Research Council",
        "aliases": (
            "erc starting grant", "erc consolidator grant", "erc advanced grant",
            "erc synergy grant", "european research council grant",
        ),
        "mechanism": "team_building_research_grant",
        "introduction": (
            "Frontier-research grants led by a principal investigator; funded projects "
            "can recruit research-team members."
        ),
    },
    {
        "family": "COST Actions",
        "aliases": ("cost action", "cost open call"),
        "mechanism": "networking_and_short_term_mobility_only",
        "introduction": (
            "Funds networking, training and short-term mobility rather than research salaries."
        ),
    },
)

AMOUNT_RE = re.compile(
    r"(?P<currency>€|eur|euro|£|gbp|chf|nok|sek|dkk|pln|huf|czk|ron)"
    r"\s*(?P<number>\d[\d\s.,]*)\s*(?P<scale>million|mio|m|thousand|k)?",
    re.IGNORECASE,
)
ISO_DATE_RE = re.compile(r"\b(20\d{2})[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])\b")
EU_DATE_RE = re.compile(r"\b(0?[1-9]|[12]\d|3[01])[-/.](0?[1-9]|1[0-2])[-/.](20\d{2})\b")
EN_MONTH_DATE_RE = re.compile(
    r"\b(0?[1-9]|[12]\d|3[01])\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(20\d{2})\b",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[^\W\d_][^\W_]{2,}", re.UNICODE)

STOPWORDS = {
    "about", "after", "again", "also", "among", "and", "are", "based", "been",
    "before", "being", "between", "both", "can", "could", "develop", "development",
    "each", "for", "from", "have", "into", "more", "other", "our", "project", "research",
    "should", "such", "than", "that", "the", "their", "them", "then", "there",
    "these", "they", "this", "through", "using", "will", "with", "within", "would",
}

OUTPUT_FIELDS = (
    "rank", "relevance_score", "opportunity_type", "personnel_support",
    "programme_family", "programme_explanation", "title", "funder",
    "source_domain", "country", "language", "status", "deadline",
    "amount", "matched_project_terms", "personnel_evidence", "evidence_excerpt",
    "url", "discovered_from", "checked_at_utc",
)


def eprint(*values: object) -> None:
    print(*values, file=sys.stderr, flush=True)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def normalized(text: str) -> str:
    value = unicodedata.normalize("NFKC", html.unescape(text or ""))
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def folded(text: str) -> str:
    return normalized(text).casefold()


def truncate(text: str, size: int) -> str:
    text = normalized(text)
    return text if len(text) <= size else text[: size - 1].rstrip() + "…"


@dataclass
class ProjectProfile:
    name: str
    summary: str
    objectives: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    applicant_types: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    personnel_roles: list[str] = field(default_factory=list)
    preferred_funding_types: list[str] = field(default_factory=list)
    target_trl_start: int | None = None
    target_trl_end: int | None = None
    duration_months: int | None = None

    @classmethod
    def load(cls, path: Path) -> "ProjectProfile":
        raw = path.read_text(encoding="utf-8-sig")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            words = [
                word for word, _ in Counter(
                    token.casefold() for token in WORD_RE.findall(raw)
                    if token.casefold() not in STOPWORDS
                ).most_common(35)
            ]
            return cls(name=path.stem, summary=raw, keywords=words)

        required = ("name", "summary")
        missing = [key for key in required if not str(data.get(key, "")).strip()]
        if missing:
            raise ValueError(f"Project JSON is missing required field(s): {', '.join(missing)}")
        needs = data.get("needs") or {}
        return cls(
            name=str(data["name"]),
            summary=str(data["summary"]),
            objectives=_strings(data.get("objectives")),
            keywords=_strings(data.get("keywords")),
            technologies=_strings(data.get("technologies")),
            domains=_strings(data.get("domains")),
            applicant_types=_strings(data.get("applicant_types")),
            countries=_strings(data.get("countries")),
            personnel_roles=_strings(needs.get("personnel_roles") or data.get("personnel_roles")),
            preferred_funding_types=_strings(
                data.get("preferred_funding_types") or needs.get("funding_types")
            ),
            target_trl_start=_optional_int(data.get("target_trl_start")),
            target_trl_end=_optional_int(data.get("target_trl_end")),
            duration_months=_optional_int(data.get("duration_months")),
        )

    def match_terms(self) -> list[str]:
        explicit = self.explicit_match_terms()
        objective_words = [
            token.casefold()
            for text in [self.summary, *self.objectives]
            for token in WORD_RE.findall(text)
            if token.casefold() not in STOPWORDS
        ]
        frequent = [word for word, _ in Counter(objective_words).most_common(45)]
        return _unique([*explicit, *frequent])

    def explicit_match_terms(self) -> list[str]:
        return _unique(
            self.keywords + self.technologies + self.domains + self.personnel_roles
        )


@dataclass
class Source:
    domain: str
    website_url: str
    organization: str
    country: str
    language: str


@dataclass
class Page:
    url: str
    status: int
    content_type: str
    body: bytes
    fetched_at: str


@dataclass
class ParsedPage:
    title: str
    text: str
    links: list[tuple[str, str]]


@dataclass
class Match:
    relevance_score: int
    opportunity_type: str
    personnel_support: str
    programme_family: str
    programme_explanation: str
    title: str
    funder: str
    source_domain: str
    country: str
    language: str
    status: str
    deadline: str
    amount: str
    matched_project_terms: str
    personnel_evidence: str
    evidence_excerpt: str
    url: str
    discovered_from: str
    checked_at_utc: str
    rank: int = 0


class FundingHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._in_title = False
        self._skip_depth = 0
        self._href: str | None = None
        self._anchor_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            values = dict(attrs)
            self._href = values.get("href")
            self._anchor_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._href:
            self.links.append((self._href, normalized(" ".join(self._anchor_parts))))
            self._href = None
            self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        value = normalized(data)
        if not value:
            return
        self.text_parts.append(value)
        if self._in_title:
            self.title_parts.append(value)
        if self._href is not None:
            self._anchor_parts.append(value)


class Fetcher:
    def __init__(self, cache_dir: Path, timeout: float, cache_hours: float) -> None:
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.cache_seconds = cache_hours * 3600
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.context = ssl.create_default_context()

    def fetch(self, url: str, *, accept: str = "text/html,application/xml;q=0.9,*/*;q=0.4") -> Page:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        body_path = self.cache_dir / f"{key}.bin"
        meta_path = self.cache_dir / f"{key}.json"
        if body_path.exists() and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                age = time.time() - float(meta["saved_at_epoch"])
                if age <= self.cache_seconds:
                    return Page(
                        url=str(meta.get("final_url") or url),
                        status=int(meta["status"]),
                        content_type=str(meta.get("content_type") or ""),
                        body=body_path.read_bytes(),
                        fetched_at=str(meta["fetched_at"]),
                    )
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": accept,
                "Accept-Language": "en,*;q=0.5",
                "Accept-Encoding": "gzip",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout, context=self.context) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError(f"Response exceeds {MAX_RESPONSE_BYTES} bytes")
            if response.headers.get("Content-Encoding", "").casefold() == "gzip":
                body = gzip.decompress(body)
            page = Page(
                url=response.geturl(),
                status=int(response.status),
                content_type=response.headers.get_content_type(),
                body=body,
                fetched_at=utc_now(),
            )
        body_path.write_bytes(page.body)
        meta_path.write_text(
            json.dumps(
                {
                    "source_url": url,
                    "final_url": page.url,
                    "status": page.status,
                    "content_type": page.content_type,
                    "fetched_at": page.fetched_at,
                    "saved_at_epoch": time.time(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return page


def _strings(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Sequence):
        return [str(item) for item in value if str(item).strip()]
    raise ValueError(f"Expected a string or list, got {type(value).__name__}")


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = normalized(value)
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return result


def load_sources(path: Path, *, countries: set[str], limit: int | None) -> list[Source]:
    result: list[Source] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            country = normalized(row.get("country", ""))
            code = normalized(row.get("country_code", ""))
            if countries and country.casefold() not in countries and code.casefold() not in countries:
                continue
            domain = normalized(row.get("domain", "")).casefold()
            website = normalized(row.get("website_url", ""))
            if not domain:
                continue
            if not website:
                website = f"https://{domain}/"
            result.append(
                Source(
                    domain=domain,
                    website_url=website,
                    organization=normalized(row.get("organization", "")) or domain,
                    country=country,
                    language=normalized(row.get("language", "")),
                )
            )
            if limit is not None and len(result) >= limit:
                break
    return result


def decode_body(page: Page) -> str:
    sample = page.body[:2000]
    match = re.search(br"charset=['\"]?([A-Za-z0-9._-]+)", sample, re.IGNORECASE)
    candidates = [match.group(1).decode("ascii", "ignore")] if match else []
    candidates.extend(["utf-8", "windows-1252", "latin-1"])
    for encoding in candidates:
        try:
            return page.body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return page.body.decode("utf-8", errors="replace")


def parse_html(page: Page) -> ParsedPage:
    parser = FundingHTMLParser()
    parser.feed(decode_body(page))
    title = normalized(" ".join(parser.title_parts))
    text = normalized(" ".join(parser.text_parts))
    links: list[tuple[str, str]] = []
    for href, anchor in parser.links:
        absolute = urllib.parse.urljoin(page.url, href)
        parsed = urllib.parse.urlsplit(absolute)
        if parsed.scheme in {"http", "https"}:
            clean = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
            links.append((clean, anchor))
    return ParsedPage(title=title, text=text, links=links)


def parse_pdf(page: Page) -> ParsedPage:
    if PdfReader is None:
        raise RuntimeError("PDF call found; install pypdf to extract and score it")
    reader = PdfReader(io.BytesIO(page.body))
    text_parts: list[str] = []
    for pdf_page in reader.pages[:60]:
        text_parts.append(pdf_page.extract_text() or "")
        if sum(len(part) for part in text_parts) >= 120_000:
            break
    metadata = reader.metadata or {}
    title = normalized(str(metadata.get("/Title") or ""))
    if not title:
        title = normalized(
            Path(urllib.parse.urlsplit(page.url).path).name.rsplit(".", 1)[0].replace("-", " ")
        )
    return ParsedPage(title=title, text=normalized(" ".join(text_parts)), links=[])


def robots_policy(fetcher: Fetcher, base_url: str) -> tuple[urllib.robotparser.RobotFileParser, list[str]]:
    parsed = urllib.parse.urlsplit(base_url)
    robots_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    sitemaps: list[str] = []
    try:
        page = fetcher.fetch(robots_url, accept="text/plain,*/*;q=0.2")
        lines = decode_body(page).splitlines()
        parser.parse(lines)
        for line in lines:
            if line.casefold().startswith("sitemap:"):
                value = line.split(":", 1)[1].strip()
                if value.startswith(("http://", "https://")):
                    sitemaps.append(value)
    except Exception:
        parser.parse([])
    return parser, _unique(sitemaps)


def same_source_domain(url: str, source_domain: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").casefold().rstrip(".")
    return host == source_domain or host.endswith("." + source_domain)


def has_funding_signal(text: str) -> bool:
    value = folded(text)
    return any(term in value for term in FUNDING_TERMS)


def likely_call_link(url: str, anchor: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    combined = urllib.parse.unquote(parsed.path + " " + parsed.query + " " + anchor)
    return has_funding_signal(combined)


def sitemap_urls(
    fetcher: Fetcher,
    sitemap_url: str,
    policy: urllib.robotparser.RobotFileParser,
    source_domain: str,
    max_urls: int,
    *,
    depth: int = 0,
) -> list[str]:
    if depth > 1 or not policy.can_fetch(USER_AGENT, sitemap_url):
        return []
    try:
        page = fetcher.fetch(sitemap_url, accept="application/xml,text/xml,*/*;q=0.2")
        body = page.body
        if sitemap_url.casefold().endswith(".gz"):
            body = gzip.decompress(body)
        root = ET.fromstring(body)
    except Exception:
        return []

    locs = [normalized(node.text or "") for node in root.iter() if node.tag.endswith("loc")]
    result: list[str] = []
    is_index = root.tag.endswith("sitemapindex")
    if is_index:
        promising = [url for url in locs if likely_call_link(url, "")]
        selected = promising or locs[:8]
        for child in selected[:8]:
            result.extend(
                sitemap_urls(
                    fetcher, child, policy, source_domain,
                    max_urls=max_urls - len(result), depth=depth + 1,
                )
            )
            if len(result) >= max_urls:
                break
    else:
        for url in locs:
            if (
                same_source_domain(url, source_domain)
                and likely_call_link(url, "")
                and policy.can_fetch(USER_AGENT, url)
            ):
                result.append(url)
                if len(result) >= max_urls:
                    break
    return _unique(result)[:max_urls]


def discover_pages(
    fetcher: Fetcher,
    source: Source,
    max_pages: int,
    max_sitemap_urls: int,
) -> tuple[list[tuple[str, str]], urllib.robotparser.RobotFileParser, list[str]]:
    policy, sitemaps = robots_policy(fetcher, source.website_url)
    errors: list[str] = []
    if not sitemaps:
        base = urllib.parse.urlsplit(source.website_url)
        sitemaps = [
            urllib.parse.urlunsplit((base.scheme, base.netloc, "/sitemap.xml", "", "")),
            urllib.parse.urlunsplit((base.scheme, base.netloc, "/sitemap_index.xml", "", "")),
        ]

    discovered: list[tuple[str, str]] = []
    home_url = source.website_url
    if policy.can_fetch(USER_AGENT, home_url):
        try:
            home = fetcher.fetch(home_url)
            parsed = parse_html(home)
            if has_funding_signal(parsed.title + " " + parsed.text[:15_000]):
                discovered.append((home.url, "homepage"))
            for url, anchor in parsed.links:
                if (
                    same_source_domain(url, source.domain)
                    and likely_call_link(url, anchor)
                    and policy.can_fetch(USER_AGENT, url)
                ):
                    discovered.append((url, "homepage_link"))
        except Exception as error:
            errors.append(f"homepage {type(error).__name__}: {truncate(str(error), 160)}")
            eprint(f"[{source.domain}] homepage: {type(error).__name__}: {error}")

    for sitemap in sitemaps[:4]:
        for url in sitemap_urls(
            fetcher, sitemap, policy, source.domain, max_sitemap_urls
        ):
            discovered.append((url, "sitemap"))
            if len(discovered) >= max_pages * 3:
                break

    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for url, origin in discovered:
        canonical = canonical_url(url)
        if canonical not in seen:
            seen.add(canonical)
            unique.append((url, origin))
        if len(unique) >= max_pages:
            break
    return unique, policy, errors


def canonical_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [
        (key, value) for key, value in query
        if not key.casefold().startswith(("utm_", "fbclid", "gclid"))
    ]
    path = re.sub(r"/+", "/", parsed.path or "/")
    return urllib.parse.urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc.casefold(), path.rstrip("/") or "/",
         urllib.parse.urlencode(query), "")
    )


def find_deadline(text: str) -> str:
    value = normalized(text)
    regions: list[str] = []
    lower = value.casefold()
    for label in DEADLINE_LABELS:
        start = lower.find(label)
        if start >= 0:
            regions.append(value[start:start + 180])
    today = dt.date.today()
    found: list[dt.date] = []
    for region in regions:
        for year, month, day in ISO_DATE_RE.findall(region):
            _append_date(found, int(year), int(month), int(day))
        for day, month, year in EU_DATE_RE.findall(region):
            _append_date(found, int(year), int(month), int(day))
        for day, month_name, year in EN_MONTH_DATE_RE.findall(region):
            month = dt.datetime.strptime(month_name[:3], "%b").month
            _append_date(found, int(year), month, int(day))
        if found:
            future = sorted(date for date in found if date >= today)
            return (future[0] if future else max(found)).isoformat()
    return ""


def _append_date(target: list[dt.date], year: int, month: int, day: int) -> None:
    try:
        target.append(dt.date(year, month, day))
    except ValueError:
        pass


def find_amount(text: str) -> str:
    lower = folded(text)
    regions = []
    for signal in ("funding", "budget", "maximum", "up to", "grant amount", "award"):
        start = lower.find(signal)
        if start >= 0:
            regions.append(text[max(0, start - 80):start + 240])
    regions.append(text[:20_000])
    for region in regions:
        match = AMOUNT_RE.search(region)
        if match:
            return normalized(match.group(0))
    return ""


def status_from(text: str, deadline: str) -> str:
    lower = folded(text)
    if any(term in lower for term in CLOSED_TERMS):
        return "closed"
    if deadline:
        try:
            return "open_or_upcoming" if dt.date.fromisoformat(deadline) >= dt.date.today() else "closed"
        except ValueError:
            pass
    if any(term in lower for term in OPEN_TERMS):
        return "open"
    return "unknown"


def classify_scheme(text: str) -> tuple[str, str, str]:
    lower = folded(text)
    for scheme in SCHEME_WATCHLIST:
        if any(alias in lower for alias in scheme["aliases"]):
            return (
                str(scheme["family"]),
                str(scheme["mechanism"]),
                str(scheme["introduction"]),
            )
    return "", "", ""


def classify_personnel(text: str, scheme_mechanism: str) -> tuple[str, str]:
    lower = folded(text)
    if scheme_mechanism:
        support = scheme_mechanism
    elif any(term in lower for term in MANPOWER_DIRECT_TERMS):
        support = "direct_salary_or_fellowship"
    elif any(term in lower for term in MANPOWER_MOBILITY_TERMS):
        support = "staff_mobility_or_secondment"
    elif any(term in lower for term in TEAM_GRANT_TERMS):
        support = "personnel_costs_likely_eligible"
    else:
        support = "not_identified"
    evidence_terms = [
        term for term in (*MANPOWER_DIRECT_TERMS, *MANPOWER_MOBILITY_TERMS, *TEAM_GRANT_TERMS)
        if term in lower
    ][:5]
    return support, "; ".join(evidence_terms)


def classify_opportunity(text: str, personnel_support: str) -> str:
    lower = folded(text)
    if "cost action" in lower:
        return "networking_and_mobility"
    if any(term in lower for term in ("prize", "award", "challenge", "competition", "concours")):
        return "prize_or_challenge"
    if any(term in lower for term in ("fellowship", "scholarship", "stipendium", "bourse")):
        return "fellowship_or_scholarship"
    if "doctoral network" in lower or "doctoral programme" in lower:
        return "doctoral_training"
    if personnel_support == "staff_mobility_or_secondment":
        return "staff_exchange_or_mobility"
    if any(term in lower for term in ("innovation", "sme", "startup", "start-up")):
        return "innovation_grant"
    return "research_or_project_grant"


def score_page(
    project: ProjectProfile,
    parsed: ParsedPage,
    source: Source,
    url: str,
    discovered_from: str,
    checked_at: str,
) -> Match | None:
    searchable = truncate(parsed.title + " " + parsed.text, 120_000)
    lower = folded(searchable)
    if not has_funding_signal(lower):
        return None

    explicit_terms = project.explicit_match_terms()
    explicit_keys = {folded(term) for term in explicit_terms}
    match_terms = project.match_terms()
    matched: list[str] = []
    explicit_matched: list[str] = []
    derived_matched: list[str] = []
    thematic_points = 0
    for term in match_terms:
        key = folded(term)
        if len(key) < 3:
            continue
        if key in lower:
            matched.append(term)
            if key in explicit_keys:
                explicit_matched.append(term)
                thematic_points += 6 if " " in key else 3
            else:
                derived_matched.append(term)
                thematic_points += 1
    thematic_points = min(thematic_points, 45)

    scheme_family, scheme_mechanism, scheme_intro = classify_scheme(searchable)
    # A general funder page is not a project match merely because it contains
    # one broad word from the prose. Discipline-neutral manpower programmes are
    # the exception because they form an intentional second result track.
    if not explicit_matched and len(derived_matched) < 4 and not scheme_family:
        return None
    personnel_support, personnel_evidence = classify_personnel(searchable, scheme_mechanism)
    opportunity_type = classify_opportunity(searchable, personnel_support)
    deadline = find_deadline(searchable)
    status = status_from(searchable, deadline)

    score = 15 + thematic_points
    if parsed.title and has_funding_signal(parsed.title):
        score += 10
    if deadline:
        score += 8
    if status in {"open", "open_or_upcoming"}:
        score += 7
    elif status == "closed":
        score -= 25
    if scheme_family:
        score += 5
    if project.personnel_roles and personnel_support != "not_identified":
        score += 10
    if project.preferred_funding_types:
        preferences = " ".join(project.preferred_funding_types).casefold()
        if any(word in lower for word in preferences.split()):
            score += 5
    if project.applicant_types:
        applicant_hits = sum(
            1 for applicant in project.applicant_types if folded(applicant) in lower
        )
        score += min(5, applicant_hits * 2)
    score = max(0, min(100, score))

    evidence = evidence_excerpt(searchable, matched, personnel_evidence)
    return Match(
        relevance_score=score,
        opportunity_type=opportunity_type,
        personnel_support=personnel_support,
        programme_family=scheme_family,
        programme_explanation=scheme_intro,
        title=truncate(parsed.title or url.rsplit("/", 1)[-1].replace("-", " "), 300),
        funder=source.organization,
        source_domain=source.domain,
        country=source.country,
        language=source.language,
        status=status,
        deadline=deadline,
        amount=find_amount(searchable),
        matched_project_terms="; ".join(matched[:20]),
        personnel_evidence=personnel_evidence,
        evidence_excerpt=evidence,
        url=url,
        discovered_from=discovered_from,
        checked_at_utc=checked_at,
    )


def evidence_excerpt(text: str, matched_terms: list[str], personnel_evidence: str) -> str:
    lower = folded(text)
    signals = [
        *matched_terms[:5],
        *(personnel_evidence.split("; ") if personnel_evidence else []),
        *DEADLINE_LABELS[:5],
        *FUNDING_TERMS[:8],
    ]
    locations = [lower.find(folded(term)) for term in signals if folded(term) in lower]
    start = max(0, (min(locations) if locations else 0) - 180)
    return truncate(text[start:start + 900], 900)


def crawl_source(
    project: ProjectProfile,
    source: Source,
    fetcher: Fetcher,
    max_pages: int,
    max_sitemap_urls: int,
    min_score: int,
    include_closed: bool,
) -> tuple[list[Match], dict[str, object]]:
    started = time.monotonic()
    matches: list[Match] = []
    pages, policy, errors = discover_pages(fetcher, source, max_pages, max_sitemap_urls)
    for url, origin in pages:
        if not policy.can_fetch(USER_AGENT, url):
            continue
        try:
            page = fetcher.fetch(url)
            is_pdf = "pdf" in page.content_type or page.url.casefold().endswith(".pdf")
            if is_pdf:
                parsed = parse_pdf(page)
            elif "html" in page.content_type or page.content_type.startswith("text/"):
                parsed = parse_html(page)
            else:
                continue
            match = score_page(project, parsed, source, page.url, origin, page.fetched_at)
            if match and match.relevance_score >= min_score:
                if include_closed or match.status != "closed":
                    matches.append(match)
        except Exception as error:
            errors.append(f"{type(error).__name__}: {truncate(str(error), 160)}")
    return matches, {
        "domain": source.domain,
        "pages_discovered": len(pages),
        "matches": len(matches),
        "errors": errors[:5],
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def deduplicate_matches(matches: Iterable[Match]) -> list[Match]:
    best: dict[str, Match] = {}
    for match in matches:
        key = canonical_url(match.url)
        previous = best.get(key)
        if previous is None or match.relevance_score > previous.relevance_score:
            best[key] = match
    result = sorted(
        best.values(),
        key=lambda item: (
            item.status not in {"open", "open_or_upcoming"},
            -item.relevance_score,
            item.deadline or "9999-12-31",
            item.title.casefold(),
        ),
    )
    for rank, match in enumerate(result, start=1):
        match.rank = rank
    return result


def write_csv(path: Path, matches: Iterable[Match]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for match in matches:
            writer.writerow(asdict(match))
    temporary.replace(path)


def write_report(
    path: Path,
    project: ProjectProfile,
    sources: list[Source],
    matches: list[Match],
    source_reports: list[dict[str, object]],
    started_at: str,
) -> None:
    status_counts = Counter(match.status for match in matches)
    personnel_counts = Counter(match.personnel_support for match in matches)
    report = {
        "generated_at_utc": utc_now(),
        "started_at_utc": started_at,
        "project": project.name,
        "source_count": len(sources),
        "domains_processed": len(source_reports),
        "pages_discovered": sum(int(item["pages_discovered"]) for item in source_reports),
        "match_count": len(matches),
        "status_counts": dict(sorted(status_counts.items())),
        "personnel_support_counts": dict(sorted(personnel_counts.items())),
        "domains_with_errors": sum(bool(item["errors"]) for item in source_reports),
        "source_reports": sorted(source_reports, key=lambda item: str(item["domain"])),
        "limitations": [
            "Results are discovery candidates, not confirmed eligibility decisions.",
            "JavaScript-only pages, login walls, PDFs, and blocked crawlers may not be indexed.",
            "Amounts and deadlines are extracted conservatively and require source verification.",
            "COST is classified as networking/mobility support, not research-salary funding.",
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search the European funding-source CSV for calls matching a project."
    )
    parser.add_argument("--project", type=Path, required=True, help="Project JSON, TXT, or Markdown file")
    parser.add_argument(
        "--sources", type=Path, default=Path("eu_funding_sources_1200.csv"),
        help="Funding-source CSV generated by build_eu_funding_sources.py",
    )
    parser.add_argument("--output", type=Path, default=Path("funding_matches.csv"))
    parser.add_argument("--report", type=Path, default=Path("funding_search_report.json"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/funding-pages"))
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--cache-hours", type=float, default=24)
    parser.add_argument("--max-pages-per-domain", type=int, default=8)
    parser.add_argument("--max-sitemap-urls", type=int, default=40)
    parser.add_argument("--min-score", type=int, default=25)
    parser.add_argument("--include-closed", action="store_true")
    parser.add_argument(
        "--country", action="append", default=[],
        help="Restrict by country name/code; may be repeated",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only the first N sources (for smoke tests)",
    )
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if args.workers < 1 or args.workers > 64:
        raise ValueError("--workers must be between 1 and 64")
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")
    if args.max_pages_per_domain < 1:
        raise ValueError("--max-pages-per-domain must be positive")
    if not 0 <= args.min_score <= 100:
        raise ValueError("--min-score must be between 0 and 100")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        validate_args(args)
        project = ProjectProfile.load(args.project)
        countries = {item.casefold() for item in args.country}
        sources = load_sources(args.sources, countries=countries, limit=args.limit)
    except (OSError, ValueError) as error:
        eprint(f"Input error: {error}")
        return 2
    if not sources:
        eprint("No sources matched the supplied filters.")
        return 2

    started_at = utc_now()
    eprint(
        f"Searching {len(sources)} domains for {project.name!r}; "
        f"up to {args.max_pages_per_domain} pages/domain."
    )
    fetcher = Fetcher(args.cache_dir, args.timeout, args.cache_hours)
    all_matches: list[Match] = []
    reports: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                crawl_source,
                project,
                source,
                fetcher,
                args.max_pages_per_domain,
                args.max_sitemap_urls,
                args.min_score,
                args.include_closed,
            ): source
            for source in sources
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            source = futures[future]
            try:
                matches, report = future.result()
            except Exception as error:
                matches = []
                report = {
                    "domain": source.domain,
                    "pages_discovered": 0,
                    "matches": 0,
                    "errors": [f"{type(error).__name__}: {truncate(str(error), 180)}"],
                    "elapsed_seconds": 0,
                }
            all_matches.extend(matches)
            reports.append(report)
            if completed % 25 == 0 or completed == len(futures):
                eprint(
                    f"Processed {completed}/{len(futures)} domains; "
                    f"{len(all_matches)} candidate matches before deduplication."
                )

    matches = deduplicate_matches(all_matches)
    write_csv(args.output, matches)
    write_report(args.report, project, sources, matches, reports, started_at)
    eprint(f"Wrote {len(matches)} matches to {args.output}")
    eprint(f"Wrote crawl evidence and diagnostics to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
