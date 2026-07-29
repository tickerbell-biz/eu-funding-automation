# European Funding Sources Directory

This package builds and maintains a high-recall directory of European websites
that may publish grants, support funds, calls, challenges, prizes, or related
funding opportunities.

## Files

- `eu_funding_sources_1200.csv` — the generated directory.
- `build_eu_funding_sources.py` — the reproducible collection and update program.
- `eu_funding_sources_validation.json` — row, domain, country, and language checks.

## Data basis

The core records come from the latest Research Organization Registry (ROR) data
dump. A record is included only when it:

1. is active;
2. is classified by ROR as a `funder`;
3. has an official website;
4. is located in the configured European scope; and
5. has a unique registrable domain.

The ROR release used for the supplied CSV is v2.10, published 20 July 2026.
ROR data is released under CC0. A small curated supplement adds major
cross-European call portals and fills countries with no ROR funder website.

This is intentionally a high-recall discovery directory. A ROR `funder` may
fund only its own research or a restricted community, so the CSV does **not**
claim that every organization currently has an open, externally eligible call.
Use `relevance_note` and check the live call before applying.

## Column meanings

The four requested fields come first:

- `domain` — unique registrable domain.
- `country` — organization country or European region.
- `language` — language detected on the website; if no live check succeeds, the
  country's official/national languages are supplied as an explicit fallback.
- `last_update` — best available update date.

Supporting fields explain provenance:

- `last_update_basis` is `http_last_modified`, `sitemap_lastmod`,
  `ror_record_last_modified`, or `directory_release_date`.
- `ror_record_last_modified` is the date ROR last changed its record.
- `website_last_modified` is populated only when the website supplies a
  verifiable HTTP or sitemap date.
- `last_checked_utc`, `http_status`, and `reachable` describe the live check.
- The program records how language was determined while processing; the concise
  CSV retains the resulting `language` and `language_code`.
- `source_record` links to ROR or identifies a curated seed.

## Run or update

Python 3.10 or newer is sufficient; no third-party packages are needed.

```bash
python3 build_eu_funding_sources.py \
  --output eu_funding_sources_1200.csv \
  --report eu_funding_sources_validation.json \
  --target 1200 \
  --max-per-country 70
```

To check live website language, reachability, and update metadata:

```bash
python3 build_eu_funding_sources.py \
  --check-sites \
  --workers 24 \
  --timeout 8
```

The program:

- resolves the newest ROR release through the permanent Zenodo concept record;
- checks the archive size and checksum;
- filters active European funders with official websites;
- normalizes IDNs and URLs;
- deduplicates by registrable domain;
- balances countries so the directory is not dominated by large states;
- optionally checks homepage language and last-modified metadata; and
- writes atomically, then validates domain uniqueness and coverage.

## Recommended production schedule

Run the registry refresh monthly. Run live website checks less aggressively
(for example, monthly with 12–24 workers and an 8-second timeout). Preserve the
previous CSV and compare by `domain` to identify additions, removals, and date
changes. A missing `website_last_modified` is normal: many websites do not
publish reliable modification metadata.

## Primary references

- ROR: https://ror.org/
- ROR data dump documentation: https://ror.readme.io/docs/data-dump
- Programmatic Zenodo retrieval: https://ror.readme.io/docs/zenodo
- ROR schema fields: https://ror.readme.io/docs/fields
- EU Funding & Tenders calls: https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/calls-for-proposals
- European Commission funding programmes: https://commission.europa.eu/funding-and-tenders/find-funding/eu-funding-programmes_en
