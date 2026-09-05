(() => {
const tg = window.Telegram?.WebApp;
tg?.ready(); tg?.expand();
const qs = new URLSearchParams(location.search);
let room = qs.get("room") || ""; const startParam = tg?.initDataUnsafe?.start_param || qs.get("startapp") || ""; if(!room && startParam.startsWith("room_")) room=startParam.slice(5);
const movieId = qs.get("movie") || "";
let initData = tg?.initData || "";
let me = 0, participants = [], lastChat = "", peer = null, localStream = null, shareUrl = "";
let pendingIce = [], signalingBusy = false, lastVersion = -1;

const $=id=>document.getElementById(id);
const video=$("video"), remoteVideo=$("remoteVideo"), remoteWrap=$("remoteVideoWrap");

async function api(path, body=null, query={}) {
  let url=path;
  if(body===null){ const p=new URLSearchParams(query); url += Object.keys(query).length ? "?"+p.toString() : ""; }
  const headers={"X-Telegram-Init-Data":initData};
  if(body!==null) headers["Content-Type"]="application/json";
  const r=await fetch(url,{method:body===null?"GET":"POST",headers,body:body?JSON.stringify(body):undefined});
  const d=await r.json(); if(!d.ok) throw Error(d.error||"Server xatosi"); return d.data;
}

async function boot(){
  if(!initData){$("waiting").textContent="❌ Telegram Mini App sessiyasi topilmadi.";return}
  try{
    if(!room){
      const d=await api("/api/kino/create",{movie:movieId}); room=d.room; history.replaceState(null,"",`?movie=${encodeURIComponent(movieId)}&room=${room}`);
    }
    const d=await api("/api/kino/join",null,{room});
    me=d.user_id; participants=d.participants; shareUrl=d.share_url||location.href;
    $("title").textContent="🎬 "+d.movie.title;
    $("roomInfo").textContent=`2 kishilik xona • ${room.slice(0,6)}`;
    video.src=d.stream_path;
    $("waiting").textContent=participants.length>=2?"":"👥 Do‘stingizni kuting...";
    renderPeople(); ensurePeer();
    setInterval(poll,900); setInterval(pollChat,1000); setInterval(pollSignals,500);
  }catch(e){$("waiting").textContent="❌ "+e.message}
}
function renderPeople(){ $("people").innerHTML=participants.map((p,i)=>`<div class="person">🟢 ${p===me?"Siz":"Do‘st"}</div>`).join(""); }

async function syncState(force=false){
  const d=await api("/api/kino/state",null,{room});
  participants=d.participants||participants; renderPeople();
  const desired=d.playing ? d.position+(Date.now()/1000-d.updated_at) : d.position;
  if(force || d.version!==lastVersion){
    lastVersion=d.version;
    if(Math.abs((video.currentTime||0)-desired)>1.2 && Number.isFinite(desired)) { try{video.currentTime=desired}catch{} }
    if(d.playing){ if(video.paused) video.play().catch(()=>{}) } else if(!video.paused) video.pause();
  }
}
async function poll(){try{await syncState(false); if(participants.length===2) ensurePeer()}catch{}}
let sendTimer;
function pushState(playing){ clearTimeout(sendTimer); sendTimer=setTimeout(()=>api("/api/kino/state",{room,playing,position:video.currentTime||0}).catch(()=>{}),80); }
video.addEventListener("play",()=>pushState(true)); video.addEventListener("pause",()=>pushState(false)); video.addEventListener("seeked",()=>pushState(!video.paused));

async function pollChat(){
 try{const items=await api("/api/kino/chat",null,{room,after:lastChat}); for(const m of items){addMsg(m);lastChat=m.id}}catch{}
}
function addMsg(m){const d=document.createElement("div");d.className="msg"+(m.user_id===me?" me":"");d.innerHTML=`<b>${escapeHtml(m.name)}</b>${escapeHtml(m.text)}`;$("messages").appendChild(d);$("messages").scrollTop=$("messages").scrollHeight}
$("chatForm").addEventListener("submit",async e=>{e.preventDefault();const t=$("chatInput").value.trim();if(!t)return;try{await api("/api/kino/chat",{room,text:t});$("chatInput").value="";await pollChat()}catch(e){alert(e.message)}});

function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}

async function sendSignal(target,payload){try{await api("/api/kino/signal",{room,target_user_id:target,payload})}catch{}}
async function pollSignals(){
 try{
  const list=await api("/api/kino/signals",null,{room});
  for(const item of list){const p=item.payload||{}; if(p.type==="offer") await handleOffer(item.from,p.sdp); else if(p.type==="answer") await handleAnswer(p.sdp); else if(p.type==="ice") await handleIce(p.candidate)}
 }catch{}
}
function rtcConfig(){const ice=[{urls:"stun:stun.l.google.com:19302"},{urls:"stun:stun.cloudflare.com:3478"}];return {iceServers:ice}}
function makePeer(target){
 if(peer) return peer;
 peer=new RTCPeerConnection(rtcConfig());
 // Audio/video transceiverlar boshidan yaratiladi. Shuning uchun foydalanuvchi
 // kamera/mikrofonni keyinroq yoqsa ham qayta negotiation shart emas.
 peer.addTransceiver("audio",{direction:"sendrecv"});
 peer.addTransceiver("video",{direction:"sendrecv"});
 if(localStream) localStream.getTracks().forEach(track=>{
   const sender=peer.getTransceivers().find(t=>t.receiver.track.kind===track.kind)?.sender;
   if(sender) sender.replaceTrack(track.enabled?track:null).catch(()=>{});
 });
 peer.ontrack=e=>{remoteWrap.classList.remove("hidden");remoteVideo.srcObject=e.streams[0]};
 peer.onicecandidate=e=>{if(e.candidate)sendSignal(target,{type:"ice",candidate:e.candidate})};
 return peer;
}
async function ensurePeer(){
 if(participants.length!==2 || !me) return;
 const target=participants.find(x=>x!==me); if(!target)return;
 const pc=makePeer(target);
 // Deterministic initiator: smaller Telegram user id creates offer.
 if(me<target && !pc.localDescription && !signalingBusy){
   signalingBusy=true;
   try{const offer=await pc.createOffer();await pc.setLocalDescription(offer);await sendSignal(target,{type:"offer",sdp:offer.sdp})}catch{} finally{signalingBusy=false}
 }
}
async function handleOffer(from,sdp){
 const pc=makePeer(from); await pc.setRemoteDescription({type:"offer",sdp});
 for(const c of pendingIce){try{await pc.addIceCandidate(c)}catch{}} pendingIce=[];
 const answer=await pc.createAnswer();await pc.setLocalDescription(answer);await sendSignal(from,{type:"answer",sdp:answer.sdp});
}
async function handleAnswer(sdp){if(peer && !peer.currentRemoteDescription) await peer.setRemoteDescription({type:"answer",sdp})}
async function handleIce(c){if(!peer||!peer.remoteDescription){pendingIce.push(c);return}try{await peer.addIceCandidate(c)}catch{}}

async function toggleMedia(kind){
 try{
  if(!localStream) localStream=new MediaStream();
  let track=localStream.getTracks().find(t=>t.kind===kind);
  if(!track){
    const stream=await navigator.mediaDevices.getUserMedia(kind==="audio"?{audio:true,video:false}:{audio:false,video:true});
    track=stream.getTracks()[0]; localStream.addTrack(track);
  }
  const enabled=!track.enabled; track.enabled=enabled;
  const target=participants.find(x=>x!==me);
  if(!peer && participants.length===2) makePeer(target);
  if(peer){
    const sender=peer.getSenders().find(s=>s.track?.kind===kind) || peer.getTransceivers().find(t=>t.receiver.track.kind===kind)?.sender;
    if(sender) await sender.replaceTrack(enabled?track:null);
  }
  if(kind==="audio")$("mic").textContent=enabled?"🎤 Mikrofon":"🔇 Mikrofon";
  else {$("cam").textContent=enabled?"📹 Kamera":"📵 Kamera"; if(enabled){remoteWrap.classList.remove("hidden");}}
  if(participants.length===2) await ensurePeer();
 }catch(e){alert("Kamera/mikrofon ruxsati berilmadi: "+e.message)}
}
$("mic").onclick=()=>toggleMedia("audio"); $("cam").onclick=()=>toggleMedia("video");
$("share").onclick=async()=>{const url=shareUrl||location.href;try{await navigator.clipboard.writeText(url);tg?.showAlert("Kino xonasi havolasi nusxalandi.")}catch{prompt("Xona havolasi:",url)}};
boot();
})();