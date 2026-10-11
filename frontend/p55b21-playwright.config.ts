import { defineConfig } from '@playwright/test';
export default defineConfig({
 testDir:'./tests',testMatch:'p55b21-memory.spec.ts',workers:1,timeout:20000,
 outputDir:'../var/reports/p55b21-global-scoped-r1/browser-final-1/artifacts',
 reporter:[['list'],['json',{outputFile:'../var/reports/p55b21-global-scoped-r1/browser-final-1/results.json'}],
 ['junit',{outputFile:'../var/reports/p55b21-global-scoped-r1/browser-final-1/junit.xml'}]],
 use:{baseURL:'http://127.0.0.1:15488',headless:true,
 launchOptions:{executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',chromiumSandbox:true}},
 webServer:{command:'node ../var/reports/p55c-query-understand-r1/serve_ui.mjs',url:'http://127.0.0.1:15488',reuseExistingServer:false},
});
