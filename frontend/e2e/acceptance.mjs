import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");
const browser = await chromium.launch({ channel: "chrome", headless: true });
const origin = process.env.ACCEPTANCE_URL || "http://127.0.0.1:18173";
const output = process.env.ACCEPTANCE_OUTPUT || "/tmp/rag-frontend-acceptance";
await mkdir(output, { recursive: true });
let passed = 0;
const longAnswer = "## 測試回答\n\n" + "這是一段驗證捲動與閱讀的回答。\n\n".repeat(45) + '\n<img src=x onerror="window.hacked=true">\n\n[危險](javascript:alert(1))';
try {
for (const viewport of [{width:360,height:740},{width:360,height:420},{width:1440,height:900}]) {
  const context = await browser.newContext({ viewport });
  let authenticated = false;
  let history = [];
  let clearFails = true;
  let streamMode = "answer";
  let polls = 0;
  await context.route("**/api/v1/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body, status = 200) => route.fulfill({ status, contentType:"application/json", body:JSON.stringify(body) });
    if (path.endsWith("/auth/session")) return authenticated ? json({authenticated:true,authEnabled:true}) : json({detail:"Missing credential"},401);
    if (path.endsWith("/auth/login")) { authenticated = request.postDataJSON().apiKey === "acceptance-only-key"; return authenticated ? route.fulfill({status:204}) : json({detail:"登入失敗"},401); }
    if (path.endsWith("/auth/logout")) { authenticated=false; return route.fulfill({status:204}); }
    if (path.endsWith("/messages")) {
      if (request.method() === "DELETE") { if(clearFails) return json({detail:"無法清除"},503); history=[]; return route.fulfill({status:204}); }
      return json({items:history});
    }
    if (path.endsWith("/chat/stream")) {
      if(streamMode === "expired") { authenticated=false; return json({detail:"Session expired"},401); }
      if(streamMode === "rate") return route.fulfill({status:429,headers:{"Retry-After":"12"},contentType:"application/json",body:JSON.stringify({detail:"Rate limit exceeded"})});
      const events = [{type:"status",stage:"retrieving",message:"檢索中"},{type:"delta",text:longAnswer},{type:"sources",items:[{label:"操作手冊"}]},{type:"metadata",elapsedMs:1500,responseSource:"rag",cacheHit:false}];
      if(streamMode !== "truncated") events.push({type:"done"});
      history.push({role:"user",content:request.postDataJSON().message},{role:"assistant",content:longAnswer});
      return route.fulfill({contentType:"application/x-ndjson",body:events.map(e=>JSON.stringify(e)+"\n").join("")});
    }
    if(path.endsWith("/admin/manuals")) return json({jobId:"job-1",status:"queued"},202);
    if(path.includes("/admin/jobs/")) { polls++; return json({jobId:"job-1",status:polls===1?"running":"succeeded"}); }
    return json({detail:"not found"},404);
  });
  const page = await context.newPage();
  const errors = []; page.on("pageerror", e => errors.push(e.message));
  await page.goto(origin);
  await page.getByLabel("APP 登入密碼").fill("acceptance-only-key");
  await page.getByLabel("APP 登入密碼").press("Tab");
  assert.equal(await page.getByRole("button",{name:"登入",exact:true}).evaluate(e=>e===document.activeElement),true);
  await page.keyboard.press("Enter");
  const input = page.getByRole("textbox",{name:"輸入問題"});
  await input.waitFor(); await page.waitForFunction(()=>!document.querySelector("textarea")?.disabled);
  assert.equal(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}).includes("acceptance-only-key")),false);
  await input.fill("驗收問題"); await input.press("Enter");
  await page.getByText("1.5 秒").waitFor();
  await page.getByText("查看 1 筆參考資料").click();
  const layout = await page.evaluate(()=>{
    const composer=document.querySelector(".composer").getBoundingClientRect();
    const nav=document.querySelector(".bottom-nav").getBoundingClientRect();
    const thread=document.querySelector(".thread");
    const buttons=[...document.querySelectorAll("button:not([disabled]),summary")].map(e=>({label:e.textContent,width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height}));
    return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,composerBottom:composer.bottom,navTop:nav.top,navBottom:nav.bottom,scrollable:thread.scrollHeight>thread.clientHeight,font:getComputedStyle(document.querySelector("textarea")).fontSize,pageWidth:document.querySelector(".page").getBoundingClientRect().width,buttons};
  });
  assert.ok(layout.scrollWidth <= layout.width, JSON.stringify(layout));
  assert.ok(layout.composerBottom <= layout.navTop && layout.navBottom <= layout.height+1, JSON.stringify(layout));
  assert.ok(layout.pageWidth <= 760);
  assert.ok(layout.scrollable); assert.equal(layout.font,"16px");
  for(const button of layout.buttons) assert.ok(button.width>=44 && button.height>=44,JSON.stringify(button));
  assert.equal(await page.locator('.message-bubble img, a[href^="javascript:"]').count(),0);
  assert.equal(await page.evaluate(()=>window.hacked),undefined);
  await page.screenshot({path:`${output}/chat-${viewport.width}x${viewport.height}.png`});
  page.on("dialog", dialog=>dialog.accept());
  await page.getByRole("button",{name:"清除對話"}).click();
  await page.getByRole("alert").filter({hasText:"無法清除"}).waitFor();
  assert.ok(await page.locator(".message").count()>0);
  clearFails=false; await page.getByRole("button",{name:"清除對話"}).click();
  await page.getByText("想了解什麼？").waitFor();
  streamMode="truncated"; await input.fill("不完整串流"); await input.press("Enter");
  await page.getByRole("alert").filter({hasText:"串流回應不完整"}).waitFor();
  streamMode="rate"; await input.fill("速率限制"); await input.press("Enter");
  await page.getByRole("alert").filter({hasText:"Rate limit exceeded"}).waitFor();
  await page.getByRole("button",{name:"文件管理",exact:true}).click();
  await page.getByLabel("選擇手冊檔案").setInputFiles({name:"manual.exe",mimeType:"application/octet-stream",buffer:Buffer.from("invalid")});
  await page.getByRole("alert").filter({hasText:"只支援"}).waitFor();
  await page.getByLabel("選擇手冊檔案").setInputFiles({name:"manual.csv",mimeType:"text/csv",buffer:Buffer.from("question,answer\nq,a")});
  await page.getByRole("button",{name:"上傳並更新知識庫"}).click();
  await page.getByText("succeeded",{exact:true}).waitFor();
  const calls=polls; await page.waitForTimeout(2200); assert.equal(polls,calls);
  await page.screenshot({path:`${output}/documents-${viewport.width}x${viewport.height}.png`});
  await page.getByRole("button",{name:"知識問答",exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector("textarea")?.disabled);
  streamMode="expired"; await input.fill("過期"); await input.press("Enter");
  await page.getByLabel("APP 登入密碼").waitFor();
  await page.getByLabel("APP 登入密碼").fill("acceptance-only-key"); await page.getByLabel("APP 登入密碼").press("Enter");
  await page.getByRole("button",{name:"登出"}).click(); await page.getByLabel("APP 登入密碼").waitFor();
  assert.deepEqual(errors,[]);
  console.log(`PASS ${viewport.width}x${viewport.height}: login, keyboard, chat/sources, layout, XSS, clear, truncated/429/401, upload/poll, logout`);
  passed++; await context.close();
}
console.log(`${passed} viewport journeys passed; screenshots: ${output}`);
} finally { await browser.close(); }
