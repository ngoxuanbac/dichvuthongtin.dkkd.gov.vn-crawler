import json
import sys
import warnings
from typing import Optional

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
_LEGAL_KEYWORDS = ["loại hình doanh nghiệp", "loại hình", "hình thức"]
_CAPTCHA_MARKERS = ["g-recaptcha", "grecaptcha", "recaptcha"]


class DKKDCrawler:
    """Crawler for the DKKD business registration information portal."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": _USER_AGENT,
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        })
        self._h: Optional[str] = None

    def _load_session_token(self) -> str:
        resp = self._session.get(SEARCH_PAGE, verify=False, timeout=30)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.content.decode("utf-8", errors="replace"), "lxml")
        hd = soup.find("input", {"name": "ctl00$hdParameter"})
        if not hd or not hd.get("value"):
            raise RuntimeError("Session token not found in page — site may have changed")

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
                    is_main = "(Chính)" in code_text or "(Chính)" in desc_text
                    code = code_text.replace("(Chính)", "").strip()
                    lines.append(BusinessLine(code=code, description=desc_text, is_main=is_main))

            page_index += 1

        return lines

    def get_detail_fields(self, taxcode: str, company_id: str = "") -> dict:
        result = self._get_detail_fields_via_requests(taxcode, company_id)
        if result is not None:
            return result
        return self._get_detail_fields_via_cloakbrowser(taxcode, company_id)

    def _get_detail_fields_via_requests(self, taxcode: str, company_id: str) -> Optional[dict]:
        """
        Navigate to the detail page via the correct POST flow.
        The site requires a form POST with company ID to reach the detail page —
        a direct GET with ?taxcode=X just redirects to the home page.
        Returns None when captcha is present (always), so execution falls through
        to the browser-based method.
        """
        if not company_id:
            return None

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
        # Captcha required — hand off to browser
        if any(marker in html.lower() for marker in _CAPTCHA_MARKERS):
            return None

        return _parse_detail_html(BeautifulSoup(html, "lxml"))

    def _get_detail_fields_via_cloakbrowser(self, taxcode: str, company_id: str = "") -> dict:
        try:
            from cloakbrowser import launch
        except ImportError:
            print(
                "[crawler] cloakbrowser not installed — install with: uv add cloakbrowser\n"
                "  Skipping legal_form and establishment_date.",
                file=sys.stderr,
            )
            return {}

        result: dict = {}
        # headless=False is required: Google's reCAPTCHA audio challenge is disabled in
        # headless mode even with fingerprint spoofing, but works in headed mode.
        browser = launch(headless=False, humanize=True, locale="vi-VN")
        ctx = browser.new_context(
            extra_http_headers={"Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8"},
        )
        page = ctx.new_page()

        try:
            page.goto(SEARCH_PAGE, wait_until="networkidle", timeout=30_000)

            # Fill search field
            page.locator("input#ctl00_FldSearch").fill(taxcode)

            if company_id:
                # Set company ID directly via JS — avoids needing to click autocomplete
                page.evaluate(
                    f"document.getElementById('ctl00_FldSearchID').value = '{company_id}'"
                )
            else:
                # Wait for jQuery-UI autocomplete dropdown and click the first suggestion
                page.wait_for_timeout(1500)
                first_item = page.locator(".ui-autocomplete .ui-menu-item a").first
                if first_item.is_visible(timeout=4_000):
                    first_item.click()
                    # Autocomplete's select handler already clicks the search button,
                    # so we land directly on the captcha page.
                    page.wait_for_load_state("networkidle", timeout=25_000)
                    if page.locator("input#ctl00_C_btnSubmit").is_visible(timeout=3_000):
                        _solve_captcha_and_submit(page)
                        page.wait_for_load_state("networkidle", timeout=30_000)
                    html = page.content()
                    result = _parse_detail_html(BeautifulSoup(html, "lxml"))
                    return result

            # Click the search button to navigate to the captcha page
            page.locator("input#ctl00_btnSearch").click()
            page.wait_for_load_state("networkidle", timeout=25_000)

            # Handle captcha and submit
            if page.locator("input#ctl00_C_btnSubmit").is_visible(timeout=5_000):
                _solve_captcha_and_submit(page)
                page.wait_for_load_state("networkidle", timeout=30_000)

            html = page.content()
            result = _parse_detail_html(BeautifulSoup(html, "lxml"))

        except Exception as exc:
            print(f"[crawler] CloakBrowser error: {exc}", file=sys.stderr)
        finally:
            browser.close()

        return result

    def scrape_by_taxcode(self, taxcode: str) -> Optional[CompanyDetail]:
        payload = json.dumps({"searchField": taxcode, "h": self._token()})
        resp = self._session.post(
            SEARCH_API, data=payload, verify=False, timeout=30, headers=_JSON_HEADERS,
        )
        resp.raise_for_status()

        raw_list = resp.json().get("d", [])
        raw = next((r for r in raw_list if r.get("Enterprise_Gdt_Code") == taxcode), None)
        if not raw:
            return None

        company_id = str(raw.get("Id", ""))
        business_lines = self.get_business_lines(company_id)
        extra = self.get_detail_fields(taxcode, company_id)

        return _parse_company_detail(raw, business_lines, extra)


def _solve_captcha_and_submit(page) -> None:
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
    # Step 1 — click the checkbox
    try:
        page.wait_for_selector("iframe[src*='recaptcha'][src*='anchor']", timeout=10_000)
        checkbox_frame = page.frame_locator("iframe[title='reCAPTCHA']")
        checkbox_frame.locator("#recaptcha-anchor").click()
    except Exception:
        pass

    # Step 2 — wait for auto-solve (up to 6 s)
    for _ in range(6):
        page.wait_for_timeout(1_000)
        gr_len = page.evaluate(
            "document.querySelector('[name=g-recaptcha-response]')"
            "? document.querySelector('[name=g-recaptcha-response]').value.length : 0"
        )
        if gr_len > 0:
            print("[captcha] auto-solved", file=sys.stderr)
            break
    else:
        # Step 3 — auto-solve didn't happen, try the audio challenge
        try:
            challenge_frame = page.frame_locator("iframe[src*='bframe']")
            audio_btn = challenge_frame.locator("#recaptcha-audio-button")
            if audio_btn.is_visible(timeout=3_000):
                _solve_audio_challenge(page, challenge_frame)
            else:
                print("[captcha] audio button not visible (may be IP-blocked)", file=sys.stderr)
        except Exception as exc:
            print(f"[captcha] challenge handling failed: {exc}", file=sys.stderr)

    # Step 4 — submit the site's form via JS (bypasses overlay elements)
    try:
        page.evaluate("document.getElementById('ctl00_C_btnSubmit').click()")
    except Exception as exc:
        print(f"[captcha] submit click failed: {exc}", file=sys.stderr)


def _configure_ffmpeg(AudioSegment) -> None:
    """Point pydub at ffmpeg when winget installed it but the shell PATH is stale."""
    import os, shutil, glob
    if shutil.which("ffmpeg"):
        return
    # Common winget install location on Windows
    pattern = os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg-*\bin\ffmpeg.exe"
    )
    candidates = glob.glob(pattern)
    if candidates:
        ffmpeg_exe = candidates[0]
        AudioSegment.converter = ffmpeg_exe
        AudioSegment.ffmpeg = ffmpeg_exe
        AudioSegment.ffprobe = ffmpeg_exe.replace("ffmpeg.exe", "ffprobe.exe")


def _solve_audio_challenge(page, challenge_frame) -> None:
    """Download the reCAPTCHA audio challenge and transcribe it using Google Speech API (free)."""
    import io
    import tempfile

    try:
        import speech_recognition as sr
        from pydub import AudioSegment
    except ImportError:
        print("[captcha] SpeechRecognition/pydub not installed. Run: uv add SpeechRecognition pydub", file=sys.stderr)
        return

    _configure_ffmpeg(AudioSegment)

    try:
        challenge_frame.locator("#recaptcha-audio-button").click()
        page.wait_for_timeout(2_000)

        # Get the audio download URL
        audio_url = challenge_frame.locator(".rc-audiochallenge-download-link").get_attribute("href", timeout=8_000)
        if not audio_url:
            print("[captcha] could not find audio URL", file=sys.stderr)
            return

        print(f"[captcha] downloading audio challenge...", file=sys.stderr)
        audio_bytes = requests.get(audio_url, verify=False, timeout=20).content

        # Convert MP3 → WAV in memory (pydub needs ffmpeg for MP3 decoding)
        mp3_buf = io.BytesIO(audio_bytes)
        segment = AudioSegment.from_mp3(mp3_buf)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = tmp.name
        segment.export(wav_path, format="wav")

        # Transcribe with Google Speech Recognition (free, no key)
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)

        try:
            import os
            os.unlink(wav_path)
        except Exception:
            pass

        transcription = recognizer.recognize_google(audio_data)
        print(f"[captcha] transcription: {transcription!r}", file=sys.stderr)

        # Fill response and verify
        challenge_frame.locator("#audio-response").fill(transcription)
        page.wait_for_timeout(500)
        challenge_frame.locator("#recaptcha-verify-button").click()
        page.wait_for_timeout(2_500)
        print("[captcha] audio challenge submitted", file=sys.stderr)

    except Exception as exc:
        print(f"[captcha] audio challenge failed: {exc}", file=sys.stderr)


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


def _parse_detail_html(soup: BeautifulSoup) -> dict:
    result: dict = {}

    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True).lower()
        value = cells[-1].get_text(strip=True)
        if not value:
            continue

        if not result.get("establishment_date") and any(kw in label for kw in _DATE_KEYWORDS):
            result["establishment_date"] = value
        if not result.get("legal_form") and any(kw in label for kw in _LEGAL_KEYWORDS):
            result["legal_form"] = value

        if len(result) == 2:
            return result

    for tag in soup.find_all(attrs={"id": True}):
        tag_id = tag.get("id", "").lower().replace("_", "").replace("-", "")
        text = tag.get_text(strip=True)
        if not text:
            continue

        if not result.get("establishment_date") and any(
            kw in tag_id for kw in ["ngaythanhlap", "dateofformation", "foundeddate", "startdate", "ngaycap"]
        ):
            result["establishment_date"] = text

        if not result.get("legal_form") and any(
            kw in tag_id for kw in ["loaihinhdn", "enterprisetype", "businesstype", "loaihinh"]
        ):
            result["legal_form"] = text

        if len(result) == 2:
            break

    return result


def search(query: str) -> list[Company]:
    return DKKDCrawler().search(query)


def scrape_by_taxcode(taxcode: str) -> Optional[CompanyDetail]:
    return DKKDCrawler().scrape_by_taxcode(taxcode)
