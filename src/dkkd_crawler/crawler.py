import json
import sys
import unicodedata
import warnings
from typing import Any, Callable, Optional

import requests
from bs4 import BeautifulSoup

from .models import BusinessLine, Company, CompanyDetail

warnings.filterwarnings("ignore", category=requests.packages.urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://dichvuthongtin.dkkd.gov.vn"
SEARCH_PAGE = f"{BASE_URL}/inf/default.aspx"
SEARCH_API = f"{BASE_URL}/inf/Public/Srv.aspx/GetSearch"
LOAD_MORE_API = f"{BASE_URL}/inf/Forms/Searches/EnterpriseInfo.aspx/LoadMore"
 
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_JSON_HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": SEARCH_PAGE,
}

_DATE_KEYWORDS = ["ngày thành lập", "ngày đăng ký", "ngày bắt đầu hoạt động", "ngày cấp"]
_LEGAL_KEYWORDS = ["loại hình pháp lý", "loại hình doanh nghiệp", "loại hình", "hình thức"]
_CAPTCHA_MARKERS = ["g-recaptcha", "grecaptcha", "recaptcha"]
_NAME_KEYWORDS = ["tên doanh nghiệp", "tên công ty"]
_FOREIGN_NAME_KEYWORDS = ["tên tiếng nước ngoài", "tên nước ngoài", "foreign name"]
_SHORT_NAME_KEYWORDS = ["tên viết tắt", "short name"]
_STATUS_KEYWORDS = ["tình trạng hoạt động", "trạng thái"]
_TAX_CODE_KEYWORDS = ["mã số thuế", "tax code"]
_ENTERPRISE_CODE_KEYWORDS = ["mã số doanh nghiệp", "mã doanh nghiệp", "enterprise code"]
_LEGAL_REP_KEYWORDS = ["người đại diện", "đại diện theo pháp luật"]
_ADDRESS_KEYWORDS = ["địa chỉ trụ sở chính", "địa chỉ"]
_ADDRESS_FOREIGN_KEYWORDS = ["địa chỉ nước ngoài", "foreign address"]


