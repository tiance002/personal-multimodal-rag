import { chromium } from '../frontend/node_modules/playwright/index.mjs';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const out='output/playwright/memory-review-native';await fs.mkdir(out,{recursive:true});
const context=await chromium.launchPersistentContext('E:/RAG-Tests/playwright-memory-review-port-14',{
 executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,extraHTTPHeaders:{Origin:'http://127.0.0.1:56426'},viewport:{width:1440,height:1000}});
let page;
try {
 page=await context.newPage();const requests=[];page.on('console',m=>console.log('UI_CONSOLE',m.type(),m.text()));page.on('pageerror',e=>console.log('UI_PAGE_ERROR',e.message));page.on('request',r=>requests.push({method:r.method(),url:r.url()}));
 const api='http://127.0.0.1:56425/api/v1';
 const data=async(response)=>{assert.equal(response.status(),200,await response.text());return (await response.json()).data};
 const pending=await data(await page.request.get(api+'/memory/items?status=pending'));
 const confirm=pending.filter(i=>i.topic==='Review confirm fixture');const reject=pending.filter(i=>i.topic==='Review reject fixture');
 assert.ok(confirm.length<=1&&reject.length<=1,'fixture pending identity ambiguous');
 const seeded=confirm.length&&reject.length?{confirm_id:confirm[0].id,reject_id:reject[0].id,existing_failed_ui_fixture_reused:true}:await data(await page.request.post('http://127.0.0.1:56425/_fixture/memory-review-seed'));
 await data(await page.request.put(api+'/memory/settings',{data:{enabled:true}}));
 await page.goto('http://127.0.0.1:56426',{waitUntil:'networkidle'});
 await page.getByRole('button',{name:'长期记忆',exact:true}).click();
 const toggle=page.getByRole('checkbox',{name:'启用我的长期记忆',exact:true});await toggle.waitFor();await page.getByText(/记忆已启用/).waitFor();assert.equal(await toggle.isChecked(),true);
 const before=await (await page.request.get('http://127.0.0.1:56425/_fixture/metrics')).json();
 const [offResponse]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/memory/settings')&&r.request().method()==='PUT'),toggle.click()]);assert.equal(offResponse.status(),200);await page.getByText(/记忆未启用/).waitFor();
 assert.equal((await data(await page.request.get(api+'/memory/settings'))).effective,false);
 await page.reload({waitUntil:'networkidle'});await page.getByRole('button',{name:'长期记忆',exact:true}).click();
 await page.getByText(/记忆未启用/).waitFor();assert.equal(await toggle.isChecked(),false);
 const [onResponse]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/memory/settings')&&r.request().method()==='PUT'),toggle.click()]);assert.equal(onResponse.status(),200);await page.getByText(/记忆已启用/).waitFor();
 await page.getByRole('button',{name:'确认 Review confirm fixture',exact:true}).click();
 await page.getByRole('button',{name:'确认 Review confirm fixture',exact:true}).waitFor({state:'hidden'});
 await page.getByRole('button',{name:'拒绝 Review reject fixture',exact:true}).click();
 await page.getByText('Synthetic review reject content',{exact:true}).waitFor({state:'hidden'});
 const items=await data(await page.request.get(api+'/memory/items'));
 assert.equal(items.find(i=>i.id===seeded.confirm_id).status,'active');assert.ok(!items.some(i=>i.id===seeded.reject_id));
 const after=await (await page.request.get('http://127.0.0.1:56425/_fixture/metrics')).json();
 assert.equal(after.wire_calls,before.wire_calls);assert.equal(after.real_provider_calls,0);
 assert.equal(requests.filter(r=>r.method==='PUT'&&r.url.endsWith('/memory/settings')).length,2);
 assert.equal(requests.filter(r=>r.method==='POST'&&r.url.endsWith('/confirm')).length,1);
 assert.equal(requests.filter(r=>r.method==='POST'&&r.url.endsWith('/reject')).length,1);
 await page.screenshot({path:out+'/reviewed.png',fullPage:true});
 await fs.writeFile(out+'/readback.json',JSON.stringify({kind:'ACTUAL_NATIVE_REACT_HTTP_PG_MEMORY_REVIEW',seeded,before,after,items,requests,snapshot:await page.locator('body').ariaSnapshot(),real_provider_calls:0},null,2));
 console.log('ACTUAL_MEMORY_REVIEW_BROWSER_PASS');
} catch(error) {
 if(page){await page.screenshot({path:out+'/failure.png',fullPage:true});await fs.writeFile(out+'/failure-dom.txt',await page.locator('body').ariaSnapshot());await fs.writeFile(out+'/failure-toggle.json',JSON.stringify(await page.getByRole('checkbox',{name:'启用我的长期记忆'}).evaluate(e=>({html:e.outerHTML,checked:e.checked,disabled:e.disabled,rect:e.getBoundingClientRect().toJSON(),pointer:getComputedStyle(e).pointerEvents})),null,2));}
 throw error;
} finally {await context.close();}
