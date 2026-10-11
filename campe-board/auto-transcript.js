"use strict";
const {parseSrt,mine,toClock}=require("./miner");
function timeValue(raw){
 const v=String(raw).trim().replace(",",".").split(":");
 if(v.length!==2&&v.length!==3)return NaN;
 const sec=Number(v.pop()),min=Number(v.pop()),h=v.length?Number(v.pop()):0;
 if(!Number.isFinite(sec)||!Number.isFinite(min)||!Number.isFinite(h)||sec<0||sec>=60||min<0||min>=60||h<0)return NaN;
 return h*3600+min*60+sec;
}
function srtTime(sec){
 const n=Math.max(0,Number(sec)),whole=Math.floor(n),ms=Math.round((n-whole)*1000);
 return [Math.floor(whole/3600),Math.floor(whole%3600/60),whole%60].map(x=>String(x).padStart(2,"0")).join(":")+","+String(ms).padStart(3,"0");
}
function parseGenerated(raw,start=0,end=1e10){
 const body=String(raw||"").replace(/\x60{3}(?:srt|vtt|text)?/ig,"").replace(/^WEBVTT\s*/i,"").replace(/\r/g,"");
 const silence=/^\s*(NO_AUDIO|SIN_VOZ|SIN AUDIO)\s*$/i.test(body.trim());
 if(silence)return [];
 const lines=body.split("\n"),out=[];let current=null;
 for(const rawLine of lines){
  const line=rawLine.trim();
  const match=line.match(/((?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*((?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{1,3})/);
  if(match){if(current)out.push(current);current={start:timeValue(match[1]),end:timeValue(match[2]),text:""};continue;}
  if(current&&line&&!/^\d+$/.test(line)&&!/^NOTE\b|^STYLE\b|^REGION\b/i.test(line))current.text+=(current.text?" ":"")+line.replace(/<[^>]*>/g,"");
 }
 if(current)out.push(current);
 const items=out.filter(x=>Number.isFinite(x.start)&&Number.isFinite(x.end)&&x.end>x.start&&x.text.length);
 const relative=start>=120&&items.length&&items.every(x=>x.start>=0&&x.end<=(end-start)+10)&&items.some(x=>x.start<start-15);
 if(relative)for(const cue of items){cue.start+=start;cue.end+=start;}
 return items.filter(x=>x.start>=start-15&&x.end<=end+15).map(x=>({start:Math.max(0,x.start),end:x.end,text:x.text}));
}
function toSrt(cues){return cues.map((x,i)=>String(i+1)+"\n"+srtTime(x.start)+" --> "+srtTime(x.end)+"\n"+x.text+"\n").join("\n");}
function materialize(url,raw,opts={}){
 const cues=parseSrt(raw),duration=Number(opts.duration)||Math.ceil(cues[cues.length-1].end);
 return {url,videoId:url.slice(-11),title:opts.title||"Live de YouTube",channel:opts.channel||"",
 duration,subtitleType:opts.subtitleType||"desconocido",language:opts.language||"sin especificar",
 source:opts.source||"sin especificar",rawSrt:raw,cues,clips:mine(cues,duration,5),totalCues:cues.length,full:true,
 method:opts.method||"Minado editorial heurístico",transcriptionDisclaimer:opts.transcriptionDisclaimer||""};
}
async function fromGemini({url,duration,mcpTool,onProgress=()=>{},title="",channel=""}){
 if(!Number.isFinite(duration)||duration<=0)throw Error("No se obtuvo la duración del live para una transcripción completa.");
 if(duration>3*3600)throw Error("Este prototipo transcribe automáticamente hasta tres horas por live.");
 const span=360,n=Math.ceil(duration/span),all=[];
 for(let i=0;i<n;i++){
  const start=i*span,end=Math.min(duration,(i+1)*span);
  onProgress({stage:"Reconociendo audio con Gemini · bloque "+(i+1)+" de "+n,progress:Math.round(i/n*95)});
  const instruction=[
   "Transcribe solo el AUDIO REAL del video, sin resumen, comentarios ni fact-checking.",
   "Transcribe ÚNICAMENTE las palabras pronunciadas entre "+toClock(start)+" y "+toClock(end)+".",
   "Conserva el idioma ORIGINAL, incluso si está en inglés. No traduzcas, parafrasees, corrijas ni completes frases.",
   "Entrega solo SRT o WEBVTT con timestamps ABSOLUTOS del video y frases breves de 2-8 segundos.",
   "Usa [inaudible] para palabras que no entiendas. No inventes palabras.",
   "No incluyas contenido fuera del intervalo. Si no hay voz, responde SIN_VOZ.",
   "Mantén todas las frases audibles de principio a fin del intervalo."
  ].join("\n");
  const answer=await mcpTool("analizar_video",{url,instruccion:instruction});
  const output=answer.text||answer.result||"";
  if(typeof output!=="string"||!output.trim())throw Error("Gemini no devolvió audio transcrito en bloque "+(i+1)+".");
  const chunk=parseGenerated(output,start,end);
  if(!chunk.length&&!/^\s*(SIN_VOZ|NO_AUDIO)\s*$/i.test(output.trim()))throw Error("Gemini no entregó timestamps válidos en bloque "+(i+1)+".");
  if(chunk.length&&end-start>90&&chunk[chunk.length-1].end<end-65)
   throw Error("El bloque "+(i+1)+" quedó incompleto; no se marcará como transcripción íntegra.");
  all.push(...chunk);
 }
 if(all.length<3)throw Error("No se obtuvo suficiente audio hablado para generar clips.");
 all.sort((a,b)=>a.start-b.start);
 const unique=all.filter((x,i)=>!i||x.start!==all[i-1].start||x.text!==all[i-1].text);
 return materialize(url,toSrt(unique),{title,channel,duration,source:"Gemini · reconocimiento de audio",
  subtitleType:"ai",language:"idioma del audio",method:"Audio transcrito con IA y ranking editorial",
  transcriptionDisclaimer:"Transcripción automática desde audio, no subtítulos literales certificados. Puede contener errores y necesita revisión del audio."});
}
module.exports={parseGenerated,toSrt,materialize,fromGemini,timeValue,srtTime};
