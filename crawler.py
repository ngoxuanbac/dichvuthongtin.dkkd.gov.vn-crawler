"""
Crawler for dichvuthongtin.dkkd.gov.vn
Searches for Vietnamese company information by tax code or company name,
and scrapes full details (business lines) for an exact tax code match.

The site uses an hdParameter session token on the search API and a
LoadMore page method for paginated business lines — no CAPTCHA is
required for either endpoint.

Usage:
    python crawler.py <taxcode_or_name>         # search only
    python crawler.py --detail <taxcode>         # full detail

Examples:
    python crawler.py 0105987432
    python crawler.py "SOFTDREAMS"
    python crawler.py --detail 0105987432
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
    """Full company details combining search data and paginated business lines."""
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
    city_id: Optional[str] = None
    district_id: Optional[str] = None
    ward_id: Optional[str] = None
    business_lines: list[BusinessLine] = field(default_factory=list)

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

    def scrape_by_taxcode(self, taxcode: str) -> Optional[CompanyDetail]:
        """
        Full pipeline: search by exact tax code and return complete details.

        Finds the company with an exact match on *taxcode*, then fetches all
        paginated business lines via the LoadMore endpoint.

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
        return _parse_company_detail(raw, business_lines)


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


def _parse_company_detail(raw: dict, business_lines: list[BusinessLine]) -> CompanyDetail:
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
        city_id=raw.get("City_Id") or None,
        district_id=raw.get("District_Id") or None,
        ward_id=raw.get("Ward_Id") or None,
        business_lines=business_lines,
    )


def search(query: str) -> list[Company]:
    """Module-level helper: search by tax code or company name."""
    return DKKDCrawler().search(query)


def scrape_by_taxcode(taxcode: str) -> Optional[CompanyDetail]:
    """Module-level helper: full detail scrape for an exact tax code match."""
    return DKKDCrawler().scrape_by_taxcode(taxcode)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Sử dụng: python crawler.py [--detail] <mã_số_thuế_hoặc_tên_công_ty>")
        print("Ví dụ:   python crawler.py 0105987432")
        print("         python crawler.py --detail 0105987432")
        sys.exit(1)

    detail_mode = "--detail" in args
    query_args = [a for a in args if a != "--detail"]
    if not query_args:
        print("Lỗi: thiếu mã số thuế hoặc tên công ty", file=sys.stderr)
        sys.exit(1)

    query = query_args[0]

    if detail_mode:
        print(f"Đang cào toàn bộ thông tin cho mã số thuế: {query}")
        print("-" * 60)
        try:
            detail = scrape_by_taxcode(query)
        except Exception as exc:
            print(f"Lỗi: {exc}", file=sys.stderr)
            sys.exit(1)

        if not detail:
            print("Không tìm thấy công ty khớp chính xác với mã số thuế.")
            return

        print(detail)
    else:
        print(f"Đang tìm kiếm: {query}")
        print("-" * 60)
        try:
            results = search(query)
        except Exception as exc:
            print(f"Lỗi: {exc}", file=sys.stderr)
            sys.exit(1)

        if not results:
            print("Không tìm thấy kết quả.")
            return

        print(f"Tìm thấy {len(results)} kết quả:\n")
        for i, company in enumerate(results, 1):
            print(f"[{i}]")
            print(company)
            print()


if __name__ == "__main__":
    main()
