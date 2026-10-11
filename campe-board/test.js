"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),{spawn}=require("node:child_process");
const {parseSrt,mine}=require("./miner");
const html=fs.readFileSync(__dirname+"/index.html","utf8"),script=html.match(/<script>([\s\S]*?)<\/script>/);
assert(script);new vm.Script(script[1],{filename:"tablero-ui.js"});
assert(html.includes("Supadata")&&html.includes("id=\"supadataKey\""));
const stamp=n=>[Math.floor(n/3600),Math.floor(n%3600/60),n%60].map(x=>String(x).padStart(2,"0")).join(":")+",000";
let srt="";for(let t=0,i=1;t<520;t+=3,i++)srt+=i+"\n"+stamp(t)+" --> "+stamp(t+3)+"\nPero atención: Trump y México, petróleo, gasolina, bolsillo, millones y contradicciones. "+t+"\n\n";
const cues=parseSrt(srt);assert(cues.length>150);
const clips=mine(cues,520,5);assert(clips.length>=3&&clips.length<=5&&clips.every(c=>c.literal&&c.cuts.length));
(async()=>{
 const port=35000+Math.floor(Math.random()*10000);
 const child=spawn(process.execPath,[__dirname+"/server.js"],{stdio:"ignore",env:{
  ...process.env,APP_PASSWORD:"test-only-pass",SESSION_SECRET:"test-secret",GEMINI_API_KEY:"",PORT:String(port)
 }});
 const root="http://127.0.0.1:"+port;
 try{
  let ready=false;for(let i=0;i<100;i++){try{if((await fetch(root+"/health")).ok){ready=true;break}}catch{}await new Promise(ok=>setTimeout(ok,100));}
  assert(ready);
  const input={url:"https://www.youtube.com/live/Jvg9okd2N8M"};
  const unauth=await fetch(root+"/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(input)});
  assert.equal(unauth.status,401);
  const login=await fetch(root+"/login",{method:"POST",redirect:"manual",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"password=test-only-pass"});
  assert.equal(login.status,303);
  const cookie=login.headers.get("set-cookie").split(";")[0];
  const imported=await fetch(root+"/api/import-srt",{method:"POST",headers:{"Content-Type":"application/json","Cookie":cookie},body:JSON.stringify({url:input.url,rawSrt:srt})});
  const result=await imported.json();
  assert.equal(imported.status,200,JSON.stringify(result));
  assert.equal(result.rawSrt,srt);
  assert(result.clips.length>=3);
  const invalid=await fetch(root+"/api/jobs",{method:"POST",headers:{"Content-Type":"application/json","Cookie":cookie},body:JSON.stringify({url:"https://evil.example/video",supadataKey:"key-for-test-123"})});
  assert.equal(invalid.status,400);
  const retired=await fetch(root+"/api/extract",{method:"POST",headers:{"Content-Type":"application/json","Cookie":cookie},body:JSON.stringify(input)});
  assert.equal(retired.status,410);
  console.log("PASS: valid mobile interface JS, SRT, ranking, session private, no Gemini route.");
 }finally{child.kill();}
})().catch(e=>{console.error(e);process.exitCode=1});