class DKKDCrawler:
    """Crawler for the DKKD business registration information portal."""

    def __init__(
        self,
        debug: bool = False,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": _USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._h: Optional[str] = None
        self._debug = debug
        self._status_callback = status_callback
        # Cached from _get_detail_fields_via_requests so _get_captcha_page_url
        # doesn't have to repeat the same GET+POST navigation a second time.
        self._last_captcha_url: Optional[str] = None

    def _log(self, message: str) -> None:
        if self._debug:
            print(message, file=sys.stderr)

    def _status(self, message: str) -> None:
        if self._status_callback:
            self._status_callback(message)

    def _load_session_token(self) -> str:
        self._status("loading session token")
        resp = self._session.get(SEARCH_PAGE, verify=False, timeout=30)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.content.decode("utf-8", errors="replace"), "lxml")
        hd = soup.find("input", {"name": "ctl00$hdParameter"})
        if not hd or not hd.get("value"):
            raise RuntimeError("session token not found in page — site may have changed")

        self._h = hd["value"]
        return self._h

    def _token(self) -> str:
        if not self._h:
            self._load_session_token()
        return self._h  # type: ignore[return-value]

    def search(self, query: str) -> list[Company]:
        payload = json.dumps({"searchField": query, "h": self._token()})
        resp = self._session.post(
            SEARCH_API, data=payload, verify=False, timeout=30, headers=_JSON_HEADERS,
        )
        resp.raise_for_status()
        return [_parse_company(c) for c in resp.json().get("d", [])]

    def find_exact_by_taxcode(self, taxcode: str) -> Optional[Company]:
        return next((c for c in self.search(taxcode) if c.tax_code == taxcode), None)

    def get_business_lines(self, company_id: str) -> list[BusinessLine]:
        self._status("loading business lines")
        lines: list[BusinessLine] = []
        page_index = 0
        load_more_headers = {
            **_JSON_HEADERS,
            "Referer": f"{BASE_URL}/inf/Forms/Searches/EnterpriseInfo.aspx",
        }

        while True:
            payload = json.dumps({"PageIndex": str(page_index), "EnterpriseID": company_id})
            resp = self._session.post(
                LOAD_MORE_API, data=payload, verify=False, timeout=30, headers=load_more_headers,
            )
            resp.raise_for_status()

            html_fragment = resp.json().get("d", "")
            if not html_fragment:
                break

            soup = BeautifulSoup(f"<table>{html_fragment}</table>", "lxml")
            rows = soup.find_all("tr")
            if not rows:
                break

            for row in rows:
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    code_text = cells[0].get_text(strip=True)
                    desc_text = cells[1].get_text(separator="\n", strip=True)
                    is_main = _contains_main_business_marker(code_text) or _contains_main_business_marker(desc_text)
                    code = _strip_main_business_marker(code_text)
                    lines.append(BusinessLine(code=code, description=desc_text, is_main=is_main))

            page_index += 1
            self._status(f"loaded business lines page {page_index}")

        self._status(f"loaded {len(lines)} business lines")
        return lines

    def get_detail_fields(self, taxcode: str, company_id: str = "") -> dict:
        self._status("loading detail page")
        result = self._get_detail_fields_via_requests(taxcode, company_id)
        if result is not None:
            self._status("detail page parsed via requests")
            return result
        self._status("captcha flow required")
        if company_id:
            return self._get_detail_fields_via_cloakbrowser(taxcode, company_id)
        return self._get_detail_fields_via_cloakbrowser(taxcode)

    def _get_detail_fields_via_requests(self, taxcode: str, company_id: str) -> Optional[dict]:
        """
        Navigate to the detail page via the correct POST flow.
        The site requires a form POST with company ID to reach the detail page —
        a direct GET with ?taxcode=X just redirects to the home page.
        Returns None when captcha is present (always), so execution falls through
        to the browser-based method.
        """
        if not company_id:
            try:
                resp = self._session.get(SEARCH_PAGE, verify=False, timeout=30)
                resp.raise_for_status()
            except requests.RequestException:
                return None
            html = resp.content.decode("utf-8", errors="replace")
            if any(marker in html.lower() for marker in _CAPTCHA_MARKERS):
                return None
            return _parse_detail_html(BeautifulSoup(html, "lxml"))

        try:
            # Fetch a fresh page to get VIEWSTATE / EVENTVALIDATION tokens
            resp0 = self._session.get(SEARCH_PAGE, verify=False, timeout=30)
            resp0.raise_for_status()
            soup0 = BeautifulSoup(resp0.content.decode("utf-8", errors="replace"), "lxml")

            def _val(name: str) -> str:
                el = soup0.find("input", {"name": name})
                return el["value"] if el and el.get("value") else ""

            form_data = {
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
                "__VIEWSTATE": _val("__VIEWSTATE"),
                "__EVENTVALIDATION": _val("__EVENTVALIDATION"),
                "ctl00$nonceKeyFld": _val("ctl00$nonceKeyFld"),
                "ctl00$hdParameter": _val("ctl00$hdParameter"),
                "ctl00$searchtype": "1",
                "ctl00$FldSearch": taxcode,
                "ctl00$FldSearchID": str(company_id),
                "ctl00$btnSearch": "Tìm kiếm >>",
            }
            resp = self._session.post(
                SEARCH_PAGE,
                data=form_data,
                verify=False,
                timeout=30,
                allow_redirects=True,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": SEARCH_PAGE,
                },
            )
            resp.raise_for_status()
        except requests.RequestException:
            return None

        html = resp.content.decode("utf-8", errors="replace")
        # If the site redirected back to the home page, the navigation failed
        if "<title>" in html and "Trang chủ" in html[:2000]:
            return None
        # Captcha required — hand off to browser (cache the URL, see _last_captcha_url).
        if any(marker in html.lower() for marker in _CAPTCHA_MARKERS):
            if "EnterpriseInfo.aspx" in resp.url:
                self._last_captcha_url = resp.url
            return None

        return _parse_detail_html(BeautifulSoup(html, "lxml"))

    def _get_detail_fields_via_cloakbrowser(self, taxcode: str, company_id: str = "") -> dict:
        """
        Navigate to the captcha page using the requests session (which handles the
        ASP.NET form POST correctly), then hand the URL + session cookies to CloakBrowser
        so it can render the reCAPTCHA, solve the audio challenge, and read the result.
        """
        try:
            from cloakbrowser import launch
        except ImportError:
            self._log(
                "[crawler] cloakbrowser is not installed — install with: uv add cloakbrowser\n"
                "  skipping legal_form and establishment_date."
            )
            return {}

        captcha_url = self._get_captcha_page_url(taxcode, company_id)
        if not captcha_url:
            self._log("[crawler] could not reach captcha page via requests")
            return {}

        session_cookies = [
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain or "dichvuthongtin.dkkd.gov.vn",
                "path": c.path or "/",
            }
            for c in self._session.cookies
        ]

        result: dict = {}
        self._status("opening browser for captcha")
        browser = launch(headless=not self._debug, humanize=self._debug, locale="en-US")
        ctx = browser.new_context(
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            ignore_https_errors=True,
        )
        if session_cookies:
            ctx.add_cookies(session_cookies)

        page = ctx.new_page()
        try:
            # Navigate directly to the captcha page (no form submission in browser)
            page.goto(captcha_url, wait_until="networkidle", timeout=30_000)

            if page.locator("input#ctl00_C_btnSubmit").is_visible(timeout=5_000):
                start_url = page.url
                self._status("solving captcha")
                _solve_captcha_and_submit(page, self._debug)
                self._status("waiting for detail page after captcha")
                _wait_for_detail_page(page, start_url)
                _wait_for_manual_captcha_resolution(page, self._debug)

            html = page.content()
            self._status("parsing detail fields")
            result = _parse_detail_html(BeautifulSoup(html, "lxml"))

        except Exception as exc:
            self._log(f"[crawler] CloakBrowser error: {exc}")
        finally:
            browser.close()

        return result

    def _get_captcha_page_url(self, taxcode: str, company_id: str) -> Optional[str]:
        """POST the search form via requests and return the captcha page URL."""
        if not company_id:
            return None
        if self._last_captcha_url:
            cached, self._last_captcha_url = self._last_captcha_url, None
            return cached
        try:
            resp0 = self._session.get(SEARCH_PAGE, verify=False, timeout=30)
            resp0.raise_for_status()
            soup0 = BeautifulSoup(resp0.content.decode("utf-8", errors="replace"), "lxml")

            def _val(name: str) -> str:
                el = soup0.find("input", {"name": name})
                return el["value"] if el and el.get("value") else ""

            form_data = {
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
                "__VIEWSTATE": _val("__VIEWSTATE"),
                "__EVENTVALIDATION": _val("__EVENTVALIDATION"),
                "ctl00$nonceKeyFld": _val("ctl00$nonceKeyFld"),
                "ctl00$hdParameter": _val("ctl00$hdParameter"),
                "ctl00$searchtype": "1",
                "ctl00$FldSearch": taxcode,
                "ctl00$FldSearchID": str(company_id),
                "ctl00$btnSearch": "Tìm kiếm >>",
            }
            resp = self._session.post(
                SEARCH_PAGE,
                data=form_data,
                verify=False,
                timeout=30,
                allow_redirects=True,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": SEARCH_PAGE,
                },
            )
            resp.raise_for_status()
            # The final URL should be EnterpriseInfo.aspx?h=XXXX (the captcha page)
            if "EnterpriseInfo.aspx" in resp.url:
                return resp.url
        except Exception as exc:
            self._log(f"[crawler] captcha page navigation failed: {exc}")
        return None

    def scrape_by_taxcode(self, taxcode: str) -> Optional[CompanyDetail]:
        self._status("searching company by tax code")
        company_id = self._find_company_id_by_taxcode(taxcode)
        if not company_id:
            self._status("no exact company match found")
            return None

        detail_fields = self.get_detail_fields(taxcode, company_id)
        if not detail_fields:
            raise RuntimeError("Could not extract data from detail page (captcha/redirect flow not completed)")

        # Always fetch business lines via LoadMore API so output includes full ngành nghề.
        detail_fields["business_lines"] = self.get_business_lines(company_id)

        scraped_tax_code = detail_fields.get("tax_code")
        if scraped_tax_code and _normalize_identifier(scraped_tax_code) != _normalize_identifier(taxcode):
            self._status("tax code mismatch on detail page")
            return None

        self._status("building final result")
        return _build_company_detail_from_detail(detail_fields, taxcode, company_id)

    def _find_company_id_by_taxcode(self, taxcode: str) -> Optional[str]:
        self._status("calling search endpoint")
        payload = json.dumps({"searchField": taxcode, "h": self._token()})
        resp = self._session.post(
            SEARCH_API, data=payload, verify=False, timeout=30, headers=_JSON_HEADERS,
        )
        resp.raise_for_status()

        raw_list = resp.json().get("d", [])
        raw = next((r for r in raw_list if r.get("Enterprise_Gdt_Code") == taxcode), None)
        if not raw:
            return None
        return str(raw.get("Id", "")) or None


