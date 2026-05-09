"""Tests for the DKKD crawler."""
import json
from unittest.mock import MagicMock, patch

import pytest

from crawler import Company, DKKDCrawler, _parse_company, search


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
            "Ho_Address": "Số 1 Đường ABC, Hà Nội",
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


class TestDKKDCrawler:
    def _make_crawler_with_mocked_session(self, main_page_html: str, api_response: dict):
        crawler = DKKDCrawler()
        mock_session = MagicMock()

        main_resp = _make_response(main_page_html)
        api_resp = _make_response(api_response)

        mock_session.get.return_value = main_resp
        mock_session.post.return_value = api_resp

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
        assert crawler._session.get.call_count == 1  # Token loaded only once

    def test_raises_on_missing_token(self):
        crawler = DKKDCrawler()
        bad_page = "<html><body>No token here</body></html>"
        mock_session = MagicMock()
        mock_session.get.return_value = _make_response(bad_page)
        crawler._session = mock_session

        with pytest.raises(RuntimeError, match="Session token not found"):
            crawler.search("anything")


class TestSearchFunction:
    def test_module_level_search(self):
        with patch("crawler.DKKDCrawler") as MockCrawler:
            instance = MockCrawler.return_value
            instance.search.return_value = [
                Company(id="1", name="X", enterprise_code="0001", tax_code="0002")
            ]
            results = search("query")
        assert len(results) == 1
        instance.search.assert_called_once_with("query")
