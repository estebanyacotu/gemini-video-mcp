"use strict";
const SIGNALS=[
 [/\b(trump|putin|zelensk|rubio|sheinbaum|harfuch|méxico|estados unidos|ucrania|rusia)\b/gi,1.2],
 [/\b(petróleo|petrolera|gasolina|combustible|bolsillo|dinero|dólar|precio|cost[oó]|economía|arancel|impuesto|millones|miles|mercado)\b/gi,2.5],
 [/\b(ataque|bombarde|guerra|amenaz|explosi|destru|muerte|asesina|corrupci|traici|escándalo|mentira|secreto|denuncia|fraude|presión|bloque|peligro|caos|indignante|responsable)\b/gi,2.0],
 [/\b(pero|sin embargo|resulta que|la verdad|no es cierto|falso|ahora bien|por qué|quién|qué pasó|a pesar|mientras tanto|atención|ojo|lo peor|el problema|porque)\b/gi,1.5],
];
const srtSeconds=(s)=>{const m=String(s).trim().match(/^(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{1,3})$/);return m?Number(m[1]||0)*3600+Number(m[2])*60+Number(m[3])+Number(m[4].padEnd(3,"0"))/1000:NaN};
function toClock(n){n=Math.max(0,Math.floor(n));return [Math.floor(n/3600),Math.floor(n%3600/60),n%60].map(x=>String(x).padStart(2,"0")).join(":");}
function parseSrt(raw){
 if(typeof raw!=="string"||!raw.trim())throw Error("El motor no devolvió subtítulos.");
 const entries=[],blocks=raw.replace(/^\uFEFF/,"").replace(/\r/g,"").split(/\n\s*\n/);
 for(const block of blocks){
  const lines=block.split("\n").map(x=>x.trim()).filter(Boolean);
  const idx=lines.findIndex(x=>x.includes("-->"));if(idx<0)continue;
  const match=lines[idx].match(/([\d:,\.]+)\s*-->\s*([\d:,\.]+)/);if(!match)continue;
  const start=srtSeconds(match[1]),end=srtSeconds(match[2]);if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start)continue;
  const literal=lines.slice(idx+1).join(" ").replace(/<[^>]*>/g,"").replace(/\{\\[^}]*\}/g,"").trim();
  if(literal)entries.push({start,end,text:literal});
 }
 entries.sort((a,b)=>a.start-b.start);
 if(entries.length===0)throw Error("Se recibió un archivo SRT sin segmentos válidos.");
 return entries.filter((x,i)=>i===0||x.start!==entries[i-1].start||x.text!==entries[i-1].text);
}
function between(cues,a,b){return cues.filter(x=>x.end>a&&x.start<b);}
function literal(cues,a,b){return between(cues,a,b).map(x=>x.text).join(" ").trim();}
function countMatches(s,re){return (s.match(re)||[]).length;}
function tension(text){let val=0;for(let i=0;i<SIGNALS.length;i++){const [re,w]=SIGNALS[i];val+=Math.min(11,countMatches(text,re))*w;}return val;}
function cutsFor(cues,start,end){
 const bounds=[start,start+(end-start)/3,start+(end-start)*2/3,end];
 const parts=[];
 for(let k=0;k<3;k++){let a=bounds[k],b=bounds[k+1];const line=literal(cues,a,b);if(line)parts.push({start:toClock(a),end:toClock(b),literal:line,weight:tension(line),ordinal:k});}
 if(!parts.length)return [];
 const strongest=parts.reduce((a,b)=>a.weight>=b.weight?a:b);
 return [strongest,...parts.filter(p=>p!==strongest).sort((a,b)=>a.ordinal-b.ordinal)].map((p,i)=>({
  id:require("node:crypto").randomUUID(),role:i===0?"Gancho":i===parts.length-1?"Cierre":"Desarrollo",start:p.start,end:p.end,literal:p.literal
 }));
}
function mine(cues,duration,max=5){
 const first=cues[0].start, last=Math.max(...cues.map(c=>c.end));
 const horizon=Math.min(Math.max(duration||0,last),last+10);
 const size=Math.min(100,Math.max(62,horizon-first-1));
 if(size<61||horizon-first<62)return [];
 const opportunities=[];
 for(let pos=first;pos+62<=horizon;pos+=24){
  const end=Math.min(horizon,pos+size),segment=between(cues,pos,end),speech=segment.map(x=>x.text).join(" ");
  if(segment.length<9||speech.length<220)continue;
  const raw=tension(speech);
  const density=raw/Math.max(1,segment.length);
  const peak=Math.max(...segment.map(x=>tension(x.text)));
  const narrative=/\b(pero|porque|entonces|por eso|ahora|sin embargo|resulta)\b/i.test(speech)?1:0;
  const value=density*2+Math.log1p(raw)+peak*.4+narrative;
  opportunities.push({start:pos,end,raw,value,speech,peak,narrative});
 }
 opportunities.sort((a,b)=>b.value-a.value);
 const picked=[];
 for(const p of opportunities){if(picked.length>=max)break;if(picked.some(x=>Math.max(x.start,p.start)<Math.min(x.end,p.end)+40))continue;picked.push(p);}
 if(picked.length<Math.min(3,Math.floor((horizon-first)/62))){
  for(const p of opportunities){if(picked.length>=Math.min(3,max))break;if(picked.some(x=>Math.max(x.start,p.start)<Math.min(x.end,p.end)))continue;picked.push(p);}
 }
 const maxVal=Math.max(...opportunities.map(x=>x.value),1);
 return picked.sort((a,b)=>b.value-a.value).map((p,i)=>{
  const rawScore=Math.max(1,p.value/maxVal);
  const hook=Math.max(1,Math.min(10,Math.round(5+rawScore*5)));
  const conflict=tension(p.speech),tens=Math.max(1,Math.min(10,Math.round(4+Math.min(1,conflict/60)*6)));
  const autonomy=Math.max(4,Math.min(10,Math.round(6+Math.min(1,p.speech.length/700)*4)));
  const firstStrong=between(cues,p.start,p.end).slice().sort((a,b)=>tension(b.text)-tension(a.text))[0];
  const title=(firstStrong?.text||"Candidato").slice(0,92).trim();
  return {id:require("node:crypto").randomUUID(),title,start:toClock(p.start),end:toClock(p.end),
   hook,tension:tens,autonomy,literal:literal(cues,p.start,p.end),cuts:cutsFor(cues,p.start,p.end),
   source:"subtítulos",method:"ranking editorial heurístico",originalOrder:i+1};
 });
}
module.exports={parseSrt,mine,between,literal,toClock,srtSeconds,cutsFor};
