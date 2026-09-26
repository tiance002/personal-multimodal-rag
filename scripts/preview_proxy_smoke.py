from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser(description="Browser-check a frontend's same-origin API proxy.")
    parser.add_argument("--base-url", default="http://127.0.0.1:4173")
    parser.add_argument("--expect-server", default="")
    parser.add_argument("--report", type=Path, default=Path("var/reports/preview-proxy-smoke.json"))
    args = parser.parse_args()
    report: dict[str, object] = {}
    with sync_playwright() as playwright:
        browser_path = os.getenv("PLAYWRIGHT_CHROME_PATH")
        if not browser_path:
            system_chrome = Path(os.getenv("ProgramFiles", "")) / "Google/Chrome/Application/chrome.exe"
            browser_path = str(system_chrome) if system_chrome.is_file() else None
        browser = playwright.chromium.launch(headless=True, **({"executable_path": browser_path} if browser_path else {}))
        page = browser.new_page()
        frontend_response = page.goto(args.base_url.rstrip("/") + "/", wait_until="networkidle")
        report["frontend_status"] = page.title() or "loaded"
        report["server"] = frontend_response.headers.get("server", "") if frontend_response is not None else ""
        response = page.request.get(args.base_url.rstrip("/") + "/api/v1/knowledge-bases")
        report["api_proxy_status"] = response.status
        body = response.json()
        report["api_envelope_valid"] = isinstance(body, dict) and isinstance(body.get("data"), list)
        browser.close()
    report["server_valid"] = not args.expect_server or report["server"].lower().startswith(args.expect_server.lower())
    report["status"] = "PASS" if report["api_proxy_status"] == 200 and report["api_envelope_valid"] and report["server_valid"] else "FAIL"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