def _solve_captcha_and_submit(page, debug: bool = False) -> None:
    """
    Solve reCAPTCHA v2 via audio challenge (free) then click the site's Submit button.

    Flow:
      1. Click the reCAPTCHA checkbox.
      2. Wait up to ~6 s for auto-solve (g-recaptcha-response populated).
      3. If a challenge appears, switch to the audio tab, download the MP3,
         transcribe with Google Speech Recognition (free API), fill the answer
         and click Verify.
      4. Click the site's Submit button via JS (bypasses any overlay).

    Note: Google temporarily blocks audio challenges if the same IP attempts it
    many times in rapid succession.  When blocked, re-run after a few hours or
    use a different IP / VPN.
    """
    try:
        page.wait_for_selector("iframe[src*='recaptcha'][src*='anchor']", timeout=10_000)
        checkbox_frame = page.frame_locator("iframe[title='reCAPTCHA']")
        checkbox_frame.locator("#recaptcha-anchor").click()
    except Exception:
        pass

    # Poll every 300ms (up to 6s total) instead of a flat 1s so auto-solve is
    # caught as soon as it resolves rather than always burning a full second.
    for _ in range(20):
        page.wait_for_timeout(300)
        gr_len = page.evaluate(
            "document.querySelector('[name=g-recaptcha-response]')"
            "? document.querySelector('[name=g-recaptcha-response]').value.length : 0"
        )
        if gr_len > 0:
            if debug:
                print("[captcha] auto-solved", file=sys.stderr)
            break
    else:
        try:
            challenge_frame = page.frame_locator("iframe[src*='bframe']")
            audio_btn = challenge_frame.locator("#recaptcha-audio-button")
            if audio_btn.is_visible(timeout=3_000):
                _solve_audio_challenge(page, challenge_frame, debug)
            else:
                if debug:
                    print("[captcha] audio button not visible (may be IP-blocked)", file=sys.stderr)
        except Exception as exc:
            if debug:
                print(f"[captcha] challenge handling failed: {exc}", file=sys.stderr)

    # Submit via JS — bypasses overlay elements that block a direct click.
    try:
        page.evaluate("document.getElementById('ctl00_C_btnSubmit').click()")
    except Exception as exc:
        if debug:
            print(f"[captcha] submit click failed: {exc}", file=sys.stderr)


