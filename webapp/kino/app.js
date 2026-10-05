(() => {
  "use strict";

  const tg = window.Telegram?.WebApp;
  try { tg?.ready(); tg?.expand(); } catch (_) {}

  const qs = new URLSearchParams(location.search);
  let room = qs.get("room") || "";
  const startParam = tg?.initDataUnsafe?.start_param || qs.get("startapp") || "";
  let movieId = qs.get("movie") || "";
  if (!room && startParam.startsWith("room_")) {
    // `room_<xona>_<kino>`: kino ID server qayta ishga tushganda xonani tiklash uchun kerak.
    const [rid, mid] = startParam.slice(5).split("_");
    room = rid;
    if (mid && !movieId) movieId = mid;
  }
  const initData = tg?.initData || (() => {
    // Router (webapp/index.html) Main Mini App'dan o'tishda initData nusxasini saqlaydi.
    try { return sessionStorage.getItem("student_ai_tg_init_data") || ""; } catch (_) { return ""; }
  })();
  let currentMovieId = movieId;

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
  let lastServerState = null;   // oxirgi muvaffaqiyatli /state javobi (overlay bosilganda ishlatiladi)
  let pendingState = null;      // metadata kelguncha kutayotgan server holati
  let resumeAfterLoad = null;   // stream tiklanganda joriy pozitsiyani qaytarish uchun
  let authLost = false;
  let roomGone = false;
  const loops = {};              // polling taymerlari (nomi -> timeout id)
  let restoring = false;
  let signalBoostUntil = 0;
  let lastDriftFixAt = 0;
  let connectionLost = false;
  let clockOffsetMs = 0;
  let reconnectTimer = null;
  let lastIceRestartAt = 0;
  let statsTimer = null;
  let lastOutboundStats = null;
  let goodNetworkSince = 0;
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
  const moviePicker = $("moviePicker");
  const movieList = $("movieList");

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
    if (!d.ok) {
      const err = Error(d.error || "Server xatosi");
      err.code = d.code || "";
      throw err;
    }
    return d.data;
  }

  // Sessiya (initData) yoki xona yo'qolganda foydalanuvchi sababni ko'rishi kerak;
  // aks holda polling jimgina uzilib, video "qotib" qolgandek ko'rinadi.
  function stopLoops() {
    Object.keys(loops).forEach((k) => { clearTimeout(loops[k]); delete loops[k]; });
  }
  function giveUp(code) {
    if (authLost || roomGone) return;
    if (code === "auth") authLost = true; else roomGone = true;
    stopLoops();
    setStatus(code === "auth"
      ? "⚠️ Sessiya muddati tugadi. Mini App'ni yopib, Telegramdan qayta oching. Video buferdagi qismigacha davom etadi."
      : "⚠️ Kino xonasi yopilgan va tiklab bo'lmadi. Havolani qayta oching yoki yangi xona yarating.");
    setTimeout(() => setStatus(""), 9000);
  }
  function onApiError(e) {
    const code = e && e.code;
    if (code === "auth") return giveUp("auth");
    if (code === "room" || code === "member") {
      // Server qayta ishga tushgan bo'lishi mumkin (xonalar xotirada). Avval tiklashga urinamiz.
      restoreRoom().then((ok) => { if (!ok) giveUp("room"); });
    }
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // Xonani shu ID va kino bilan qayta tiklaydi, so'ng O'Z holatimizni (haqiqiy pozitsiya)
  // serverga yozadi: yangi xona bo'sh (0:00, pauza) bo'ladi, uni qo'llab qo'ysak hamma boshiga qaytardi.
  async function restoreRoom() {
    if (restoring || authLost || !room) return restoring;
    const mid = currentMovieId || movieId;
    if (!mid) return false;
    restoring = true;
    try {
      setStatus("🔄 Xona tiklanmoqda...");
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const d = await api("/api/kino/join", null, { room, movie: mid });
          me = d.user_id ?? me;
          participants = d.participants || participants;
          await applyMovie(d);
          lastVersion = Number(d.state?.version ?? 0);
          stateReady = true;
          const s = await api("/api/kino/state", {
            room, playing: !video.paused, position: Number(video.currentTime || 0),
          });
          lastVersion = Math.max(lastVersion, Number(s.version ?? lastVersion));
          setStatus("");
          return true;
        } catch (e) {
          if (e.code === "auth") { giveUp("auth"); return false; }
          await sleep(1200 * (attempt + 1));
        }
      }
      return false;
    } finally {
      restoring = false;
    }
  }

  // Polling: avval ~6 so'rov/s edi (900ms + 700ms + 300ms). Endi holatga qarab moslashadi.
  function startLoop(name, fn, intervalFn) {
    const tick = async () => {
      if (authLost || roomGone) return;
      try { await fn(); } catch (_) {}
      if (authLost || roomGone) return;
      loops[name] = setTimeout(tick, intervalFn());
    };
    loops[name] = setTimeout(tick, 0);
  }
  const slow = (ms) => (document.hidden || connectionLost ? Math.max(ms, 3000) : ms);
  const stateInterval = () => slow(900);
  const chatInterval = () => slow(1500);
  const signalInterval = () => {
    // Kelishuv (offer/answer/ICE) paytida tez, ulanib bo'lgach sekin; yolg'iz bo'lsa juda sekin.
    if (participants.length < 2) return slow(2500);
    const negotiating = Date.now() < signalBoostUntil || !peer || peer.connectionState !== "connected" || makingOffer;
    return slow(negotiating ? 300 : 1200);
  };
  const boostSignals = (ms = 8000) => { signalBoostUntil = Date.now() + ms; };

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

      const d = await api("/api/kino/join", null, { room, movie: movieId });
      me = d.user_id;
      participants = d.participants || [];
      shareUrl = d.share_url || location.href;
      await applyMovie(d);
      $("roomInfo").textContent = `2 kishilik xona • ${room.slice(0, 6)}`;
      setStatus("⏳ Kino yuklanmoqda...");
      playOverlay.classList.remove("hidden");
      renderPeople();
      ensurePeer();

      // Har bir timer alohida guard bilan ishlaydi: sekin tarmoqda bir xil
      // polling funksiyasi ustma-ust ishlamaydi.
      await pollChat();
      await pollSignals();
      await pollState();
      startLoop("state", pollState, stateInterval);
      startLoop("chat", pollChat, chatInterval);
      startLoop("signals", pollSignals, signalInterval);
    } catch (e) {
      console.error("KINO boot", e);
      setStatus("❌ " + e.message);
    }
  }

  // ---- Dasturiy hodisalarni foydalanuvchi harakatidan ajratish ----------------
  // video.play()/pause()/currentTime= natijasidagi "play"/"pause"/"seeked" hodisalari
  // ASINXRON keladi. Avvalgi `suppressVideoEvents` bayrog'i setTimeout(0) bilan darrov
  // o'chirilgani uchun bu hodisalar serverga qaytib ketardi (masalan, keyin kirgan
  // odamning "seeked" hodisasi butun xonani PAUSE qilardi). Endi har bir dasturiy
  // amal o'z hodisasini aynan bir marta "yutib" yuboradi.
  const quietUntil = { play: 0, pause: 0, seeked: 0 };
  const markQuiet = (kind, ms) => { quietUntil[kind] = Date.now() + ms; };
  const consumeQuiet = (kind) => {
    if (Date.now() < quietUntil[kind]) { quietUntil[kind] = 0; return true; }
    return false;
  };
  const resetQuiet = () => { quietUntil.play = quietUntil.pause = quietUntil.seeked = 0; };

  function quietSeek(t) {
    if (!Number.isFinite(t)) return;
    if (Math.abs((video.currentTime || 0) - t) < 0.05) return;
    markQuiet("seeked", 8000);
    try { video.currentTime = Math.max(0, t); } catch (_) { quietUntil.seeked = 0; }
  }
  function quietPause() {
    if (video.paused) return;
    markQuiet("pause", 2500);
    video.pause();
  }
  async function quietPlay() {
    if (!video.paused) return true;
    markQuiet("play", 2500);
    try { await video.play(); return true; }
    catch (_) { quietUntil.play = 0; return false; }
  }

  // Server soati bilan ishlaymiz (brauzer soati farq qilishi mumkin).
  function updateClock(d, started, finished) {
    if (Number.isFinite(Number(d.server_now))) {
      const midpoint = started + (finished - started) / 2;
      clockOffsetMs = Number(d.server_now) * 1000 - midpoint;
    }
  }
  function desiredPosition(d) {
    let desired = Number(d.position) || 0;
    if (d.playing && Number.isFinite(Number(d.updated_at))) {
      const nowServerSec = (Date.now() + clockOffsetMs) / 1000;
      desired += Math.max(0, nowServerSec - Number(d.updated_at));
    }
    return desired;
  }
  async function fetchState() {
    const started = Date.now();
    const d = await api("/api/kino/state", null, { room });
    updateClock(d, started, Date.now());
    lastServerState = d;
    return d;
  }

  async function applyMovie(d, { force = false } = {}) {
    if (!d?.movie || !d?.stream_path) return;
    const nextId = String(d.movie.id);
    const changed = !!currentMovieId && currentMovieId !== nextId;
    currentMovieId = nextId;
    $("title").textContent = "🎬 " + d.movie.title;
    // State polling applyMovie'ni qayta-qayta chaqiradi: kino o'zgarmagan bo'lsa
    // (yoki majburiy qayta yuklash so'ralmagan bo'lsa) video elementiga tegmaymiz.
    if (!force && !changed && video.getAttribute("data-movie-id") === nextId && video.src) return;

    // Faqat stream tokeni yangilanayotgan bo'lsa (bir xil kino) joriy joydan davom etamiz.
    resumeAfterLoad = (force && !changed && video.getAttribute("data-movie-id") === nextId)
      ? { time: Number(video.currentTime || 0), play: !video.paused }
      : null;
    pendingState = null;
    resetQuiet();
    setStatus("⏳ Kino tayyorlanmoqda...");
    video.setAttribute("data-movie-id", nextId);
    video.src = d.stream_path;
    video.load();   // paused=true qiladi, currentTime=0 (alohida 'pause' hodisasi chiqmaydi)
    playOverlay.classList.remove("hidden");
  }

  // Server holatini pleyerga qo'llaydi. Metadata kelmaguncha currentTime'ni ishonchli
  // o'rnatib bo'lmaydi, shuning uchun holat 'loadedmetadata'gacha kutadi.
  async function applyServerState(d) {
    if (video.readyState < 1) { pendingState = d; return; }
    pendingState = null;
    const desired = desiredPosition(d);
    const drift = Math.abs((video.currentTime || 0) - desired);
    if (!d.playing) {
      // Masofadagi pauza avtoritetli: aynan o'sha joyga qo'yamiz.
      if (drift > 0.15) quietSeek(desired);
      quietPause();
    } else {
      // Ijro paytida har pollda seek qilmaymiz — faqat sezilarli farqda.
      if (drift > 1.75) quietSeek(desired);
      if (video.paused) {
        const ok = await quietPlay();
        // Brauzer avtoplay'ni blokladi: foydalanuvchi bosishi kerak (overlay tugmasi).
        if (!ok) playOverlay.classList.remove("hidden");
      }
    }
  }

  function renderPeople() {
    $("people").innerHTML = participants
      .map((p) => `<div class="person">🟢 ${p === me ? "Siz" : "Do‘st"}</div>`)
      .join("");
  }

  // Ijro paytida buferlash yoki tarmoq sababli ortda qolgan (yoki oldinga ketgan) odamni
  // xona vaqtiga qaytaradi. Faqat holat o'zgarmagan pollda, sezilarli farqda (>3s), video
  // bufer kutmayotgan paytda va kamida 6s oralig'ida; ijro/pauza holatiga tegmaydi.
  function correctDrift(d) {
    if (!d.playing || video.paused || video.seeking || video.readyState < 3) return;
    if (Date.now() - lastDriftFixAt < 6000) return;
    const desired = desiredPosition(d);
    if (Math.abs((video.currentTime || 0) - desired) > 3) {
      lastDriftFixAt = Date.now();
      quietSeek(desired);
    }
  }

  async function pollState() {
    if (statePolling || !room || authLost || roomGone) return;
    statePolling = true;
    try {
      const d = await fetchState();
      connectionLost = false;

      participants = d.participants || participants;
      renderPeople();
      await applyMovie(d);
      if (participants.length === 2) ensurePeer();

      const version = Number(d.version ?? -1);
      const firstSync = !stateReady;
      // Versiya o'zgarmagan bo'lsa pleyerga tegmaymiz. Bu offline'dan qaytganda
      // buferdagi videoni orqaga qaytarib yubormaslik uchun muhim.
      if (!firstSync && version <= lastVersion) {
        correctDrift(d);
        return;
      }
      lastVersion = Math.max(lastVersion, version);
      stateReady = true;

      const remoteEvent = Number(d.actor_id) !== Number(me);
      // Birinchi sinxronda (keyin kirgan odam yoki sahifani qayta ochgan foydalanuvchi)
      // holat HAR DOIM qo'llanadi — actor o'zimiz bo'lsa ham, chunki qayta ochilganda
      // local pozitsiya 0 bo'ladi. Keyingi o'zgarishlarda esa o'zimiz yuborgan holatning
      // aks-sadosi qo'llanmaydi (aks holda pleyer orqaga sakrab ketadi).
      if (firstSync || remoteEvent) await applyServerState(d);
    } catch (e) {
      // Internet uzilganda videoga tegmaymiz: brauzer buferdagi qism bilan davom etadi.
      connectionLost = true;
      onApiError(e);
    } finally {
      statePolling = false;
    }
  }

  window.addEventListener("offline", () => {
    connectionLost = true;
    setStatus("📡 Internet uzildi — video mavjud buffer bilan davom etadi.");
    setTimeout(() => { if (connectionLost) setStatus(""); }, 2500);
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !authLost && !roomGone) pollState();
  });

  window.addEventListener("online", async () => {
    connectionLost = false;
    setStatus("📡 Internet qaytdi — xona holati sinxronlanmoqda...");
    // A successful reconnect must immediately reconcile the authoritative
    // room state (including a pause/seek made by the other participant).
    await pollState(true);
    setStatus("");
  });

  // 📱 Android/Telegram klaviaturasi ochilganda WebView viewport qisqaradi.
  // CSS sticky playerni saqlab qoladi, bu listener esa keyboard balandligini
  // CSS ga beradi va chat inputga fokuslanganda player yo'qolib ketishini oldini oladi.
  function syncViewport() {
    const vv = window.visualViewport;
    if (!vv) return;
    const keyboard = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
    document.documentElement.style.setProperty("--keyboard-height", `${keyboard}px`);
    document.body.classList.toggle("keyboard-open", keyboard > 120);
  }
  window.visualViewport?.addEventListener("resize", syncViewport);
  window.visualViewport?.addEventListener("scroll", syncViewport);
  window.addEventListener("resize", syncViewport);
  syncViewport();

  // Stream URL'dagi token yaroqsiz bo'lib qolsa (yoki server qayta ishga tushsa) video
  // 'error' beradi. Foydalanuvchini "Kino ochilmadi"da qoldirmasdan, yangi token bilan
  // joriy joydan davom ettiramiz (10 daqiqada ko'pi bilan 3 urinish).
  const recoverLog = [];
  let recovering = false;
  async function recoverStream() {
    const now = Date.now();
    while (recoverLog.length && now - recoverLog[0] > 600000) recoverLog.shift();
    if (recovering || authLost || roomGone || recoverLog.length >= 3) return false;
    recovering = true;
    recoverLog.push(now);
    try {
      setStatus("🔄 Aloqa tiklanmoqda...");
      const d = await api("/api/kino/join", null, { room });   // yangi stream tokeni
      await applyMovie(d, { force: true });
      return true;
    } catch (e) {
      onApiError(e);
      return false;
    } finally {
      recovering = false;
    }
  }

  video.addEventListener("error", async () => {
    const e = video.error;
    console.error("KINO video error", e?.code, e?.message);
    if (await recoverStream()) return;
    if (authLost || roomGone) return;
    setStatus("❌ Kino ochilmadi. Server oqimi vaqtincha mavjud emas yoki fayl MP4 (H.264/AAC) emas.");
  });
  video.addEventListener("loadedmetadata", async () => {
    setStatus("");
    if (pendingState) {
      const d = pendingState;
      pendingState = null;
      await applyServerState(d);
    } else if (resumeAfterLoad) {
      const r = resumeAfterLoad;
      resumeAfterLoad = null;
      quietSeek(r.time);
      if (r.play && !(await quietPlay())) playOverlay.classList.remove("hidden");
    }
  });
  video.addEventListener("canplay", () => {
    setStatus("");
  });

  // Overlay — foydalanuvchi harakati (autoplay bloklanganda yagona yo'l). play() ni
  // gesture ichida DARHOL chaqirish shart (iOS), shuning uchun tarmoqni kutmaymiz va
  // oxirgi ma'lum server holatidan foydalanamiz (u ≤1 soniya eskirgan).
  playOverlay.onclick = () => {
    const d = lastServerState;
    if (d && stateReady) {
      if (d.playing) {
        // Xona allaqachon ijro etilmoqda: biz unga QO'SHILAMIZ, holatni o'zgartirmaymiz.
        // (Eski kod bu yerda 0:00 ni yuborib, birinchi odamni ham boshiga qaytarardi.)
        quietSeek(desiredPosition(d));
        markQuiet("play", 2500);
      } else {
        // Xona to'xtatilgan: umumiy pozitsiyadan boshlaymiz; "play" hammaga yuboriladi.
        quietSeek(Number(d.position) || 0);
      }
    }
    video.play().catch((e) => {
      quietUntil.play = 0;
      setStatus("❌ Video ishga tushmadi: " + e.message);
    });
  };

  let stateSendTimer = null;
  function pushState(playing) {
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
      } catch (e) {
        // Offline paytida local ijroga tegmaymiz.
        connectionLost = true;
        onApiError(e);
      }
    }, 120);
  }
  video.addEventListener("play", () => {
    playOverlay.classList.add("hidden");
    if (consumeQuiet("play")) return;
    pushState(true);
  });
  video.addEventListener("pause", () => {
    if (video.currentTime < 0.2 && video.readyState >= 2) playOverlay.classList.remove("hidden");
    if (consumeQuiet("pause")) return;
    pushState(false);
  });
  video.addEventListener("seeked", () => {
    if (consumeQuiet("seeked")) return;
    pushState(!video.paused);
  });

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
    } catch (e) {
      // Keyingi poll aynan o‘sha cursor bilan qayta urinadi.
      onApiError(e);
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

  const chatInput = $("chatInput");
  chatInput.addEventListener("focus", () => {
    setTimeout(() => chatInput.scrollIntoView({block:"nearest", inline:"nearest"}), 80);
    syncViewport();
  });
  chatInput.addEventListener("blur", () => setTimeout(syncViewport, 80));

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

  async function openMoviePicker() {
    try {
      const movies = await api("/api/kino/movies", null, { room });
      movieList.innerHTML = (movies || []).map(m => {
        const current = String(m.id) === String(currentMovieId);
        return `<button class="movie-item ${current ? "current" : ""}" type="button" data-movie-id="${escapeHtml(m.id)}" ${current ? "disabled" : ""}>
          🎬 ${escapeHtml(m.title)}${current ? " • Hozirgi kino" : ""}
        </button>`;
      }).join("") || "<div>📭 Kino katalogi bo‘sh.</div>";
      moviePicker.classList.remove("hidden");
    } catch (e) {
      tg?.showAlert?.(e.message);
    }
  }

  movieList.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-movie-id]");
    if (!btn || btn.disabled) return;
    try {
      btn.disabled = true;
      const d = await api("/api/kino/change_movie", { room, movie_id: btn.dataset.movieId });
      await applyMovie(d);
      lastVersion = Math.max(lastVersion, Number(d.version ?? lastVersion));
      stateReady = true;
      lastServerState = d;
      moviePicker.classList.add("hidden");
      setStatus("🎬 Yangi kino tanlandi.");
      setTimeout(() => setStatus(""), 1200);
    } catch (err) {
      btn.disabled = false;
      tg?.showAlert?.(err.message);
    }
  });

  $("nextMovie").onclick = openMoviePicker;
  $("closeMoviePicker").onclick = () => moviePicker.classList.add("hidden");

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
    } catch (e) {
      // Keyingi poll qayta urinadi.
      onApiError(e);
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
    const urls = Array.isArray(window.KINO_TURN_URLS) && window.KINO_TURN_URLS.length
      ? window.KINO_TURN_URLS : (window.KINO_TURN_URL ? [window.KINO_TURN_URL] : []);
    const user = window.KINO_TURN_USERNAME || "";
    const credential = window.KINO_TURN_CREDENTIAL || "";
    if (user && credential) {
      for (const url of urls) if (url) ice.push({ urls: url, username: user, credential });
    }
    return {
      iceServers: ice,
      bundlePolicy: "max-bundle",
      rtcpMuxPolicy: "require",
      iceCandidatePoolSize: 4,
    };
  }

  function videoSenders(pc) {
    return pc?.getSenders?.().filter(s => s.track?.kind === "video") || [];
  }

  async function tuneVideoSender(sender, tier = "auto") {
    if (!sender?.track || sender.track.kind !== "video") return;
    try {
      const p = sender.getParameters();
      p.encodings ||= [{}];
      const e = p.encodings[0];
      // WebRTC congestion control remains in charge; these are upper bounds.
      const limits = { low: 420_000, medium: 850_000, high: 1_650_000, auto: 1_650_000 };
      e.maxBitrate = limits[tier] || limits.auto;
      e.maxFramerate = 30;
      if ("scaleResolutionDownBy" in e) e.scaleResolutionDownBy = tier === "low" ? 2 : tier === "medium" ? 1.5 : 1;
      if ("degradationPreference" in p) p.degradationPreference = "maintain-framerate";
      await sender.setParameters(p);
    } catch (e) { console.debug("KINO video sender tune", e); }
  }

  async function tuneAllVideoSenders(tier = "auto") {
    for (const sender of videoSenders(peer)) await tuneVideoSender(sender, tier);
  }

  function scheduleIceRestart(reason = "network") {
    const now = Date.now();
    if (!peer || !peerTarget || makingOffer || now - lastIceRestartAt < 5000) return;
    clearTimeout(reconnectTimer);
    boostSignals();
    reconnectTimer = setTimeout(async () => {
      if (!peer || !peerTarget || makingOffer) return;
      if (!["failed", "disconnected", "checking"].includes(peer.connectionState) &&
          !["failed", "disconnected", "checking"].includes(peer.iceConnectionState)) return;
      lastIceRestartAt = Date.now();
      try {
        makingOffer = true;
        if (peer.restartIce) peer.restartIce();
        await peer.setLocalDescription(await peer.createOffer({ iceRestart: true }));
        await sendSignal(peerTarget, { type: "offer", sdp: peer.localDescription.sdp, reason });
      } catch (e) { console.warn("KINO ICE restart", e); }
      finally { makingOffer = false; }
    }, reason === "failed" ? 250 : 1200);
  }

  async function collectRtcStats() {
    const pc = peer;
    if (!pc || pc.connectionState === "closed") return;
    try {
      const report = await pc.getStats();
      let outbound = null, candidate = null;
      report.forEach(r => {
        if (r.type === "outbound-rtp" && r.kind === "video") outbound = r;
        if (r.type === "candidate-pair" && r.state === "succeeded" && r.nominated) candidate = r;
      });
      if (!outbound) return;
      const prev = lastOutboundStats;
      const bytesDelta = prev ? Math.max(0, (outbound.bytesSent || 0) - prev.bytes) : 0;
      const packetsDelta = prev ? Math.max(0, (outbound.packetsSent || 0) - prev.sent) : 0;
      const lostDelta = prev ? Math.max(0, (outbound.packetsLost || 0) - prev.lost) : 0;
      const lossRatio = packetsDelta ? lostDelta / (packetsDelta + lostDelta) : 0;
      const reason = outbound.qualityLimitationReason || "none";
      const bad = reason === "bandwidth" || lossRatio > 0.06 || (candidate?.currentRoundTripTime || 0) > 0.65;
      const good = reason === "none" && lossRatio < 0.015 && (candidate?.currentRoundTripTime || 0) < 0.35;
      if (bad) {
        goodNetworkSince = 0;
        await tuneAllVideoSenders("low");
      } else if (good) {
        if (!goodNetworkSince) goodNetworkSince = Date.now();
        if (Date.now() - goodNetworkSince > 12000) await tuneAllVideoSenders("high");
      }
      lastOutboundStats = { bytes: outbound.bytesSent || 0, sent: outbound.packetsSent || 0, lost: outbound.packetsLost || 0 };
      if (candidate?.availableOutgoingBitrate && candidate.availableOutgoingBitrate < 550_000) await tuneAllVideoSenders("low");
      else if (candidate?.availableOutgoingBitrate && candidate.availableOutgoingBitrate < 1_000_000) await tuneAllVideoSenders("medium");
    } catch (e) { console.debug("KINO getStats", e); }
  }

  function startRtcStats() {
    clearInterval(statsTimer);
    statsTimer = setInterval(collectRtcStats, 5000);
    collectRtcStats();
  }

  function makePeer(target) {
    if (peer && peerTarget === Number(target)) return peer;
    if (peer) { try { peer.close(); } catch (_) {} }
    peerTarget = Number(target);
    makingOffer = false; pendingIce = []; ignoreOffer = false; lastOutboundStats = null;
    const polite = Number(me) > Number(target);
    peer = new RTCPeerConnection(rtcConfig());
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
      if (event.candidate) sendSignal(target, { type: "ice", candidate: event.candidate.toJSON ? event.candidate.toJSON() : event.candidate });
    };
    peer.onconnectionstatechange = () => {
      const s = peer?.connectionState;
      if (s === "connected") {
        connectionLost = false;
        remoteWrap.classList.remove("hidden");
        if (participants.length >= 2) setStatus("");
        tuneAllVideoSenders("high");
        startRtcStats();
      } else if (s === "failed" || s === "disconnected") {
        console.warn("KINO WebRTC connection", s);
        setStatus("📡 Kamera aloqasi tiklanmoqda...");
        scheduleIceRestart(s);
      }
    };
    peer.oniceconnectionstatechange = () => {
      const s = peer?.iceConnectionState;
      if (s === "failed" || s === "disconnected") scheduleIceRestart(s);
      if (s === "connected" || s === "completed") setStatus("");
    };
    peer.onnegotiationneeded = async () => {
      try {
        if (!peer || peer.signalingState !== "stable" || makingOffer) return;
        makingOffer = true;
        await peer.setLocalDescription(await peer.createOffer());
        await sendSignal(target, { type: "offer", sdp: peer.localDescription.sdp });
      } catch (e) { console.warn("KINO negotiation xatosi", e); }
      finally { makingOffer = false; }
    };
    peer.__polite = polite;
    startRtcStats();
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
      video:{facingMode:"user",width:{ideal:1280,max:1280},height:{ideal:720,max:720},frameRate:{ideal:30,max:30}}
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
        const tr=pc.getTransceivers().find(t=>t.receiver?.track?.kind===kind);
        if(tr?.sender){
          if(enable){
            // Masofadagi offer'dan yaratilgan transceiver "recvonly" bo'ladi: replaceTrack bitta
            // o'zi yuborishni boshlamaydi. Yo'nalishni sendrecv qilamiz (bu qayta kelishuvni
            // — negotiationneeded — ishga tushiradi), so'ng trackni qo'yamiz.
            if(tr.direction==="recvonly"||tr.direction==="inactive") tr.direction="sendrecv";
            await tr.sender.replaceTrack(track);
            if(kind==="video") await tuneVideoSender(tr.sender,"high");
          } else {
            await tr.sender.replaceTrack(null);
          }
        } else if(enable){
          const s=pc.addTrack(track,localStream);
          if(kind==="video") await tuneVideoSender(s,"high");
        }
        boostSignals();   // yangi offer/answer tez almashinsin
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
