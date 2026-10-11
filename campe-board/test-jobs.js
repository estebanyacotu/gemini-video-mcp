"use strict";
const assert=require("node:assert/strict");
const http=require("node:http"),{spawn}=require("node:child_process");
const stamp=t=>[Math.floor(t/3600),Math.floor(t%3600/60),t%60].map(x=>String(x).padStart(2,"0")).join(":")+".000";
let geminiCalls=0,captionsCalls=0;
const mock=http.createServer(async(req,res)=>{
 let body="";for await(const p of req)body+=p;
 const args=JSON.parse(body),name=args?.params?.name;
 const reply=(result,isError=false)=>{
  res.setHeader("Content-Type","application/json");
  res.end(JSON.stringify({jsonrpc:"2.0",id:args.id,result:{structuredContent:result,isError,...(isError?{content:[{type:"text",text:"Subtitle track unavailable"}]}:{})}}));
 };
 if(name==="get_raw_subtitles"){captionsCalls++;return reply({},true);}
 if(name==="get_video_info")return reply({videoId:"Jvg9okd2N8M",title:"Live sin subtítulos disponibles",duration:720});
 if(name==="analizar_video"){
  geminiCalls++;
  const begin=(geminiCalls-1)*360,lines=[];
  for(let s=begin;s<begin+360;s+=5)lines.push(stamp(s)+" --> "+stamp(s+5)+"\nAtención el precio de la gasolina subió pero Trump y México tienen contradicciones.");
  return reply({result:"WEBVTT\n\n"+lines.join("\n\n")});
 }
 throw Error("Unexpected "+name);
});
(async()=>{
 await new Promise(r=>mock.listen(0,"127.0.0.1",r));
 const port=40000+Math.floor(Math.random()*10000);
 const child=spawn(process.execPath,[__dirname+"/server.js"],{stdio:"ignore",env:{...process.env,APP_PASSWORD:"test-job",SESSION_SECRET:"test-secret",PORT:String(port),TRANSCRIPTOR_MCP_URL:"http://127.0.0.1:"+mock.address().port+"/mcp"}});
 const root="http://127.0.0.1:"+port;
 try{
  for(let i=0;i<80;i++){try{if((await fetch(root+"/health")).ok)break}catch{}await new Promise(ok=>setTimeout(ok,100))}
  const login=await fetch(root+"/login",{method:"POST",redirect:"manual",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"password=test-job"});
  assert.equal(login.status,303);
  const cookie=login.headers.get("set-cookie").split(";")[0];
  const input={url:"https://www.youtube.com/live/Jvg9okd2N8M"};
  const unauth=await fetch(root+"/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(input)});
  assert.equal(unauth.status,401);
  const created=await fetch(root+"/api/jobs",{method:"POST",headers:{"Content-Type":"application/json","Cookie":cookie},body:JSON.stringify(input)});
  assert.equal(created.status,202);
  const {id}=await created.json();assert(id);
  let report;
  for(let i=0;i<100;i++){
   const res=await fetch(root+"/api/jobs/"+id,{headers:{"Cookie":cookie}});
   assert.equal(res.status,200);
   report=await res.json();
   if(report.status!=="running")break;
   await new Promise(ok=>setTimeout(ok,100));
  }
  assert.equal(report.status,"complete",JSON.stringify(report));
  assert.equal(report.result.subtitleType,"ai");
  assert.equal(report.result.cues.length,144);
  assert(report.result.clips.length>=3&&report.result.clips.length<=5);
  assert.equal(captionsCalls,1);assert.equal(geminiCalls,2);
  assert(report.result.transcriptionDisclaimer.includes("IA"));
  const blocked=await fetch(root+"/api/jobs/"+id);
  assert.equal(blocked.status,401);
  console.log("PASS: autonomous browser job, YouTube caption failure, automatic Gemini audio transcription, 3–5 sourced clips.");
 }finally{child.kill();mock.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