def _wait_for_detail_page(page, start_url: str) -> None:
    """Wait until submit finishes and detail page is ready for parsing."""
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:
        pass

    try:
        page.wait_for_function(
            """(initialUrl) => {
                const current = window.location.href;
                if (current !== initialUrl) return true;

                const submitBtn = document.querySelector('#ctl00_C_btnSubmit');
                const hasCaptcha =
                    !!document.querySelector('iframe[src*="recaptcha"]') ||
                    !!document.querySelector('.g-recaptcha');
                const bodyText = (document.body?.innerText || '').toLowerCase();
                const hasDetailText =
                    bodyText.includes('loại hình') ||
                    bodyText.includes('ngày thành lập') ||
                    bodyText.includes('enterprise type') ||
                    bodyText.includes('date of formation');

                const submitHidden = !submitBtn || submitBtn.offsetParent === null;
                return submitHidden && !hasCaptcha && hasDetailText;
            }""",
            arg=start_url,
            timeout=45_000,
        )
    except Exception:
        # Keep best-effort behavior: callers still parse whatever HTML is available.
        pass

    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass


def _wait_for_manual_captcha_resolution(page, debug: bool = False) -> None:
    """Give user time to solve captcha manually when auto-solve is blocked."""
    has_captcha = False
    try:
        has_captcha = bool(
            page.evaluate(
                """() => !!document.querySelector('iframe[src*="recaptcha"], .g-recaptcha, .rc-doscaptcha-body')"""
            )
        )
    except Exception:
        return

    if not has_captcha:
        return

    if debug:
        print("[captcha] please solve captcha manually in the browser if needed...", file=sys.stderr)
    try:
        page.wait_for_function(
            """() => {
                const hasCaptcha =
                    !!document.querySelector('iframe[src*="recaptcha"]') ||
                    !!document.querySelector('.g-recaptcha') ||
                    !!document.querySelector('.rc-doscaptcha-body');
                if (hasCaptcha) return false;
                const bodyText = (document.body?.innerText || '').toLowerCase();
                return bodyText.includes('loại hình') || bodyText.includes('ngày thành lập');
            }""",
            timeout=180_000,
        )
    except Exception:
        pass


