# dkkd-crawler

Crawler tra cứu thông tin doanh nghiệp từ `https://dichvuthongtin.dkkd.gov.vn`.
Luồng `scrape_by_taxcode` ưu tiên lấy dữ liệu từ **trang chi tiết doanh nghiệp** và lấy danh sách ngành nghề qua endpoint `LoadMore` theo `EnterpriseID` (không dùng payload từ API search để dựng kết quả cuối).

## Yêu cầu

- Python `>= 3.11`

## Cài đặt

```bash
python -m pip install -e .[dev]
```

## Sử dụng CLI

```bash
dkkd-crawler <ma_so_thue>
```

Ví dụ:

```bash
dkkd-crawler 0105987432
```

Hoặc chạy qua module:

```bash
python -m dkkd_crawler.cli 0105987432
```

Chạy debug (hiện UI browser + log chi tiết):

```bash
dkkd-crawler 0105987432 --debug
```

### Lưu ý CAPTCHA

- Một số trường hợp trang chi tiết yêu cầu reCAPTCHA.
- Mặc định tool chạy ngầm (headless), không hiện UI browser.
- Ở chế độ mặc định, CLI hiển thị spinner ASCII + trạng thái theo phase (search/detail/captcha/business lines).
- Có thể nhấn `Ctrl+C` để hủy tác vụ; CLI sẽ thoát an toàn.
- Chỉ khi thêm `--debug` mới hiện UI browser và log chi tiết.
- Nếu CAPTCHA audio bị Google chặn tự động, có thể cần xác nhận CAPTCHA thủ công trên browser.
- Captcha audio chỉ dùng remote transcription server (không dùng local transcription).

## Dữ liệu trả về (JSON)

CLI in JSON UTF-8 với các key chính:

- `companyName`
- `foreignName`
- `shortName`
- `status`
- `taxCode`
- `enterpriseCode`
- `legalForm`
- `establishmentDate`
- `legalRepresentative`
- `headOfficeAddress`
- `foreignAddress`
- `cityId`
- `districtId`
- `wardId`
- `businessLines` (danh sách ngành, gồm `code`, `description`, `isMain`)

## Dùng như thư viện Python

```python
from dkkd_crawler.crawler import search, scrape_by_taxcode

companies = search("softdream")
detail = scrape_by_taxcode("0105987432")

if detail:
    print(detail.to_dict())
```

## Build và test

```bash
# Build package
uv build

# Chạy test
python -m pytest -q
```
