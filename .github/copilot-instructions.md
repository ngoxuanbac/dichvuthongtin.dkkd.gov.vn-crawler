# Copilot instructions for `dichvuthongtin.dkkd.gov.vn-crawler`

## Build, test, and lint commands

Use these commands from the repository root:

```bash
# Install project + dev dependencies
python -m pip install -e .[dev]

# Build distribution artifacts (hatchling backend)
uv build

# Run full test suite
python -m pytest -q

# Run a single test
python -m pytest tests/test_crawler.py::TestParseCompany::test_full_record -q
```

There is currently no dedicated lint tool configured in `pyproject.toml`.

## High-level architecture

- **Entry points**
  - CLI entrypoint: `dkkd-crawler` -> `dkkd_crawler.cli:main`
  - Library entrypoints: module-level `search(query)` and `scrape_by_taxcode(taxcode)` wrappers in `src/dkkd_crawler/crawler.py`
- **Core crawler flow (`DKKDCrawler`)**
  - A shared `requests.Session` is created once per crawler instance.
  - `_load_session_token()` fetches `ctl00$hdParameter` (`h` token) from the ASP.NET search page and caches it in `self._h`.
  - `search()` calls `GetSearch` JSON endpoint and maps raw records with `_parse_company`.
  - `scrape_by_taxcode()` performs search, enforces **exact** `Enterprise_Gdt_Code` match, then enriches with:
    1. `get_business_lines()` paging `LoadMore` HTML fragments into `BusinessLine` objects.
    2. `get_detail_fields()` for legal form + establishment date.
- **Detail enrichment and captcha handling**
  - `get_detail_fields()` tries `_get_detail_fields_via_requests()` first (ASP.NET form POST with `__VIEWSTATE`, `__EVENTVALIDATION`, etc.).
  - If detail extraction fails or captcha is detected, it falls back to `_get_detail_fields_via_cloakbrowser()`.
  - CloakBrowser path opens a non-headless browser, reuses session cookies, solves reCAPTCHA audio challenge (`_solve_captcha_and_submit` / `_solve_audio_challenge`), then parses HTML via `_parse_detail_html`.
  - Audio transcription prefers free remote servers (`_transcribe_via_server`) and falls back to local SpeechRecognition + pydub (`_transcribe_local`).
- **Data model boundary**
  - `src/dkkd_crawler/models.py` dataclasses (`Company`, `BusinessLine`, `CompanyDetail`) are the canonical typed output from crawler parsing.
  - CLI prints `CompanyDetail.to_dict()` as UTF-8 JSON for downstream consumers.

## Key conventions in this repo

- **ASP.NET navigation is intentional**: do not replace detail-page POST flow with direct querystring GET; the crawler depends on hidden form fields and token flow.
- **Exact tax-code filtering is required**: for detail scraping, only accept exact `Enterprise_Gdt_Code == taxcode`; substring/suffix matches (e.g., branch codes) are not valid.
- **Optional-field normalization pattern**:
  - Parser layer stores missing optionals as `None` (`value or None`).
  - `CompanyDetail.to_dict()` normalizes missing optionals to empty strings for JSON output.
- **HTML parsing strategy for detail fields**:
  - Parse both table label/value rows and id-based spans/tags.
  - Vietnamese keyword matching in lowercase is expected (`_DATE_KEYWORDS`, `_LEGAL_KEYWORDS`).
- **Windows/Vietnamese output compatibility**:
  - CLI reconfigures stdout to UTF-8 before printing.
  - User-visible/error strings are Vietnamese; keep that language consistent when changing CLI/crawler messages.
- **Tests are highly mock-driven**:
  - `tests/test_crawler.py` uses `MagicMock` sessions and `_make_response` helper rather than live network calls.
  - Keep public behavior stable across `DKKDCrawler` methods and module-level wrappers (`search`, `scrape_by_taxcode`) because tests assert both call wiring and payload shapes.
