"use strict";
const BASE="https://generativelanguage.googleapis.com/v1beta";
const MODEL=process.env.GEMINI_MODEL||"gemini-3.8-flash";
const KEY=process.env.GEMINI_API_KEY||"";
function statusError(http,body){
 const code=String(body?.error?.status||"ERROR");
 const msg=String(body?.error?.message||"Error de Google").replace(/[A-Za-z0-9_-]{28,}/g,"[oculto]").slice(0,230);
 const e=new Error("Google Gemini "+http+" "+code+": "+msg);
 e.status=http;e.reason=code;return e;
}
async function googleFetch(url,options={},timeout=150000){
 if(!KEY)throw Error("Falta configurar la clave de Gemini en Render.");
 const controller=new AbortController();
 const timer=setTimeout(()=>controller.abort(),timeout);
 try{
  const response=await fetch(BASE+url,{
   ...options,signal:controller.signal,
   headers:{"x-goog-api-key":KEY,"Content-Type":"application/json",...(options.headers||{})}
  });
  const raw=await response.text();
  let body={};try{body=JSON.parse(raw)}catch{}
  if(!response.ok)throw statusError(response.status,body);
  return body;
 }catch(e){if(e.name==="AbortError")throw Error("Google tardó demasiado en responder.");throw e}
 finally{clearTimeout(timer)}
}
async function analyzeVideo(url,instruction){
 if(!/^https:\/\/www\.youtube\.com\/watch\?v=[\w-]{11}$/.test(url))throw Error("URL YouTube inválida.");
 const body=await googleFetch("/models/"+encodeURIComponent(MODEL)+":generateContent",{
  method:"POST",body:JSON.stringify({
   contents:[{role:"user",parts:[
    {file_data:{file_uri:url}},
    {text:instruction}
   ]}],
   generationConfig:{temperature:0,maxOutputTokens:8192}
  })
 },170000);
 const candidate=body.candidates?.[0];
 const output=(candidate?.content?.parts||[]).map(p=>p.text||"").join("\n").trim();
 if(!output){
  const reason=candidate?.finishReason||body.promptFeedback?.blockReason||"sin salida";
  throw Error("Google no proporcionó transcripción ni información del video: "+String(reason));
 }
 return {text:output};
}
async function testCredential(){
 if(!KEY)return {ok:false,status:"sin clave"};
 try{
  const data=await googleFetch("/models?pageSize=100",{method:"GET"},20000);
  const available=(data.models||[]).some(m=>m.name==="models/"+MODEL);
  return {ok:true,status:"conexión autenticada",model:MODEL,modelListed:available};
 }catch(e){return {ok:false,status:e.status||"red",reason:e.reason||e.message.slice(0,90)}}
}
async function testVideo(url){
 const output=await analyzeVideo(url,
  "Comprueba si puedes acceder al video real y reconocer su audio. Responde solo JSON: {\"accessible\":true/false,\"duration_seconds\":numero}. Incluye duración solo si es verificable. No inventes.");
 let json=null;try{json=JSON.parse(output.text.match(/\{[\s\S]*?\}/)?.[0]||"")}catch{}
 return {ok:json?.accessible===true,duration:Number(json?.duration_seconds)||0};
}
module.exports={analyzeVideo,testCredential,testVideo};
