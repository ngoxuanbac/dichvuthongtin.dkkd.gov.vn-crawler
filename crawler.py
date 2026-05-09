"""
Crawler for dichvuthongtin.dkkd.gov.vn
Searches for Vietnamese company information by tax code or company name.

Usage:
    python crawler.py <taxcode_or_name>

Examples:
    python crawler.py 0105987432
    python crawler.py "SOFTDREAMS"
"""
import sys
import json
import warnings
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore", category=requests.packages.urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://dichvuthongtin.dkkd.gov.vn"
SEARCH_PAGE = f"{BASE_URL}/inf/default.aspx"
SEARCH_API = f"{BASE_URL}/inf/Public/Srv.aspx/GetSearch"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


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
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": SEARCH_PAGE,
            },
        )
        resp.raise_for_status()

        raw_list = resp.json().get("d", [])
        return [_parse_company(c) for c in raw_list]


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


def search(query: str) -> list[Company]:
    """Module-level helper: search by tax code or company name."""
    return DKKDCrawler().search(query)


def main() -> None:
    if len(sys.argv) < 2:
        print("Sử dụng: python crawler.py <mã_số_thuế_hoặc_tên_công_ty>")
        print("Ví dụ:   python crawler.py 0105987432")
        sys.exit(1)

    query = sys.argv[1]
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
