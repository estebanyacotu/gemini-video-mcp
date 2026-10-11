"use strict";
const assert=require("node:assert/strict");
process.env.GEMINI_API_KEY="test-only";
process.env.GEMINI_MODEL="gemini-3.8-flash";
process.env.GEMINI_FALLBACK_MODEL="gemini-3.6-flash";
const {analyzeVideo}=require("./gemini-direct");
const url="https://www.youtube.com/watch?v=dQw4w9WgXcQ";
const original=global.fetch;
const response=(status,body)=>({ok:status>=200&&status<300,status,text:async()=>JSON.stringify(body)});
const success=text=>response(200,{candidates:[{content:{parts:[{text}]}}]});
(async()=>{
 let seen=[];
 try{
  global.fetch=async(endpoint,opts)=>{
   seen.push({endpoint,payload:JSON.parse(opts.body)});
   if(endpoint.includes("gemini-3.8-flash"))return response(503,{error:{status:"UNAVAILABLE",message:"Temporary capacity"}});
   return success("WEBVTT\n\n00:00:00.000 --> 00:00:03.000\nAudio real");
  };
  let result=await analyzeVideo(url,"Transcribe");
  assert.equal(result.model,"gemini-3.6-flash");
  assert.equal(result.fallbackFrom,"gemini-3.8-flash");
  assert.equal(seen.length,2);
  assert(seen[0].endpoint.includes("gemini-3.8-flash"));
  assert(seen[1].endpoint.includes("gemini-3.6-flash"));
  assert.deepEqual(seen[0].payload,seen[1].payload);
  assert.equal(seen[0].payload.generationConfig.maxOutputTokens,16384);
  seen=[];
  global.fetch=async(endpoint,opts)=>{seen.push(endpoint);return success("Result from primary");};
  result=await analyzeVideo(url,"Transcribe");
  assert.equal(result.model,"gemini-3.8-flash");
  assert.equal(result.fallbackFrom,undefined);
  assert.equal(seen.length,1);
  seen=[];
  global.fetch=async(endpoint)=>{seen.push(endpoint);return response(403,{error:{status:"PERMISSION_DENIED",message:"Unavailable URL"}});};
  await assert.rejects(analyzeVideo(url,"Transcribe"),e=>e.status===403);
  assert.equal(seen.length,1,"Permission denied must not be hidden with a model switch.");
  console.log("PASS: Gemini 3.8 preferred; 503 routes to 3.6; 403 preserved; no API key exposed.");
 }finally{global.fetch=original}
})().catch(e=>{console.error(e);process.exitCode=1});
