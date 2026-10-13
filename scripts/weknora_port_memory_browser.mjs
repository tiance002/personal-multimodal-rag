import { chromium } from '../frontend/node_modules/playwright/index.mjs';
import fs from 'node:fs/promises';
const out='output/playwright/memory-native';await fs.mkdir(out,{recursive:true});
const context=await chromium.launchPersistentContext('E:/RAG-Tests/playwright-memory-port-13',{executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,viewport:{width:1440,height:1000}});
try {
 const page=await context.newPage(); const requests=[];page.on('request',r=>requests.push({method:r.method(),url:r.url()}));
 await page.goto('http://127.0.0.1:56426',{waitUntil:'networkidle'});
 await page.getByRole('button',{name:'长期记忆',exact:true}).click();
 await page.getByText('本页没有记忆。',{exact:true}).waitFor();
 await page.screenshot({path:out+'/memory-empty.png',fullPage:true});
 const snapshot=await page.locator('body').ariaSnapshot();
 const metrics=await (await page.request.get('http://127.0.0.1:56425/_fixture/metrics')).json();
 await fs.writeFile(out+'/readback.json',JSON.stringify({snapshot,requests,metrics,delete:'NOT RUN: no synthetic item in this browser owner'},null,2));
 console.log(snapshot);console.log(JSON.stringify({metrics,mutationRequests:requests.filter(x=>x.method!=='GET')}));
} finally {await context.close();}