_TRANSCRIBE_SERVERS = [
    "https://engageub.pythonanywhere.com",
    "https://engageub1.pythonanywhere.com",
]


def _transcribe_via_server(audio_url: str, lang: str = "en-US", debug: bool = False) -> Optional[str]:
    """
    Send the reCAPTCHA audio URL to a free transcription server.
    The server downloads the MP3 and returns the spoken text — no local ffmpeg needed.
    Returns None if all servers fail or return an invalid response.
    """
    audio_url = audio_url.replace("recaptcha.net", "google.com")
    for server in _TRANSCRIBE_SERVERS:
        try:
            resp = requests.post(
                server,
                data={"input": audio_url, "lang": lang},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=60,
            )
            text = resp.text.strip()
            # Server returns "0" or HTML on failure, valid text otherwise
            if text and text != "0" and "<" not in text and len(text) <= 50:
                return text
        except Exception as exc:
            if debug:
                print(f"[captcha] server {server} failed: {exc}", file=sys.stderr)
    return None


def _solve_audio_challenge(page, challenge_frame, debug: bool = False) -> None:
    """
    Switch the reCAPTCHA to audio mode and solve it.

    Audio URL is read from #audio-source[src] (the <source> element inside the
    audio player — NOT the download link).  The URL is sent to a free transcription
    server which returns the spoken digits; we fill them in and click Verify.
    """
    _DOSCAPTCHA = ".rc-doscaptcha-body"
    MAX_ATTEMPTS = 5

    try:
        challenge_frame.locator("#recaptcha-audio-button").click()
        # No fixed sleep here — the #audio-source lookup below already waits
        # (timeout=6_000) for the audio frame to swap in.
    except Exception as exc:
        if debug:
            print(f"[captcha] audio button click failed: {exc}", file=sys.stderr)
        return

    seen_url: str = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        # Stop if Google detected automation
        try:
            dos = challenge_frame.locator(_DOSCAPTCHA)
            if dos.is_visible(timeout=500):
                if debug:
                    print("[captcha] automated-queries block detected, stopping", file=sys.stderr)
                return
        except Exception:
            pass

        try:
            audio_url = challenge_frame.locator("#audio-source").get_attribute("src", timeout=6_000)
        except Exception:
            audio_url = None

        if not audio_url:
            if debug:
                print(f"[captcha] attempt {attempt}: audio source not found yet", file=sys.stderr)
            page.wait_for_timeout(800)
            continue

        # Reload if same URL repeated (means previous answer was wrong)
        if audio_url == seen_url:
            try:
                challenge_frame.locator("#recaptcha-reload-button").click()
                # Real network round-trip to fetch a new challenge — keep this one.
                page.wait_for_timeout(1_500)
            except Exception:
                pass
            continue

        seen_url = audio_url
        if debug:
            print(f"[captcha] attempt {attempt}: transcribing...", file=sys.stderr)

        transcription = _transcribe_via_server(audio_url, "en-US", debug)

        if not transcription:
            if debug:
                print(f"[captcha] attempt {attempt}: transcription failed, reloading", file=sys.stderr)
            try:
                challenge_frame.locator("#recaptcha-reload-button").click()
                # Real network round-trip to fetch a new challenge — keep this one.
                page.wait_for_timeout(1_500)
            except Exception:
                pass
            continue

        if debug:
            print(f"[captcha] attempt {attempt}: got {transcription!r}", file=sys.stderr)
        challenge_frame.locator("#audio-response").fill(transcription)
        # Keep this pause: filling the field and submitting instantly is a
        # classic bot fingerprint reCAPTCHA's risk scoring looks for.
        page.wait_for_timeout(500)
        challenge_frame.locator("#recaptcha-verify-button").click()
        page.wait_for_timeout(2_000)

        gr_len = page.evaluate(
            "document.querySelector('[name=g-recaptcha-response]')"
            "? document.querySelector('[name=g-recaptcha-response]').value.length : 0"
        )
        if gr_len > 0:
            if debug:
                print("[captcha] audio challenge solved", file=sys.stderr)
            return

    if debug:
        print("[captcha] max attempts reached", file=sys.stderr)


