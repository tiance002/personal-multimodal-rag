import { expect, test } from "@playwright/test";

test("missing API base is clearly refused without issuing an API request", async ({ page }) => {
  const apiRequests: string[] = [];
  page.on("request", request => {
    if (new URL(request.url()).pathname.startsWith("/api/")) apiRequests.push(request.url());
  });

  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("未配置前端 API 地址");
  await expect(page.getByRole("alert")).toContainText("VITE_WEKNORA_API_BASE");
  await expect(page.getByRole("button", { name: "发送" })).toBeDisabled();
  expect(apiRequests).toEqual([]);
});
