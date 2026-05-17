from dataclasses import dataclass, field
from typing import Optional


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
        business_lines = [
            {
                "code": bl.code,
                "description": bl.description,
                "isMain": bl.is_main,
            }
            for bl in self.business_lines
        ]
        return {
            "companyName": self.name,
            "foreignName": self.name_foreign or "",
            "shortName": self.short_name or "",
            "status": self.status or "",
            "taxCode": self.tax_code,
            "enterpriseCode": self.enterprise_code,
            "legalForm": self.legal_form or "",
            "establishmentDate": self.establishment_date or "",
            "legalRepresentative": self.legal_representative or "",
            "headOfficeAddress": self.address or "",
            "foreignAddress": self.address_foreign or "",
            "cityId": self.city_id or "",
            "districtId": self.district_id or "",
            "wardId": self.ward_id or "",
            "businessLines": business_lines,
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
