import { chromium } from '../frontend/node_modules/playwright/index.mjs';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const out='output/playwright/cancel-memory-native';await fs.mkdir(out,{recursive:true});
const context=await chromium.launchPersistentContext('E:/RAG-Tests/playwright-cancel-memory-port-13',{
 executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,extraHTTPHeaders:{Origin:'http://127.0.0.1:56426'},viewport:{width:1440,height:1000}});
const observations=[];
try {
 const page=await context.newPage();const requests=[];page.on('request',r=>requests.push({method:r.method(),url:r.url()}));
 const api='http://127.0.0.1:56425/api/v1';
 const data=async(response)=>{assert.equal(response.status(),200,await response.text());return (await response.json()).data};
 const kb=await data(await page.request.post(api+'/knowledge-bases',{data:{name:'Browser cancellation fixture'}}));
 await data(await page.request.patch(api+`/knowledge-bases/${kb.id}/external-processing`,{data:{cloud_allowed:true}}));
 const doc=await data(await page.request.post(api+`/knowledge-bases/${kb.id}/documents`,{multipart:{file:{name:'browser-cancel-source.txt',mimeType:'text/plain',buffer:Buffer.from('The source handbook says blue widgets use protocol W-17. Keep the widgets dry.')}}}));
 assert.equal(doc.status,'completed');
 await page.goto('http://127.0.0.1:56426',{waitUntil:'networkidle'});
 await page.getByRole('checkbox',{name:/Browser cancellation fixture/}).check();
 await page.getByRole('textbox').fill('What protocol do blue widgets use? extract source');
 await page.getByRole('button',{name:/发送/}).click();
 await page.getByRole('button',{name:'停止生成',exact:true}).waitFor();
 for(let i=0;i<100;i++){
  const metrics=await (await page.request.get('http://127.0.0.1:56425/_fixture/metrics')).json();
  if(metrics.answer_stream_calls===1)break;
  if(i===99)throw Error('Expected active original Cheap request');
  await page.waitForTimeout(100);
 }
 await page.getByRole('button',{name:'停止生成',exact:true}).click();
 await page.getByText('已停止生成；原始运行已保存取消状态。',{exact:true}).waitFor();
 assert.equal(await page.locator('.wp-complete').count(),0);
 const metricsAfterStop=await (await page.request.get('http://127.0.0.1:56425/_fixture/metrics')).json();
 assert.equal(metricsAfterStop.answer_stream_calls,1);assert.equal(metricsAfterStop.real_provider_calls,0);
 const sessions=await data(await page.request.get(api+'/sessions'));assert.equal(sessions.length,1);
 const messages=await data(await page.request.get(api+`/sessions/${sessions[0].id}/messages`));
 const assistant=messages.find(m=>m.role==='assistant');assert.equal(assistant.turn_error_code,'MODEL_CALL_CANCELLED');assert.equal(assistant.is_completed,true);assert.equal(assistant.turn_status,'cancelled');
 const completedStop=await page.request.post(api+`/sessions/${sessions[0].id}/stop`,{data:{message_id:assistant.id}});assert.equal(completedStop.status(),200);
 await page.screenshot({path:out+'/cancelled.png',fullPage:true});
 observations.push({kind:'actual_stop',metricsAfterStop,session:sessions[0].id,assistant,snapshot:await page.locator('body').ariaSnapshot()});
 await page.reload({waitUntil:'networkidle'});await page.getByRole('button',{name:/What protocol do blue widgets/}).click();
 await page.getByText('已停止生成；原始运行已保存取消状态。',{exact:true}).waitFor();
 assert.equal(await page.locator('.wp-complete').count(),0);
 await page.getByRole('button',{name:'长期记忆',exact:true}).click();
 await page.getByText('This is a recoverable synthetic browser memory.',{exact:true}).waitFor();
 const beforeMemory=await data(await page.request.get(api+'/memory/items'));assert.equal(beforeMemory.length,1);
 const metricsBeforeDelete=await (await page.request.get('http://127.0.0.1:56425/_fixture/metrics')).json();
 await page.getByRole('button',{name:'遗忘 Browser fixture memory',exact:true}).click();
 await page.getByRole('dialog',{name:'确认遗忘记忆',exact:true}).waitFor();
 await page.getByRole('button',{name:'确认遗忘',exact:true}).click();
 await page.getByText('本页没有记忆。',{exact:true}).waitFor();
 assert.equal((await data(await page.request.get(api+'/memory/items'))).length,0);
 await page.screenshot({path:out+'/forgotten.png',fullPage:true});
 const restore=await page.request.post('http://127.0.0.1:56425/_fixture/restore-memory');assert.equal(restore.status(),200,await restore.text());
 const receipt=await restore.json();const restored=await data(await page.request.get(api+'/memory/items'));
 assert.equal(restored[0].id,beforeMemory[0].id);assert.equal(restored[0].content,beforeMemory[0].content);
 await page.getByRole('button',{name:'刷新记忆',exact:true}).click();
 await page.getByText('This is a recoverable synthetic browser memory.',{exact:true}).waitFor();
 const metricsAfterDelete=await (await page.request.get('http://127.0.0.1:56425/_fixture/metrics')).json();assert.equal(metricsAfterDelete.wire_calls,metricsBeforeDelete.wire_calls);
 observations.push({kind:'actual_delete_restore',beforeMemory,receipt,restored,metricsBeforeDelete,metricsAfterDelete});
 const generationPosts=requests.filter(r=>r.method==='POST'&&/\/sessions\/[^/]+\/messages$/.test(r.url));assert.equal(generationPosts.length,1);
 const stops=requests.filter(r=>r.method==='POST'&&r.url.endsWith('/stop'));assert.equal(stops.length,1);
 const deletes=requests.filter(r=>r.method==='DELETE'&&r.url.includes('/memory/items/'));assert.equal(deletes.length,1);
 await fs.writeFile(out+'/readback.json',JSON.stringify({observations,requests,generationPosts,stops,deletes},null,2));
 console.log(JSON.stringify({status:'ACTUAL_BROWSER_PASS',generationPosts:1,stopPosts:1,memoryDeletes:1,restored:true,realProviderCalls:0}));
} catch(error) {await fs.writeFile(out+'/failure.json',JSON.stringify({error:String(error),observations},null,2));throw error;}
finally {await context.close();}
