(function () {
  "use strict";

  const tg = window.Telegram ? window.Telegram.WebApp : null;
  if (tg) {
    tg.ready();
    tg.expand();
  }

  // ============================================================
  // DOM
  // ============================================================
  const bgCanvas = document.getElementById("bgCanvas");
  const bgCtx = bgCanvas.getContext("2d");
  const canvas = document.getElementById("canvas");
  const ctx = canvas.getContext("2d");
  const wrap = document.getElementById("canvasWrap");
  const emptyHint = document.getElementById("emptyHint");

  const undoBtn = document.getElementById("undoBtn");
  const redoBtn = document.getElementById("redoBtn");
  const clearBtn = document.getElementById("clearBtn");
  const sendBtn = document.getElementById("sendBtn");
  const statusMsg = document.getElementById("statusMsg");

  const toolTabs = document.getElementById("toolTabs");
  const panels = {
    pen: document.getElementById("panel-pen"),
    shape: document.getElementById("panel-shape"),
    sticker: document.getElementById("panel-sticker"),
  };

  const penColorsWrap = document.getElementById("penColors");
  const shapeColorsWrap = document.getElementById("shapeColors");
  const customColorInput = document.getElementById("customColor");
  const penSizeInput = document.getElementById("penSize");
  const penSizeDot = document.getElementById("penSizeDot");
  const eraserBtn = document.getElementById("eraserBtn");

  const shapeTypesWrap = document.getElementById("shapeTypes");
  const shapeSizeInput = document.getElementById("shapeSize");
  const shapeSizeDot = document.getElementById("shapeSizeDot");

  const stickerListWrap = document.getElementById("stickerList");
  const stickerSizeInput = document.getElementById("stickerSize");

  const templateBtn = document.getElementById("templateBtn");
  const templateSheet = document.getElementById("templateSheet");
  const templateGrid = document.getElementById("templateGrid");

  const promptText = document.getElementById("promptText");
  const promptShuffleBtn = document.getElementById("promptShuffleBtn");

  // ============================================================
  // 🎯 CHIZISH TAKLIFI — bot tasodifiy buyum/hayvon aytadi, foydalanuvchi
  // shuni chizadi ("Pictionary" uslubida). Sof ijodiy/o'yin xususiyati —
  // tanlangan so'z serverga umuman yuborilmaydi, faqat ekranda ko'rsatiladi.
  // ============================================================
  const DRAW_PROMPTS = [
    "🐱 Mushuk", "🐶 It", "🐰 Quyon", "🦁 Sher", "🐘 Fil", "🐢 Toshbaqa",
    "🐟 Baliq", "🦋 Kapalak", "🐝 Ari", "🐔 Xo'roz", "🐄 Sigir", "🐴 Ot",
    "🦒 Jirafa", "🐧 Pingvin", "🦉 Boyqush", "🐍 Ilon", "🐸 Qurbaqa",
    "🏠 Uy", "🚗 Mashina", "✈️ Samolyot", "🌳 Daraxt", "🌸 Gul", "☀️ Quyosh",
    "🌙 Oy", "⭐ Yulduz", "☂️ Soyabon", "📚 Kitob", "⌚ Soat", "🎈 Shar",
    "🎂 Tort", "🍎 Olma", "🍉 Tarvuz", "⚽ Futbol to'pi", "🚲 Velosiped",
    "⛰️ Tog'", "🌈 Kamalak", "❄️ Qorqop", "🎁 Sovg'a", "🎸 Gitara", "🚀 Raketa",
  ];

  function showRandomPrompt() {
    const word = DRAW_PROMPTS[Math.floor(Math.random() * DRAW_PROMPTS.length)];
    promptText.textContent = `🎯 Chizing: ${word}`;
  }

  promptShuffleBtn.addEventListener("click", showRandomPrompt);
  showRandomPrompt();

  const PALETTE = [
    "#211F1C", "#D64545", "#E88C3D", "#E8C93D",
    "#3FA55B", "#1E9E90", "#2F6FE0", "#8B4FD6", "#ffffff",
  ];
  const STICKER_EMOJIS = ["🎉", "🎂", "🎈", "❤️", "⭐", "✨", "🌸", "😊"];

  // ============================================================
  // 🖼 SHABLONLAR — barchasi vektor (canvas primitivlari) asosida
  // chiziladi, shuning uchun har qanday o'lchamga moslashadi va
  // tashqi rasm fayllariga (tarmoq so'roviga) muhtoj emas.
  // ============================================================
  function drawGridTemplate(c, w, h) {
    c.save();
    c.strokeStyle = "rgba(33,31,28,0.10)";
    c.lineWidth = 1;
    const step = 28;
    for (let x = step; x < w; x += step) {
      c.beginPath(); c.moveTo(x, 0); c.lineTo(x, h); c.stroke();
    }
    for (let y = step; y < h; y += step) {
      c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke();
    }
    c.restore();
  }

  function drawLinedTemplate(c, w, h) {
    c.save();
    c.strokeStyle = "rgba(47,111,224,0.18)";
    c.lineWidth = 1.4;
    const step = 34;
    for (let y = step; y < h; y += step) {
      c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke();
    }
    c.strokeStyle = "rgba(214,69,69,0.35)";
    c.beginPath(); c.moveTo(w * 0.12, 0); c.lineTo(w * 0.12, h); c.stroke();
    c.restore();
  }

  function drawDottedTemplate(c, w, h) {
    c.save();
    c.fillStyle = "rgba(33,31,28,0.22)";
    const step = 22;
    for (let x = step; x < w; x += step) {
      for (let y = step; y < h; y += step) {
        c.beginPath(); c.arc(x, y, 1.4, 0, Math.PI * 2); c.fill();
      }
    }
    c.restore();
  }

  function drawCardTemplate(c, w, h) {
    c.save();
    const pad = Math.min(w, h) * 0.055;
    c.strokeStyle = "#1E9E90";
    c.lineWidth = 3;
    c.strokeRect(pad, pad, w - pad * 2, h - pad * 2);
    c.strokeStyle = "rgba(30,158,144,0.55)";
    c.lineWidth = 1.2;
    c.setLineDash([4, 5]);
    c.strokeRect(pad * 1.7, pad * 1.7, w - pad * 3.4, h - pad * 3.4);
    c.setLineDash([]);
    // Burchak gullari
    const corners = [[pad, pad], [w - pad, pad], [pad, h - pad], [w - pad, h - pad]];
    c.fillStyle = "#E88C3D";
    corners.forEach(([cx, cy]) => {
      for (let i = 0; i < 5; i++) {
        const a = (i / 5) * Math.PI * 2;
        c.beginPath();
        c.arc(cx + Math.cos(a) * 9, cy + Math.sin(a) * 9, 3.4, 0, Math.PI * 2);
        c.fill();
      }
    });
    // Sarlavha joyi (banner)
    c.strokeStyle = "rgba(33,31,28,0.25)";
    c.setLineDash([3, 4]);
    c.lineWidth = 1.4;
    c.beginPath();
    c.moveTo(w * 0.28, h * 0.18);
    c.lineTo(w * 0.72, h * 0.18);
    c.stroke();
    c.setLineDash([]);
    c.restore();
  }

  function drawHeartTemplate(c, w, h) {
    c.save();
    const scale = Math.min(w, h) / 42;
    const cx = w / 2, cy = h / 2 + scale * 2;
    c.translate(cx, cy);
    c.scale(scale, -scale);
    c.strokeStyle = "#D64545";
    c.lineWidth = 1.6 / scale;
    c.setLineDash([2.2 / scale, 2.6 / scale]);
    c.beginPath();
    for (let i = 0; i <= 100; i++) {
      const t = (i / 100) * Math.PI * 2;
      const x = 16 * Math.pow(Math.sin(t), 3);
      const y = 13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t);
      if (i === 0) c.moveTo(x, y); else c.lineTo(x, y);
    }
    c.closePath();
    c.stroke();
    c.restore();
  }

  function drawStarPath(c, cx, cy, outerR, innerR, spikes, rot) {
    c.beginPath();
    for (let i = 0; i < spikes * 2; i++) {
      const r = i % 2 === 0 ? outerR : innerR;
      const a = rot + (i / (spikes * 2)) * Math.PI * 2;
      const x = cx + Math.cos(a) * r;
      const y = cy + Math.sin(a) * r;
      if (i === 0) c.moveTo(x, y); else c.lineTo(x, y);
    }
    c.closePath();
  }

  const STAR_POSITIONS = [
    { xr: 0.18, yr: 0.16, r: 13, rot: 0.3 },
    { xr: 0.78, yr: 0.12, r: 10, rot: 1.1 },
    { xr: 0.85, yr: 0.32, r: 8, rot: 0.6 },
    { xr: 0.14, yr: 0.42, r: 8, rot: 1.4 },
    { xr: 0.62, yr: 0.20, r: 7, rot: 0.2 },
    { xr: 0.30, yr: 0.72, r: 9, rot: 0.9 },
    { xr: 0.70, yr: 0.80, r: 11, rot: 0.4 },
    { xr: 0.50, yr: 0.55, r: 7, rot: 1.0 },
  ];

  function drawStarsTemplate(c, w, h) {
    c.save();
    c.strokeStyle = "rgba(232,140,61,0.55)";
    c.setLineDash([3, 3]);
    c.lineWidth = 1.4;
    STAR_POSITIONS.forEach((s) => {
      drawStarPath(c, s.xr * w, s.yr * h, s.r, s.r * 0.45, 5, s.rot);
      c.stroke();
    });
    // Yarim oy
    c.setLineDash([]);
    c.strokeStyle = "rgba(47,111,224,0.45)";
    c.lineWidth = 1.6;
    const mx = w * 0.86, my = h * 0.78, mr = Math.min(w, h) * 0.075;
    c.beginPath();
    c.arc(mx, my, mr, Math.PI * 0.3, Math.PI * 1.75);
    c.arc(mx + mr * 0.55, my, mr * 0.85, Math.PI * 1.6, Math.PI * 0.55, true);
    c.closePath();
    c.stroke();
    c.restore();
  }

  const TEMPLATES = [
    { id: "blank", label: "Bo'sh", draw: null },
    { id: "grid", label: "Katakli", draw: drawGridTemplate },
    { id: "lined", label: "Chiziqli", draw: drawLinedTemplate },
    { id: "dotted", label: "Nuqtali", draw: drawDottedTemplate },
    { id: "card", label: "Otkritka", draw: drawCardTemplate },
    { id: "heart", label: "Yurak (rangla)", draw: drawHeartTemplate },
    { id: "stars", label: "Yulduzlar", draw: drawStarsTemplate },
  ];

  let activeTemplateId = "blank";

  function renderTemplateBackground(context, w, h) {
    context.clearRect(0, 0, w, h);
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, w, h);
    const tpl = TEMPLATES.find((t) => t.id === activeTemplateId);
    if (tpl && tpl.draw) tpl.draw(context, w, h);
  }

  // ============================================================
  // HOLAT
  // ============================================================
  let activeTool = "pen"; // "pen" | "shape" | "sticker"
  const penState = { color: PALETTE[0], size: 6, isEraser: false };
  const shapeState = { type: "line", color: PALETTE[0], size: 5 };
  const stickerState = { emoji: STICKER_EMOJIS[0], size: 48 };

  let strokes = [];
  let redoStack = [];
  let currentStroke = null; // qalam bilan chizilayotgan / shakl surilayotgan holat

  function updateEmptyHint() {
    emptyHint.classList.toggle("hidden", strokes.length > 0);
  }

  function updateUndoRedoButtons() {
    undoBtn.style.opacity = strokes.length === 0 ? "0.35" : "0.7";
    redoBtn.style.opacity = redoStack.length === 0 ? "0.35" : "0.7";
  }

  // ============================================================
  // O'LCHAMLASH
  // ============================================================
  function sizeCanvas(cv, cx) {
    const dpr = window.devicePixelRatio || 1;
    const rect = wrap.getBoundingClientRect();
    cv.width = rect.width * dpr;
    cv.height = rect.height * dpr;
    cv.style.width = rect.width + "px";
    cv.style.height = rect.height + "px";
    cx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return rect;
  }

  function resizeCanvases() {
    const rect = sizeCanvas(canvas, ctx);
    sizeCanvas(bgCanvas, bgCtx);
    renderTemplateBackground(bgCtx, rect.width, rect.height);
    redraw();
  }

  // ============================================================
  // CHIZISH — bitta stroke'ni turi bo'yicha chizadi
  // ============================================================
  function strokeSmoothPath(context, points) {
    if (points.length === 0) return;
    context.beginPath();
    context.moveTo(points[0].x, points[0].y);
    if (points.length === 1) {
      context.lineTo(points[0].x + 0.01, points[0].y + 0.01);
      context.stroke();
      return;
    }
    for (let i = 1; i < points.length - 1; i++) {
      const midX = (points[i].x + points[i + 1].x) / 2;
      const midY = (points[i].y + points[i + 1].y) / 2;
      context.quadraticCurveTo(points[i].x, points[i].y, midX, midY);
    }
    const last = points[points.length - 1];
    context.lineTo(last.x, last.y);
    context.stroke();
  }

  function drawStroke(context, stroke) {
    context.save();
    if (stroke.kind === "freehand") {
      context.lineCap = "round";
      context.lineJoin = "round";
      context.lineWidth = stroke.size;
      context.globalCompositeOperation = stroke.isEraser ? "destination-out" : "source-over";
      context.strokeStyle = stroke.color;
      strokeSmoothPath(context, stroke.points);
    } else if (stroke.kind === "shape") {
      context.lineCap = "round";
      context.lineJoin = "round";
      context.lineWidth = stroke.size;
      context.strokeStyle = stroke.color;
      const { start, end } = stroke;
      context.beginPath();
      if (stroke.shapeType === "line") {
        context.moveTo(start.x, start.y);
        context.lineTo(end.x, end.y);
      } else if (stroke.shapeType === "rect") {
        const x = Math.min(start.x, end.x), y = Math.min(start.y, end.y);
        context.rect(x, y, Math.abs(end.x - start.x), Math.abs(end.y - start.y));
      } else if (stroke.shapeType === "circle") {
        const cx = (start.x + end.x) / 2, cy = (start.y + end.y) / 2;
        const rx = Math.abs(end.x - start.x) / 2, ry = Math.abs(end.y - start.y) / 2;
        context.ellipse(cx, cy, Math.max(rx, 1), Math.max(ry, 1), 0, 0, Math.PI * 2);
      }
      context.stroke();
    } else if (stroke.kind === "sticker") {
      context.font = `${stroke.size}px "Apple Color Emoji","Segoe UI Emoji",sans-serif`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(stroke.emoji, stroke.x, stroke.y);
    }
    context.restore();
  }

  function redraw() {
    const rect = wrap.getBoundingClientRect();
    ctx.clearRect(0, 0, rect.width, rect.height);
    for (const s of strokes) drawStroke(ctx, s);
  }

  function getPos(evt) {
    const rect = canvas.getBoundingClientRect();
    const point = evt.touches ? (evt.touches[0] || evt.changedTouches[0]) : evt;
    return { x: point.clientX - rect.left, y: point.clientY - rect.top };
  }

  // ============================================================
  // POINTER HODISALARI (asboblarga qarab har xil ishlaydi)
  // ============================================================
  function startDraw(evt) {
    evt.preventDefault();
    const pos = getPos(evt);

    if (activeTool === "pen") {
      currentStroke = { kind: "freehand", color: penState.color, size: penState.size, isEraser: penState.isEraser, points: [pos] };
    } else if (activeTool === "shape") {
      currentStroke = { kind: "shape", shapeType: shapeState.type, color: shapeState.color, size: shapeState.size, start: pos, end: pos };
    } else if (activeTool === "sticker") {
      const stroke = { kind: "sticker", emoji: stickerState.emoji, size: stickerState.size, x: pos.x, y: pos.y };
      strokes.push(stroke);
      redoStack = [];
      drawStroke(ctx, stroke);
      updateUndoRedoButtons();
      updateEmptyHint();
      currentStroke = null;
      return;
    }
    redoStack = [];
    updateUndoRedoButtons();
  }

  function moveDraw(evt) {
    if (!currentStroke) return;
    evt.preventDefault();
    const pos = getPos(evt);

    if (currentStroke.kind === "freehand") {
      const pts = currentStroke.points;
      pts.push(pos);
      if (pts.length === 2) {
        ctx.save();
        ctx.lineCap = "round";
        ctx.lineWidth = currentStroke.size;
        ctx.globalCompositeOperation = currentStroke.isEraser ? "destination-out" : "source-over";
        ctx.strokeStyle = currentStroke.color;
        ctx.beginPath();
        ctx.moveTo(pts[0].x, pts[0].y);
        ctx.lineTo(pts[1].x, pts[1].y);
        ctx.stroke();
        ctx.restore();
      } else if (pts.length >= 3) {
        const n = pts.length;
        const prevMid = { x: (pts[n - 3].x + pts[n - 2].x) / 2, y: (pts[n - 3].y + pts[n - 2].y) / 2 };
        const newMid = { x: (pts[n - 2].x + pts[n - 1].x) / 2, y: (pts[n - 2].y + pts[n - 1].y) / 2 };
        ctx.save();
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.lineWidth = currentStroke.size;
        ctx.globalCompositeOperation = currentStroke.isEraser ? "destination-out" : "source-over";
        ctx.strokeStyle = currentStroke.color;
        ctx.beginPath();
        ctx.moveTo(prevMid.x, prevMid.y);
        ctx.quadraticCurveTo(pts[n - 2].x, pts[n - 2].y, newMid.x, newMid.y);
        ctx.stroke();
        ctx.restore();
      }
    } else if (currentStroke.kind === "shape") {
      currentStroke.end = pos;
      redraw();
      drawStroke(ctx, currentStroke);
    }
  }

  function endDraw() {
    if (!currentStroke) return;
    strokes.push(currentStroke);
    currentStroke = null;
    updateUndoRedoButtons();
    updateEmptyHint();
  }

  canvas.addEventListener("mousedown", startDraw);
  canvas.addEventListener("mousemove", moveDraw);
  window.addEventListener("mouseup", endDraw);

  canvas.addEventListener("touchstart", startDraw, { passive: false });
  canvas.addEventListener("touchmove", moveDraw, { passive: false });
  canvas.addEventListener("touchend", endDraw);
  canvas.addEventListener("touchcancel", endDraw);

  window.addEventListener("resize", resizeCanvases);

  // ============================================================
  // TOOL TABLARI
  // ============================================================
  toolTabs.querySelectorAll(".tool-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      activeTool = tab.dataset.tool;
      toolTabs.querySelectorAll(".tool-tab").forEach((t) => t.classList.toggle("active", t === tab));
      Object.entries(panels).forEach(([key, el]) => el.classList.toggle("active", key === activeTool));
    });
  });

  // --- Qalam panel ---
  function buildSwatches(container, state, onPick) {
    PALETTE.forEach((color, i) => {
      const sw = document.createElement("div");
      sw.className = "color-swatch" + (i === 0 ? " active" : "");
      sw.style.background = color;
      if (color === "#ffffff") sw.style.boxShadow = "0 0 0 1px rgba(0,0,0,0.3)";
      sw.addEventListener("click", () => {
        state.color = color;
        [...container.children].forEach((c) => c.classList.remove("active"));
        sw.classList.add("active");
        onPick && onPick(color);
      });
      container.appendChild(sw);
    });
  }

  buildSwatches(penColorsWrap, penState, () => {
    penState.isEraser = false;
    eraserBtn.classList.remove("active");
  });
  buildSwatches(shapeColorsWrap, shapeState);

  customColorInput.addEventListener("input", (e) => {
    penState.color = e.target.value;
    penState.isEraser = false;
    eraserBtn.classList.remove("active");
    [...penColorsWrap.children].forEach((c) => c.classList.remove("active"));
  });

  function updateSizeDot(dot, size, max) {
    const px = 6 + (size / max) * 20;
    dot.style.width = px + "px";
    dot.style.height = px + "px";
  }
  updateSizeDot(penSizeDot, penState.size, 40);
  updateSizeDot(shapeSizeDot, shapeState.size, 24);

  penSizeInput.addEventListener("input", () => {
    penState.size = parseInt(penSizeInput.value, 10);
    updateSizeDot(penSizeDot, penState.size, 40);
  });

  eraserBtn.addEventListener("click", () => {
    penState.isEraser = !penState.isEraser;
    eraserBtn.classList.toggle("active", penState.isEraser);
  });

  // --- Shakl panel ---
  shapeTypesWrap.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      shapeState.type = chip.dataset.shape;
      shapeTypesWrap.querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c === chip));
    });
  });
  shapeSizeInput.addEventListener("input", () => {
    shapeState.size = parseInt(shapeSizeInput.value, 10);
    updateSizeDot(shapeSizeDot, shapeState.size, 24);
  });

  // --- Stiker panel ---
  STICKER_EMOJIS.forEach((emoji, i) => {
    const btn = document.createElement("button");
    btn.className = "sticker-btn" + (i === 0 ? " active" : "");
    btn.textContent = emoji;
    btn.addEventListener("click", () => {
      stickerState.emoji = emoji;
      [...stickerListWrap.children].forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
    });
    stickerListWrap.appendChild(btn);
  });
  stickerSizeInput.addEventListener("input", () => {
    stickerState.size = parseInt(stickerSizeInput.value, 10);
  });

  // ============================================================
  // TARIX (undo/redo/clear)
  // ============================================================
  undoBtn.addEventListener("click", () => {
    if (strokes.length === 0) return;
    redoStack.push(strokes.pop());
    redraw();
    updateUndoRedoButtons();
    updateEmptyHint();
  });

  redoBtn.addEventListener("click", () => {
    if (redoStack.length === 0) return;
    strokes.push(redoStack.pop());
    redraw();
    updateUndoRedoButtons();
    updateEmptyHint();
  });

  function clearCanvas() {
    strokes = [];
    redoStack = [];
    redraw();
    updateUndoRedoButtons();
    updateEmptyHint();
  }

  clearBtn.addEventListener("click", () => {
    if (strokes.length === 0) return;
    if (tg && tg.showConfirm) {
      tg.showConfirm("Butun rasmni tozalaymi?", (ok) => { if (ok) clearCanvas(); });
    } else if (window.confirm("Butun rasmni tozalaymi?")) {
      clearCanvas();
    }
  });

  // ============================================================
  // 🖼 SHABLON VARAG'I
  // ============================================================
  function openTemplateSheet() { templateSheet.classList.add("open"); }
  function closeTemplateSheet() { templateSheet.classList.remove("open"); }

  templateBtn.addEventListener("click", openTemplateSheet);
  templateSheet.querySelector(".sheet__backdrop").addEventListener("click", closeTemplateSheet);

  function buildTemplateGrid() {
    const dpr = window.devicePixelRatio || 1;
    const previewSize = 96;
    TEMPLATES.forEach((tpl) => {
      const card = document.createElement("div");
      card.className = "template-card" + (tpl.id === activeTemplateId ? " active" : "");

      const prevCanvas = document.createElement("canvas");
      prevCanvas.width = previewSize * dpr;
      prevCanvas.height = previewSize * dpr;
      const pctx = prevCanvas.getContext("2d");
      pctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      pctx.fillStyle = "#ffffff";
      pctx.fillRect(0, 0, previewSize, previewSize);
      if (tpl.draw) tpl.draw(pctx, previewSize, previewSize);

      const label = document.createElement("span");
      label.className = "template-card__label";
      label.textContent = tpl.label;

      card.appendChild(prevCanvas);
      card.appendChild(label);
      card.addEventListener("click", () => {
        activeTemplateId = tpl.id;
        templateGrid.querySelectorAll(".template-card").forEach((c) => c.classList.remove("active"));
        card.classList.add("active");
        const rect = wrap.getBoundingClientRect();
        renderTemplateBackground(bgCtx, rect.width, rect.height);
        closeTemplateSheet();
      });
      templateGrid.appendChild(card);
    });
  }

  buildTemplateGrid();
  resizeCanvases();
  updateUndoRedoButtons();
  updateEmptyHint();

  // ============================================================
  // YUBORISH
  // ============================================================
  function getRequestId() {
    const params = new URLSearchParams(window.location.search);
    return params.get("rid") || "";
  }

  sendBtn.addEventListener("click", async () => {
    if (strokes.length === 0) {
      statusMsg.textContent = "⚠️ Avval biror narsa chizing.";
      return;
    }
    const rid = getRequestId();
    if (!rid) {
      statusMsg.textContent = "❌ So'rov identifikatori topilmadi. /rasim buyrug'ini qayta yuboring.";
      return;
    }
    if (!tg || !tg.initData) {
      statusMsg.textContent = "❌ Telegram orqali ochilmagan. Iltimos Telegram ilovasidan foydalaning.";
      return;
    }

    sendBtn.disabled = true;
    statusMsg.textContent = "📤 Yuborilmoqda...";

    // Oq fon + tanlangan shablon + barcha chizmalarni bitta PNG'ga
    // birlashtiramiz (chizish paytidagi ikki qatlamli canvas shu yerda
    // bitta yakuniy rasmga "yopishtiriladi").
    const rect = wrap.getBoundingClientRect();
    const exportCanvas = document.createElement("canvas");
    const dpr = window.devicePixelRatio || 1;
    exportCanvas.width = rect.width * dpr;
    exportCanvas.height = rect.height * dpr;
    const exportCtx = exportCanvas.getContext("2d");
    exportCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    renderTemplateBackground(exportCtx, rect.width, rect.height);
    for (const s of strokes) drawStroke(exportCtx, s);

    const dataUrl = exportCanvas.toDataURL("image/png");

    try {
      const resp = await fetch("/miniapp/rasim/upload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rid: rid,
          init_data: tg.initData,
          image: dataUrl,
        }),
      });
      const result = await resp.json().catch(() => ({}));
      if (resp.ok && result.ok) {
        statusMsg.textContent = "✅ Yuborildi!";
        if (tg && tg.close) {
          setTimeout(() => tg.close(), 600);
        }
      } else {
        statusMsg.textContent = "❌ " + (result.error || "Yuborishda xatolik yuz berdi.");
        sendBtn.disabled = false;
      }
    } catch (e) {
      statusMsg.textContent = "❌ Tarmoq xatosi. Qayta urinib ko'ring.";
      sendBtn.disabled = false;
    }
  });
})();
