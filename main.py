"""
CLI entry point for the DKKD crawler.

Usage:
    python main.py <mã_số_thuế>

Example:
    python main.py 0105987432
"""
import sys
import json

from crawler import scrape_by_taxcode


def main() -> None:
    if len(sys.argv) != 2:
        print("Sử dụng: python main.py <mã_số_thuế>", file=sys.stderr)
        print("Ví dụ:   python main.py 0105987432", file=sys.stderr)
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


if __name__ == "__main__":
    main()
