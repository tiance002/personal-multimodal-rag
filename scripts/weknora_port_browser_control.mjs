import { chromium } from '../frontend/node_modules/playwright/index.mjs';
import fs from 'node:fs/promises';
import readline from 'node:readline';
import path from 'node:path';
const out=path.resolve('output/playwright');await fs.mkdir(out,{recursive:true});
const context=await chromium.launchPersistentContext('E:/RAG-Tests/playwright-native-port-13',{
  executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,
  viewport:{width:1440,height:1000}});
const page=context.pages()[0]||await context.newPage();
const requests=[];const errors=[];
page.on('request',r=>requests.push({method:r.method(),url:r.url()}));
page.on('pageerror',e=>errors.push(String(e)));
page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
const snapshot=async()=>page.locator('body').ariaSnapshot();
await page.goto('http://127.0.0.1:56426',{waitUntil:'networkidle'});
console.log(JSON.stringify({status:'OPENED',snapshot:await snapshot()}));
for await(const line of readline.createInterface({input:process.stdin,terminal:false})) {
  try {
    const cmd=JSON.parse(line);
    const loc=()=>cmd.role?page.getByRole(cmd.role,{name:cmd.name,exact:true}):cmd.label?page.getByLabel(cmd.label,{exact:true}):page.locator(cmd.selector);
    if(cmd.action==='click')await loc().click();
    if(cmd.action==='fill')await loc().fill(cmd.text);
    if(cmd.action==='upload')await loc().setInputFiles(cmd.file);
    if(cmd.action==='reload')await page.reload({waitUntil:'networkidle'});
    if(cmd.action==='screenshot')await page.screenshot({path:path.join(out,cmd.name),fullPage:true});
    if(cmd.action==='metrics')console.log(JSON.stringify({metrics:await (await page.request.get('http://127.0.0.1:56425/_fixture/metrics')).json(),requests,errors}));
    if(cmd.action==='truncate-next-stream') {
      await page.route('**/api/v1/sessions/*/messages',async route=>{
        if(route.request().method()!=='POST'){await route.continue();return;}
        const actual=await route.fetch();const body=await actual.text();
        const frames=body.split('\n\n').filter(x=>x&&!x.includes('"response_type":"complete"'));
        await route.fulfill({response:actual,body:frames.join('\n\n')+'\n\n'});
        await page.unroute('**/api/v1/sessions/*/messages');
      });
    }
    if(cmd.action==='close') {
      await fs.writeFile(path.join(out,'native-browser-requests.json'),JSON.stringify({requests,errors},null,2));
      await context.close();console.log(JSON.stringify({status:'CLOSED'}));process.exit(0);
    }
    const result={action:cmd.action,status:'DONE',snapshot:await snapshot()};
    console.log(JSON.stringify(result));
    await fs.appendFile(path.join(out,'native-browser-actions.jsonl'),JSON.stringify({command:cmd,result})+'\n');
  }catch(error){console.log(JSON.stringify({status:'ERROR',error:String(error)}));}
}
