import { test, expect } from "@playwright/test";

// SIMULATED API; real browser layout and input behavior, zero provider calls.
test("five model roles save local configuration and never refill secrets", async ({ page }) => {
  const roleNames = ["embedding", "rerank", "chat_cheap", "chat_expensive", "vision"];
  let config = {revision:"environment", roles:roleNames.map(role => ({role, provider:"siliconflow", model:`model-${role}`, base_url:"https://api.siliconflow.cn/v1", key_configured:true}))};
  let submitted: unknown;
  await page.route("**/api/v1/knowledge-bases", route => route.fulfill({json:{data:[]}}));
  await page.route("**/api/v1/conversations", route => route.fulfill({json:{data:[]}}));
  await page.route("**/api/v1/memory/**", route => route.fulfill({status:503,json:{error:{code:"MEMORY_PRINCIPAL_REQUIRED",message:"not configured"}}}));
  await page.route("**/api/v1/settings/models", route => route.fulfill({json:{data:{provider:"siliconflow",status:"READY_CONFIG",roles:{}}}}));
  await page.route("**/api/v1/settings/model-configuration", async route => {
    if (route.request().method() === "PUT") {
      submitted = route.request().postDataJSON();
      config = {revision:"saved-r1",roles:(submitted as typeof config).roles.map(({role,provider,model,base_url}) => ({role,provider,model,base_url,key_configured:true}))};
    }
    await route.fulfill({json:{data:config}});
  });
  await page.goto("/");
  await page.getByRole("button", {name:"系统设置",exact:true}).click();
  await expect(page.locator(".model-role-card")).toHaveCount(5);
  await expect(page.getByRole("textbox", {name:"Embedding API Key",exact:true})).toHaveValue("");
  await page.getByRole("textbox", {name:"Chat · cheap 模型名称",exact:true}).fill("new-chat-model");
  await page.getByRole("textbox", {name:"Chat · cheap API Key",exact:true}).fill("SIMULATED_TEST_KEY");
  await page.getByRole("button", {name:"保存模型配置",exact:true}).click();
  await expect(page.getByText("模型配置已保存并生效。",{exact:true})).toBeVisible();
  expect((submitted as {roles:{role:string;model:string;api_key:string}[]}).roles.find(row => row.role === "chat_cheap")).toMatchObject({model:"new-chat-model",api_key:"SIMULATED_TEST_KEY"});
  await expect(page.getByRole("textbox", {name:"Chat · cheap API Key",exact:true})).toHaveValue("");
  await expect(page.getByText("MEMORY_PRINCIPAL_REQUIRED",{exact:false})).toHaveCount(0);
  await page.reload();
  await page.getByRole("button", {name:"系统设置",exact:true}).click();
  await expect(page.getByRole("textbox", {name:"Chat · cheap 模型名称",exact:true})).toHaveValue("new-chat-model");
});

test("scope picker shows complete long names without horizontal overflow", async ({ page }) => {
  const base = "我建立的知识库：科学学习方法与原始研究资料";
  const file = "科学学习方法——关于记忆、理解与实践的研究资料及参考文献完整版.docx";
  await page.route("**/api/v1/knowledge-bases", route => route.fulfill({json:{data:[{id:"kb",name:base,graph_enabled:false}]}}));
  await page.route("**/api/v1/conversations", route => route.fulfill({json:{data:[]}}));
  await page.route("**/api/v1/knowledge-bases/kb/documents", route => route.fulfill({json:{data:[{id:"doc",file_name:file,index_status:"ready",version_no:1}]}}));
  await page.goto("/");
  await page.getByRole("button", {name:"选择知识库和文件"}).click();
  const names = page.locator(".scope-option-label");
  await expect(names.filter({hasText:base})).toBeVisible();
  await expect(names.filter({hasText:file})).toBeVisible();
  for (const width of [1200, 600]) {
    await page.setViewportSize({width,height:800});
    const result = await page.locator(".scope-popover").evaluate(element => ({overflow:element.scrollWidth > element.clientWidth, widths:[...element.querySelectorAll('.scope-option-label')].map(label=>label.getBoundingClientRect().width)}));
    expect(result.overflow).toBe(false);
    expect(result.widths.every(value=>value > 180)).toBe(true);
  }
});
