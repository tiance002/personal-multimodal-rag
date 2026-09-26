from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser(description="Browser-check the local frontend and knowledge-base UI flow.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--report", type=Path, default=Path("var/reports/frontend-smoke.json"))
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    suffix = uuid.uuid4().hex[:8]
    report: dict[str, object] = {"status": "FAIL", "checks": {}}
    with sync_playwright() as playwright:
        browser_path = os.getenv("PLAYWRIGHT_CHROME_PATH")
        if not browser_path:
            system_chrome = Path(os.getenv("ProgramFiles", "")) / "Google/Chrome/Application/chrome.exe"
            browser_path = str(system_chrome) if system_chrome.is_file() else None
        browser = playwright.chromium.launch(headless=True, **({"executable_path": browser_path} if browser_path else {}))
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        response = page.request.post(
            f"{base_url}/api/v1/knowledge-bases",
            data={
                "name": f"ui-smoke-{suffix}",
                "description": "temporary UI verification",
                "graph_enabled": True,
            },
        )
        if not response.ok:
            raise RuntimeError(f"knowledge base setup failed: {response.status} {response.text()}")
        kb = response.json()["data"]
        temp_dir = tempfile.TemporaryDirectory(prefix="rag-ui-smoke-")
        try:
            page.goto(base_url, wait_until="networkidle")
            page.get_by_text("历史会话").wait_for()
            page.get_by_role("heading", name="知识库").wait_for()
            page.locator("#kb-select").select_option(kb["id"])
            fixture = Path(temp_dir.name) / "ui-smoke.md"
            fixture.write_text("# UI smoke\n\n可回读的界面证据。", encoding="utf-8")
            page.locator('input[type="file"]').set_input_files(str(fixture))
            page.get_by_text("ui-smoke.md").wait_for(timeout=15_000)
            page.get_by_role("button", name="ui-smoke.md").click()
            page.locator(".document-row.selected").wait_for(timeout=15_000)
            page.get_by_role("button", name="图谱").click()
            page.locator(".view-tabs button.active").filter(has_text="图谱").wait_for()
            page.locator("button.graph-build").wait_for(timeout=15_000)
            screenshot = args.report.with_suffix(".png")
            page.screenshot(path=str(screenshot), full_page=True)
            report["checks"] = {
                "three_columns": page.locator(".sidebar").count() == 1 and page.locator(".library-panel").count() == 1 and page.locator(".inspector").count() == 1,
                "selected_kb": page.locator("#kb-select").input_value() == kb["id"],
                "uploaded_document_visible": page.get_by_text("ui-smoke.md").count() == 1,
                "graph_tab_selected": page.locator(".view-tabs button.active").inner_text() == "图谱",
                "graph_build_entry": page.locator("button.graph-build").count() == 1,
                "screenshot": str(screenshot),
            }
            report["status"] = "PASS" if all(value is True or isinstance(value, str) for value in report["checks"].values()) else "FAIL"
        finally:
            try:
                page.request.delete(f"{base_url}/api/v1/knowledge-bases/{kb['id']}")
            finally:
                browser.close()
                temp_dir.cleanup()
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
