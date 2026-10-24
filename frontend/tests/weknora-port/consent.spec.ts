import { expect, test } from "@playwright/test";

const envelope = (data: unknown) => ({ data });
async function mocks(page: import("@playwright/test").Page, fail = false) {
  const writes: boolean[] = [];
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/external-processing")) {
      expect(route.request().method()).toBe("PATCH");
      const body = route.request().postDataJSON();
      expect(Object.keys(body)).toEqual(["cloud_allowed"]);
      writes.push(body.cloud_allowed);
      if (fail) return route.fulfill({ status: 403, json: { error: { code: "CONSENT_DENIED", message: "许可保存失败" } } });
      return route.fulfill({ json: envelope({ id: "kb-1", name: "课程资料", cloud_allowed: body.cloud_allowed, document_count: 0 }) });
    }
    const data = path.endsWith("/runtime") ? { implementation: "weknora-python-port" }
      : path.endsWith("/knowledge-bases") ? [{ id: "kb-1", name: "课程资料", cloud_allowed: false, document_count: 0 }]
      : path.endsWith("/model-configuration") ? { revision: 1, roles: [] } : [];
    return route.fulfill({ json: envelope(data) });
  });
  return writes;
}

async function openConsent(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("menuitem", { name: "资料库", exact: true }).click();
  await page.getByRole("button", { name: /课程资料/ }).click();
  return page.getByRole("switch", { name: "允许当前知识库使用云端模型" });
}

test("current KB consent displays false and persists explicit enable/disable without model calls", async ({ page }) => {
  const writes = await mocks(page);
  const control = await openConsent(page);
  await expect(control).not.toBeChecked();
  await expect(page.getByText(/问题、检索片段和图片可发送至已配置的云端模型/)).toBeVisible();
  await control.click();
  await expect(control).toBeChecked();
  await expect(page.getByText("当前允许外发", { exact: true })).toBeVisible();
  await control.click();
  await expect(control).not.toBeChecked();
  await expect(page.getByText("当前禁止外发", { exact: true })).toBeVisible();
  expect(writes).toEqual([true, false]);
});

test("failed consent save keeps persisted false and shows inline error", async ({ page }) => {
  const writes = await mocks(page, true);
  const control = await openConsent(page);
  await control.click();
  await expect(page.getByRole("alert")).toContainText("许可保存失败");
  await expect(control).not.toBeChecked();
  expect(writes).toEqual([true]);
});

test("runtime identity change blocks consent mutation", async ({ page }) => {
  const writes = await mocks(page);
  const control = await openConsent(page);
  await page.route("**/api/v1/runtime", route => route.fulfill({ json: envelope({ implementation: "legacy-rag" }) }));
  await control.click();
  await expect(page.getByRole("alert")).toContainText("身份不匹配");
  await expect(control).not.toBeChecked();
  expect(writes).toEqual([]);
});
