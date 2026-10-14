// Actual native React -> loopback FastAPI -> isolated PG fixture, no provider network.
import { chromium, expect } from '../frontend/node_modules/@playwright/test/index.mjs';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
const root=process.cwd(), evidence=path.join(root,'docs/weknora-port/evidence');
const ready=JSON.parse(fs.readFileSync(path.join(root,'var/weknora-port/browser-fixture/ready.json'),'utf8'));
if(ready.kind!=='PUBLIC_ISOLATED_MOCK_PROVIDER_FIXTURE'||ready.provider_calls_real!==0||ready.url!=='http://127.0.0.1:56425')throw Error('NEW_CLOSED_MOCK_FIXTURE_REQUIRED');
const profile=path.join('E:/WK/run','memory-ui-'+crypto.randomBytes(6).toString('hex'));
const context=await chromium.launchPersistentContext(profile,{executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,acceptDownloads:true,viewport:{width:1440,height:1000}});
const page=await context.newPage();const mutations=[];
page.on('request',request=>{if(request.url().includes('/memory/')&&request.method()!=='GET')mutations.push({method:request.method(),path:new URL(request.url()).pathname});});
try{
 await page.goto('http://127.0.0.1:56426');
 await page.getByRole('button',{name:'长期记忆',exact:true}).click();
 await expect(page.getByRole('button',{name:'新增记忆',exact:true})).toBeEnabled();
 const content='Public fixture: widgets should remain dry.';
 const updated='Public fixture: widgets use W-17 and must remain dry.';
 await page.getByRole('button',{name:'新增记忆',exact:true}).click();
 await page.getByLabel('记忆类型',{exact:true}).selectOption('fact');
 await page.getByLabel('记忆内容',{exact:true}).fill(content);
 await page.getByLabel('记忆重要程度',{exact:true}).fill('4');
 await page.getByRole('button',{name:'保存记忆',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'新增记忆'})).toHaveCount(0);
 let card=page.locator('article.wp-memory-item').filter({hasText:content});await expect(card).toHaveCount(1);
 await card.getByRole('button',{name:/编辑/}).click();
 await expect(page.getByLabel('记忆重要程度',{exact:true})).toHaveValue('4');
 await page.getByLabel('记忆内容',{exact:true}).fill(updated);
 await page.getByRole('button',{name:'保存记忆',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'编辑记忆'})).toHaveCount(0);
 await page.reload();await page.getByRole('button',{name:'长期记忆',exact:true}).click();
 await expect(page.locator('article.wp-memory-item').filter({hasText:updated})).toHaveCount(1);
 const downloadWait=page.waitForEvent('download');await page.getByRole('button',{name:'导出记忆',exact:true}).click();
 const download=await downloadWait;const target=path.join(evidence,'P4_MEMORY_MANAGER_UI_EXPORT_20261014.json');await download.saveAs(target);
 const exported=JSON.parse(fs.readFileSync(target,'utf8'));if(exported.truncated||!exported.data.some(row=>row.content===updated&&row.importance===4)||exported.total!==exported.data.length)throw Error('PERSISTED_EXPORT_MISMATCH');
 const before=mutations.filter(row=>row.method==='DELETE'&&row.path.endsWith('/memory/items')).length;
 await page.getByRole('button',{name:'清空记忆',exact:true}).click();await page.getByRole('button',{name:'取消清空',exact:true}).click();
 if(mutations.filter(row=>row.method==='DELETE'&&row.path.endsWith('/memory/items')).length!==before)throw Error('CLEAR_CANCEL_MUTATED');
 await expect(page.locator('article.wp-memory-item').filter({hasText:updated})).toHaveCount(1);
 await page.screenshot({path:path.join(evidence,'P4_MEMORY_MANAGER_UI_20261014.png'),fullPage:true});
 await page.getByRole('button',{name:'清空记忆',exact:true}).click();await page.getByRole('button',{name:'确认清空',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'确认清空记忆'})).toHaveCount(0);
 await expect(page.locator('article.wp-memory-item')).toHaveCount(0);
 const response=await context.request.get('http://127.0.0.1:56425/api/v1/memory/items');const empty=await response.json();if(!response.ok()||empty.total!==0||empty.data.length!==0)throw Error('CLEAR_PERSISTENCE_MISMATCH');
 // Runtime identity mismatch must prevent submitting the edit to any implementation.
 await page.route('**/api/v1/runtime',route=>route.fulfill({json:{data:{implementation:'foreign-runtime'}}}));
 await page.getByRole('button',{name:'新增记忆',exact:true}).click();await page.getByLabel('记忆内容',{exact:true}).fill('DENIED fixture draft');
 const posts=mutations.filter(row=>row.method==='POST'&&row.path.endsWith('/memory/items')).length;
 await page.getByRole('button',{name:'保存记忆',exact:true}).click();await expect(page.locator('[role=alert]')).toContainText('身份不匹配');
 if(mutations.filter(row=>row.method==='POST'&&row.path.endsWith('/memory/items')).length!==posts)throw Error('IDENTITY_DENIAL_MUTATED');
 const receipt={kind:'ACTUAL_NATIVE_MEMORY_MANAGER_BROWSER_HTTP_PG',tenant:ready.principal_tenant,profile,create:true,edit:true,reload:true,export_count:exported.data.length,export_metadata_preserved:true,cancel_clear_no_write:true,confirmed_clear_persisted:true,runtime_identity_denied_no_write:true,mutations,real_provider_calls:0,backend_scope_invariants:'prior b502055 evidence reused; no foreign-owner mutation',full_goal_pass:'NOT_CLAIMED'};
 fs.writeFileSync(path.join(evidence,'P4_MEMORY_MANAGER_UI_20261014.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt,null,2));
} catch(error){console.error(error);console.error((await page.locator('body').innerText()).slice(-3000));throw error;}
finally{await context.close();}
