"use strict";
const assert=require("node:assert/strict"),http=require("node:http"),{spawn}=require("node:child_process");
const {toCues,formatResult,providerError}=require("./supadata");
const text="Atención el precio de la gasolina subió, pero Trump y México tienen contradicciones.";
const input={lang:"es",content:Array.from({length:180},(_,i)=>({text:text+" "+i,offset:i*4000,duration:4000,lang:"es"}))};
const cues=toCues(input);
assert.equal(cues.cues.length,180);
assert.equal(cues.cues[1].start,4);
const normalized=formatResult("https://www.youtube.com/watch?v=Jvg9okd2N8M",input);
assert(normalized.full&&normalized.rawSrt.includes(text));
assert(normalized.clips.length>=3&&normalized.clips.length<=5);
assert(normalized.clips.every(c=>c.cuts.length&&c.literal));
assert.equal(normalized.totalCues,180);
assert.equal(providerError(401,{message:"Unauthorized"}).status,401);
let submitCalls=0,statusCalls=0,authHeaders=[];
const mock=http.createServer((req,res)=>{
 authHeaders.push(req.headers["x-api-key"]);
 const answer=(status,obj)=>{res.writeHead(status,{"Content-Type":"application/json"});res.end(JSON.stringify(obj));};
 if(req.url.startsWith("/v1/transcript?")){submitCalls++;return answer(202,{jobId:"123e4567-e89b-12d3-a456-426614174000"});}
 if(req.url==="/v1/transcript/123e4567-e89b-12d3-a456-426614174000"){
  statusCalls++;
  return statusCalls===1?answer(200,{status:"queued"}):answer(200,{status:"completed",...input});
 }
 return answer(404,{error:"not-found",message:"not found"});
});
(async()=>{
 await new Promise(ok=>mock.listen(0,"127.0.0.1",ok));
 const port=35000+Math.floor(Math.random()*10000);
 const child=spawn(process.execPath,[__dirname+"/server.js"],{stdio:"pipe",env:{
  ...process.env,APP_PASSWORD:"test-only-password",SESSION_SECRET:"test-only-secret",
  GEMINI_API_KEY:"",PORT:String(port),
  SUPADATA_BASE_URL:"http://127.0.0.1:"+mock.address().port
 }});
 let errors="";
 child.stderr.on("data",c=>errors+=c.toString("utf8"));
 try{
  const root="http://127.0.0.1:"+port;
  let ready=false;
  for(let i=0;i<80;i++){try{if((await fetch(root+"/health")).ok){ready=true;break}}catch{}await new Promise(ok=>setTimeout(ok,100))}
  assert(ready,errors||"server startup");
  const reqData={url:"https://youtube.com/live/Jvg9okd2N8M",supadataKey:"key-for-test-123"};
  const unauthorized=await fetch(root+"/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(reqData)});
  assert.equal(unauthorized.status,401);
  const login=await fetch(root+"/login",{method:"POST",redirect:"manual",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"password=test-only-password"});
  assert.equal(login.status,303);const cookie=login.headers.get("set-cookie").split(";")[0];
  const noKey=await fetch(root+"/api/jobs",{method:"POST",headers:{"Content-Type":"application/json","Cookie":cookie},body:JSON.stringify({url:reqData.url})});
  assert.equal(noKey.status,400);
  const created=await fetch(root+"/api/jobs",{method:"POST",headers:{"Content-Type":"application/json","Cookie":cookie},body:JSON.stringify(reqData)});
  assert.equal(created.status,202);
  const {id}=await created.json();assert(id);
  let state;
  for(let i=0;i<95;i++){
   const status=await fetch(root+"/api/jobs/"+id,{headers:{"Cookie":cookie}});
   assert.equal(status.status,200);
   state=await status.json();
   if(state.status!=="running")break;
   await new Promise(ok=>setTimeout(ok,100));
  }
  assert.equal(state.status,"complete",JSON.stringify(state));
  assert.equal(state.result.provider,"supadata");
  assert.equal(state.result.totalCues,180);
  assert(state.result.clips.length>=3);
  assert.equal(submitCalls,1);
  assert(statusCalls>=2);
  assert(authHeaders.every(x=>x==="key-for-test-123"));
  const blocked=await fetch(root+"/api/jobs/"+id);
  assert.equal(blocked.status,401);
  console.log("PASS: Supadata 202 async polling, normalized timestamps, private API, 3-5 cuts, no Gemini.");
 }finally{child.kill();mock.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
