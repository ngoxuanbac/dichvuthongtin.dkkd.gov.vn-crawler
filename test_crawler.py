"""Tests for the DKKD crawler."""
import json
from unittest.mock import MagicMock, call, patch

import pytest

from crawler import (
    BusinessLine,
    Company,
    CompanyDetail,
    DKKDCrawler,
    _parse_company,
    _parse_company_detail,
    scrape_by_taxcode,
    search,
)


FAKE_HD_PARAM = "639139000000000000-ABCDEF1234567890ABCDEF1234567890ABCDEF12"

FAKE_API_RESPONSE = {
    "d": [
        {
            "__type": "Inf.BusinessLayer.BusinessEntities.ApacheSolr.Enterprise",
            "Id": "12345",
            "Name": "CÔNG TY CỔ PHẦN TEST",
            "Name_F": "TEST JOINT STOCK COMPANY",
            "Short_Name": "TEST JSC",
            "Enterprise_Code": "0001234567",
            "Enterprise_Gdt_Code": "0105987432",
            "Status": None,
            "City_Id": "01",
            "District_Id": "001",
            "Ward_Id": "00001",
            "Ho_Address": "Số 1 Đường ABC, Hà Nội",
            "Ho_Address_F": "No 1 ABC Street, Hanoi",
            "Legal_First_Name": "NGUYỄN VĂN A",
        }
    ]
}

FAKE_MAIN_PAGE = f"""
<html><body>
<form name="aspnetForm" method="post" action="./default.aspx">
<input type="hidden" name="ctl00$hdParameter" id="ctl00_hdParameter" value="{FAKE_HD_PARAM}" />
<input name="ctl00$FldSearch" type="text" id="ctl00_FldSearch" />
</form>
</body></html>
"""

FAKE_LOAD_MORE_ROWS_P0 = (
    "<tr><td>5829 (Chính)</td><td><div>Xuất bản phần mềm khác</div></td></tr>"
    "<tr><td>6201</td><td><div>Lập trình máy tính</div></td></tr>"
)
FAKE_LOAD_MORE_ROWS_P1 = (
    "<tr><td>6290</td><td><div>Dịch vụ công nghệ thông tin khác</div></td></tr>"
)


