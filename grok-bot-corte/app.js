const $=s=>document.querySelector(s),KEY='campeBoard.v1';
let S;try{S=JSON.parse(localStorage.getItem(KEY))}catch(e){}
S=S||{lives:[],active:null};S.settings=S.settings||{api:'',token:''};let form={lu:'',lt:'',lc:'',ln:'',sub:''};let busy=null,mode='in',tq='',tcand=0;let tab='lives',sortBy='rank',msg=null,editing=null,warnShown=false;
const save=()=>{try{localStorage.setItem(KEY,JSON.stringify(S))}catch(e){setMsg('Sin espacio en el navegador: exporta el JSON y borra lives viejos.','err')}};
const uid=()=>Math.random().toString(36).slice(2,9);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function parseT(s){s=String(s||'').trim();if(!/^\d{1,2}(:\d{1,2}){1,2}$/.test(s))return null;const p=s.split(':').map(Number);if(p.slice(1).some(x=>x>59))return null;return p.length==3?p[0]*3600+p[1]*60+p[2]:p[0]*60+p[1]}
const fmt=n=>{const h=Math.floor(n/3600),m=Math.floor(n%3600/60),s=n%60,z=x=>String(x).padStart(2,'0');return h?`${h}:${z(m)}:${z(s)}`:`${z(m)}:${z(s)}`};
function info(c){const a=parseT(c.inn),b=parseT(c.out);if(a==null||b==null)return{err:'IN/OUT inválido (usa MM:SS o HH:MM:SS)'};if(b<=a)return{err:'OUT debe ser mayor que IN'};const d=b-a;return{d,short:d<=60,long:d>180,err:d<=60?'Duración ≤ 60 s: no válido':null}}
const live=()=>S.lives.find(l=>l.id==S.active);
const sorted=l=>[...l.cands].sort((x,y)=>sortBy=='in'?(parseT(x.inn)??1e9)-(parseT(y.inn)??1e9):(x.rank-y.rank)||(y.hook-x.hook));
function setMsg(t,k){msg={t,k:k||''}}
function render(){document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('on',b.dataset.t==tab));
let h=msg?`<div class="msg ${msg.k}">${esc(msg.t)}</div>`:'';msg=null;
h+=({lives:vLives,texto:vTexto,cands:vCands,cmp:vCmp,tabla:vTabla})[tab]();$('#view').innerHTML=h}
function vLives(){let h=`<div class="card"><h2>Pegar link del live</h2><label>URL de YouTube</label><input id="lu" value="${esc(form.lu)}" inputmode="url" placeholder="https://www.youtube.com/live/..."><div class="row"><button class="pri" data-a="extract" ${busy?'disabled':''}>Extraer transcripción</button></div><p id="prog" class="ex">${busy?esc(busy):''}</p><label>Título (se llena solo)</label><input id="lt" value="${esc(form.lt)}"><label>Canal</label><input id="lc" value="${esc(form.lc)}"><label>Notas</label><textarea id="ln" rows="2">${esc(form.ln)}</textarea><div class="row"><button data-a="addLive">Guardar sin transcripción</button></div></div>
<div class="card"><h2>Importar subtítulos (.srt / .vtt)</h2><p class="ex">Si el servidor no puede leer YouTube, sube el archivo de subtítulos o pega su texto. Usa los campos de URL, título y canal de arriba.</p><label>Archivo</label><input id="subfile" type="file" accept=".srt,.vtt,text/plain,text/vtt"><label>o pega el texto</label><textarea id="subtxt" rows="4" placeholder="1&#10;00:00:04,680 --> 00:00:07,190&#10;...">${esc(form.sub)}</textarea><div class="row"><button class="pri" data-a="importSub">Importar subtítulos</button></div></div>
<details class="card"><summary style="min-height:44px">⚙️ Servidor (API y token)</summary><label>URL base de la API (vacío = este mismo sitio)</label><input id="sapi" inputmode="url" value="${esc(S.settings.api)}"><label>Token de acceso</label><input id="stok" type="password" value="${esc(S.settings.token)}"><p class="ex">Se guarda solo en este teléfono.</p></details>
<details class="card"><summary style="min-height:44px">Demo con datos de ejemplo</summary><p class="ex">Cortes de muestra del live v3-odNpotVc. Sin transcripción real.</p><button data-a="example">Cargar ejemplo (DEMO)</button></details>
<h2>Mis lives (${S.lives.length})</h2>`;
if(!S.lives.length)h+='<p>Aún no hay lives. Registra uno o carga el ejemplo.</p>';
S.lives.forEach(l=>h+=`<div class="card"><b>${esc(l.title||l.url)}</b> ${l.id==S.active?'<span class="badge first">ACTIVO</span>':''}${l.example?'<span class="badge">EJEMPLO</span>':''}<div class="ex">${esc(l.channel)} · ${l.cands.length} candidatos · ${l.segs?l.segs.length+' líneas de transcripción ('+esc(l.track||'')+')':'sin transcripción'}</div><div style="word-break:break-all;font-size:13px"><a style="color:#7dd3fc" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.url)}</a></div><p class="tr">${esc(l.notes)}</p><div class="row"><button data-a="pick" data-id="${l.id}">Activar</button><button data-a="delLive" data-id="${l.id}">Borrar</button></div></div>`);
return h+`<div class="card"><h2>Datos</h2><div class="row"><button data-a="expJson">Exportar JSON</button><button data-a="impJson">Importar JSON</button></div><input type="file" id="imp" accept=".json" hidden></div>`}