def _parse_company(raw: dict) -> Company:
    return Company(
        id=raw.get("Id", ""),
        name=raw.get("Name", ""),
        name_foreign=raw.get("Name_F") or None,
        short_name=raw.get("Short_Name") or None,
        enterprise_code=raw.get("Enterprise_Code", ""),
        tax_code=raw.get("Enterprise_Gdt_Code", ""),
        status=raw.get("Status") or None,
        address=raw.get("Ho_Address") or None,
    )


def _parse_company_detail(
    raw: dict,
    business_lines: list[BusinessLine],
    extra: Optional[dict] = None,
) -> CompanyDetail:
    extra = extra or {}
    legal_form = (
        extra.get("legal_form")
        or raw.get("Enterprise_Type_Name")
        or raw.get("Loai_Hinh_DN")
        or None
    )
    establishment_date = (
        extra.get("establishment_date")
        or raw.get("Enterprise_Start_Date")
        or raw.get("Date_Of_Formation")
        or raw.get("Ngay_Thanh_Lap")
        or None
    )
    return CompanyDetail(
        id=raw.get("Id", ""),
        name=raw.get("Name", ""),
        name_foreign=raw.get("Name_F") or None,
        short_name=raw.get("Short_Name") or None,
        enterprise_code=raw.get("Enterprise_Code", ""),
        tax_code=raw.get("Enterprise_Gdt_Code", ""),
        status=raw.get("Status") or None,
        address=raw.get("Ho_Address") or None,
        address_foreign=raw.get("Ho_Address_F") or None,
        legal_representative=raw.get("Legal_First_Name") or None,
        legal_form=legal_form,
        establishment_date=establishment_date,
        city_id=raw.get("City_Id") or None,
        district_id=raw.get("District_Id") or None,
        ward_id=raw.get("Ward_Id") or None,
        business_lines=business_lines,
    )


