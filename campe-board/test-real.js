"use strict";
const assert=require("node:assert/strict");
const {spawn}=require("node:child_process");
const port=41000+Math.floor(Math.random()*6000),root="http://127.0.0.1:"+port;
const proc=spawn(process.execPath,[__dirname+"/server.js"],{env:{...process.env,
 APP_PASSWORD:"automated-test-password",SESSION_SECRET:"automated-test-session-secret",PORT:String(port),
 TRANSCRIPTOR_MCP_URL:"https://gemini-video-mcp-4ev0.onrender.com/mcp"
},stdio:"ignore"});
async function main(){
 try{
  for(let i=0;i<100;i++){try{if((await fetch(root+"/health")).ok)break}catch{}await new Promise(ok=>setTimeout(ok,100));}
  const login=await fetch(root+"/login",{method:"POST",redirect:"manual",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"password=automated-test-password"});
  assert.equal(login.status,303);
  const cookie=login.headers.get("set-cookie").split(";")[0];
  const created=await fetch(root+"/api/jobs",{method:"POST",headers:{"Cookie":cookie,"Content-Type":"application/json"},body:JSON.stringify({url:"https://www.youtube.com/watch?v=dQw4w9WgXcQ"})});
  const start=await created.json();assert.equal(created.status,202,JSON.stringify(start));
  console.log("JOB_CREATED",start.id);
  let state;
  for(let i=0;i<150;i++){
   await new Promise(ok=>setTimeout(ok,2200));
   const response=await fetch(root+"/api/jobs/"+start.id,{headers:{"Cookie":cookie}});
   state=await response.json();
   if(i%8===0)console.log("PROGRESS",state.status,state.progress,state.stage);
   if(state.status!=="running")break;
  }
  assert.equal(state.status,"complete",JSON.stringify(state));
  assert(state.result.full&&state.result.cues.length>=3);
  assert(state.result.rawSrt.length>100);
  console.log("PASS_REAL_AUDIO_JOB",JSON.stringify({
   status:state.status,source:state.result.source,subtitleType:state.result.subtitleType,
   cues:state.result.cues.length,clips:state.result.clips.length,
   duration:state.result.duration
  }));
 }finally{proc.kill()}
}
main().catch(e=>{console.error("REAL_JOB_FAILED",e.message);process.exitCode=1});
