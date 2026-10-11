"use strict";
const http=require("node:http");
const crypto=require("node:crypto");
const fs=require("node:fs");
const path=require("node:path");
const index=fs.readFileSync(path.join(__dirname,"index.html"));
const PASSWORD=process.env.APP_PASSWORD||"";
const SESSION_SECRET=process.env.SESSION_SECRET||"";
const attempts=new Map();
const TTL=7*24*60*60;
const loginHtml=(bad)=>'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Acceso privado · CampeNEWS</title><style>*{box-sizing:border-box}body{background:#10151d;color:#f4f6f9;font:16px system-ui,-apple-system,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;padding:18px}.card{background:#19212c;border:1px solid #354153;border-radius:18px;width:min(100%,410px);padding:28px}h1{font-size:25px;letter-spacing:-.04em;margin:6px 0 10px}p{color:#abb8c9;font-size:14px;line-height:1.55}.key{font-size:12px;letter-spacing:.16em;color:#ffbd5d;font-weight:800}label{font-size:13px;display:block;margin-top:20px;margin-bottom:7px}input{background:#10151d;color:white;border:1px solid #47536a;padding:14px;width:100%;font:inherit;border-radius:10px}button{background:#ffbd5d;border:0;border-radius:10px;padding:14px;font:inherit;font-weight:800;color:#1a1a1a;width:100%;margin-top:14px}small{color:#f7aca5;display:block;margin-top:12px}.brand{background:#ffbd5d;color:#171b21;border-radius:11px;width:48px;height:48px;display:grid;place-items:center;font-weight:900}</style></head><body><main class="card"><div class="brand">CN</div><div class="key" style="margin-top:15px">MESA EDITORIAL PRIVADA</div><h1>FÓRMULA CAMPE NEWS</h1><p>Introduce tu clave para abrir el tablero de minería, ranking y reconstrucción de clips.</p><form action="/login" method="post"><label for="password">Clave de acceso</label><input id="password" type="password" name="password" autocomplete="current-password" required autofocus><button type="submit">Entrar al tablero →</button></form>'+(bad?'<small>Clave incorrecta o acceso temporalmente limitado.</small>':'')+'<p style="font-size:12px;margin-bottom:0">El contenido editorial se guarda únicamente en el navegador que utilices.</p></main></body></html>';
function digest(x){return crypto.createHash("sha256").update(x).digest();}
function eq(a,b){return a.length===b.length&&crypto.timingSafeEqual(a,b);}
function sign(exp){return crypto.createHmac("sha256",SESSION_SECRET).update(String(exp)).digest("base64url");}
function auth(req){let c=(req.headers.cookie||"").split(";").map(x=>x.trim()).find(x=>x.startsWith("campe_session="));if(!c)return false;let value=c.slice(14);let pieces=value.split(".");if(pieces.length!==2)return false;let exp=Number(pieces[0]);if(!Number.isSafeInteger(exp)||exp<Math.floor(Date.now()/1000))return false;return eq(Buffer.from(pieces[1]),Buffer.from(sign(exp)));}
function cookie(v,maxAge){return "campe_session="+v+"; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age="+maxAge;}
function headers(res,extra){res.setHeader("Cache-Control","no-store, private");res.setHeader("X-Content-Type-Options","nosniff");res.setHeader("X-Frame-Options","DENY");res.setHeader("Referrer-Policy","no-referrer");res.setHeader("Content-Security-Policy","default-src 'none'; connect-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'");for(let [k,v] of Object.entries(extra||{}))res.setHeader(k,v);}
function send(res,status,body,type){res.statusCode=status;headers(res,{"Content-Type":type||"text/html; charset=utf-8"});res.end(body);}
function redirect(res,c){res.statusCode=303;headers(res,c?{"Set-Cookie":c}:{});res.setHeader("Location","/");res.end();}
function ip(req){return (req.headers["x-forwarded-for"]||req.socket.remoteAddress||"unknown").toString().split(",")[0].trim();}
function limit(addr){let now=Date.now(),a=attempts.get(addr)||[];a=a.filter(t=>now-t<15*60*1000);attempts.set(addr,a);if(attempts.size>10000)attempts.clear();return a;}