def _make_response(content: str | bytes | dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    if isinstance(content, dict):
        resp.content = json.dumps(content).encode("utf-8")
        resp.json.return_value = content
    elif isinstance(content, str):
        resp.content = content.encode("utf-8")
    else:
        resp.content = content
    return resp


class TestParseCompany:
    def test_full_record(self):
        raw = {
            "Id": "1",
            "Name": "ACME Corp",
            "Name_F": "ACME",
            "Short_Name": "ACME",
            "Enterprise_Code": "0001",
            "Enterprise_Gdt_Code": "0000000001",
            "Status": "Active",
            "Ho_Address": "123 Main St",
        }
        company = _parse_company(raw)
        assert company.id == "1"
        assert company.name == "ACME Corp"
        assert company.name_foreign == "ACME"
        assert company.enterprise_code == "0001"
        assert company.tax_code == "0000000001"
        assert company.status == "Active"
        assert company.address == "123 Main St"

    def test_empty_optional_fields(self):
        raw = {
            "Id": "2",
            "Name": "Minimal Co",
            "Name_F": None,
            "Short_Name": "",
            "Enterprise_Code": "0002",
            "Enterprise_Gdt_Code": "0000000002",
            "Status": None,
            "Ho_Address": None,
        }
        company = _parse_company(raw)
        assert company.name_foreign is None
        assert company.short_name is None
        assert company.status is None
        assert company.address is None

    def test_missing_keys(self):
        company = _parse_company({})
        assert company.id == ""
        assert company.name == ""
        assert company.enterprise_code == ""


class TestParseCompanyDetail:
    def test_full_record(self):
        raw = {
            "Id": "1",
            "Name": "ACME Corp",
            "Name_F": "ACME International",
            "Short_Name": "ACME",
            "Enterprise_Code": "0001",
            "Enterprise_Gdt_Code": "0000000001",
            "Status": "ACT",
            "Ho_Address": "123 Main St",
            "Ho_Address_F": "123 Main Street",
            "Legal_First_Name": "JOHN DOE",
            "City_Id": "01",
            "District_Id": "001",
            "Ward_Id": "00001",
        }
        bl = [BusinessLine(code="6201", description="Software development")]
        detail = _parse_company_detail(raw, bl)
        assert detail.id == "1"
        assert detail.name == "ACME Corp"
        assert detail.name_foreign == "ACME International"
        assert detail.enterprise_code == "0001"
        assert detail.tax_code == "0000000001"
        assert detail.status == "ACT"
        assert detail.address == "123 Main St"
        assert detail.address_foreign == "123 Main Street"
        assert detail.legal_representative == "JOHN DOE"
        assert detail.city_id == "01"
        assert detail.district_id == "001"
        assert detail.ward_id == "00001"
        assert len(detail.business_lines) == 1
        assert detail.business_lines[0].code == "6201"

    def test_empty_optional_fields(self):
        raw = {
            "Id": "2", "Name": "Co", "Enterprise_Code": "0002",
            "Enterprise_Gdt_Code": "0002", "Name_F": None, "Legal_First_Name": None,
            "Ho_Address_F": "", "City_Id": None,
        }
        detail = _parse_company_detail(raw, [])
        assert detail.name_foreign is None
        assert detail.address_foreign is None
        assert detail.legal_representative is None
        assert detail.city_id is None
        assert detail.business_lines == []


class TestBusinessLine:
    def test_str_non_main(self):
        bl = BusinessLine(code="6201", description="Lập trình máy tính")
        assert str(bl) == "6201: Lập trình máy tính"

    def test_str_main(self):
        bl = BusinessLine(code="5829", description="Xuất bản phần mềm", is_main=True)
        assert str(bl) == "5829 (Chính): Xuất bản phần mềm"


class TestCompanyStr:
    def test_str_includes_required_fields(self):
        company = Company(
            id="1",
            name="Test Co",
            enterprise_code="0001",
            tax_code="0000000001",
        )
        output = str(company)
        assert "Test Co" in output
        assert "0001" in output
        assert "0000000001" in output

    def test_str_includes_optional_fields_when_set(self):
        company = Company(
            id="1",
            name="Test",
            enterprise_code="0001",
            tax_code="0002",
            address="123 Street",
            status="Active",
        )
        output = str(company)
        assert "123 Street" in output
        assert "Active" in output


class TestCompanyDetailStr:
    def test_str_includes_all_available_fields(self):
        detail = CompanyDetail(
            id="1",
            name="Tech Co",
            enterprise_code="0001",
            tax_code="0002",
            legal_representative="NGUYEN VAN A",
            address="123 Street",
            address_foreign="123 Street, Vietnam",
            business_lines=[
                BusinessLine(code="6201", description="Software dev", is_main=True),
                BusinessLine(code="6290", description="IT services"),
            ],
        )
        output = str(detail)
        assert "Tech Co" in output
        assert "NGUYEN VAN A" in output
        assert "123 Street, Vietnam" in output
        assert "6201" in output
        assert "6290" in output
        assert "2 ngành" in output


class TestDKKDCrawlerSearch:
    def _make_crawler_with_mocked_session(self, main_page_html: str, api_response: dict):
        crawler = DKKDCrawler()
        mock_session = MagicMock()
        mock_session.get.return_value = _make_response(main_page_html)
        mock_session.post.return_value = _make_response(api_response)
        crawler._session = mock_session
        return crawler

    def test_search_returns_companies(self):
        crawler = self._make_crawler_with_mocked_session(FAKE_MAIN_PAGE, FAKE_API_RESPONSE)
        results = crawler.search("0105987432")
        assert len(results) == 1
        assert results[0].name == "CÔNG TY CỔ PHẦN TEST"
        assert results[0].tax_code == "0105987432"

    def test_search_passes_h_parameter(self):
        crawler = self._make_crawler_with_mocked_session(FAKE_MAIN_PAGE, FAKE_API_RESPONSE)
        crawler.search("test")
        post_call = crawler._session.post.call_args
        payload = json.loads(post_call.kwargs["data"])
        assert payload["h"] == FAKE_HD_PARAM
        assert payload["searchField"] == "test"

    def test_search_empty_results(self):
        crawler = self._make_crawler_with_mocked_session(FAKE_MAIN_PAGE, {"d": []})
        results = crawler.search("nonexistent")
        assert results == []

    def test_token_loaded_once_for_multiple_searches(self):
        crawler = self._make_crawler_with_mocked_session(FAKE_MAIN_PAGE, {"d": []})
        crawler.search("first")
        crawler.search("second")
        assert crawler._session.get.call_count == 1

    def test_raises_on_missing_token(self):
        crawler = DKKDCrawler()
        mock_session = MagicMock()
        mock_session.get.return_value = _make_response("<html><body>No token here</body></html>")
        crawler._session = mock_session
        with pytest.raises(RuntimeError, match="Session token not found"):
            crawler.search("anything")


class TestFindExactByTaxcode:
    def test_finds_exact_match(self):
        crawler = DKKDCrawler()
        crawler._h = "token"
        mock_session = MagicMock()
        mock_session.post.return_value = _make_response(FAKE_API_RESPONSE)
        crawler._session = mock_session

        result = crawler.find_exact_by_taxcode("0105987432")
        assert result is not None
        assert result.tax_code == "0105987432"

    def test_returns_none_for_no_match(self):
        crawler = DKKDCrawler()
        crawler._h = "token"
        mock_session = MagicMock()
        mock_session.post.return_value = _make_response({"d": []})
        crawler._session = mock_session

        result = crawler.find_exact_by_taxcode("0000000000")
        assert result is None

    def test_returns_none_for_partial_match_only(self):
        crawler = DKKDCrawler()
        crawler._h = "token"
        mock_session = MagicMock()
        # Only a subsidiary with a suffix code returned
        mock_session.post.return_value = _make_response({
            "d": [{"Id": "1", "Name": "Sub Co", "Enterprise_Code": "X",
                   "Enterprise_Gdt_Code": "0105987432-001"}]
        })
        crawler._session = mock_session

        result = crawler.find_exact_by_taxcode("0105987432")
        assert result is None


class TestGetBusinessLines:
    def _make_load_more_mock(self, pages: list[str]) -> MagicMock:
        responses = [_make_response({"d": p}) for p in pages]
        responses.append(_make_response({"d": ""}))  # terminal empty page
        mock = MagicMock()
        mock.post.side_effect = responses
        return mock

    def test_returns_all_business_lines_across_pages(self):
        crawler = DKKDCrawler()
        crawler._h = "token"
        crawler._session = self._make_load_more_mock(
            [FAKE_LOAD_MORE_ROWS_P0, FAKE_LOAD_MORE_ROWS_P1]
        )

        lines = crawler.get_business_lines("12345")
        assert len(lines) == 3
        codes = {bl.code for bl in lines}
        assert "5829" in codes
        assert "6201" in codes
        assert "6290" in codes

    def test_marks_main_business_line(self):
        crawler = DKKDCrawler()
        crawler._h = "token"
        crawler._session = self._make_load_more_mock([FAKE_LOAD_MORE_ROWS_P0])

        lines = crawler.get_business_lines("12345")
        main_lines = [bl for bl in lines if bl.is_main]
        assert len(main_lines) == 1
        assert main_lines[0].code == "5829"

    def test_terminates_on_empty_response(self):
        crawler = DKKDCrawler()
        crawler._h = "token"
        mock_session = MagicMock()
        mock_session.post.return_value = _make_response({"d": ""})
        crawler._session = mock_session

        lines = crawler.get_business_lines("12345")
        assert lines == []
        assert mock_session.post.call_count == 1

    def test_passes_correct_page_index_and_enterprise_id(self):
        crawler = DKKDCrawler()
        crawler._h = "token"
        crawler._session = self._make_load_more_mock([FAKE_LOAD_MORE_ROWS_P0])

        crawler.get_business_lines("99999")
        call_payload = json.loads(crawler._session.post.call_args_list[0].kwargs["data"])
        assert call_payload["PageIndex"] == "0"
        assert call_payload["EnterpriseID"] == "99999"


class TestScrapeByTaxcode:
    def _make_crawler_for_scrape(
        self, search_response: dict, load_more_pages: list[str]
    ) -> DKKDCrawler:
        crawler = DKKDCrawler()
        crawler._h = "token"
        mock_session = MagicMock()

        load_more_responses = [_make_response({"d": p}) for p in load_more_pages]
        load_more_responses.append(_make_response({"d": ""}))

        # First post = search, subsequent = load_more pages
        mock_session.post.side_effect = [
            _make_response(search_response),
            *load_more_responses,
        ]
        crawler._session = mock_session
        return crawler

    def test_returns_full_detail_for_exact_match(self):
        crawler = self._make_crawler_for_scrape(
            FAKE_API_RESPONSE, [FAKE_LOAD_MORE_ROWS_P0, FAKE_LOAD_MORE_ROWS_P1]
        )
        detail = crawler.scrape_by_taxcode("0105987432")
        assert detail is not None
        assert detail.tax_code == "0105987432"
        assert detail.name == "CÔNG TY CỔ PHẦN TEST"
        assert detail.legal_representative == "NGUYỄN VĂN A"
        assert detail.address_foreign == "No 1 ABC Street, Hanoi"
        assert len(detail.business_lines) == 3

    def test_returns_none_when_no_exact_match(self):
        crawler = self._make_crawler_for_scrape({"d": []}, [])
        detail = crawler.scrape_by_taxcode("0000000000")
        assert detail is None

    def test_returns_none_when_only_subsidiary_matches(self):
        response = {
            "d": [{"Id": "1", "Name": "Sub", "Enterprise_Code": "X",
                   "Enterprise_Gdt_Code": "0105987432-001"}]
        }
        crawler = self._make_crawler_for_scrape(response, [])
        detail = crawler.scrape_by_taxcode("0105987432")
        assert detail is None

    def test_includes_city_and_ward_ids(self):
        crawler = self._make_crawler_for_scrape(FAKE_API_RESPONSE, [])
        detail = crawler.scrape_by_taxcode("0105987432")
        assert detail is not None
        assert detail.city_id == "01"
        assert detail.district_id == "001"
        assert detail.ward_id == "00001"


class TestModuleLevelHelpers:
    def test_search_module_function(self):
        with patch("crawler.DKKDCrawler") as MockCrawler:
            instance = MockCrawler.return_value
            instance.search.return_value = [
                Company(id="1", name="X", enterprise_code="0001", tax_code="0002")
            ]
            results = search("query")
        assert len(results) == 1
        instance.search.assert_called_once_with("query")

    def test_scrape_by_taxcode_module_function(self):
        with patch("crawler.DKKDCrawler") as MockCrawler:
            instance = MockCrawler.return_value
            expected = CompanyDetail(
                id="1", name="X", enterprise_code="0001", tax_code="0002"
            )
            instance.scrape_by_taxcode.return_value = expected
            result = scrape_by_taxcode("0002")
        assert result is expected
        instance.scrape_by_taxcode.assert_called_once_with("0002")
