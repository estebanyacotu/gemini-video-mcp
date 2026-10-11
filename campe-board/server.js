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
function headers(res,extra){res.setHeader("Cache-Control","no-store, private");res.setHeader("X-Content-Type-Options","nosniff");res.setHeader("X-Frame-Options","DENY");res.setHeader("Referrer-Policy","no-referrer");res.setHeader("Content-Security-Policy","default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'");for(let [k,v] of Object.entries(extra||{}))res.setHeader(k,v);}
function send(res,status,body,type){res.statusCode=status;headers(res,{"Content-Type":type||"text/html; charset=utf-8"});res.end(body);}
function redirect(res,c){res.statusCode=303;headers(res,c?{"Set-Cookie":c}:{});res.setHeader("Location","/");res.end();}
function ip(req){return (req.headers["x-forwarded-for"]||req.socket.remoteAddress||"unknown").toString().split(",")[0].trim();}
function limit(addr){let now=Date.now(),a=attempts.get(addr)||[];a=a.filter(t=>now-t<15*60*1000);attempts.set(addr,a);if(attempts.size>10000)attempts.clear();return a;}
const server=http.createServer((req,res)=>{
 if(!PASSWORD||!SESSION_SECRET){return send(res,503,"Servicio aún no configurado.","text/plain; charset=utf-8");}
 let pathname;try{pathname=new URL(req.url,"http://local").pathname;}catch(e){return send(res,400,"Ruta inválida.","text/plain");}
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