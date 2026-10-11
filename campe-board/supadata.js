"use strict";
const {materialize,toSrt}=require("./auto-transcript");
const BASE=process.env.SUPADATA_BASE_URL||"https://api.supadata.ai";
const wait=ms=>new Promise(ok=>setTimeout(ok,ms));
const clipMessage=s=>String(s||"").slice(0,230);
function providerError(status,body){
 const name=String(body?.error||body?.code||"error");
 const msg=clipMessage(body?.message||body?.details||"Error de Supadata");
 const hint=status===401||status===403?"Revisa la clave de Supadata.":status===402||status===429?"Revisa los créditos o límites de tu cuenta.":status===404?"El video no es accesible desde Supadata.":"";
 const e=new Error("Supadata ("+status+"): "+msg+". "+hint);
 e.status=status;e.providerCode=name;
 return e;
}
async function request(path,key,{timeout=105000}={}){
 const ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),timeout);
 try{
  const res=await fetch(BASE+path,{method:"GET",headers:{"x-api-key":key,"Accept":"application/json"},signal:ctl.signal});
  const raw=await res.text();
  let data;try{data=JSON.parse(raw)}catch{throw Error("Supadata devolvió una respuesta que no es JSON.");}
  if(!res.ok&&res.status!==202)throw providerError(res.status,data);
  return {status:res.status,data};
 }catch(e){
  if(e.name==="AbortError")throw Error("Supadata tardó demasiado en responder a la solicitud.");
  throw e;
 }finally{clearTimeout(timer)}
}
function toCues(obj){
 const result=obj?.result&&typeof obj.result==="object"?obj.result:obj?.data&&typeof obj.data==="object"?obj.data:obj;
 const entries=result?.content;
 if(!Array.isArray(entries))throw Error("Supadata no devolvió fragmentos con marcas de tiempo.");
 const cues=entries.map(x=>({
  start:Number(x.offset)/1000,end:(Number(x.offset)+Number(x.duration))/1000,
  text:String(x.text||"").trim()
 })).filter(x=>Number.isFinite(x.start)&&Number.isFinite(x.end)&&x.start>=0&&x.end>x.start&&x.text);
 cues.sort((a,b)=>a.start-b.start);
 const unique=cues.filter((x,i)=>i===0||x.start!==cues[i-1].start||x.text!==cues[i-1].text);
 if(unique.length<2)throw Error("Supadata no encontró suficiente voz con timestamps para crear cortes.");
 return {cues:unique,lang:result.lang||"es",duration:unique[unique.length-1].end};
}
function formatResult(url,obj){
 const result=toCues(obj);
 const duration=result.duration;
 const out=materialize(url,toSrt(result.cues),{
  duration,title:"Live de YouTube",language:result.lang,
  subtitleType:"external",
  source:"Supadata · subtítulos o reconocimiento de voz",
  method:"Transcripción Supadata con tiempos de la fuente · selección editorial automática",
  transcriptionDisclaimer:"Se conserva el texto recibido de Supadata. Si lo generó reconocimiento de voz, puede haber errores."
 });
 out.totalCues=result.cues.length;
 out.modelsUsed=[];
 out.provider="supadata";
 out.coverageNote="La última marca temporal de habla es "+Math.round(duration)+" s; el proveedor no confirma que todos los silencios y fragmentos estén cubiertos.";
 return out;
}
async function extractSupadata(url,key,onProgress=()=>{}){
 if(!key||typeof key!=="string"||key.length<12||key.length>300||/\s/.test(key))throw Error("Introduce una clave válida de Supadata.");
 onProgress({stage:"Solicitando transcripción a Supadata",progress:5});
 const path="/v1/transcript?url="+encodeURIComponent(url)+"&lang=es&mode=auto&text=false";
 const first=await request(path,key);
 if(first.status===200){
  onProgress({stage:"Preparando tabla de cortes",progress:90});
  return formatResult(url,first.data);
 }
 const jobId=first.data?.jobId;
 if(typeof jobId!=="string"||!/^[A-Za-z0-9_-]{8,120}$/.test(jobId))throw Error("Supadata no entregó un identificador válido para consultar la transcripción.");
 const started=Date.now(),limit=45*60*1000;
 let cycle=0;
 while(Date.now()-started<limit){
  await wait(3500);
  const check=await request("/v1/transcript/"+encodeURIComponent(jobId),key,{timeout:45000});
  const data=check.data;
  if(data.status==="completed"||data.status==="complete"){
   onProgress({stage:"Construyendo ranking y tabla de cortes",progress:92});
   return formatResult(url,data);
  }
  if(data.status==="failed")throw Error("Supadata no logró transcribir el video: "+clipMessage(data.error?.message||data.error));
  cycle++;
  onProgress({stage:"Supadata está "+(data.status==="queued"?"preparando":"transcribiendo")+" el video",progress:Math.min(85,10+cycle)});
 }
 throw Error("Supadata no terminó la transcripción durante el periodo máximo permitido.");
}
module.exports={extractSupadata,formatResult,toCues,providerError};
