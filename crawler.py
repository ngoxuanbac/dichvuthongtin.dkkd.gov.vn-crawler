"""
Crawler for dichvuthongtin.dkkd.gov.vn
Searches for Vietnamese company information by tax code or company name,
and scrapes full details (business lines, legal form, establishment date)
for an exact tax code match.

The site uses an hdParameter session token on the search API and a
LoadMore page method for paginated business lines. The detail page
(which holds establishment date and legal form) is protected by reCAPTCHA
and requires a headless browser via Playwright.

Usage:
    python crawler.py <taxcode_or_name>              # search only
    python crawler.py --detail <taxcode>              # full detail (text)
    python crawler.py --detail --json <taxcode>       # full detail (JSON)

Examples:
    python crawler.py 0105987432
    python crawler.py "SOFTDREAMS"
    python crawler.py --detail 0105987432
    python crawler.py --detail --json 0105987432
"""
import sys
import json
import warnings
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore", category=requests.packages.urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://dichvuthongtin.dkkd.gov.vn"
SEARCH_PAGE = f"{BASE_URL}/inf/default.aspx"
SEARCH_API = f"{BASE_URL}/inf/Public/Srv.aspx/GetSearch"
LOAD_MORE_API = f"{BASE_URL}/inf/Forms/Searches/EnterpriseInfo.aspx/LoadMore"
DETAIL_PAGE = f"{BASE_URL}/inf/Forms/Searches/EnterpriseInfo.aspx"

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


@dataclass
class Company:
    id: str
    name: str
    enterprise_code: str
    tax_code: str
    name_foreign: Optional[str] = None
    short_name: Optional[str] = None
    status: Optional[str] = None
    address: Optional[str] = None

    def __str__(self) -> str:
        lines = [f"Tên: {self.name}"]
        if self.name_foreign:
            lines.append(f"Tên nước ngoài: {self.name_foreign}")
        if self.short_name:
            lines.append(f"Tên viết tắt: {self.short_name}")
        lines.append(f"Mã doanh nghiệp: {self.enterprise_code}")
        lines.append(f"Mã số thuế: {self.tax_code}")
        if self.status:
            lines.append(f"Trạng thái: {self.status}")
        if self.address:
            lines.append(f"Địa chỉ: {self.address}")
        return "\n".join(lines)


@dataclass
class BusinessLine:
    code: str
    description: str
    is_main: bool = False

    def __str__(self) -> str:
        marker = " (Chính)" if self.is_main else ""
        return f"{self.code}{marker}: {self.description}"