const segMark=(t)=>'['+fmt(Math.floor(t))+']';
function literal(l,c){const a=parseT(c.inn),b=parseT(c.out);if(!l.segs||a==null||b==null||b<=a)return null;let out=[],last=-99;for(const g of l.segs){if(g.s<a||g.s>=b)continue;if(!g.g&&g.s-last>=30){out.push(segMark(g.s));last=g.s}out.push(g.t)}return out.join(' ')}
function lineList(l){const q=tq.trim().toLowerCase();return l.segs.filter(g=>!q||g.t.toLowerCase().includes(q)).map(g=>`<button class="ln${g.g?' gp':''}" data-a="tapline" data-t="${g.s}" data-e="${g.e}">${g.g?'':'<b>'+segMark(g.s)+'</b> '}${esc(g.t)}</button>`).join('')||'<p>Sin resultados.</p>'}
function vTexto(){const l=live();if(!l)return'<p>Activa un live primero.</p>';if(!l.segs)return'<p>Este live no tiene transcripción. Pega el link en Lives y pulsa “Extraer transcripción”.</p>';
if(!l.cands.length)return'<p>Agrega al menos un candidato en la pestaña Candidatos para marcar IN/OUT.</p>';if(tcand>=l.cands.length)tcand=0;const c=l.cands[tcand];
return`<h2>Transcripción literal</h2><p class="ex">Pista: ${esc(l.track||'')} · ${l.segs.length} líneas</p><label>Candidato</label><select id="tc">${l.cands.map((x,i)=>`<option value="${i}" ${i==tcand?'selected':''}>${i+1}. ${esc(x.tema||'(sin tema)')}</option>`).join('')}</select>
<p id="inout">IN <b>${esc(c.inn||'—')}</b> · OUT <b>${esc(c.out||'—')}</b></p><div class="row"><button data-a="mode" data-m="in" class="${mode=='in'?'pri':''}">Tocar = IN</button><button data-a="mode" data-m="out" class="${mode=='out'?'pri':''}">Tocar = OUT</button></div><div class="row"><button data-a="insert" data-i="${tcand}">Insertar transcripción literal</button></div><label>Buscar</label><input id="tq" type="search" value="${esc(tq)}" placeholder="palabra o frase"><div id="lines" class="lines">${lineList(l)}</div>`}
function vCands(){const l=live();if(!l)return'<p>Primero activa o registra un live en la pestaña Lives.</p>';
let h=`<h2>${esc(l.title||l.url)}</h2><p>Candidatos: <b>${l.cands.length}/5</b> (mínimo 3, máximo 5)</p>`;
if(l.cands.length<3)h+=`<div class="msg">Faltan ${3-l.cands.length} candidato(s) para llegar al mínimo de 3.</div>`;
h+=l.cands.length>=5?'<div class="msg">Máximo alcanzado: no puedes agregar un 6.º candidato.</div><button class="pri" data-a="addC" disabled id="addc">+ Agregar candidato</button>':'<button class="pri" data-a="addC" id="addc">+ Agregar candidato</button>';
l.cands.forEach((c,i)=>{const f=info(c);h+=`<div class="card" data-i="${i}"><b>Candidato ${i+1}</b>
<label>Tema</label><input data-f="tema" value="${esc(c.tema)}">
<div class="row"><div><label>IN</label><input data-f="inn" inputmode="numeric" placeholder="MM:SS" value="${esc(c.inn)}"></div><div><label>OUT</label><input data-f="out" inputmode="numeric" placeholder="MM:SS" value="${esc(c.out)}"></div></div>
<p class="dur">${f.d!=null?`Duración: <b>${fmt(f.d)}</b> (${f.d}s) `:''}${f.short?'<span class="badge bad">≤60s INVÁLIDO</span>':''}${f.long?'<span class="badge long">LARGO &gt;180s</span>':''}${f.err&&!f.short?`<span class="badge bad">${esc(f.err)}</span>`:''}</p>
<label>Transcripción literal</label>${l.segs&&literal(l,c)!=null?`<button data-a="insert" data-i="${i}">Insertar transcripción literal (IN–OUT)</button>`:''}<textarea data-f="transcript" rows="5">${esc(c.transcript)}</textarea>
<label>Texto del hook</label><input data-f="hookText" value="${esc(c.hookText)}">
<div class="row"><div><label>Fuerza del hook: <b>${c.hook}</b>/10</label><input type="range" min="1" max="10" data-f="hook" value="${c.hook}"></div><div><label>Ranking (1 = primero)</label><input type="number" min="1" max="5" data-f="rank" value="${c.rank}"></div></div>
<label>Título (opcional)</label><input data-f="title" value="${esc(c.title)}"><label>Caption (opcional)</label><textarea data-f="caption" rows="2">${esc(c.caption)}</textarea>
<button data-a="delC" data-i="${i}" style="margin-top:8px">Quitar</button></div>`});return h}
function durHtml(c){const f=info(c);return `${f.d!=null?`Duración: <b>${fmt(f.d)}</b> (${f.d}s) `:''}${f.short?'<span class="badge bad">≤60s INVÁLIDO</span>':''}${f.long?'<span class="badge long">LARGO &gt;180s</span>':''}${f.err&&!f.short?`<span class="badge bad">${esc(f.err)}</span>`:''}`}
function vCmp(){const l=live();if(!l)return'<p>Activa un live primero.</p>';if(!l.cands.length)return'<p>Sin candidatos.</p>';
return`<h2>Comparar</h2><div class="grid">`+sorted(l).map(c=>{const f=info(c);return`<div class="card"><b>#${c.rank} ${esc(c.tema)}</b><br>IN <b>${esc(c.inn)}</b> → OUT <b>${esc(c.out)}</b><br>Duración: <b>${f.d!=null?fmt(f.d):'—'}</b> ${f.short?'<span class="badge bad">≤60s</span>':''}${f.long?'<span class="badge long">largo</span>':''}<br>Hook: <b>${c.hook}/10</b><div style="background:#2a303b;height:8px;border-radius:4px"><div style="background:#e11d48;height:8px;border-radius:4px;width:${c.hook*10}%"></div></div></div>`}).join('')+'</div>'}
function rows(l){return sorted(l).map((c,i)=>({n:i+1,c,f:info(c),first:sortBy=='rank'&&i==0}))}
function vTabla(){const l=live();if(!l)return'<p>Activa un live primero.</p>';let h=`<h2>Tabla de cortes</h2>`;
if(l.cands.length<3)h+=`<div class="msg">Atención: hay ${l.cands.length} candidato(s); se recomienda tener al menos 3.</div>`;
h+=`<div class="row"><button data-a="sort" data-s="rank" ${sortBy=='rank'?'class="pri"':''}>Orden: ranking</button><button data-a="sort" data-s="in" ${sortBy=='in'?'class="pri"':''}>Orden: IN</button><button data-a="copyMd">Copiar tabla</button><button data-a="csv">Descargar CSV</button></div>`;
const R=rows(l),tx=(c,i)=>`<div class="tr clamp" data-tx="${i}">${esc(c.transcript)}</div><button class="sm" data-a="exp" data-i="${i}">ver más</button>`;
h+=`<table class="wide"><tr><th>#</th><th>Tema</th><th>IN</th><th>OUT</th><th>Dur.</th><th>Hook</th><th>Transcripción literal</th><th>Prioridad</th></tr>`+R.map((r,i)=>`<tr><td>${r.n}</td><td>${esc(r.c.tema)}${r.first?' <span class="badge first">EDITAR PRIMERO</span>':''}</td><td>${esc(r.c.inn)}</td><td>${esc(r.c.out)}</td><td>${r.f.d!=null?fmt(r.f.d):'—'}${r.f.short?' ⚠':''}${r.f.long?' (largo)':''}</td><td>${r.c.hook}/10</td><td>${tx(r.c,'w'+i)}</td><td>${r.c.rank}</td></tr>`).join('')+'</table>';
h+='<div class="stack">'+R.map((r,i)=>`<div class="card"><b>${r.n}. ${esc(r.c.tema)}</b> ${r.first?'<span class="badge first">EDITAR PRIMERO</span>':''}<br>IN ${esc(r.c.inn)} · OUT ${esc(r.c.out)} · Dur. <b>${r.f.d!=null?fmt(r.f.d):'—'}</b> ${r.f.short?'<span class="badge bad">≤60s</span>':''}${r.f.long?'<span class="badge long">largo</span>':''}<br>Hook ${r.c.hook}/10 · Prioridad ${r.c.rank}${tx(r.c,'s'+i)}</div>`).join('')+'</div>';return h}
function csvTxt(l){const q=s=>'"'+String(s??'').replace(/"/g,'""')+'"';return'\ufeff'+['#,Tema,IN,OUT,Duración,Hook,Transcripción literal,Prioridad'].concat(rows(l).map(r=>[r.n,q(r.c.tema),r.c.inn,r.c.out,r.f.d!=null?fmt(r.f.d):'',r.c.hook,q(r.c.transcript),r.c.rank].join(','))).join('\r\n')}
function mdTxt(l){const m=s=>String(s).replace(/\|/g,'\\|').replace(/\n/g,' ');return'| # | Tema | IN | OUT | Duración | Hook | Transcripción literal | Prioridad |\n|---|---|---|---|---|---|---|---|\n'+rows(l).map(r=>`| ${r.n} | ${m(r.c.tema)} | ${r.c.inn} | ${r.c.out} | ${r.f.d!=null?fmt(r.f.d):''} | ${r.c.hook}/10 | ${m(r.c.transcript)} | ${r.c.rank} |`).join('\n')}
function dl(name,txt,type){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([txt],{type}));a.download=name;document.body.appendChild(a);a.click();a.remove()}
const A={extract(){const u=$('#lu').value.trim();if(!u){setMsg('Pega el link de YouTube.','err');return}doExtract(u);return 1},
importSub(){const t=form.sub.trim();if(!t){setMsg('Elige un archivo .srt/.vtt o pega el texto.','err');return}const segs=parseSubs(t);if(!segs){setMsg('No se encontraron subtítulos válidos en ese texto (¿es SRT o VTT?).','err');return}
const u=form.lu.trim();const id=ytId(u);let l=id&&S.lives.find(x=>x.videoId==id);if(!l){l={id:uid(),cands:[]};S.lives.push(l)}
Object.assign(l,{url:u,title:form.lt.trim()||l.title||('Importado '+(id||'')),channel:form.lc.trim()||l.channel||'',notes:form.ln||l.notes||'',segs:segs.map(g=>g),track:'importado .'+(/^\s*WEBVTT/.test(t)?'vtt':'srt')});if(id)l.videoId=id;S.active=l.id;form={lu:'',lt:'',lc:'',ln:'',sub:''};save();setMsg(`Subtítulos importados: ${segs.length} líneas.`,'ok');tab='texto';return},
mode(b){mode=b.dataset.m;render();return 1},
tapline(b){const l=live(),c=l.cands[tcand];const t=+b.dataset.t,e=+b.dataset.e;if(mode=='in'){c.inn=fmt(Math.floor(t));mode='out'}else{c.out=fmt(Math.max(Math.floor(e),Math.floor(t)+1));mode='in'}save();const f=info(c);msg=null;const y=window.scrollY;render();window.scrollTo(0,y);return 1},
insert(b){const l=live(),c=l.cands[+b.dataset.i];const t=literal(l,c);if(t==null||!t){setMsg('Define IN y OUT válidos (OUT mayor que IN) con transcripción disponible.','err');return}c.transcript=t;setMsg('Transcripción literal insertada (editable en Candidatos).','ok')},
addLive(){const u=$('#lu').value.trim(),t=$('#lt').value.trim();if(!u&&!t){setMsg('Pega la URL o escribe un título.','err');return}const l={id:uid(),url:u,title:t,channel:$('#lc').value.trim()||'FÓRMULA CAMPE NEWS',notes:$('#ln').value,cands:[]};S.lives.push(l);S.active=l.id;setMsg('Live guardado y activado. Agrega de 3 a 5 candidatos.','ok')},
example(){const e=window.EJEMPLO;const l={id:uid(),url:e.url,title:e.title,channel:e.channel,notes:e.notes,example:true,cands:e.cands.map((c,i)=>({...c,rank:i+1}))};l.cands.sort((a,b)=>b.hook-a.hook);l.cands.forEach((c,i)=>c.rank=i+1);S.lives.push(l);S.active=l.id;setMsg('Ejemplo cargado (datos de muestra).','ok')},
pick(b){S.active=b.dataset.id;setMsg('Live activado.','ok')},delLive(b){if(confirm('¿Borrar este live?')){S.lives=S.lives.filter(l=>l.id!=b.dataset.id);if(S.active==b.dataset.id)S.active=S.lives[0]?.id||null}},
addC(){const l=live();if(l.cands.length>=5){setMsg('Máximo 5 candidatos: no puedes agregar un 6.º.','err');return}l.cands.push({tema:'',inn:'',out:'',transcript:'',hookText:'',hook:5,rank:l.cands.length+1,title:'',caption:''})},
delC(b){live().cands.splice(+b.dataset.i,1)},sort(b){sortBy=b.dataset.s},
exp(b){const e=document.querySelector(`[data-tx="${b.dataset.i}"]`);e.classList.toggle('clamp');b.textContent=e.classList.contains('clamp')?'ver más':'ver menos';return 1},
copyMd(){const t=mdTxt(live());(navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(()=>{setMsg('Tabla copiada (Markdown).','ok');render()},()=>{window.__md=t;setMsg('No se pudo copiar automáticamente.','err');render()});window.__md=t;return 1},
csv(){dl('tabla_cortes.csv',csvTxt(live()),'text/csv;charset=utf-8');setMsg('CSV descargado.','ok')},
expJson(){dl('campe-board.json',JSON.stringify({lives:S.lives,active:S.active},null,1),'application/json');setMsg('JSON exportado.','ok')},impJson(){$('#imp').click();return 1}};
document.addEventListener('click',e=>{const n=e.target.closest('nav button');if(n){tab=n.dataset.t;if(tab=='tabla'&&live()&&live().cands.length<3&&!warnShown){}render();return}
const b=e.target.closest('[data-a]');if(!b||b.disabled)return;if(!A[b.dataset.a](b)){save();render()}});
document.addEventListener('input',e=>{const fk={lu:'lu',lt:'lt',lc:'lc',ln:'ln',subtxt:'sub'}[e.target.id];if(fk)form[fk]=e.target.value;if(e.target.id=='tq'){tq=e.target.value;$('#lines').innerHTML=lineList(live());return}if(e.target.id=='sapi'||e.target.id=='stok'){S.settings.api=$('#sapi').value.trim();S.settings.token=$('#stok').value.trim();save();return}const f=e.target.dataset.f;if(!f)return;const c=live().cands[+e.target.closest('[data-i]').dataset.i];let v=e.target.value;if(f=='hook'||f=='rank')v=+v;c[f]=v;save();if(f=='inn'||f=='out')e.target.closest('.card').querySelector('.dur').innerHTML=durHtml(c);if(f=='hook'){e.target.previousElementSibling.querySelector('b').textContent=v}});
document.addEventListener('change',e=>{if(e.target.id=='subfile'){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=()=>{form.sub=String(r.result);setMsg('Archivo cargado ('+f.name+'). Pulsa “Importar subtítulos”.','ok');render()};r.readAsText(f);return}if(e.target.id=='tc'){tcand=+e.target.value;render();return}if(e.target.id=='imp'){const r=new FileReader();r.onload=()=>{try{const d=JSON.parse(r.result);if(!Array.isArray(d.lives))throw 0;S={lives:d.lives,active:d.active||null,settings:S.settings};save();setMsg('JSON importado.','ok')}catch(x){setMsg('Archivo JSON inválido.','err')}render()};r.readAsText(e.target.files[0])}});

/* ---- Importar subtítulos (.srt/.vtt): port de server.py; no altera palabras ---- */
function tsec(x){const p=x.replace(',','.').split(':').map(Number);while(p.length<3)p.unshift(0);return p[0]*3600+p[1]*60+p[2]}
const r2=n=>Math.round(n*100)/100;
function parseVttJs(txt){const words=[];let last=null;const rolling=/<\d+:\d+:\d+\.\d+><c/.test(txt);
for(const b of txt.split(/\n\n+/)){const m=b.match(/(\d+:\d+:\d+[.,]\d+) --> (\d+:\d+:\d+[.,]\d+)/);if(!m)continue;const a=tsec(m[1]),e=tsec(m[2]);if(e-a<0.05)continue;
const lines=b.split('\n').slice(1).filter(l=>l.trim());if(!lines.length)continue;
if(rolling){const raw=lines[lines.length-1];const clean=raw.replace(/<[^>]+>/g,'').trim();if(words.length&&clean===last)continue;last=clean;let t=a;
for(const p of raw.split(/(<\d+:\d+:\d+\.\d+>)/)){const mm=p.match(/^<(\d+:\d+:\d+\.\d+)>$/);if(mm){t=tsec(mm[1]);continue}
for(const w of p.replace(/<\/?c[^>]*>/g,'').split(/\s+/).filter(Boolean))words.push([r2(t),w,e])}}
else{const clean=lines.map(l=>l.replace(/<[^>]+>/g,'').trim()).join(' ').trim();if(!clean||clean===last)continue;last=clean;const ws=clean.split(/\s+/),st=(e-a)/ws.length;ws.forEach((w,i)=>words.push([r2(a+i*st),w,e]))}}
return words}
function parseSrtJs(txt){const words=[];let prev=new Set();
for(const b of txt.replace(/\r\n/g,'\n').trim().split(/\n\s*\n/)){const ls=b.split('\n');const i=ls.findIndex(l=>l.includes('-->'));if(i<0)continue;
const m=ls[i].match(/(\d+:\d+:\d+[.,]\d+)\s*-->\s*(\d+:\d+:\d+[.,]\d+)/);if(!m)continue;const a=tsec(m[1]),e=tsec(m[2]);
const lines=ls.slice(i+1).map(l=>l.replace(/<[^>]+>/g,'').trim()).filter(Boolean);const nw=lines.filter(l=>!prev.has(l));prev=new Set(lines);if(!nw.length)continue;
const ws=nw.join(' ').split(/\s+/).filter(Boolean),dur=Math.min(Math.max(e-a,0.5),0.5*ws.length),st=dur/ws.length;ws.forEach((w,k)=>words.push([r2(a+k*st),w,e]))}
return words}
function buildSegsJs(words,gapS=8){const segs=[];let cur=[],curLast=0;
const flush=nx=>{if(!cur.length)return;let end=nx!=null?Math.min(nx,cur[cur.length-1][2]):cur[cur.length-1][2];if(end<=cur[0][0])end=cur[cur.length-1][0]+0.5;segs.push({s:cur[0][0],e:r2(end),t:cur.map(w=>w[1]).join(' ')});cur=[]};
for(const w of words){if(cur.length){const gap=w[0]-cur[cur.length-1][0]>gapS;
const brk=gap||cur.length>=10||(cur.length>=5&&/[.?!…]["”»)]?$/.test(cur[cur.length-1][1]))||(w[0]-cur[0][0]>6);
if(brk){flush(gap?null:w[0]);if(gap)segs.push({s:Math.min(curLast+1,w[0]),e:w[0],t:`[~${Math.trunc(w[0]-curLast)} s sin transcripción automática]`,g:1})}}
cur.push(w);curLast=w[0]}
flush();return segs}
function parseSubs(txt){const t=txt.replace(/^\ufeff/,'');const w=/^\s*WEBVTT/.test(t)?parseVttJs(t):parseSrtJs(t);return w.length?buildSegsJs(w):null}
const ytId=u=>{const m=String(u||'').match(/(?:youtu\.be\/|\/live\/|\/shorts\/|\/embed\/|[?&]v=)([\w-]{11})/)||String(u||'').trim().match(/^([\w-]{11})$/);return m?m[1]:null};
function api(){return(S.settings.api||'').replace(/\/+$/,'')}
async function doExtract(u){S.settings.api=($('#sapi')?.value??S.settings.api).trim();S.settings.token=($('#stok')?.value??S.settings.token).trim();save();
if(!S.settings.token){setMsg('Falta el token de acceso (abre “Servidor” abajo).','err');render();return}
const meta={t:$('#lt').value.trim(),c:$('#lc').value.trim(),n:$('#ln').value};busy='Descargando subtítulos… puede tardar hasta un minuto';render();
try{const r=await fetch(api()+'/api/transcript?url='+encodeURIComponent(u),{headers:{'X-Access-Token':S.settings.token}});let d={};try{d=await r.json()}catch(e){}
if(!r.ok){busy=null;setMsg((r.status==401?'Token incorrecto. ':'')+(d.message||('Error '+r.status))+(/^(youtube_error|descarga_fallida|upstream_)/.test(d.error||'')?' YouTube está bloqueando al servidor: usa “Importar subtítulos (.srt / .vtt)” más abajo con el archivo de subtítulos del video.':''),'err');render();return}
const v=d.video;const segs=d.segments.map(g=>({s:g.start_sec,e:g.end_sec,t:g.text,...(g.gap?{g:1}:{})}));
let l=S.lives.find(x=>x.videoId==v.id);if(!l){l={id:uid(),cands:[],notes:meta.n};S.lives.push(l)}
Object.assign(l,{videoId:v.id,url:u,title:meta.t||l.title||v.title,channel:meta.c||l.channel||v.channel,segs,track:d.track.type+' '+d.track.lang,duration:v.duration});S.active=l.id;busy=null;form={lu:'',lt:'',lc:'',ln:'',sub:''};
setMsg(`Transcripción lista: ${segs.length} líneas (${d.track.type}, ${d.track.lang}${d.cached?', en caché':''}).`+(v.is_live?' El live sigue en curso: la transcripción puede estar incompleta.':''),'ok');save();tab='texto';render()}
catch(e){busy=null;setMsg('No se pudo conectar con el servidor ('+e.message+'). Revisa la URL de la API.','err');render()}}
async function sha(s){const b=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s));return[...new Uint8Array(b)].map(x=>x.toString(16).padStart(2,'0')).join('')}
function start(){$('#gate').hidden=true;$('#app').hidden=false;render()}
const ex=document.createElement('script');ex.src='example.js';document.head.appendChild(ex);
if(!window.PASS_HASH||sessionStorage.getItem('campeOK')==window.PASS_HASH)start();else{$('#gate').hidden=false;const go=async()=>{if(await sha($('#pass').value.trim())==window.PASS_HASH){sessionStorage.setItem('campeOK',window.PASS_HASH);start()}else $('#gerr').hidden=false};$('#gbtn').onclick=go;$('#pass').onkeydown=e=>{if(e.key=='Enter')go()}}
