import argparse
import json
import sys
import threading
import time

from .crawler import scrape_by_taxcode

# Ensure stdout handles Vietnamese characters on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def _scrape_with_spinner(taxcode: str, debug: bool):
    if debug:
        return scrape_by_taxcode(taxcode, debug=True)

    last_status = ""

    # Show status lines on non-interactive terminals (logs/redirected output).
    if not sys.stderr.isatty():
        def _status_callback(status: str) -> None:
            nonlocal last_status
            if not status or status == last_status:
                return
            last_status = status
            print(f"[status] {status}", file=sys.stderr, flush=True)

        return scrape_by_taxcode(taxcode, debug=False, status_callback=_status_callback)

    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    message = "starting"
    done = threading.Event()
    lock = threading.Lock()

    def _status_callback(status: str) -> None:
        nonlocal message, last_status
        if not status or status == last_status:
            return
        last_status = status
        with lock:
            message = status

    def _spinner() -> None:
        i = 0
        while not done.wait(0.12):
            frame = frames[i % len(frames)]
            i += 1
            with lock:
                current = message
            print(f"\r{frame} {current}\033[K", end="", file=sys.stderr, flush=True)

    spinner = threading.Thread(target=_spinner, daemon=True)
    spinner.start()

    try:
        return scrape_by_taxcode(taxcode, debug=False, status_callback=_status_callback)
    finally:
        done.set()
        spinner.join(timeout=1.0)
        # Clear entire spinner line before printing final output/errors
        print("\r\033[2K", end="", file=sys.stderr, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dkkd-crawler",
        description="Lookup company information by tax code",
    )
    parser.add_argument("taxcode", help="Company tax code")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode: show browser UI and verbose logs",
    )
    args = parser.parse_args()

    taxcode = args.taxcode
    start_time = time.perf_counter()
    try:
        detail = _scrape_with_spinner(taxcode, debug=args.debug)
    except KeyboardInterrupt:
        print("cancelled by user (Ctrl+C).", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        elapsed = time.perf_counter() - start_time
        print(f"[time] processed in {elapsed:.2f}s", file=sys.stderr)

    if not detail:
        print(f"not found company with tax code: {taxcode}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(detail.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
