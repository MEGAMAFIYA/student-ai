(() => {
  "use strict";
  const tg = window.Telegram?.WebApp;
  try { tg?.ready(); tg?.expand(); } catch (_) {}

  const q = new URLSearchParams(location.search);
  let room = q.get("room") || "";
  const startParam = tg?.initDataUnsafe?.start_param || q.get("startapp") || "";
  if (!room && startParam.startsWith("game_")) room = startParam.slice(5);
  const initData = tg?.initData || "";

  let state = null, selected = null, mySide = null, myStyle = "classic", myMark = null;
  let lastVersion = -1, lastChat = "", busy = false, winShown = false;
  let peer = null, peerTarget = 0, makingOffer = false, pendingIce = [], ignoreOffer = false;
  let localStream = new MediaStream();
  let mediaBusy = false;
  const mediaPermissionKey = "student_ai_media_permission_v2";
  const emojiList = ["😀","😂","😍","🥰","😎","😢","😡","😮","👏","🔥","❤️","💯","👍","👎","🎉","🏆","⚡","🤝","😄","🤣","😉","😘","🤗","🙏","💪","🙌","✨","🎯","🎮","♟️","⚪","⚫","😭"];
  const $ = id => document.getElementById(id);
  const board = $("board");
  const chessSymbols = {K:"♔",Q:"♕",R:"♖",B:"♗",N:"♘",P:"♙",k:"♚",q:"♛",r:"♜",b:"♝",n:"♞",p:"♟"};

  async function api(path, body = null, params = {}) {
    let url = path;
    if (body === null) {
      const s = new URLSearchParams(params);
      if ([...s].length) url += "?" + s;
    }
    const headers = {"X-Telegram-Init-Data": initData};
    if (body !== null) headers["Content-Type"] = "application/json";
    const r = await fetch(url, {method: body === null ? "GET" : "POST", headers, body: body !== null ? JSON.stringify(body) : undefined, cache: "no-store"});
    let d; try { d = await r.json(); } catch (_) { throw Error("Server javobi noto‘g‘ri."); }
    if (!d.ok) throw Error(d.error || "Xatolik");
    return d.data;
  }
  function status(t) { $("turnBadge").textContent = t; }
  function escapeHtml(s) { return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }

  async function boot() {
    if (!initData) return status("Telegram ichidan oching");
    if (!room) return status("Xona topilmadi");
    try {
      state = await api("/api/game/join", null, {room});
      $("gameTitle").textContent = state.game === "chess" ? "♟ Shaxmat" : state.game === "checkers" ? "⚪ Rus shashkasi" : state.game === "memory" ? "🧠 Xotira o‘yini" : state.game === "quoridor" ? "🧱 Quoridor" : "❌⭕ X-O o‘yini";
      renderLobby(); render();
      await pollChat();
      setInterval(poll, 650);
      setInterval(pollSignals, 180);
      setInterval(pollChat, 650);
      ensurePeer();
    } catch (e) { status("❌ " + e.message); }
  }

  const memSizeLabels = {"4x4":"4×4 (8 juft)","4x6":"4×6 (12 juft)","6x6":"6×6 (18 juft)"};
  let myMemSize = null, myMemSymbols = null, memButtonsBuilt = false;

  function buildMemoryButtons() {
    if (memButtonsBuilt || !state) return;
    const sizes = state.memory_sizes || Object.keys(memSizeLabels);
    $("memSizeRow").innerHTML = sizes.map(s => `<button class="choice memsize" data-size="${s}">📐 <b>${escapeHtml(memSizeLabels[s] || s)}</b></button>`).join("");
    const symbolSets = state.memory_symbol_sets || {};
    $("memSymbolsRow").innerHTML = Object.entries(symbolSets).map(([key, label]) => `<button class="choice memsym" data-symbols="${key}">${escapeHtml(label)}</button>`).join("");
    document.querySelectorAll(".memsize").forEach(b => b.onclick = () => { myMemSize = b.dataset.size; renderLobby(); });
    document.querySelectorAll(".memsym").forEach(b => b.onclick = () => { myMemSymbols = b.dataset.symbols; renderLobby(); });
    memButtonsBuilt = true;
  }

  function renderLobby() {
    if (!state) return;
    const ps = state.players || [];
    $("lobbyQuoridor").classList.toggle("hidden", state.game !== "quoridor");
    if (state.game === "quoridor") {
      $("lobbyChess").classList.add("hidden"); $("lobbyMemory").classList.add("hidden"); $("lobbyTTT").classList.add("hidden");
      $("lobbyText").textContent = "Raqib qo‘shilishini kutyapmiz. Havolani do‘stingizga yuboring.";
      $("players").innerHTML = ps.map(p => `<span>● ${escapeHtml(p.name || "O‘yinchi")}</span>`).join("");
      return;
    }
    if (state.game === "memory") {
      $("lobbyChess").classList.add("hidden");
      $("lobbyTTT").classList.add("hidden");
      $("lobbyMemory").classList.remove("hidden");
      buildMemoryButtons();
      if (myMemSize == null) myMemSize = state.memory_size;
      if (myMemSymbols == null) myMemSymbols = state.memory_symbols;
      $("lobbyText").textContent = ps.length < 2 ? "Do‘stingiz ham shu xona orqali kirishini kuting." : "Ikkalangiz katak o‘lchami va belgilarni tanlab, tayyor bo‘ling.";
      $("players").innerHTML = ps.map(p => `<span>● ${escapeHtml(p.name || (p.id === state.me ? "Siz" : "Do‘st"))} ${state.memory_ready?.[String(p.id)] ? "✅ Tayyor" : "⏳"}</span>`).join("");
      document.querySelectorAll(".memsize").forEach(b => b.classList.toggle("selected", b.dataset.size === myMemSize));
      document.querySelectorAll(".memsym").forEach(b => b.classList.toggle("selected", b.dataset.symbols === myMemSymbols));
      const iAmReady = !!state.memory_ready?.[String(state.me)];
      $("memReadyBtn").disabled = iAmReady;
      $("memReadyBtn").textContent = iAmReady ? "⏳ Do‘stingiz kutilmoqda…" : "✓ Tayyor";
      const readyCount = Object.keys(state.memory_ready || {}).length;
      $("memReadyStatus").textContent = ps.length < 2 ? "" : `Tayyor: ${readyCount}/2`;
      return;
    }
    if (state.game === "tictactoe") {
      $("lobbyChess").classList.add("hidden");
      $("lobbyMemory").classList.add("hidden");
      $("lobbyTTT").classList.remove("hidden");
      const me = ps.find(p => p.id === state.me);
      if (myMark == null) myMark = me?.mark || null;
      $("lobbyText").textContent = ps.length < 2 ? "Do‘stingiz ham shu xona orqali kirishini kuting." : "Ikkalangiz belgi (X yoki O) tanlang.";
      $("players").innerHTML = ps.map(p => `<span>● ${escapeHtml(p.name || (p.id === state.me ? "Siz" : "Do‘st"))} ${p.mark ? (p.mark === "x" ? "❌ X" : "⭕ O") : "— tanlamagan"}</span>`).join("");
      document.querySelectorAll(".mark").forEach(b => b.classList.toggle("selected", b.dataset.mark === myMark));
      $("markReadyBtn").disabled = !myMark;
      return;
    }
    $("lobbyMemory").classList.add("hidden");
    $("lobbyTTT").classList.add("hidden");
    $("lobbyChess").classList.remove("hidden");
    $("lobbyText").textContent = ps.length < 2 ? "Do‘stingiz ham shu xona orqali kirishini kuting." : "Ikkalangiz rang va ko‘rinishni tanlang.";
    $("players").innerHTML = ps.map(p => `<span>● ${escapeHtml(p.name || (p.id === state.me ? "Siz" : "Do‘st"))} ${p.side === "w" ? "⚪ Oq" : p.side === "b" ? "⚫ Qora" : "— tanlamagan"}</span>`).join("");
    document.querySelectorAll(".side").forEach(b => b.classList.toggle("selected", b.dataset.side === mySide));
    document.querySelectorAll(".style").forEach(b => b.classList.toggle("selected", b.dataset.style === myStyle));
    $("readyBtn").disabled = !mySide;
  }
  document.querySelectorAll(".side").forEach(b => b.onclick = () => { mySide = b.dataset.side; renderLobby(); });
  document.querySelectorAll(".style").forEach(b => b.onclick = () => { myStyle = b.dataset.style; renderLobby(); });
  document.querySelectorAll(".mark").forEach(b => b.onclick = () => { myMark = b.dataset.mark; renderLobby(); });
  $("readyBtn").onclick = async () => {
    try { state = await api("/api/game/choose", null, {room, side:mySide, style:myStyle}); renderLobby(); render(); }
    catch (e) { tg?.showAlert?.(e.message); }
  };
  $("markReadyBtn").onclick = async () => {
    try { state = await api("/api/game/tictactoe/mark", {room, mark: myMark}); renderLobby(); render(); }
    catch (e) { tg?.showAlert?.(e.message); }
  };
  $("memReadyBtn").onclick = async () => {
    try {
      state = await api("/api/game/memory/config", {room, size: myMemSize, symbols: myMemSymbols});
      state = await api("/api/game/memory/ready", {room});
      renderLobby(); render();
    } catch (e) { tg?.showAlert?.(e.message); }
  };

  function playerById(id) { return state?.players?.find(p => Number(p.id) === Number(id)); }
  function render() {
    if (!state) return;
    if (state.status === "lobby") { renderLobby(); $("lobby").classList.remove("hidden"); $("game").classList.add("hidden"); return; }
    $("lobby").classList.add("hidden"); $("game").classList.remove("hidden");
    const me = playerById(state.me), op = state.players.find(p => Number(p.id) !== Number(state.me));
    const isQ = state.game === "quoridor";
    board.classList.toggle("hidden", isQ); $("qBoard").classList.toggle("hidden", !isQ); $("qPanel").classList.toggle("hidden", !isQ);
    if (!isQ) $("rematchBtn").classList.add("hidden");
    if (isQ) {
      fillPlayer($("playerMe"), me, true); fillPlayer($("playerOpp"), op, false);
      $("memScoreboard").classList.add("hidden");
      const myTurn = state.status === "playing" && state.q.my_pid === state.turn;
      status(state.status === "finished" ? "🏁 Tugadi" : myTurn ? "🟢 Sizning yurishingiz" : "🕐 Raqib yurmoqda");
      renderQuoridor();
    } else if (state.game === "memory") {
      fillPlayer($("playerMe"), me, true); fillPlayer($("playerOpp"), op, false);
      const myTurn = String(state.turn) === String(state.me);
      status(state.status === "finished" ? "🏁 Tugadi" : myTurn ? "🟢 Sizning navbatingiz" : "🕐 Do‘stingiz navbati");
      drawMemoryBoard();
      renderMemScoreboard();
    } else if (state.game === "tictactoe") {
      myMark = me?.mark || myMark;
      fillPlayer($("playerMe"), me, true); fillPlayer($("playerOpp"), op, false);
      const myTurn = state.turn === myMark;
      status(state.status === "finished" ? "🏁 Tugadi" : myTurn ? "🟢 Sizning navbatingiz" : "🕐 Do‘stingiz navbati");
      drawTTTBoard();
      $("memScoreboard").classList.add("hidden");
    } else {
      mySide = me?.side || mySide; myStyle = me?.style || myStyle;
      fillPlayer($("playerMe"), me, true); fillPlayer($("playerOpp"), op, false);
      status(state.status === "finished" ? "🏁 Tugadi" : state.turn === mySide ? "🟢 Sizning yurishingiz" : "🕐 Do‘stingiz yurmoqda");
      drawBoard();
      $("memScoreboard").classList.add("hidden");
    }
    if (state.status === "finished") {
      const iWon = (state.game === "memory" || state.game === "tictactoe" || isQ)
        ? String(state.winner) === String(state.me)
        : state.winner === mySide;
      const text = state.winner ? `🏆 ${iWon ? "Siz yutdingiz!" : "Do‘stingiz yutdi!"}` : "🤝 Durang";
      let extra = state.reason || "";
      if (isQ) {
        const wp = state.q.winner_pid === "1" ? state.q.p1 : state.q.p2;
        extra = (state.reason === "surrender" ? "🏳️ Taslim bo‘ldi. " : "🎯 Maqsad qatoriga yetdi. ") + (wp ? "G‘olib: " + (wp.username ? "@" + wp.username : wp.name) : "");
      }
      $("result").textContent = text + "  " + extra;
      $("result").classList.remove("hidden");
      if (isQ) { $("rematchBtn").classList.remove("hidden"); }
      if (state.winner && !winShown) { winShown = true; winFx(); }
    } else { $("result").classList.add("hidden"); winShown = false; if (isQ) { $("rematchBtn").classList.add("hidden"); $("rematchBtn").textContent = "🔄 Qayta o‘ynash"; } }
  }
  function fillPlayer(card, p, mine) {
    const name = p?.name || (mine ? "Siz" : "Do‘stingiz");
    card.querySelector(".player-name").textContent = name;
    const label = card.querySelector(".side-label");
    if (state.game === "quoridor") {
      const pid = p && state.q.p1 && Number(state.q.p1.id) === Number(p.id) ? "1" : "2";
      label.textContent = `${pid === "1" ? "🔵" : "🔴"} 🧱 ${state.q.walls_left?.[pid] ?? 0}`;
    } else if (state.game === "memory") {
      const sc = (state.scores && p) ? (state.scores[String(p.id)] || 0) : 0;
      label.textContent = `🎯 ${sc} juft`;
    } else if (state.game === "tictactoe") {
      label.textContent = p?.mark === "x" ? "❌ X" : p?.mark === "o" ? "⭕ O" : "";
    } else {
      label.textContent = p?.side === "w" ? "⚪ Oq" : p?.side === "b" ? "⚫ Qora" : "";
    }
    card.querySelector(".avatar").textContent = mine ? "🙂" : "👤";
  }

  function drawTTTBoard() {
    board.className = "board ttt";
    board.style.gridTemplateColumns = "repeat(3, 1fr)";
    board.innerHTML = "";
    const canPlay = state.status === "playing" && state.turn === myMark;
    (state.board || []).forEach((v, idx) => {
      const btn = document.createElement("button");
      btn.className = "card ttt-cell " + (v ? "card-revealed" : "card-hidden");
      if (state.win_line && state.win_line.includes(idx)) btn.classList.add("card-matched");
      const face = document.createElement("span");
      face.className = "card-face";
      face.textContent = v === "x" ? "❌" : v === "o" ? "⭕" : "";
      btn.appendChild(face);
      if (!v && canPlay) btn.onclick = () => tttMove(idx); else btn.disabled = true;
      board.appendChild(btn);
    });
  }
  async function tttMove(idx) {
    if (busy) return; busy = true;
    try { state = await api("/api/game/tictactoe/move", {room, index: idx}); render(); tone(420, .07); }
    catch (e) { tg?.showAlert?.(e.message); }
    finally { busy = false; }
  }

  function renderMemScoreboard() {
    const box = $("memScoreboard");
    box.classList.remove("hidden");
    const me = playerById(state.me), op = state.players.find(p => Number(p.id) !== Number(state.me));
    const myScore = (state.scores && me) ? (state.scores[String(me.id)] || 0) : 0;
    const opScore = (state.scores && op) ? (state.scores[String(op.id)] || 0) : 0;
    box.innerHTML = `<span>🙂 Siz: <b>${myScore}</b></span><span>👤 ${escapeHtml(op?.name || "Do‘st")}: <b>${opScore}</b></span>`;
  }

  let lastRevealTs = 0, memBusy = false;
  function drawMemoryBoard() {
    const rows = state.rows || 4, cols = state.cols || 4;
    board.className = "board memory";
    board.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
    const fresh = state.last_reveal && state.last_reveal.ts !== lastRevealTs ? state.last_reveal : null;
    board.innerHTML = "";
    (state.board || []).forEach((cell, idx) => {
      const btn = document.createElement("button");
      const canFlip = cell.state === "hidden" && state.status === "playing" && String(state.turn) === String(state.me);
      btn.className = "card " + (cell.state === "hidden" ? "card-hidden" : cell.state === "matched" ? "card-matched" : "card-revealed");
      if (fresh && fresh.indices.includes(idx)) btn.classList.add(fresh.matched ? "pop-match" : "pop-miss");
      const face = document.createElement("span");
      face.className = "card-face";
      face.textContent = cell.state === "hidden" ? "❔" : cell.symbol;
      btn.appendChild(face);
      if (canFlip) btn.onclick = () => flipCard(idx); else btn.disabled = true;
      board.appendChild(btn);
    });
    if (state.last_reveal) lastRevealTs = state.last_reveal.ts;
  }
  async function flipCard(idx) {
    if (memBusy) return; memBusy = true;
    try {
      state = await api("/api/game/memory/flip", {room, index: idx});
      render();
      if (state.last_reveal) tone(state.last_reveal.matched ? 560 : 160, .08);
    } catch (e) { tg?.showAlert?.(e.message); }
    finally { memBusy = false; }
  }

  function orient(r,c) { return mySide === "b" ? [7-r,7-c] : [r,c]; }
  function drawBoard() {
    board.className = "board"; board.style.gridTemplateColumns = "";
    board.innerHTML = ""; const b = state.board;
    for (let vr=0;vr<8;vr++) for (let vc=0;vc<8;vc++) {
      const [r,c] = orient(vr,vc), sq = document.createElement("button");
      sq.className = "sq " + (((r+c)%2) ? "dark" : "light"); sq.dataset.r=r; sq.dataset.c=c;
      if (state.game === "checkers" && (r+c)%2===0) { sq.classList.add("empty-square"); sq.disabled=true; }
      if (selected && selected[0]===r && selected[1]===c) sq.classList.add("selected");
      if (selected && isLegalTarget(r,c)) {
        const isEpCapture = state.game === "chess" && state.ep && state.ep[0]===r && state.ep[1]===c;
        sq.classList.add((b[r][c] || isEpCapture) ? "capture" : "legal");
      }
      const p = b[r][c];
      if (p) {
        const d=document.createElement("span");
        if (state.game === "chess") { d.className=`piece ${p===p.toUpperCase()?"white":"black"} ${(playerById(state.me)?.style||"classic")}`; d.textContent=chessSymbols[p]||p; }
        else { d.className=`checker ${p.toLowerCase()==="b"?"b ":""}${p===p.toUpperCase()?"king ":""}`; }
        sq.appendChild(d);
      }
      if (!(state.game === "checkers" && (r+c)%2===0)) sq.onclick=()=>clickSquare(r,c); board.appendChild(sq);
    }
  }
  function targetsFor(r, c) {
    if (!state?.legal_moves) return [];
    return state.legal_moves.filter(m => m[0] === r && m[1] === c).map(m => [m[2], m[3]]);
  }
  function isLegalTarget(r,c) { if(!selected)return false; return targetsFor(selected[0],selected[1]).some(x=>x[0]===r&&x[1]===c); }
  function clickSquare(r,c){
    if(state.status!=="playing"||state.turn!==mySide)return;
    const p=state.board[r][c];
    if(selected&&isLegalTarget(r,c)){
      const moved=state.board[selected[0]][selected[1]];
      const isPromotion = state.game==="chess" && moved && moved.toLowerCase()==="p" && (r===0||r===7);
      const fr=selected; selected=null; drawBoard();
      if (isPromotion) { askPromotion(fr,[r,c]); } else { sendMove(fr,[r,c]); }
      return;
    }
    if(p&&p.toLowerCase()===mySide){selected=[r,c];drawBoard();}else{selected=null;drawBoard();}
  }
  function askPromotion(fr, to) {
    $("promoPicker").classList.remove("hidden");
    const handler = (ev) => {
      const btn = ev.target.closest(".promo-choice");
      if (!btn) return;
      $("promoPicker").classList.add("hidden");
      $("promoPicker").removeEventListener("click", handler);
      sendMove(fr, to, btn.dataset.p);
    };
    $("promoPicker").addEventListener("click", handler);
  }
  async function sendMove(fr,to,promotion="q"){
    if(busy)return;busy=true;
    try{const old=state;state=await api("/api/game/move",{room,from:fr,to,promotion});render();fx(state.last_move?.captured?"capture":"move",to);tone(state.last_move?.captured?180:420,.07);}
    catch(e){tg?.showAlert?.(e.message);}finally{busy=false;}
  }

  // ---------- 🧱 Quoridor (Mini App) ----------
  // Server (game.py + quoridor.py) barcha qoidalarni tekshiradi; bu yerda faqat interfeys.
  const QG = 0.26, QT = 9 + 8 * QG;            // devor yo'lagi / doska o'lchami (katak = 1 birlik)
  let qMode = "move", qDraft = null, qBuilt = false, qPawns = {}, qLayers = {};
  const qFlip = () => state?.q?.my_pid === "2";   // 2-o'yinchi doskani aylantirib ko'radi: o'zi doim pastda
  const qPct = v => (v / QT * 100) + "%";
  function qRect(x, y, w, h) {                     // birlik koordinatalar -> % (aylantirish bilan)
    if (qFlip()) { x = QT - x - w; y = QT - y - h; }
    return `left:${qPct(x)};top:${qPct(y)};width:${qPct(w)};height:${qPct(h)}`;
  }
  const qCellXY = (r, c) => [c * (1 + QG), r * (1 + QG)];
  function qWallRect(o, r, c) {
    return o === "h" ? qRect(c * (1 + QG), r * (1 + QG) + 1, 2 + QG, QG) : qRect(c * (1 + QG) + 1, r * (1 + QG), QG, 2 + QG);
  }
  function qBuild() {
    if (qBuilt) return;
    const b = $("qBoard"); b.innerHTML = "";
    for (const n of ["cells", "walls", "pawns"]) { const d = document.createElement("div"); d.style.cssText = "position:absolute;inset:0;pointer-events:none"; b.appendChild(d); qLayers[n] = d; }
    for (const pid of ["1", "2"]) { const e = document.createElement("div"); e.className = "qpawn p" + pid; qLayers.pawns.appendChild(e); qPawns[pid] = e; }
    const ov = document.createElement("div"); ov.className = "qover hidden"; b.appendChild(ov); qLayers.over = ov;
    ov.addEventListener("click", ev => {
      const rc = b.getBoundingClientRect();
      let x = (ev.clientX - rc.left) / rc.width * QT, y = (ev.clientY - rc.top) / rc.height * QT;
      if (qFlip()) { x = QT - x; y = QT - y; }
      const k = v => Math.max(0, Math.min(7, Math.round((v - (1 + QG / 2)) / (1 + QG))));
      const c = k(x), r = k(y);
      if (qDraft && qDraft.r === r && qDraft.c === c) qDraft.o = qDraft.o === "h" ? "v" : "h";
      else qDraft = {o: qDraft?.o || "h", r, c};
      renderQuoridor();
    });
    qBuilt = true;
  }
  function qWallProblem(d) {
    const q = state.q;
    if (q.legal_walls.includes(`${d.o}:${d.r}:${d.c}`)) return null;
    for (const w of q.walls) {
      if (w.r === d.r && w.c === d.c && w.o !== d.o) return "Devorlar kesishmasligi kerak.";
      if (w.o === d.o && ((d.o === "h" && w.r === d.r && Math.abs(w.c - d.c) <= 1) || (d.o === "v" && w.c === d.c && Math.abs(w.r - d.r) <= 1))) return "Bu joyda devor bor (ustma-ust tushadi).";
    }
    return "Bu devor yo‘lni butunlay to‘sib qo‘yadi — mumkin emas.";
  }
  function renderQuoridor() {
    qBuild();
    const q = state.q, myPid = q.my_pid, playing = state.status === "playing";
    const myTurn = playing && myPid === state.turn;
    const myWalls = q.walls_left[myPid] || 0;
    if (!myTurn || myWalls <= 0) { if (qMode === "wall") qMode = "move"; }
    if (qMode !== "wall") qDraft = null;
    // katakllar
    const goalMe = myPid === "1" ? 0 : 8, goalOpp = myPid === "1" ? 8 : 0;
    const legal = new Set(qMode === "move" && myTurn ? q.legal_moves.map(m => m[0] + "," + m[1]) : []);
    qLayers.cells.innerHTML = "";
    for (let r = 0; r < 9; r++) for (let c = 0; c < 9; c++) {
      const [x, y] = qCellXY(r, c), el = document.createElement("button");
      el.className = "qcell" + (r === goalMe ? " goal-me" : r === goalOpp ? " goal-opp" : "") + (legal.has(r + "," + c) ? " legal" : "");
      el.style.cssText = qRect(x, y, 1, 1);
      if (legal.has(r + "," + c)) el.onclick = () => qSendMove(r, c); else el.tabIndex = -1;
      qLayers.cells.appendChild(el);
    }
    // devorlar
    qLayers.walls.innerHTML = "";
    q.walls.forEach((w, i) => {
      const el = document.createElement("div"); el.className = "qwall" + (i === q.walls.length - 1 ? " last" : "");
      el.style.cssText = qWallRect(w.o, w.r, w.c); qLayers.walls.appendChild(el);
    });
    let problem = null;
    if (qDraft) {
      problem = qWallProblem(qDraft);
      const el = document.createElement("div"); el.className = "qwall " + (problem ? "draft-bad" : "draft-ok");
      el.style.cssText = qWallRect(qDraft.o, qDraft.r, qDraft.c); qLayers.walls.appendChild(el);
    }
    // piyodalar
    for (const pid of ["1", "2"]) {
      const [r, c] = q.pawns[pid], [x, y] = qCellXY(r, c), e = qPawns[pid];
      const pad = 0.14;
      e.style.cssText = qRect(x + pad, y + pad, 1 - 2 * pad, 1 - 2 * pad);
      e.className = "qpawn p" + pid + (pid === myPid ? " mine" : "") + (playing && state.turn === pid ? " turn" : "");
    }
    // boshqaruv
    qLayers.over.classList.toggle("hidden", !(qMode === "wall" && myTurn));
    $("qModeRow").classList.toggle("hidden", !playing);
    $("qModeMove").classList.toggle("active", qMode === "move");
    $("qModeWall").classList.toggle("active", qMode === "wall");
    $("qModeWall").textContent = `🧱 Devor qo‘yish (${myWalls})`;
    $("qModeWall").disabled = !myTurn || myWalls <= 0;
    $("qModeMove").disabled = !myTurn;
    $("qWallRow").classList.toggle("hidden", qMode !== "wall");
    $("qWallConfirm").classList.toggle("hidden", qMode !== "wall");
    $("qOrH").classList.toggle("active", (qDraft?.o || "h") === "h");
    $("qOrV").classList.toggle("active", qDraft?.o === "v");
    $("qWallOk").disabled = !qDraft || !!problem;
    const info = $("qInfo");
    if (state.status === "finished") info.textContent = q.last || "";
    else if (qMode === "wall") info.textContent = !qDraft ? "Doskada devor markazi bo‘ladigan joyga bosing (katta kataklar burchagiga)." : problem ? "⛔ " + problem : "✅ Joylashtirish mumkin — tasdiqlang. Xuddi shu joyga yana bossangiz yo‘nalish almashadi.";
    else info.textContent = (myTurn ? "Sariq belgili katakni bosib yuring. " : "Raqib navbati. ") + (q.last ? "↪️ " + q.last : "");
  }
  async function qSendMove(r, c) {
    if (busy) return; busy = true;
    try { state = await api("/api/game/quoridor/move", {room, r, c}); render(); tone(420, .07); try { tg?.HapticFeedback?.impactOccurred?.("light"); } catch (_) {} }
    catch (e) { tg?.showAlert?.(e.message); }
    finally { busy = false; }
  }
  async function qSendWall() {
    if (busy || !qDraft) return; busy = true;
    const d = qDraft;
    try { state = await api("/api/game/quoridor/wall", {room, r: d.r, c: d.c, o: d.o}); qDraft = null; qMode = "move"; render(); tone(260, .09); try { tg?.HapticFeedback?.impactOccurred?.("medium"); } catch (_) {} }
    catch (e) { tg?.showAlert?.(e.message); }
    finally { busy = false; }
  }
  $("qModeMove").onclick = () => { qMode = "move"; renderQuoridor(); };
  $("qModeWall").onclick = () => { qMode = "wall"; renderQuoridor(); };
  $("qOrH").onclick = () => { if (qDraft) { qDraft.o = "h"; renderQuoridor(); } };
  $("qOrV").onclick = () => { if (qDraft) { qDraft.o = "v"; renderQuoridor(); } };
  $("qWallOk").onclick = qSendWall;
  $("qWallCancel").onclick = () => { qMode = "move"; qDraft = null; renderQuoridor(); };
  $("qRules").onclick = () => tg?.showAlert ? tg.showAlert("Maqsad: raqib boshlagan qatorga birinchi yetib borish.\nNavbatda: 1 katak yuring YOKI devor qo‘ying (10 tadan).\nRaqib yonida bo‘lsa — ustidan sakrang (orqasi to‘silgan bo‘lsa yon tomonga).\nRaqibning maqsad qatoriga boradigan barcha yo‘lni to‘sish mumkin emas.") : alert("Qoidalar: 1 katak yuring yoki devor qo‘ying; birinchi bo‘lib qarshi qatorga yetgan g‘olib.");
  $("qShareBtn").onclick = () => {
    const u = state?.share_url || ""; if (!u) return;
    const link = "https://t.me/share/url?url=" + encodeURIComponent(u) + "&text=" + encodeURIComponent("🧱 Quoridor o‘ynaymizmi? Havolani bosing:");
    if (tg?.openTelegramLink) tg.openTelegramLink(link); else window.open(link, "_blank");
  };
  $("rematchBtn").onclick = async () => {
    try { state = await api("/api/game/rematch", {room}); qDraft = null; qMode = "move"; render(); if (state.status === "finished") { $("rematchBtn").textContent = "⏳ Raqib roziligi kutilmoqda…"; } }
    catch (e) { tg?.showAlert?.(e.message); }
  };

  async function poll(){
    try{const d=await api("/api/game/state",null,{room});if(lastVersion<0||d.version>lastVersion){lastVersion=d.version;state=d;render();ensurePeer();}}
    catch(e){console.warn("game state",e);}
  }
  function fx(kind,pos){const [vr,vc]=orient(pos[0],pos[1]),el=board.children[vr*8+vc];if(!el)return;const rect=el.getBoundingClientRect(),wrap=board.getBoundingClientRect();for(let i=0;i<(kind==="capture"?22:8);i++){const x=document.createElement("i");x.className=kind==="capture"?(i%2?"ember":"shard"):"particle";x.style.left=(rect.left-wrap.left+rect.width/2)+"px";x.style.top=(rect.top-wrap.top+rect.height/2)+"px";x.style.setProperty("--x",((Math.random()-.5)*150)+"px");x.style.setProperty("--y",((Math.random()-.65)*150)+"px");$("fx").appendChild(x);setTimeout(()=>x.remove(),900);}}
  function winFx(){for(let i=0;i<70;i++){const x=document.createElement("i");x.className="confetti";x.style.left=(Math.random()*100)+"%";x.style.top="8%";x.style.setProperty("--x",((Math.random()-.5)*260)+"px");x.style.setProperty("--y",(Math.random()*520)+"px");$("fx").appendChild(x);setTimeout(()=>x.remove(),1400);}}
  let audioCtx=null; function tone(freq,dur){if($("soundBtn").dataset.on!=="1")return;try{audioCtx ||= new AudioContext();const o=audioCtx.createOscillator(),g=audioCtx.createGain();o.frequency.value=freq;o.type="sine";g.gain.value=.035;o.connect(g);g.connect(audioCtx.destination);o.start();g.gain.exponentialRampToValueAtTime(.001,audioCtx.currentTime+dur);o.stop(audioCtx.currentTime+dur);}catch(_){} }
  $("soundBtn").dataset.on="1";$("soundBtn").onclick=()=>{const on=$("soundBtn").dataset.on==="1";$("soundBtn").dataset.on=on?"":"1";$("soundBtn").textContent=on?"🔇 Ovoz":"🔊 Ovoz";};

  // ---------- WebRTC: perfect-negotiation + pre-created transceivers ----------
  function rtcConfig(){const ice=[{urls:"stun:stun.l.google.com:19302"},{urls:"stun:stun.cloudflare.com:3478"}];if(window.GAME_TURN_URL&&window.GAME_TURN_USERNAME&&window.GAME_TURN_CREDENTIAL)ice.push({urls:window.GAME_TURN_URL,username:window.GAME_TURN_USERNAME,credential:window.GAME_TURN_CREDENTIAL});return{iceServers:ice,bundlePolicy:"max-bundle",rtcpMuxPolicy:"require"};}
  function makePeer(target){
    if(peer&&peerTarget===Number(target))return peer;
    if(peer)try{peer.close();}catch(_){ }
    peerTarget=Number(target);makingOffer=false;pendingIce=[];ignoreOffer=false;
    const pc=new RTCPeerConnection(rtcConfig()); peer=pc; pc.__polite=Number(state.me)>Number(target);
    try{pc.addTransceiver("audio",{direction:"sendrecv"});pc.addTransceiver("video",{direction:"sendrecv"});}catch(_){ }
    for(const track of localStream.getTracks()){try{const tr=pc.getTransceivers().find(t=>t.receiver?.track?.kind===track.kind);if(tr)tr.sender.replaceTrack(track.enabled?track:null);else pc.addTrack(track,localStream);}catch(_){}}
    pc.ontrack=e=>{const stream=e.streams?.[0]||null;if(stream){$("remoteVideo").srcObject=stream;}else{let s=$("remoteVideo").srcObject;if(!s){s=new MediaStream();$("remoteVideo").srcObject=s;}if(!s.getTracks().some(t=>t.id===e.track.id))s.addTrack(e.track);}if(e.track.kind==="video"){$("playerOpp").querySelector(".video-slot").classList.add("live");$("remoteVideo").play().catch(()=>{});}};
    pc.onicecandidate=e=>{if(e.candidate)sendSignal({type:"ice",candidate:e.candidate.toJSON?e.candidate.toJSON():e.candidate});};
    pc.onnegotiationneeded=async()=>{try{if(!peer||pc!==peer||pc.signalingState!=="stable"||makingOffer)return;makingOffer=true;await pc.setLocalDescription(await pc.createOffer());await sendSignal({type:"offer",sdp:pc.localDescription.sdp});}catch(e){console.warn("offer",e);}finally{makingOffer=false;}};
    pc.onconnectionstatechange=()=>{if(pc.connectionState==="failed"){try{pc.restartIce?.();}catch(_){}}};
    return pc;
  }
  function ensurePeer(){if(!state||state.players?.length!==2)return;const op=state.players.find(p=>Number(p.id)!==Number(state.me));if(!op)return;makePeer(op.id);}
  async function sendSignal(payload){if(!peerTarget)return;try{await api("/api/game/signal",{room,target_user_id:peerTarget,payload});}catch(e){console.warn("signal",e);}}
  let remoteCameraOff=true;
  async function pollSignals(){try{const list=await api("/api/game/signals",null,{room});for(const item of list||[])await handleSignal(item);}catch(_){} }
  async function handleSignal(item){const p=item.payload||{};if(!p.type)return;try{if(p.type==="offer"){const pc=makePeer(item.from);const collision=makingOffer||pc.signalingState!=="stable";ignoreOffer=!pc.__polite&&collision;if(ignoreOffer)return;await pc.setRemoteDescription({type:"offer",sdp:p.sdp});await flushPendingIce();await pc.setLocalDescription(await pc.createAnswer());await sendSignal({type:"answer",sdp:pc.localDescription.sdp});}else if(p.type==="answer"){if(peer?.signalingState==="have-local-offer"){await peer.setRemoteDescription({type:"answer",sdp:p.sdp});await flushPendingIce();}}else if(p.type==="ice"&&p.candidate){if(!peer?.remoteDescription){pendingIce.push(p.candidate);}else{try{await peer.addIceCandidate(p.candidate);}catch(e){if(!ignoreOffer)console.warn("ICE",e);}}}else if(p.type==="media"){if(p.kind==="video"){remoteCameraOff=!p.enabled;const slot=$("playerOpp").querySelector(".video-slot");slot.classList.toggle("live",!!p.enabled&&!!$("remoteVideo").srcObject);}}}catch(e){console.warn("signal handle",e);}}
  async function flushPendingIce(){if(!peer?.remoteDescription)return;const a=pendingIce;pendingIce=[];for(const c of a){try{await peer.addIceCandidate(c);}catch(_){}}}

  // One permission flow. Once browser/Telegram grants it, the flag prevents this UI
  // from intentionally asking again. The browser still controls the actual permission.
  async function obtainMedia(){
    if(mediaBusy)return false; mediaBusy=true;
    try{
      if(!navigator.mediaDevices?.getUserMedia)throw Error("Bu Telegram/WebView kamera va mikrofon API'sini qo‘llamaydi.");
      const existingA=localStream.getAudioTracks()[0],existingV=localStream.getVideoTracks()[0];
      if(existingA&&existingV){localStorage.setItem(mediaPermissionKey,"granted");return true;}
      const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:{facingMode:"user",width:{ideal:720},height:{ideal:720}}});
      for(const t of stream.getTracks()){t.enabled=false;localStream.addTrack(t);}
      localStorage.setItem(mediaPermissionKey,"granted"); ensurePeer();
      return true;
    }catch(e){console.warn("media permission",e);tg?.showAlert?.("Kamera va mikrofon ruxsati berilmadi. Telegram sozlamalaridan ruxsatni yoqing.");return false;}
    finally{mediaBusy=false;}
  }
  async function toggleMedia(kind){
    if(!(await obtainMedia()))return;
    const track=localStream.getTracks().find(t=>t.kind===kind);if(!track)return;
    const enable=!track.enabled;track.enabled=enable;ensurePeer();const pc=peer;
    if(pc){let tr=pc.getTransceivers().find(t=>t.receiver?.track?.kind===kind);if(tr){try{await tr.sender.replaceTrack(enable?track:null);}catch(_){}}else if(enable){try{pc.addTrack(track,localStream);}catch(_){}}}
    if(kind==="video"){$("callBtn").textContent=enable?"📹 Kamera ON":"📵 Kamera";$("localVideo").srcObject=localStream;$("playerMe").querySelector(".video-slot").classList.toggle("live",enable);if(enable)$("localVideo").play().catch(()=>{});else $("localVideo").pause();await sendSignal({type:"media",kind:"video",enabled});}
    else $("micBtn").textContent=enable?"🔊 Mikrofon ON":"🔇 Mikrofon";
  }
  $("callBtn").onclick=()=>toggleMedia("video"); $("micBtn").onclick=()=>toggleMedia("audio");

  // ---------- Mini App chat + emoji effects ----------
  function emojiFromText(text){const m=String(text).match(/[\p{Extended_Pictographic}]/u);return m?.[0]||"";}
  function emojiEffect(emoji,fromEl){if(!emoji)return;for(let i=0;i<5;i++){const e=document.createElement("span");e.className="emoji-burst";e.textContent=emoji;e.style.left=((fromEl?.getBoundingClientRect?.().left||innerWidth/2)+(fromEl?.offsetWidth||0)/2)+"px";e.style.top=((fromEl?.getBoundingClientRect?.().top||innerHeight-60))+"px";e.style.setProperty("--x",((Math.random()-.5)*100)+"px");e.style.setProperty("--y",((Math.random()-.5)*50)+"px");e.style.setProperty("--r",((Math.random()-.5)*40)+"deg");document.body.appendChild(e);setTimeout(()=>e.remove(),900);}try{tg?.HapticFeedback?.impactOccurred?.("light");}catch(_){} }
  function addMsg(m){const box=$("messages");if(box.querySelector(`[data-message-id="${CSS.escape(String(m.id))}"]`))return;const d=document.createElement("div");d.className="msg"+(Number(m.user_id)===Number(state.me)?" me":"");d.dataset.messageId=String(m.id);d.innerHTML=`<b>${escapeHtml(m.name||"Do‘st")}</b><span>${escapeHtml(m.text)}</span>`;box.appendChild(d);box.scrollTop=box.scrollHeight;const em=emojiFromText(m.text);if(em)emojiEffect(em,d);}
  async function pollChat(){try{const items=await api("/api/game/chat",null,{room,after:lastChat});for(const m of items||[]){addMsg(m);lastChat=m.id;}}catch(e){console.warn("chat",e);}}
  $("chatForm").addEventListener("submit",async e=>{e.preventDefault();if(busy)return;const input=$("chatInput"),text=input.value.trim();if(!text)return;busy=true;try{const clientId=crypto.randomUUID?crypto.randomUUID():`${Date.now()}_${Math.random()}`;const item=await api("/api/game/chat",{room,text,client_id:clientId});input.value="";if(item){addMsg(item);lastChat=item.id;emojiEffect(emojiFromText(item.text),$("emojiBtn"));}}catch(err){tg?.showAlert?.(err.message);}finally{busy=false;}});
  const picker=$("emojiPicker");picker.innerHTML=emojiList.map(e=>`<button type="button" data-emoji="${e}">${e}</button>`).join("");
  $("emojiBtn").onclick=()=>picker.classList.toggle("hidden");picker.addEventListener("click",e=>{const b=e.target.closest("button");if(!b)return;const input=$("chatInput");input.value+=b.dataset.emoji;input.focus();emojiEffect(b.dataset.emoji,b);picker.classList.add("hidden");});
  document.addEventListener("click",e=>{if(!picker.contains(e.target)&&e.target!==$("emojiBtn"))picker.classList.add("hidden");});

  $("resignBtn").onclick=async()=>{if(confirm("Taslim bo‘lasizmi?")){try{state=await api("/api/game/resign",{room});render();}catch(e){tg?.showAlert?.(e.message)}}};
  boot();
})();
