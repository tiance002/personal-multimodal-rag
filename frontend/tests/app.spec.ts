import { test, expect } from "@playwright/test";

test("three-column workbench keeps document and graph tabs explicit", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("历史会话")).toBeVisible();
  await expect(page.getByRole("heading", { name: "知识库" })).toBeVisible();
  await expect(page.getByRole("button", { name: "文档" })).toBeVisible();
  await expect(page.getByRole("button", { name: "图谱" })).toBeVisible();
  await expect(page.getByRole("button", { name: "智能推理" })).toBeVisible();
});