def _parse_span_detail_fields(soup: BeautifulSoup, result: dict[str, Any]) -> None:
    """
    Parse detail fields from <span> elements with viewInput class.
    The new HTML structure uses spans like:
    <span id="ctl00_C_NAMEFld" class="viewInput ...">NHÀ SÁCH BIKA 01</span>
    """
    for span in soup.find_all("span", class_="viewInput"):
        span_id = span.get("id", "").lower()
        text = span.get_text(" ", strip=True)
        if not text:
            continue

        if span_id.endswith("namefld") and not result.get("name"):
            if "name_f" not in span_id:  # Exclude name_foreign field
                result["name"] = text
        elif span_id.endswith("name_ffld") and not result.get("name_foreign"):
            result["name_foreign"] = text
        elif span_id.endswith("short_namefld") and not result.get("short_name"):
            result["short_name"] = text
        elif span_id.endswith("statusnamefld") and not result.get("status"):
            result["status"] = text
        elif span_id.endswith("enterprise_gdt_codefld") and not result.get("enterprise_code"):
            # This field contains the enterprise code (e.g., "00001")
            result["enterprise_code"] = text
        elif span_id.endswith("enterprise_typefld") and not result.get("legal_form"):
            result["legal_form"] = text
        elif span_id.endswith("founding_date") and not result.get("establishment_date"):
            result["establishment_date"] = text
        elif span_id.endswith("representative") and not result.get("legal_representative"):
            result["legal_representative"] = text
        elif span_id.endswith("ho_address") and not result.get("address"):
            result["address"] = text


def _parse_detail_html(soup: BeautifulSoup) -> dict:
    result: dict[str, Any] = {}
    business_lines: list[BusinessLine] = []
    page_text = soup.get_text(" ", strip=True).lower()
    if any(marker in page_text for marker in _CAPTCHA_MARKERS):
        return {}

    # Parse <span> elements with viewInput class (new HTML format after CAPTCHA)
    _parse_span_detail_fields(soup, result)

    # Parse table rows (legacy format)
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        label_raw = cells[0].get_text(" ", strip=True)
        label = label_raw.lower()
        value = cells[-1].get_text(" ", strip=True)
        if not value:
            continue

        maybe_line = _parse_business_line_row(label_raw, value)
        if maybe_line:
            business_lines.append(maybe_line)
            continue

        if _is_detail_label_only(value, label_raw):
            continue

        _fill_detail_field_from_label(result, label, value)

        if not result.get("establishment_date") and any(kw in label for kw in _DATE_KEYWORDS):
            result["establishment_date"] = value
        if not result.get("legal_form") and any(kw in label for kw in _LEGAL_KEYWORDS):
            result["legal_form"] = value

    for tag in soup.find_all(attrs={"id": True}):
        tag_id = tag.get("id", "").lower().replace("_", "").replace("-", "")
        text = tag.get_text(" ", strip=True)
        if not text:
            continue
        if _is_detail_label_only(text):
            continue

        if not result.get("establishment_date") and any(
            kw in tag_id for kw in ["ngaythanhlap", "dateofformation", "foundeddate", "startdate", "ngaycap"]
        ):
            result["establishment_date"] = text

        if not result.get("legal_form") and any(
            kw in tag_id for kw in ["loaihinhdn", "enterprisetype", "businesstype", "loaihinh"]
        ):
            result["legal_form"] = text

    hidden_values = _extract_hidden_values(soup)
    for key, value in hidden_values.items():
        if value and not result.get(key):
            result[key] = value

    if business_lines:
        result["business_lines"] = business_lines

    return result


def _fill_detail_field_from_label(result: dict[str, Any], label: str, value: str) -> None:
    if not result.get("name") and any(kw in label for kw in _NAME_KEYWORDS):
        result["name"] = value
    elif not result.get("name_foreign") and any(kw in label for kw in _FOREIGN_NAME_KEYWORDS):
        result["name_foreign"] = value
    elif not result.get("short_name") and any(kw in label for kw in _SHORT_NAME_KEYWORDS):
        result["short_name"] = value
    elif not result.get("status") and any(kw in label for kw in _STATUS_KEYWORDS):
        result["status"] = value
    elif not result.get("tax_code") and any(kw in label for kw in _TAX_CODE_KEYWORDS):
        result["tax_code"] = value
    elif not result.get("enterprise_code") and any(kw in label for kw in _ENTERPRISE_CODE_KEYWORDS):
        result["enterprise_code"] = value
    elif not result.get("legal_representative") and any(kw in label for kw in _LEGAL_REP_KEYWORDS):
        result["legal_representative"] = value
    elif not result.get("address_foreign") and any(kw in label for kw in _ADDRESS_FOREIGN_KEYWORDS):
        result["address_foreign"] = value
    elif not result.get("address") and any(kw in label for kw in _ADDRESS_KEYWORDS):
        result["address"] = value


