"use strict";
const assert=require("node:assert/strict");
const {spawn}=require("node:child_process");
const port=39000+Math.floor(Math.random()*10000);
const child=spawn(process.execPath,[__dirname+"/server.js"],{
 env:Object.assign({},process.env,{
  APP_PASSWORD:"integration-test-password",
  SESSION_SECRET:"integration-test-secret",
  PORT:String(port)
 }),stdio:"ignore"
});
async function run(){
 const root="http://127.0.0.1:"+port;
 try{
  let ready=false;
  for(let i=0;i<120;i++){try{if((await fetch(root+"/health")).ok){ready=true;break}}catch{}await new Promise(r=>setTimeout(r,100));}
  assert(ready,"Servidor no inició.");
  const login=await fetch(root+"/login",{method:"POST",redirect:"manual",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"password=integration-test-password"});
  assert.equal(login.status,303,"Login");
  const cookie=login.headers.get("set-cookie").split(";")[0];
  const response=await fetch(root+"/api/extract",{
   method:"POST",headers:{"Content-Type":"application/json","Cookie":cookie},
   body:JSON.stringify({url:"https://www.youtube.com/live/Jvg9okd2N8M"}),signal:AbortSignal.timeout(240000)
  });
  const body=await response.json();
  assert.equal(response.status,200,JSON.stringify(body).slice(0,900));
  assert(body.full&&body.cues.length>100&&body.rawSrt.length>5000,"SRT original completo");
  assert(body.clips.length>=3&&body.clips.length<=5,"Ranking 3-5");
  assert(body.clips.every(x=>x.cuts.length&&x.literal),"Cortes reales");
  console.log("PASS LIVE",body.videoId,"cues",body.totalCues,"clips",body.clips.length,
   "bytes",body.rawSrt.length,"source",body.source);
 }finally{child.kill();}
}
run().catch(e=>{console.error(e);process.exitCode=1;});
