(() => {
"use strict";
const tg=window.Telegram?.WebApp; try{tg?.ready();tg?.expand()}catch(_){}
const q=new URLSearchParams(location.search);
let room=q.get("room")||""; const sp=tg?.initDataUnsafe?.start_param||q.get("startapp")||"";
if(!room&&sp.startsWith("game_"))room=sp.slice(5);
const initData=tg?.initData||"";
let state=null, selected=null, mySide=null, myStyle="classic", lastVersion=-1, busy=false;
let peer=null, target=0, localStream=new MediaStream(), makingOffer=false, polite=false, pendingIce=[];
const $=id=>document.getElementById(id), board=$("board");
const chessSymbols={K:"♔",Q:"♕",R:"♖",B:"♗",N:"♘",P:"♙",k:"♚",q:"♛",r:"♜",b:"♝",n:"♞",p:"♟"};

async function api(path, body=null, params={}){
  let url=path; if(body===null){const s=new URLSearchParams(params);if([...s].length)url+="?"+s}
  const h={"X-Telegram-Init-Data":initData}; if(body!==null)h["Content-Type"]="application/json";
  const r=await fetch(url,{method:body===null?"GET":"POST",headers:h,body:body?JSON.stringify(body):undefined,cache:"no-store"});
  let d; try{d=await r.json()}catch(_){throw Error("Server javobi noto‘g‘ri.")}; if(!d.ok)throw Error(d.error||"Xatolik");return d.data;
}
function status(t){$("turnBadge").textContent=t}
function escape(s){return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
async function boot(){
 if(!initData){status("Telegram ichidan oching");return}
 if(!room){status("Xona topilmadi");return}
 try{state=await api("/api/game/join",null,{room}); $("gameTitle").textContent=state.game==="chess"?"♟ Shaxmat":"⚪ Rus shashkasi"; renderLobby(); render();
 setInterval(poll,650); setInterval(pollSignals,220);
 }catch(e){status("❌ "+e.message)}
}
function renderLobby(){
 const ps=state.players||[]; $("lobbyText").textContent=ps.length<2?"Do‘stingiz ham shu havola orqali kirishini kuting.":"Ikkalangiz rang va ko‘rinishni tanlang.";
 $("players").innerHTML=ps.map(p=>`<span>● ${p.id===state.me?"Siz":"Do‘st"} ${p.side==="w"?"⚪ Oq":p.side==="b"?"⚫ Qora":"— tanlamagan"}</span>`).join("");
 document.querySelectorAll(".side").forEach(b=>b.classList.toggle("selected",b.dataset.side===mySide));
 document.querySelectorAll(".style").forEach(b=>b.classList.toggle("selected",b.dataset.style===myStyle));
 $("readyBtn").disabled=!mySide;
}
document.querySelectorAll(".side").forEach(b=>b.onclick=()=>{mySide=b.dataset.side;renderLobby()});
document.querySelectorAll(".style").forEach(b=>b.onclick=()=>{myStyle=b.dataset.style;renderLobby()});
$("readyBtn").onclick=async()=>{try{state=await api("/api/game/choose",null,{room,side:mySide,style:myStyle});renderLobby();render()}catch(e){tg?.showAlert?.(e.message)}};

function render(){
 if(!state)return;
 if(state.status==="lobby"){$("lobby").classList.remove("hidden");$("game").classList.add("hidden");return}
 $("lobby").classList.add("hidden");$("game").classList.remove("hidden");
 mySide=(state.players.find(p=>p.id===state.me)||{}).side||mySide; myStyle=(state.players.find(p=>p.id===state.me)||{}).style||myStyle;
 const me=state.players.find(p=>p.id===state.me),op=state.players.find(p=>p.id!==state.me);
 $("playerMe").innerHTML=`${me?.side==="w"?"⚪":"⚫"} Siz`; $("playerOpp").innerHTML=`${op?(op.side==="w"?"⚪":"⚫")+" Do‘st":"Do‘st kutilyapti…"}`; $("playerOpp").className="playercard right";
 status(state.status==="finished"?"🏁 Tugadi":state.turn===mySide?"🟢 Sizning yurishingiz":"🕐 Do‘stingiz yurmoqda");
 drawBoard();
 if(state.status==="finished"){const text=state.winner?`🏆 ${state.winner===mySide?"Siz yutdingiz!":"Do‘stingiz yutdi!"}`:"🤝 Durang";$("result").textContent=text+"  "+(state.reason||"");$("result").classList.remove("hidden")}
 else $("result").classList.add("hidden");
}
function orient(r,c){return mySide==="b"?[7-r,7-c]:[r,c]}
function drawBoard(){
 board.innerHTML=""; const b=state.board;
 for(let vr=0;vr<8;vr++)for(let vc=0;vc<8;vc++){
   const [r,c]=orient(vr,vc), sq=document.createElement("button");sq.className="sq "+(((r+c)%2)?"dark":"light");
   sq.dataset.r=r;sq.dataset.c=c;
   if(selected&&selected[0]===r&&selected[1]===c)sq.classList.add("selected");
   if(selected&&isLegalTarget(r,c))sq.classList.add(b[r][c]?"capture":"legal");
   const p=b[r][c];
   if(p){const d=document.createElement("span");if(state.game==="chess"){d.className="piece "+(p===p.toUpperCase()?"white ":"black ")+(state.players.find(x=>x.id===state.me)?.style||"classic");d.textContent=chessSymbols[p]||p}
   else{d.className="checker "+(p.toLowerCase()==="b"?"b ":"")+(p===p.toUpperCase()?"king ":"");}sq.appendChild(d)}
   sq.onclick=()=>clickSquare(r,c);board.appendChild(sq)
 }
}
function isLegalTarget(r,c){if(!selected)return false; if(state.game==="chess")return chessTargets(selected[0],selected[1]).some(x=>x[0]===r&&x[1]===c);return checkerTargets(selected[0],selected[1]).some(x=>x[0]===r&&x[1]===c)}
// Client-side hints only; server remains authoritative.
function chessTargets(r,c){
 const p=state.board[r][c];if(!p||p.toUpperCase()!==p&&mySide==="w"||p.toUpperCase()===p&&mySide==="b")return [];
 const out=[];const own=p===p.toUpperCase(), opp=x=>x&&x.toUpperCase()!==own;
 const add=(rr,cc)=>{if(rr<0||rr>7||cc<0||cc>7)return false;if(!state.board[rr][cc]){out.push([rr,cc]);return true}if(opp(state.board[rr][cc]))out.push([rr,cc]);return false};
 if(p.toLowerCase()==="p"){const d=own?-1:1;if(!state.board[r+d]?.[c]){out.push([r+d,c]);const st=own?6:1;if(r===st&&!state.board[r+2*d][c])out.push([r+2*d,c])}for(const dc of[-1,1]){if(opp(state.board[r+d]?.[c+dc]))out.push([r+d,c+dc])}}
 if(p.toLowerCase()==="n")for(const [dr,dc]of[[2,1],[2,-1],[-2,1],[-2,-1],[1,2],[1,-2],[-1,2],[-1,-2]])add(r+dr,c+dc);
 if("brq".includes(p.toLowerCase())){let ds=[];if("bq".includes(p.toLowerCase()))ds.push(...[[-1,-1],[-1,1],[1,-1],[1,1]]);if("rq".includes(p.toLowerCase()))ds.push(...[[-1,0],[1,0],[0,-1],[0,1]]);for(const[dr,dc]of ds){let rr=r+dr,cc=c+dc;while(add(rr,cc)){rr+=dr;cc+=dc}}}
 if(p.toLowerCase()==="k")for(const[dr,dc]of[[-1,-1],[-1,0],[-1,1],[0,-1],[0,1],[1,-1],[1,0],[1,1]])add(r+dr,c+dc);
 return out.filter(x=>x[0]>=0&&x[0]<8&&x[1]>=0&&x[1]<8)
}
function checkerTargets(r,c){
 const p=state.board[r][c];if(!p||p.toLowerCase()!==mySide)return[];const caps=[];const dirs=[[-1,-1],[-1,1],[1,-1],[1,1]];
 if(p===p.toUpperCase()){for(const[d1,d2]of dirs){let rr=r+d1,cc=c+d2,seen=false;while(rr>=0&&rr<8&&cc>=0&&cc<8){let q=state.board[rr][cc];if(!q){if(seen)caps.push([rr,cc])}else{if(q.toLowerCase()===mySide||seen)break;seen=true}rr+=d1;cc+=d2}}}
 else for(const[dr,dc]of dirs){let q=state.board[r+dr]?.[c+dc];if(q&&q.toLowerCase()!==mySide&&state.board[r+2*dr]?.[c+2*dc]===null)caps.push([r+2*dr,c+2*dc])}
 if(caps.length)return caps;
 if(p===p.toUpperCase()){const out=[];for(const[dr,dc]of dirs){let rr=r+dr,cc=c+dc;while(rr>=0&&rr<8&&cc>=0&&cc<8&&!state.board[rr][cc]){out.push([rr,cc]);rr+=dr;cc+=dc}}return out}
 const d=mySide==="w"?-1:1;return[-1,1].map(dc=>[r+d,c+dc]).filter(x=>x[0]>=0&&x[0]<8&&x[1]>=0&&x[1]<8&&!state.board[x[0]][x[1]])
}
function clickSquare(r,c){
 if(state.status!=="playing"||state.turn!==mySide)return;
 const p=state.board[r][c];
 if(selected&&isLegalTarget(r,c)){sendMove(selected,[r,c]);selected=null;drawBoard();return}
 if(p&&p.toLowerCase()===mySide){selected=[r,c];drawBoard()}else{selected=null;drawBoard()}
}
async function sendMove(fr,to){
 if(busy)return;busy=true;
 try{
  const old=state; state=await api("/api/game/move",{room,from:fr,to,promotion:"q"});render();
  if(old?.last_move&&state.last_move?.capture)fx("capture",to);else fx("move",to);
  tone(state.last_move?.capture?180:420,0.07);
 }catch(e){tg?.showAlert?.(e.message)}finally{busy=false}
}
async function poll(){try{const d=await api("/api/game/state",null,{room});if(lastVersion<0||d.version>lastVersion){const was=state?.version;state=d;lastVersion=d.version;render();if(was!==undefined&&d.last_move&&d.last_move.to)fx(d.last_move.captured?"capture":"move",d.last_move.to);ensurePeer()}}catch(e){console.warn(e)}}
function fx(kind,pos){const [vr,vc]=orient(pos[0],pos[1]),el=board.children[vr*8+vc];if(!el)return;const rect=el.getBoundingClientRect(),wrap=board.getBoundingClientRect();for(let i=0;i<(kind==="capture"?20:7);i++){const x=document.createElement("i");x.className=kind==="capture"?(i%2?"ember":"shard"):"particle";x.style.left=(rect.left-wrap.left+rect.width/2)+"px";x.style.top=(rect.top-wrap.top+rect.height/2)+"px";x.style.setProperty("--x",((Math.random()-.5)*150)+"px");x.style.setProperty("--y",((Math.random()-.65)*150)+"px");$("fx").appendChild(x);setTimeout(()=>x.remove(),900)}}
function tone(freq,dur){if(!$("soundBtn").dataset.on)return;try{const a=new AudioContext(),o=a.createOscillator(),g=a.createGain();o.frequency.value=freq;o.type="sine";g.gain.value=.035;o.connect(g);g.connect(a.destination);o.start();g.gain.exponentialRampToValueAtTime(.001,a.currentTime+dur);o.stop(a.currentTime+dur)}catch(_){}}
$("soundBtn").dataset.on="1";$("soundBtn").onclick=()=>{const on=$("soundBtn").dataset.on==="1";$("soundBtn").dataset.on=on?"":"1";$("soundBtn").textContent=on?"🔇 Ovoz":"🔊 Ovoz"};

// WebRTC video/audio
function rtcConfig(){return{iceServers:[{urls:"stun:stun.l.google.com:19302"},{urls:"stun:stun.cloudflare.com:3478"},...(window.GAME_TURN_URL&&window.GAME_TURN_USERNAME&&window.GAME_TURN_CREDENTIAL?[{urls:window.GAME_TURN_URL,username:window.GAME_TURN_USERNAME,credential:window.GAME_TURN_CREDENTIAL}]:[])]}}
function ensurePeer(){if(!state||state.players.length!==2)return;const op=state.players.find(p=>p.id!==state.me);if(!op)return;if(peer&&target===op.id)return;target=op.id;peer=new RTCPeerConnection(rtcConfig());polite=Number(state.me)>Number(op.id);for(const t of localStream.getTracks())peer.addTrack(t,localStream);peer.ontrack=e=>{$("remoteVideo").srcObject=e.streams[0]||new MediaStream([e.track]);$("remoteVideo").play().catch(()=>{})};peer.onicecandidate=e=>{if(e.candidate)sendSignal({type:"ice",candidate:e.candidate.toJSON?.()||e.candidate})};peer.onnegotiationneeded=async()=>{try{if(peer.signalingState!=="stable"||makingOffer)return;makingOffer=true;await peer.setLocalDescription(await peer.createOffer());await sendSignal({type:"offer",sdp:peer.localDescription.sdp})}finally{makingOffer=false}}}
async function sendSignal(payload){try{await api("/api/game/signal",{room,target_user_id:target,payload})}catch(e){}}
async function pollSignals(){try{const list=await api("/api/game/signals",null,{room});for(const x of list||[])await handleSignal(x)}catch(e){}}
async function handleSignal(x){const p=x.payload||{};ensurePeer();if(!peer)return;try{if(p.type==="offer"){const collision=makingOffer||peer.signalingState!=="stable";if(collision&&!polite)return;await peer.setRemoteDescription({type:"offer",sdp:p.sdp});for(const c of pendingIce)await peer.addIceCandidate(c);pendingIce=[];await peer.setLocalDescription(await peer.createAnswer());await sendSignal({type:"answer",sdp:peer.localDescription.sdp})}else if(p.type==="answer"){if(peer.signalingState==="have-local-offer")await peer.setRemoteDescription({type:"answer",sdp:p.sdp})}else if(p.type==="ice"){if(peer.remoteDescription)await peer.addIceCandidate(p.candidate);else pendingIce.push(p.candidate)}}catch(e){console.warn(e)}}
async function media(kind){try{const stream=await navigator.mediaDevices.getUserMedia(kind==="video"?{video:{facingMode:"user",width:{ideal:720},height:{ideal:720}},audio:false}:{audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});const track=stream.getTracks()[0];localStream.addTrack(track);ensurePeer();const sender=peer?.getSenders().find(s=>s.track?.kind===kind);if(sender)await sender.replaceTrack(track);else if(peer)peer.addTrack(track,localStream);if(kind==="video"){$("localVideo").srcObject=localStream;$("callPanel").classList.remove("hidden");$("callHint").textContent="Kamera yoqildi — o‘yin davom etadi."}else{$("callPanel").classList.remove("hidden");$("callHint").textContent="Mikrofon yoqildi — ovozli suhbat ishlayapti."}}catch(e){tg?.showAlert?.("Ruxsat berilmadi yoki qurilma topilmadi: "+e.message)}}
$("callBtn").onclick=()=>media("video");$("micBtn").onclick=()=>media("audio");$("closeCall").onclick=()=>$("callPanel").classList.add("hidden");
$("resignBtn").onclick=async()=>{if(confirm("Taslim bo‘lasizmi?")){try{state=await api("/api/game/resign",{room});render()}catch(e){tg?.showAlert?.(e.message)}}};
boot();
})();