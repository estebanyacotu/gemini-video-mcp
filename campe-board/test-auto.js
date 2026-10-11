"use strict";
const assert=require("node:assert/strict");
const {parseGenerated,toSrt,fromGemini}=require("./auto-transcript");
const sample="WEBVTT\n\n00:18.500 --> 00:22.500\nNo somos extraños al amor\n\n00:22.500 --> 00:26.500\nTú conoces las reglas\n";
let cues=parseGenerated(sample,0,60);
assert.equal(cues.length,2,"VTT válido debe convertirse en cues.");
assert.equal(cues[0].start,18.5);
assert.equal(cues[0].end,22.5);
assert(toSrt(cues).includes("00:00:18,500 --> 00:00:22,500"));
const relative=parseGenerated("00:00.000 --> 00:03.000\nTexto dicho\n\n00:03.000 --> 00:06.000\nMás texto",360,720);
assert.equal(relative[0].start,360,"Gemini may return relative timestamps.");
(async()=>{
 let calls=0;
 const mcpTool=async(name,args)=>{
  assert.equal(name,"analizar_video");
  calls++;
  const base=(calls-1)*360,lines=[];
  for(let i=0;i<360;i+=5){
   const from=base+i,to=from+5;
   function c(v){return [Math.floor(v/3600),Math.floor(v%3600/60),v%60].map(x=>String(x).padStart(2,"0")).join(":")+".000"}
   lines.push(c(from)+" --> "+c(to)+"\nPero hoy sucedió algo importante: Trump, gasolina y bolsillo de México, conflicto y consecuencias.");
  }
  return {text:"WEBVTT\n\n"+lines.join("\n\n")};
 };
 const result=await fromGemini({url:"https://www.youtube.com/watch?v=Jvg9okd2N8M",duration:720,mcpTool,title:"Live de prueba"});
 assert.equal(calls,2,"Two six-minute ranges.");
 assert.equal(result.subtitleType,"ai");
 assert(result.cues.length>=140);
 assert(result.rawSrt.length>2000);
 assert.equal(result.full,true);
 assert(result.clips.length>=3&&result.clips.length<=5);
 assert(result.transcriptionDisclaimer.includes("IA"));
 console.log("PASS: audio fallback produces timestamped SRT, complete coverage, candidates, source label");
})().catch(e=>{console.error(e);process.exitCode=1});
