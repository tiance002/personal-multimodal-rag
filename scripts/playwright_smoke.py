from __future__ import annotations

import json
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    suffix = uuid.uuid4().hex[:8]
    report: dict[str, object] = {"status": "FAIL", "checks": {}}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        response = page.request.post("http://127.0.0.1:8000/api/v1/knowledge-bases", data={"name": f"ui-smoke-{suffix}", "description": "temporary UI verification"})
        if not response.ok:
            raise RuntimeError(f"knowledge base setup failed: {response.status} {response.text()}")
        kb = response.json()["data"]
        try:
            page.goto("http://127.0.0.1:5173", wait_until="networkidle")
            page.get_by_text("历史会话").wait_for()
            page.get_by_role("heading", name="知识库").wait_for()
            page.locator("#kb-select").select_option(kb["id"])
            fixture = Path("var/ui-smoke.md")
            fixture.parent.mkdir(parents=True, exist_ok=True)
            fixture.write_text("# UI smoke\n\n可回读的界面证据。", encoding="utf-8")
            page.locator('input[type="file"]').set_input_files(str(fixture.resolve()))
            page.get_by_text("ui-smoke.md").wait_for(timeout=15_000)
            page.get_by_role("button", name="ui-smoke.md").click()
            page.locator(".document-row.selected").wait_for(timeout=15_000)
            page.get_by_role("button", name="图谱").click()
            page.locator(".view-tabs button.active").filter(has_text="图谱").wait_for()
            page.locator("button.graph-build").wait_for(timeout=15_000)
            page.screenshot(path="var/reports/frontend-smoke.png", full_page=True)
            report["checks"] = {
                "three_columns": page.locator(".sidebar").count() == 1 and page.locator(".library-panel").count() == 1 and page.locator(".inspector").count() == 1,
                "selected_kb": page.locator("#kb-select").input_value() == kb["id"],
                "uploaded_document_visible": page.get_by_text("ui-smoke.md").count() == 1,
                "graph_tab_selected": page.locator(".view-tabs button.active").inner_text() == "图谱",
                "graph_build_entry": page.locator("button.graph-build").count() == 1,
                "screenshot": "var/reports/frontend-smoke.png",
            }
            report["status"] = "PASS" if all(value is True or isinstance(value, str) for value in report["checks"].values()) else "FAIL"
        finally:
            page.request.delete(f"http://127.0.0.1:8000/api/v1/knowledge-bases/{kb['id']}")
            browser.close()
    path = Path("var/reports/frontend-smoke.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
