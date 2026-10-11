// Browser interaction is real; every API is SIMULATED, no DB/model request.
import { test, expect } from '@playwright/test';
test('memory uses conversation documents; late old scope cannot replace new rows',async({page})=>{
 const rows: Record<string,any[]>={A:[],B:[]};const sent:any[]=[];
 const conversations=[{id:'conv-a',title:'会话A',knowledge_base_scope:['kb'],document_scope:['A']},
  {id:'conv-b',title:'会话B',knowledge_base_scope:['kb'],document_scope:['B']}];
 let held=false;let release:(()=>void)|undefined;
 const gate=new Promise<void>(r=>{release=r;});
 await page.route('**/api/**',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname;
  let data:any=[];const body=req.method()==='GET'?{}:req.postDataJSON();
  if(p.endsWith('/knowledge-bases'))data=[{id:'kb',name:'知识库',graph_enabled:false}];
  else if(p.endsWith('/conversations'))data=conversations;
  else if(p.endsWith('/documents'))data=[{id:'A',file_name:'A.txt',index_status:'ready',version_no:1}, {id:'B',file_name:'B.txt',index_status:'ready',version_no:1}];
  else if(p.endsWith('/memory/settings'))data={read_enabled:true,write_mode:'auto',automatic_extraction:'MODEL_IDENTITY_NOT_CONFIGURED'};
  else if(p.endsWith('/memory/list')){
   sent.push(body);const key=body.document_scope[0];
   if(key==='A' && rows.A.length && !held){held=true;await gate;}
   data=rows[key]??[];
  } else if(p.endsWith('/memory/items')){
   sent.push(body);const key=body.document_scope[0];
   rows[key].push({id:'memory-'+key,kind:body.kind,fact_key:body.fact_key,content:body.content,status:'active',origin:'explicit',version:1,sources:[]});data={id:'memory-'+key};
  } else if(p.endsWith('/delete')){
   sent.push(body);rows[body.document_scope[0]]=[];
  }
  await route.fulfill({json:{data,meta:{request_id:'SIMULATED'}}});
 });
 await page.goto('/');
 await page.getByRole('button',{name:'会话A',exact:true}).click();
 await page.getByRole('button',{name:'系统设置',exact:true}).click();
 await expect(page.getByText(/尚不可用（未配置可信提取模型/)).toBeVisible();
 await page.getByLabel('事实标识').fill('language');await page.getByLabel('记忆内容').fill('A ONLY');
 await page.getByRole('button',{name:'明确保存这条记忆'}).click();
 await expect.poll(()=>held).toBe(true);
 await page.getByRole('button',{name:'会话B',exact:true}).click();
 // Selecting a conversation intentionally returns to chat; reopen settings.
 await page.getByRole('button',{name:'系统设置',exact:true}).click();
 await expect.poll(()=>sent.some(x=>JSON.stringify(x.document_scope)==='["B"]')).toBe(true);
 release?.();
 await expect(page.getByText('A ONLY',{exact:true})).toHaveCount(0);
 await page.getByLabel('事实标识').fill('language');await page.getByLabel('记忆内容').fill('B ONLY');
 await page.getByRole('button',{name:'明确保存这条记忆'}).click();
 await expect(page.getByText('B ONLY',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'删除',exact:true}).click();
 await expect(page.getByText('B ONLY',{exact:true})).toHaveCount(0);
 expect(rows.A[0].content).toBe('A ONLY');expect(rows.B).toEqual([]);
 expect(sent.filter(x=>x.content==='A ONLY')[0].document_scope).toEqual(['A']);
 expect(sent.filter(x=>x.content==='B ONLY')[0].document_scope).toEqual(['B']);
 await page.screenshot({path:'../var/reports/p55c-query-understand-r1/browser-edge-repair-1/memory-scope.png'});
});