def _parse_business_line_row(code_cell_text: str, desc_text: str) -> Optional[BusinessLine]:
    if not desc_text:
        return None

    code_candidate = _strip_main_business_marker(code_cell_text)
    normalized_code = "".join(ch for ch in code_candidate if ch.isdigit())
    if not normalized_code:
        return None
    if len(normalized_code) < 3 or len(normalized_code) > 6:
        return None

    is_main = _contains_main_business_marker(code_cell_text) or _contains_main_business_marker(desc_text)
    return BusinessLine(code=code_candidate, description=desc_text, is_main=is_main)


def _extract_hidden_values(soup: BeautifulSoup) -> dict[str, str]:
    mapping = {
        "enterprise_code": ["Enterprise_Code", "enterprisecode"],
        # Note: tax_code is NOT extracted from detail HTML
        # It comes from the search API or scrape_by_taxcode parameter
        "city_id": ["City_Id", "cityid"],
        "district_id": ["District_Id", "districtid"],
        "ward_id": ["Ward_Id", "wardid"],
    }
    found: dict[str, str] = {}

    for tag in soup.find_all(["input", "span"]):
        # Skip label elements (they contain Vietnamese text, not values)
        tag_classes = str(tag.get("class", "")).lower()
        if "lbledit" in tag_classes or tag.name == "label":
            continue

        attrs = [
            str(tag.get("id", "")),
            str(tag.get("name", "")),
            str(tag.get("class", "")),
        ]
        attr_blob = " ".join(attrs).lower().replace("_", "")
        value = tag.get("value") if tag.name == "input" else tag.get_text(" ", strip=True)
        value = (value or "").strip()
        if not value:
            continue

        for key, tokens in mapping.items():
            if found.get(key):
                continue
            if any(token.lower().replace("_", "") in attr_blob for token in tokens):
                found[key] = value

    return found


def _is_detail_label_only(value: str, label: str = "") -> bool:
    value_norm = " ".join(value.split()).strip().lower().rstrip(":").strip()
    if not value_norm:
        return True

    label_norm = " ".join(label.split()).strip().lower().rstrip(":").strip()
    if label_norm and value_norm == label_norm:
        return True

    return value_norm in {"loại hình pháp lý", "loại hình doanh nghiệp", "ngày thành lập", "ngày cấp"}


def _contains_main_business_marker(text: str) -> bool:
    normalized = _ascii_fold(text).lower().replace(" ", "")
    return "(chinh)" in normalized


def _strip_main_business_marker(text: str) -> str:
    return text.replace("(Chính)", "").replace("(Chính)", "").strip()


def _ascii_fold(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def _normalize_identifier(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum()).lower()


def _build_company_detail_from_detail(detail: dict[str, Any], taxcode: str, company_id: str) -> CompanyDetail:
    business_lines = detail.get("business_lines") or []
    if not isinstance(business_lines, list):
        business_lines = []

    return CompanyDetail(
        id=company_id,
        name=detail.get("name", ""),
        enterprise_code=detail.get("enterprise_code", ""),
        tax_code=detail.get("tax_code") or taxcode,
        name_foreign=detail.get("name_foreign") or None,
        short_name=detail.get("short_name") or None,
        status=detail.get("status") or None,
        address=detail.get("address") or None,
        address_foreign=detail.get("address_foreign") or None,
        legal_representative=detail.get("legal_representative") or None,
        legal_form=detail.get("legal_form") or None,
        establishment_date=detail.get("establishment_date") or None,
        city_id=detail.get("city_id") or None,
        district_id=detail.get("district_id") or None,
        ward_id=detail.get("ward_id") or None,
        business_lines=[bl for bl in business_lines if isinstance(bl, BusinessLine)],
    )


def search(query: str) -> list[Company]:
    return DKKDCrawler().search(query)


def scrape_by_taxcode(
    taxcode: str,
    debug: bool = False,
    status_callback: Optional[Callable[[str], None]] = None,
) -> Optional[CompanyDetail]:
    return DKKDCrawler(debug=debug, status_callback=status_callback).scrape_by_taxcode(taxcode)
