import json
import sys

from .crawler import scrape_by_taxcode

# Ensure stdout handles Vietnamese characters on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        print("Sử dụng: dkkd-crawler <mã_số_thuế>", file=sys.stderr)
        print("Ví dụ:   dkkd-crawler 0105987432", file=sys.stderr)
        sys.exit(1)

    taxcode = sys.argv[1]
    try:
        detail = scrape_by_taxcode(taxcode)
    except Exception as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        sys.exit(1)

    if not detail:
        print(f"Không tìm thấy công ty với mã số thuế: {taxcode}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(detail.to_dict(), ensure_ascii=False, indent=2))
