// Real Edge interactions, every API response SIMULATED; no DB/model transport.
import { test,expect } from '@playwright/test';
test('explicit Global choice, Scoped default, late responses, edit/source/delete',async({page},testInfo)=>{
 const rows:Record<string,any[]>={SCOPED:[],GLOBAL:[]};const sent:any[]=[];
 let held=false;let release:(()=>void)|undefined;const gate=new Promise<void>(r=>{release=r;});
 await page.route('**/api/v1/**',async route=>{
  const req=route.request(),p=new URL(req.url()).pathname,b=req.method()==='GET'?{}:req.postDataJSON();let data:any=[];
  if(p.endsWith('/knowledge-bases'))data=[{id:'kb',name:'KB',graph_enabled:false}];
  else if(p.endsWith('/conversations'))data=[{id:'conv',title:'会话A',knowledge_base_scope:['kb'],document_scope:['A']}];
  else if(p.endsWith('/documents'))data=[{id:'A',file_name:'A.txt',index_status:'ready',version_no:1}];
  else if(p.endsWith('/memory/settings'))data={read_enabled:true,write_mode:'explicit_only',automatic_extraction:'MODEL_IDENTITY_NOT_CONFIGURED'};
  else if(p.endsWith('/memory/list')){
   sent.push(b);const key=b.scope_type;
   if(key==='SCOPED' && rows.SCOPED.length && !held){held=true;await gate;}
   data=[...(rows[key]??[])];
  } else if(p.endsWith('/delete')){sent.push(b);rows[b.scope_type]=[];}
  else if(p.includes('/memory/items')){
   sent.push(b);const key=b.scope_type;
   if(req.method()==='PATCH')rows[key]=[];
   rows[key].push({id:'memory-'+key,scope_type:key,kind:b.kind,fact_key:b.fact_key,content:b.content,status:'active',origin:'explicit',version:req.method()==='PATCH'?2:1,sources:[{source:{type:'SIMULATED_OWNER_SOURCE'}}]});data={id:'memory-'+key};
  }
  await route.fulfill({json:{data,meta:{request_id:'SIMULATED'}}});
 });
 await page.goto('/');await page.getByRole('button',{name:'会话A',exact:true}).click();
 await page.getByRole('button',{name:'系统设置',exact:true}).click();
 await expect(page.getByLabel('记忆范围')).toHaveValue('SCOPED');
 await page.getByLabel('事实标识').fill('language');await page.getByLabel('记忆内容').fill('SCOPED A');
 await page.getByRole('button',{name:'明确保存这条记忆'}).click();await expect.poll(()=>held).toBe(true);
 await page.getByLabel('记忆范围').selectOption('GLOBAL');
 await expect.poll(()=>sent.some(b=>b.scope_type==='GLOBAL')).toBe(true);
 release?.();await expect(page.getByText('SCOPED A',{exact:true})).toHaveCount(0);
 await expect(page.getByLabel('分类')).toHaveValue('preference');
 await expect(page.getByLabel('分类').locator('option[value="fact"]')).toHaveCount(0);
 await page.getByLabel('事实标识').fill('language');await page.getByLabel('记忆内容').fill('GLOBAL Chinese');
 await page.getByRole('button',{name:'明确保存这条记忆'}).click();await expect(page.getByText('GLOBAL Chinese',{exact:true})).toBeVisible();
 await page.getByText('查看来源',{exact:true}).click();await expect(page.getByText(/SIMULATED_OWNER_SOURCE/)).toBeVisible();
 await page.getByRole('button',{name:'编辑',exact:true}).click();await page.getByLabel('记忆内容').fill('GLOBAL English');
 await page.getByRole('button',{name:'保存编辑'}).click();await expect(page.getByText('GLOBAL English',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'删除',exact:true}).click();await expect(page.getByText('GLOBAL English',{exact:true})).toHaveCount(0);
 const original=sent.find(b=>b.content==='SCOPED A');expect(original.scope_type).toBe('SCOPED');expect(original.document_scope).toEqual(['A']);
 for(const b of sent.filter(b=>b.scope_type==='GLOBAL')){expect(b.knowledge_base_scope).toEqual([]);expect(b.document_scope).toEqual([]);}
 await page.getByLabel('记忆范围').selectOption('SCOPED');await expect(page.getByText('SCOPED A',{exact:true})).toBeVisible();
 await page.screenshot({path:testInfo.outputPath('memory-scope.png')});
});