@dataclass
class CompanyDetail:
    """Full company details combining search data, detail page, and paginated business lines."""
    id: str
    name: str
    enterprise_code: str
    tax_code: str
    name_foreign: Optional[str] = None
    short_name: Optional[str] = None
    status: Optional[str] = None
    address: Optional[str] = None
    address_foreign: Optional[str] = None
    legal_representative: Optional[str] = None
    legal_form: Optional[str] = None
    establishment_date: Optional[str] = None
    city_id: Optional[str] = None
    district_id: Optional[str] = None
    ward_id: Optional[str] = None
    business_lines: list[BusinessLine] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a dict using Vietnamese field names matching the expected output format."""
        return {
            "ten_doanh_nghiep": self.name,
            "ten_tieng_nuoc_ngoai": self.name_foreign or "",
            "ten_viet_tat": self.short_name or "",
            "tinh_trang_hoat_dong": self.status or "",
            "ma_so_doanh_nghiep": self.tax_code,
            "loai_hinh_phap_ly": self.legal_form or "",
            "ngay_bat_dau_thanh_lap": self.establishment_date or "",
            "nguoi_dai_dien_phap_luat": self.legal_representative or "",
            "dia_chi_tru_so_chinh": self.address or "",
        }

    def __str__(self) -> str:
        lines = [f"Tên: {self.name}"]
        if self.name_foreign:
            lines.append(f"Tên nước ngoài: {self.name_foreign}")
        if self.short_name:
            lines.append(f"Tên viết tắt: {self.short_name}")
        lines.append(f"Mã doanh nghiệp: {self.enterprise_code}")
        lines.append(f"Mã số thuế: {self.tax_code}")
        if self.status:
            lines.append(f"Trạng thái: {self.status}")
        if self.legal_form:
            lines.append(f"Loại hình: {self.legal_form}")
        if self.establishment_date:
            lines.append(f"Ngày thành lập: {self.establishment_date}")
        if self.legal_representative:
            lines.append(f"Người đại diện pháp luật: {self.legal_representative}")
        if self.address:
            lines.append(f"Địa chỉ: {self.address}")
        if self.address_foreign:
            lines.append(f"Địa chỉ (nước ngoài): {self.address_foreign}")
        if self.business_lines:
            lines.append(f"Ngành nghề kinh doanh ({len(self.business_lines)} ngành):")
            for bl in self.business_lines:
                lines.append(f"  {bl}")
        return "\n".join(lines)


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
        """Fetch the main page and extract the hdParameter session token required for API calls."""
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
        """
        Search for companies by tax code (mã số thuế) or company name.

        Args:
            query: Tax code or company name fragment

        Returns:
            List of matching Company objects
        """
        payload = json.dumps({"searchField": query, "h": self._token()})

        resp = self._session.post(
            SEARCH_API,
            data=payload,
            verify=False,
            timeout=30,
            headers=_JSON_HEADERS,
        )
        resp.raise_for_status()

        raw_list = resp.json().get("d", [])
        return [_parse_company(c) for c in raw_list]

    def find_exact_by_taxcode(self, taxcode: str) -> Optional[Company]:
        """Return the company whose tax code exactly matches *taxcode*, or None."""
        return next((c for c in self.search(taxcode) if c.tax_code == taxcode), None)

    def get_business_lines(self, company_id: str) -> list[BusinessLine]:
        """
        Fetch all business lines for a company by iterating LoadMore pages.

        Args:
            company_id: The company's internal Id from the search results

        Returns:
            List of BusinessLine objects
        """
        lines: list[BusinessLine] = []
        page_index = 0
        load_more_headers = {
            **_JSON_HEADERS,
            "Referer": f"{BASE_URL}/inf/Forms/Searches/EnterpriseInfo.aspx",
        }

        while True:
            payload = json.dumps({"PageIndex": str(page_index), "EnterpriseID": company_id})
            resp = self._session.post(
                LOAD_MORE_API,
                data=payload,
                verify=False,
                timeout=30,
                headers=load_more_headers,
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

    def get_detail_fields(self, taxcode: str) -> dict:
        """
        Retrieve extra company fields (legal_form, establishment_date) from the detail page.

        Tries a plain HTTP request first; if reCAPTCHA is detected falls back to
        Playwright (headless browser). Returns an empty dict when neither strategy
        can retrieve the data.

        Args:
            taxcode: Tax code used to search and navigate to the company page

        Returns:
            Dict with keys 'legal_form' and/or 'establishment_date'
        """
        result = self._get_detail_fields_via_requests(taxcode)
        if result is not None:
            return result

        return self._get_detail_fields_via_playwright(taxcode)

    def _get_detail_fields_via_requests(self, taxcode: str) -> Optional[dict]:
        """
        Try to fetch the detail page with the existing requests session.

        Returns None if the page is protected by reCAPTCHA (signals fallback needed).
        Returns a dict (possibly empty) on success.
        """
        try:
            resp = self._session.get(
                DETAIL_PAGE,
                params={"taxcode": taxcode},
                verify=False,
                timeout=30,
                headers={"Accept": "text/html,application/xhtml+xml,*/*", "Referer": SEARCH_PAGE},
            )
            resp.raise_for_status()
        except requests.RequestException:
            return None

        html = resp.content.decode("utf-8", errors="replace")
        if any(marker in html.lower() for marker in _CAPTCHA_MARKERS):
            return None  # reCAPTCHA present — caller will use Playwright

        return _parse_detail_html(BeautifulSoup(html, "lxml"))

    def _get_detail_fields_via_playwright(self, taxcode: str) -> dict:
        """
        Use a headless Chromium browser (Playwright) to navigate the site,
        bypass reCAPTCHA, and extract legal_form / establishment_date.

        Requires:  pip install playwright && playwright install chromium
        """
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            print(
                "[crawler] playwright not installed — install with:\n"
                "  pip install playwright && playwright install chromium\n"
                "  Skipping legal_form and establishment_date.",
                file=sys.stderr,
            )
            return {}

        result: dict = {}
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=_USER_AGENT,
                locale="vi-VN",
                extra_http_headers={"Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8"},
            )
            page = ctx.new_page()

            try:
                # Step 1 — load the search page to establish a valid session
                page.goto(SEARCH_PAGE, wait_until="networkidle", timeout=30_000)

                # Step 2 — fill in the search box and submit
                search_input = page.locator(
                    "input[name='ctl00$FldSearch'], input#ctl00_FldSearch, input[type='text']"
                ).first
                search_input.fill(taxcode)

                # Click the search button (common Vietnamese labels)
                search_btn = page.locator(
                    "input[type='submit'], button[type='submit'], "
                    "input[value='Tìm kiếm'], button:has-text('Tìm kiếm'), "
                    "a:has-text('Tìm kiếm')"
                ).first
                search_btn.click()
                page.wait_for_load_state("networkidle", timeout=20_000)

                # Step 3 — click the matching company link in results
                company_link = page.locator(
                    f"a:has-text('{taxcode}'), "
                    "table.result-table td a, "
                    ".search-result a, "
                    "a[href*='EnterpriseInfo']"
                ).first
                company_link.click()
                page.wait_for_load_state("networkidle", timeout=20_000)

                html = page.content()
                result = _parse_detail_html(BeautifulSoup(html, "lxml"))

            except PWTimeout:
                print("[crawler] Playwright timed out loading detail page.", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"[crawler] Playwright error: {exc}", file=sys.stderr)
            finally:
                browser.close()

        return result

    def scrape_by_taxcode(self, taxcode: str) -> Optional["CompanyDetail"]:
        """
        Full pipeline: search by exact tax code and return complete details
        including legal form and establishment date from the detail page.

        Args:
            taxcode: Exact tax code (mã số thuế) to look up

        Returns:
            CompanyDetail with all available information, or None if not found
        """
        payload = json.dumps({"searchField": taxcode, "h": self._token()})
        resp = self._session.post(
            SEARCH_API,
            data=payload,
            verify=False,
            timeout=30,
            headers=_JSON_HEADERS,
        )
        resp.raise_for_status()

        raw_list = resp.json().get("d", [])
        raw = next((r for r in raw_list if r.get("Enterprise_Gdt_Code") == taxcode), None)
        if not raw:
            return None

        company_id = raw.get("Id", "")
        business_lines = self.get_business_lines(company_id)
        extra = self.get_detail_fields(taxcode)

        return _parse_company_detail(raw, business_lines, extra)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

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
    # The API response may already contain these fields; extra (from detail page) wins.
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
    """Extract legal_form and establishment_date from a detail page BeautifulSoup object."""
    result: dict = {}

    # Strategy 1: scan table rows for label/value pairs
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

    # Strategy 2: scan ASP.NET control IDs
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


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def search(query: str) -> list[Company]:
    """Module-level helper: search by tax code or company name."""
    return DKKDCrawler().search(query)


def scrape_by_taxcode(taxcode: str) -> Optional[CompanyDetail]:
    """Module-level helper: full detail scrape for an exact tax code match."""
    return DKKDCrawler().scrape_by_taxcode(taxcode)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Sử dụng: python crawler.py [--detail] [--json] <mã_số_thuế_hoặc_tên_công_ty>")
        print("Ví dụ:   python crawler.py 0105987432")
        print("         python crawler.py --detail 0105987432")
        print("         python crawler.py --detail --json 0105987432")
        sys.exit(1)

    detail_mode = "--detail" in args
    json_mode = "--json" in args
    query_args = [a for a in args if a not in ("--detail", "--json")]
    if not query_args:
        print("Lỗi: thiếu mã số thuế hoặc tên công ty", file=sys.stderr)
        sys.exit(1)

    query = query_args[0]

    if detail_mode:
        print(f"Đang cào toàn bộ thông tin cho mã số thuế: {query}", file=sys.stderr)
        try:
            detail = scrape_by_taxcode(query)
        except Exception as exc:
            print(f"Lỗi: {exc}", file=sys.stderr)
            sys.exit(1)

        if not detail:
            print("Không tìm thấy công ty khớp chính xác với mã số thuế.", file=sys.stderr)
            return

        if json_mode:
            print(json.dumps(detail.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(detail)
    else:
        print(f"Đang tìm kiếm: {query}", file=sys.stderr)
        try:
            results = search(query)
        except Exception as exc:
            print(f"Lỗi: {exc}", file=sys.stderr)
            sys.exit(1)

        if not results:
            print("Không tìm thấy kết quả.", file=sys.stderr)
            return

        if json_mode:
            print(json.dumps([vars(r) for r in results], ensure_ascii=False, indent=2))
        else:
            print(f"Tìm thấy {len(results)} kết quả:\n")
            for i, company in enumerate(results, 1):
                print(f"[{i}]")
                print(company)
                print()


if __name__ == "__main__":
    main()
