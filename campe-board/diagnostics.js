const base="https://gemini-video-mcp-4ev0.onrender.com";
(async()=>{
 const r=await fetch(base+"/health",{signal:AbortSignal.timeout(30000)});console.log("HEALTH",r.status,await r.text());
 const ctl=AbortSignal.timeout(175000);
 const prompt="Transcribe exclusivamente los primeros 10 segundos de audio hablado de este video en español. Devuelve exactamente WEBVTT con marcas de tiempo. Si no puedes acceder al audio responde NO_AUDIO. No supongas contenido.";
 try{
  const response=await fetch(base+"/mcp",{method:"POST",signal:ctl,headers:{"Content-Type":"application/json","Accept":"application/json, text/event-stream","MCP-Protocol-Version":"2025-03-26"},body:JSON.stringify({jsonrpc:"2.0",id:24,method:"tools/call",params:{name:"analizar_video",arguments:{url:"https://www.youtube.com/watch?v=kUdNL60QKtC",instruccion:prompt}}})});
  const body=await response.text(); console.log("GEMINI_STATUS",response.status); console.log("GEMINI_RESPONSE",body.slice(0,1800));
 }catch(e){console.log("GEMINI_ERROR",e.message)}
})();