const {parseSrt,mine}=require("./miner");
const MCP_URL=process.env.TRANSCRIPTOR_MCP_URL||"https://gemini-video-mcp-4ev0.onrender.com/mcp";
function videoUrl(raw){
 const u=new URL(String(raw||"").trim());
 if(u.protocol!=="https:")throw Error("El enlace debe utilizar HTTPS.");
 const host=u.hostname.toLowerCase();let id;
 if(host==="youtu.be")id=u.pathname.split("/")[1];
 else if(["youtube.com","www.youtube.com","m.youtube.com"].includes(host)){
  if(u.pathname==="/watch")id=u.searchParams.get("v");
  else if(/^\/(live|shorts|embed)\/[A-Za-z0-9_-]{11}/.test(u.pathname))id=u.pathname.split("/")[2];
 }
 if(!id||!/^[A-Za-z0-9_-]{11}$/.test(id))throw Error("Pega un enlace válido de un live de YouTube.");
 return "https://www.youtube.com/watch?v="+id;
}
async function mcpTool(name,args){
 const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),150000);
 try{
  const response=await fetch(MCP_URL,{method:"POST",signal:controller.signal,headers:{
   "Content-Type":"application/json","Accept":"application/json, text/event-stream",
   "MCP-Protocol-Version":"2025-03-26"
  },body:JSON.stringify({jsonrpc:"2.0",id:1,method:"tools/call",params:{name,arguments:args}})});
  const text=await response.text();
  if(!response.ok)throw Error("El transcriptor respondió HTTP "+response.status);
  let data;
  if((response.headers.get("content-type")||"").includes("text/event-stream")){
   const events=text.split(/\r?\n/).filter(line=>line.startsWith("data:")).map(x=>x.slice(5).trim());
   data=events.map(x=>{try{return JSON.parse(x)}catch{return null}}).find(x=>x&&("result"in x||"error"in x));
  }else data=JSON.parse(text);
  if(!data)throw Error("Respuesta del transcriptor incompleta.");
  if(data.error)throw Error(String(data.error.message||"Error MCP"));
  if(data.result?.isError){
   const problem=data.result.content?.find(c=>c.text)?.text||"El transcriptor no obtuvo subtítulos.";
   throw Error(problem.slice(0,500));
  }
  let value=data.result?.structuredContent;
  if(!value){const field=data.result?.content?.find(c=>typeof c.text==="string");if(field){try{value=JSON.parse(field.text)}catch{value={content:field.text}}}}
  if(value?.result&&!value.content&&!value.title)value=value.result;
  if(!value||typeof value!=="object")throw Error("El transcriptor devolvió un formato desconocido.");
  return value;
 }finally{clearTimeout(timeout)}
}
function readJson(req,max=4096){
 return new Promise((resolve,reject)=>{
  let body="",bytes=0;
  req.on("data",chunk=>{
   bytes+=chunk.length;
   if(bytes>max){reject(Error("El enlace enviado es demasiado largo."));req.destroy();return}
   body+=chunk.toString("utf8");
  });
  req.on("end",()=>{try{resolve(JSON.parse(body))}catch{reject(Error("JSON inválido."))}});
  req.on("error",reject);
 });
}
async function extract(req,res){
 if(!auth(req))return send(res,401,JSON.stringify({error:"Sesión caducada. Vuelve a entrar."}),"application/json");
 const origin=req.headers.origin;const host=req.headers.host;
 if(origin&&new URL(origin).host!==host)return send(res,403,JSON.stringify({error:"Origen no autorizado."}),"application/json");
 if(!String(req.headers["content-type"]||"").startsWith("application/json"))return send(res,415,JSON.stringify({error:"Se requiere JSON."}),"application/json");
 try{
  const payload=await readJson(req);
  const url=videoUrl(payload.url);
  // No simulated data: both calls go to the user's running subtitle service.
  const subtitles=await mcpTool("get_raw_subtitles",{url,format:"srt"});
  if(!subtitles.content||subtitles.is_truncated)throw Error("El servicio no entregó el SRT completo.");
  if(subtitles.total_length&&subtitles.content.length!==subtitles.total_length)throw Error("La respuesta SRT no coincide con su longitud declarada.");
  const cues=parseSrt(subtitles.content);
  let info={};try{info=await mcpTool("get_video_info",{url})}catch(e){console.error("Optional metadata failed",e.message)}
  const duration=Number(info.duration)||Math.ceil(cues[cues.length-1].end);
  const clips=mine(cues,duration,5);
  return send(res,200,JSON.stringify({
   url,videoId:subtitles.videoId||url.slice(-11),
   title:info.title||"Live de YouTube",channel:info.channel||"",
   duration,subtitleType:subtitles.type||"desconocido",
   language:subtitles.lang||"desconocido",source:subtitles.source||"yt-dlp",
   rawSrt:subtitles.content,cues,clips,
   totalCues:cues.length,full:true,method:"subtítulos extraídos del video; ranking editorial heurístico"
  }),"application/json; charset=utf-8");
 }catch(err){console.error("Extraction error",err.message);
  const invalid=/enlace|youtube|HTTPS|JSON inválido|demasiado largo/i.test(err.message);
  return send(res,invalid?400:502,JSON.stringify({error:String(err.message||"No se pudo extraer la transcripción.").slice(0,500)}),"application/json; charset=utf-8");
 }
}

const server=http.createServer((req,res)=>{
 if(!PASSWORD||!SESSION_SECRET){return send(res,503,"Servicio aún no configurado.","text/plain; charset=utf-8");}
 let pathname;try{pathname=new URL(req.url,"http://local").pathname;}catch(e){return send(res,400,"Ruta inválida.","text/plain");}
 if(pathname==="/api/extract"&&req.method==="POST"){extract(req,res);return;}
 if(pathname==="/health"&&req.method==="GET")return send(res,200,JSON.stringify({ok:true,app:"campe-news-board",storage:"browser-local"}),"application/json; charset=utf-8");
 if(pathname==="/logout"&&req.method==="POST")return redirect(res,cookie("",0));
 if(pathname==="/login"&&req.method==="POST"){
   const addr=ip(req),past=limit(addr);
   if(past.length>=8)return send(res,429,loginHtml(true));
   let body="",size=0;
   req.on("data",chunk=>{size+=chunk.length;if(size>2048){req.destroy();return;}body+=chunk.toString("utf8");});
   req.on("end",()=>{
     let pass="";try{pass=new URLSearchParams(body).get("password")||"";}catch(e){}
     if(eq(digest(pass),digest(PASSWORD))){attempts.delete(addr);const exp=Math.floor(Date.now()/1000)+TTL;return redirect(res,cookie(exp+"."+sign(exp),TTL));}
     past.push(Date.now());attempts.set(addr,past);return send(res,401,loginHtml(true));
   });
   return;
 }
 if((pathname==="/"||pathname==="/login")&&(req.method==="GET"||req.method==="HEAD")){
   if(!auth(req)){return send(res,401,req.method==="HEAD"?"":loginHtml(false));}
   return send(res,200,req.method==="HEAD"?"":index);
 }
 return send(res,404,"No encontrado.","text/plain; charset=utf-8");
});
const port=Number(process.env.PORT||10000);
server.listen(port,"0.0.0.0",()=>console.log("CampeNEWS board listening on "+port));