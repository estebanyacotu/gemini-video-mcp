"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const http=require("node:http");
const {spawn}=require("node:child_process");
const {parseSrt,mine}=require("./miner");
const html=fs.readFileSync(__dirname+"/index.html","utf8");
const script=html.match(/<script>([\s\S]*?)<\/script>/);
assert(script);
new vm.Script(script[1],{filename:"tablero-ui.js"});
const stamp=n=>[Math.floor(n/3600),Math.floor(n%3600/60),n%60].map(x=>String(x).padStart(2,"0")).join(":")+",000";
let srt="";
for(let t=0,i=1;t<520;t+=3,i++)srt+=i+"\n"+stamp(t)+" --> "+stamp(t+3)+"\nPero atención: Trump y México, petróleo, gasolina, bolsillo, millones y contradicciones. "+t+"\n\n";
const cues=parseSrt(srt);
assert(cues.length>150);
const clips=mine(cues,520,5);
assert(clips.length>=3&&clips.length<=5);
assert(clips.every(c=>c.literal&&c.cuts.length));
const mock=http.createServer(async(req,res)=>{
 let body="";for await(const c of req)body+=c;
 const request=JSON.parse(body);
 const data=request.params.name==="get_raw_subtitles"
  ? {videoId:"Jvg9okd2N8M",type:"auto",lang:"es-orig",format:"srt",content:srt,total_length:srt.length,is_truncated:false}
  : {title:"Live de prueba",duration:520,channel:"Canal"};
 res.setHeader("Content-Type","application/json");
 res.end(JSON.stringify({jsonrpc:"2.0",id:1,result:{structuredContent:data,isError:false}}));
});
async function run(){
 await new Promise(ok=>mock.listen(0,"127.0.0.1",ok));
 const port=35000+Math.floor(Math.random()*10000);
 const child=spawn(process.execPath,[__dirname+"/server.js"],{env:Object.assign({},process.env,{
  APP_PASSWORD:"test-only-pass",SESSION_SECRET:"test-only-secret",PORT:String(port),
  TRANSCRIPTOR_MCP_URL:"http://127.0.0.1:"+mock.address().port+"/mcp"
 }),stdio:"ignore"});
 const root="http://127.0.0.1:"+port;
 try{
  let ready=false;
  for(let i=0;i<100;i++){try{if((await fetch(root+"/health")).ok){ready=true;break}}catch{}await new Promise(r=>setTimeout(r,100));}
  assert(ready);
  const data={url:"https://www.youtube.com/live/Jvg9okd2N8M"};
  const unauth=await fetch(root+"/api/extract",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
  assert.equal(unauth.status,401);
  const login=await fetch(root+"/login",{method:"POST",redirect:"manual",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"password=test-only-pass"});
  assert.equal(login.status,303);
  const cookie=login.headers.get("set-cookie").split(";")[0];
  const result=await fetch(root+"/api/extract",{method:"POST",headers:{"Content-Type":"application/json","Cookie":cookie},body:JSON.stringify(data)});
  const response=await result.json();
  assert.equal(result.status,200,JSON.stringify(response));
  assert.equal(response.rawSrt,srt);
  assert.equal(response.totalCues,cues.length);
  assert(response.full&&response.clips.length>=3);
  const invalid=await fetch(root+"/api/extract",{method:"POST",headers:{"Content-Type":"application/json","Cookie":cookie},body:JSON.stringify({url:"https://evil.example/video"})});
  assert.equal(invalid.status,400);
  console.log("PASS: JS UI, parsing SRT, minería, autenticación, API y URL segura");
 }finally{child.kill();mock.close();}
}
run().catch(e=>{console.error(e);process.exitCode=1;});
