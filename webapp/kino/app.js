(() => {
  "use strict";

  const tg = window.Telegram?.WebApp;
  try { tg?.ready(); tg?.expand(); } catch (_) {}

  const qs = new URLSearchParams(location.search);
  let room = qs.get("room") || "";
  const startParam = tg?.initDataUnsafe?.start_param || qs.get("startapp") || "";
  if (!room && startParam.startsWith("room_")) room = startParam.slice(5);
  const movieId = qs.get("movie") || "";
  const initData = tg?.initData || "";

  let me = 0;
  let participants = [];
  let lastChat = "";
  let shareUrl = "";
  let peer = null;
  let peerTarget = 0;
  let localStream = new MediaStream();
  let pendingIce = [];
  let chatPolling = false;
  let signalPolling = false;
  let statePolling = false;
  let chatSendBusy = false;
  let lastVersion = -1;
  let makingOffer = false;
  let ignoreOffer = false;
  let suppressVideoEvents = false;
  let connectionLost = false;
  let clockOffsetMs = 0;
  let stateReady = false;
  const mediaPermissionKey = "student_ai_media_permission_v2";
  const emojiList = ["😀","😂","😍","🥰","😎","😢","😡","😮","👏","🔥","❤️","💯","👍","👎","🎉","🏆","⚡","🤝","😄","🤣","😉","😘","🤗","🙏","💪","🙌","✨","🎯","🎮","😭"];

  const $ = (id) => document.getElementById(id);
  const video = $("video");
  const remoteVideo = $("remoteVideo");
  const remoteWrap = $("remoteVideoWrap");
  const localVideo = $("localVideo");
  const localWrap = $("localVideoWrap");
  const playOverlay = $("playOverlay");

  async function api(path, body = null, query = {}) {
    let url = path;
    if (body === null) {
      const p = new URLSearchParams(query);
      if (Object.keys(query).length) url += "?" + p.toString();
    }
    const headers = { "X-Telegram-Init-Data": initData };
    if (body !== null) headers["Content-Type"] = "application/json";
    const r = await fetch(url, {
      method: body === null ? "GET" : "POST",
      headers,
      body: body !== null ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
    const d = await r.json();
    if (!d.ok) throw Error(d.error || "Server xatosi");
    return d.data;
  }

  function setStatus(text) {
    const el = $("waiting");
    el.textContent = text || "";
    el.classList.toggle("hidden", !text);
  }

  async function boot() {
    if (!initData) {
      setStatus("❌ Telegram Mini App sessiyasi topilmadi. Telegram ichidan qayta oching.");
      return;
    }
    try {
      if (!room) {
        const d = await api("/api/kino/create", { movie: movieId });
        room = d.room;
        history.replaceState(null, "", `?movie=${encodeURIComponent(movieId)}&room=${encodeURIComponent(room)}`);
      }

      const d = await api("/api/kino/join", null, { room });
      me = d.user_id;
      participants = d.participants || [];
      shareUrl = d.share_url || location.href;
      $("title").textContent = "🎬 " + d.movie.title;
      $("roomInfo").textContent = `2 kishilik xona • ${room.slice(0, 6)}`;
      video.src = d.stream_path;
      video.load();
      setStatus("⏳ Kino yuklanmoqda...");
      playOverlay.classList.remove("hidden");
      renderPeople();
      ensurePeer();

      // Har bir timer alohida guard bilan ishlaydi: sekin tarmoqda bir xil
      // polling funksiyasi ustma-ust ishlamaydi.
      setInterval(pollState, 900);
      setInterval(pollChat, 700);
      setInterval(pollSignals, 250);
      await pollChat();
      await pollSignals();
      await pollState();
    } catch (e) {
      console.error("KINO boot", e);
      setStatus("❌ " + e.message);
    }
  }

  function renderPeople() {
    $("people").innerHTML = participants
      .map((p) => `<div class="person">🟢 ${p === me ? "Siz" : "Do‘st"}</div>`)
      .join("");
  }

  async function pollState(forceSync = false) {
    if (statePolling || !room) return;
    statePolling = true;
    try {
      const requestStarted = Date.now();
      const d = await api("/api/kino/state", null, { room });
      const requestFinished = Date.now();
      connectionLost = false;

      // The server clock is authoritative. The old implementation compared
      // server epoch time directly with the browser clock, which can differ
      // and makes the player jump backwards/forwards on every poll.
      if (Number.isFinite(Number(d.server_now))) {
        const midpoint = requestStarted + (requestFinished - requestStarted) / 2;
        clockOffsetMs = Number(d.server_now) * 1000 - midpoint;
      }

      participants = d.participants || participants;
      renderPeople();
      if (participants.length === 2) ensurePeer();

      const version = Number(d.version ?? -1);
      const isNewState = !stateReady || version > lastVersion;
      const remoteEvent = stateReady && Number(d.actor_id) !== Number(me);
      const shouldSync = isNewState;

      if (shouldSync) {
        lastVersion = Math.max(lastVersion, version);
        stateReady = true;

        // Reconnection is only a trigger to fetch state. If the room version
        // did not change, never touch the local player. This is essential for
        // offline playback: coming back online must not rewind buffered video.
        if (!isNewState) return;

        // Do not apply our own state echo back onto the video. We already have
        // the exact local position; applying the server echo is what caused
        // the repeating/re-winding behaviour during playback.
        if (!remoteEvent && stateReady) return;

        const nowServerMs = Date.now() + clockOffsetMs;
        let desired = Number(d.position) || 0;
        if (d.playing && Number.isFinite(Number(d.updated_at))) {
          desired += Math.max(0, (nowServerMs / 1000) - Number(d.updated_at));
        }

        suppressVideoEvents = true;
        try {
          const local = Number(video.currentTime || 0);
          const drift = Math.abs(local - desired);
          const remotePaused = !d.playing;

          if (remotePaused) {
            // A remote pause is authoritative. Even if we were offline and
            // continued playing buffered data, reconnecting must land exactly
            // at the position where the other participant paused.
            if (Number.isFinite(desired) && drift > 0.15) {
              video.currentTime = Math.max(0, desired);
            }
            if (!video.paused) video.pause();
          } else {
            // For normal playback never continuously seek on every poll.
            // Correct only meaningful drift; this removes the periodic jump.
            if (Number.isFinite(desired) && drift > 1.75) {
              video.currentTime = Math.max(0, desired);
            }
            if (video.paused) await video.play().catch(() => {});
          }
        } finally {
          setTimeout(() => { suppressVideoEvents = false; }, 0);
        }
      }
    } catch (_) {
      // IMPORTANT: when connectivity disappears, leave the video completely
      // alone. The browser continues with whatever has already been buffered.
      // On the next successful request, a newer remote version is reconciled.
      connectionLost = true;
    } finally {
      statePolling = false;
    }
  }

  window.addEventListener("offline", () => {
    connectionLost = true;
    setStatus("📡 Internet uzildi — video mavjud buffer bilan davom etadi.");
    setTimeout(() => { if (connectionLost) setStatus(""); }, 2500);
  });

  window.addEventListener("online", async () => {
    connectionLost = false;
    setStatus("📡 Internet qaytdi — xona holati sinxronlanmoqda...");
    // A successful reconnect must immediately reconcile the authoritative
    // room state (including a pause/seek made by the other participant).
    await pollState(true);
    setStatus("");
  });

  video.addEventListener("error", () => {
    const e = video.error;
    console.error("KINO video error", e?.code, e?.message);
    setStatus("❌ Kino ochilmadi. Fayl MP4 (H.264/AAC) bo‘lishi kerak.");
  });
  video.addEventListener("loadedmetadata", () => {
    setStatus("");
  });
  video.addEventListener("canplay", () => {
    setStatus("");
  });
  playOverlay.onclick = async () => {
    try { await video.play(); } catch (e) { setStatus("❌ Video ishga tushmadi: " + e.message); }
  };
  video.addEventListener("pause", () => {
    if (video.currentTime < 0.2 && video.readyState >= 2) playOverlay.classList.remove("hidden");
  });
  video.addEventListener("play", () => playOverlay.classList.add("hidden"));
  let stateSendTimer = null;
  function pushState(playing) {
    if (suppressVideoEvents) return;
    clearTimeout(stateSendTimer);
    stateSendTimer = setTimeout(async () => {
      try {
        const d = await api("/api/kino/state", {
          room,
          playing: !!playing,
          position: Number(video.currentTime || 0),
        });
        connectionLost = false;
        if (d && Number.isFinite(Number(d.version))) {
          lastVersion = Math.max(lastVersion, Number(d.version));
          stateReady = true;
        }
      } catch (_) {
        // Keep local playback untouched while offline.
        connectionLost = true;
      }
    }, 120);
  }
  video.addEventListener("play", () => pushState(true));
  video.addEventListener("pause", () => pushState(false));
  video.addEventListener("seeked", () => pushState(!video.paused));

  async function pollChat() {
    if (chatPolling || !room) return;
    chatPolling = true;
    try {
      const items = await api("/api/kino/chat", null, { room, after: lastChat });
      for (const m of items || []) {
        // Cursor faqat render muvaffaqiyatli bo‘lgandan keyin siljiydi.
        addMsg(m);
        lastChat = m.id;
      }
    } catch (_) {
      // Keyingi poll aynan o‘sha cursor bilan qayta urinadi.
    } finally {
      chatPolling = false;
    }
  }

  function emojiFromText(text){const m=String(text).match(/[\p{Extended_Pictographic}]/u);return m?.[0]||"";}
  function emojiEffect(emoji,el){if(!emoji)return;for(let i=0;i<5;i++){const x=document.createElement("span");x.className="emoji-burst";x.textContent=emoji;const r=el?.getBoundingClientRect?.();x.style.left=((r?.left||innerWidth/2)+(r?.width||0)/2)+"px";x.style.top=(r?.top||innerHeight-60)+"px";x.style.setProperty("--x",((Math.random()-.5)*100)+"px");x.style.setProperty("--y",((Math.random()-.5)*50)+"px");x.style.setProperty("--r",((Math.random()-.5)*40)+"deg");document.body.appendChild(x);setTimeout(()=>x.remove(),900);}try{tg?.HapticFeedback?.impactOccurred?.("light");}catch(_){}}
  function addMsg(m) {
    const box = $("messages");
    if (box.querySelector(`[data-message-id="${CSS.escape(String(m.id))}"]`)) return;
    const d = document.createElement("div"); d.className="msg"+(Number(m.user_id)===Number(me)?" me":""); d.dataset.messageId=String(m.id);
    d.innerHTML=`<b>${escapeHtml(m.name)}</b><span>${escapeHtml(m.text)}</span>`; box.appendChild(d); box.scrollTop=box.scrollHeight;
    const em=emojiFromText(m.text); if(em)emojiEffect(em,d);
  }

  const picker=$("emojiPicker");
  picker.innerHTML=emojiList.map(e=>`<button type="button" data-emoji="${e}">${e}</button>`).join("");
  $("emojiBtn").onclick=()=>picker.classList.toggle("hidden");
  picker.addEventListener("click",e=>{const b=e.target.closest("button");if(!b)return;const input=$("chatInput");input.value+=b.dataset.emoji;input.focus();emojiEffect(b.dataset.emoji,b);picker.classList.add("hidden");});
  document.addEventListener("click",e=>{if(!picker.contains(e.target)&&e.target!==$("emojiBtn"))picker.classList.add("hidden");});

  $("chatForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (chatSendBusy) return;
    const input = $("chatInput");
    const text = input.value.trim();
    if (!text) return;
    chatSendBusy = true;
    const clientId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}_${Math.random()}`;
    try {
      const item = await api("/api/kino/chat", { room, text, client_id: clientId });
      input.value = "";
      if (item) {
        addMsg(item);
        lastChat = item.id;
      }
    } catch (e2) {
      alert(e2.message);
    } finally {
      chatSendBusy = false;
    }
  });

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  async function sendSignal(target, payload) {
    try {
      await api("/api/kino/signal", { room, target_user_id: target, payload });
    } catch (e) {
      console.warn("KINO signal yuborilmadi", e);
    }
  }

  async function pollSignals() {
    if (signalPolling || !room) return;
    signalPolling = true;
    try {
      const list = await api("/api/kino/signals", null, { room });
      for (const item of list || []) {
        await handleSignal(item);
      }
    } catch (_) {
      // Keyingi poll qayta urinadi.
    } finally {
      signalPolling = false;
    }
  }

  async function handleSignal(item) {
    const p = item.payload || {};
    if (!p.type) return;
    try {
      if (p.type === "offer") await handleOffer(item.from, p.sdp);
      else if (p.type === "answer") await handleAnswer(p.sdp);
      else if (p.type === "ice" && p.candidate) await handleIce(p.candidate);
    } catch (e) {
      console.warn("KINO WebRTC signal xatosi", p.type, e);
    }
  }

  function rtcConfig() {
    const ice = [
      { urls: "stun:stun.l.google.com:19302" },
      { urls: "stun:stun.cloudflare.com:3478" },
    ];
    // TURN server berilgan bo‘lsa, mobil/operator NAT holatlarida ham
    // P2P ulanishni relay orqali tiklash imkoniyati paydo bo‘ladi.
    const turnUrl = window.KINO_TURN_URL || "";
    const turnUser = window.KINO_TURN_USERNAME || "";
    const turnCred = window.KINO_TURN_CREDENTIAL || "";
    if (turnUrl && turnUser && turnCred) {
      ice.push({ urls: turnUrl, username: turnUser, credential: turnCred });
    }
    return { iceServers: ice, bundlePolicy: "max-bundle", rtcpMuxPolicy: "require" };
  }

  function makePeer(target) {
    if (peer && peerTarget === target) return peer;
    if (peer) {
      try { peer.close(); } catch (_) {}
    }
    peerTarget = Number(target);
    const polite = Number(me) > Number(target);
    peer = new RTCPeerConnection(rtcConfig());

    // Foydalanuvchi kamera/mikrofonni keyin yoqqanda ham track qo‘shiladi;
    // shu sababli transceiverlarni oldindan majburan yaratmaymiz.
    for (const track of localStream.getTracks()) {
      try { peer.addTrack(track, localStream); } catch (_) {}
    }

    peer.ontrack = (event) => {
      let stream = event.streams && event.streams[0];
      if (!stream) {
        if (!remoteVideo.srcObject) remoteVideo.srcObject = new MediaStream();
        stream = remoteVideo.srcObject;
        if (!stream.getTracks().some(t => t.id === event.track.id)) stream.addTrack(event.track);
      }
      remoteVideo.srcObject = stream;
      remoteWrap.classList.remove("hidden");
      remoteVideo.play().catch(() => {});
    };

    peer.onicecandidate = (event) => {
      if (event.candidate) sendSignal(target, {
        type: "ice",
        candidate: event.candidate.toJSON ? event.candidate.toJSON() : event.candidate,
      });
    };

    peer.onconnectionstatechange = () => {
      const s = peer?.connectionState;
      if (s === "connected") {
        remoteWrap.classList.remove("hidden");
        if (participants.length >= 2) setStatus("");
      } else if (s === "failed") {
        // ICE failed bo‘lsa RTCPeerConnection'ni yangilab, yana negotiation
        // boshlashga imkon beramiz. TURN bo‘lsa aynan shu fallback foydali.
        console.warn("KINO WebRTC connection failed");
      }
    };

    peer.onnegotiationneeded = async () => {
      try {
        if (!peer || peer.signalingState !== "stable" || makingOffer) return;
        makingOffer = true;
        await peer.setLocalDescription(await peer.createOffer());
        await sendSignal(target, { type: "offer", sdp: peer.localDescription.sdp });
      } catch (e) {
        console.warn("KINO negotiation xatosi", e);
      } finally {
        makingOffer = false;
      }
    };

    peer.__polite = polite;
    return peer;
  }

  function ensurePeer() {
    if (participants.length !== 2 || !me) return;
    const target = participants.find(x => Number(x) !== Number(me));
    if (!target) return;
    makePeer(target);
  }

  async function handleOffer(from, sdp) {
    const pc = makePeer(from);
    const offerCollision = makingOffer || pc.signalingState !== "stable";
    ignoreOffer = !pc.__polite && offerCollision;
    if (ignoreOffer) return;

    await pc.setRemoteDescription({ type: "offer", sdp });
    await flushPendingIce();
    await pc.setLocalDescription(await pc.createAnswer());
    await sendSignal(from, { type: "answer", sdp: pc.localDescription.sdp });
  }

  async function handleAnswer(sdp) {
    if (!peer || peer.signalingState !== "have-local-offer") return;
    await peer.setRemoteDescription({ type: "answer", sdp });
    await flushPendingIce();
  }

  async function handleIce(candidate) {
    if (!peer || !peer.remoteDescription) {
      pendingIce.push(candidate);
      return;
    }
    try { await peer.addIceCandidate(candidate); } catch (e) { console.warn("ICE", e); }
  }

  async function flushPendingIce() {
    if (!peer?.remoteDescription) return;
    const items = pendingIce;
    pendingIce = [];
    for (const c of items) {
      try { await peer.addIceCandidate(c); } catch (_) {}
    }
  }

  async function obtainMediaOnce() {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("Bu Telegram/WebView muhitida kamera yoki mikrofon API mavjud emas.");
    const a=localStream.getAudioTracks()[0], v=localStream.getVideoTracks()[0];
    if(a&&v){localStorage.setItem(mediaPermissionKey,"granted");return true;}
    const stream=await navigator.mediaDevices.getUserMedia({
      audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},
      video:{facingMode:"user",width:{ideal:720},height:{ideal:720}}
    });
    for(const track of stream.getTracks()){track.enabled=false;localStream.addTrack(track);}
    localStorage.setItem(mediaPermissionKey,"granted");
    ensurePeer();
    return true;
  }

  async function toggleMedia(kind) {
    try {
      await obtainMediaOnce();
      let track=localStream.getTracks().find(t=>t.kind===kind);
      if(!track)throw new Error(kind==="audio"?"Mikrofon track olinmadi.":"Kamera track olinmadi.");
      const enable=!track.enabled; track.enabled=enable; ensurePeer();
      const pc=peer;
      if(pc){
        let sender=pc.getTransceivers().find(t=>t.receiver?.track?.kind===kind)?.sender;
        if(sender)await sender.replaceTrack(enable?track:null);
        else if(enable)pc.addTrack(track,localStream);
      }
      if(kind==="audio") $("mic").textContent=enable?"🔊 Mikrofon ON":"🔇 Mikrofon";
      else{
        $("cam").textContent=enable?"📹 Kamera ON":"📵 Kamera";
        if(enable){localVideo.srcObject=localStream;localWrap.classList.remove("hidden");await localVideo.play().catch(()=>{});}
        else{localVideo.srcObject=null;localWrap.classList.add("hidden");}
      }
    } catch(e){
      console.error("KINO media",kind,e);
      alert("Kamera va mikrofon ruxsati berilmadi. Telegram sozlamalaridan ruxsatni yoqing.");
    }
  }

  $("mic").onclick = () => toggleMedia("audio");
  $("cam").onclick = () => toggleMedia("video");
  $("share").onclick = async () => {
    const url = shareUrl || location.href;
    try {
      await navigator.clipboard.writeText(url);
      tg?.showAlert?.("Kino xonasi havolasi nusxalandi.");
    } catch (_) {
      prompt("Xona havolasi:", url);
    }
  };

  boot();
})();
