(function () {
  "use strict";

  const tg = window.Telegram ? window.Telegram.WebApp : null;
  if (tg) {
    tg.ready();
    tg.expand();
  }

  const canvas = document.getElementById("canvas");
  const ctx = canvas.getContext("2d");
  const wrap = document.getElementById("canvasWrap");
  const brushSizeInput = document.getElementById("brushSize");
  const eraserBtn = document.getElementById("eraserBtn");
  const undoBtn = document.getElementById("undoBtn");
  const redoBtn = document.getElementById("redoBtn");
  const clearBtn = document.getElementById("clearBtn");
  const sendBtn = document.getElementById("sendBtn");
  const statusMsg = document.getElementById("statusMsg");
  const colorsWrap = document.getElementById("colors");

  const PALETTE = ["#111111", "#e03131", "#2f9e44", "#1971c2", "#f08c00", "#9c36b5", "#ffffff"];
  let currentColor = PALETTE[0];
  let isEraser = false;

  // --- Rang tanlash paneli ---
  PALETTE.forEach((color, i) => {
    const sw = document.createElement("div");
    sw.className = "color-swatch" + (i === 0 ? " active" : "");
    sw.style.background = color;
    if (color === "#ffffff") sw.style.boxShadow = "0 0 0 1px rgba(0,0,0,0.3)";
    sw.addEventListener("click", () => {
      currentColor = color;
      isEraser = false;
      eraserBtn.classList.remove("active");
      [...colorsWrap.children].forEach((c) => c.classList.remove("active"));
      sw.classList.add("active");
    });
    colorsWrap.appendChild(sw);
  });

  // --- Strokes (undo/redo uchun) ---
  let strokes = [];
  let redoStack = [];
  let currentStroke = null;

  function resizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const rect = wrap.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    canvas.style.width = rect.width + "px";
    canvas.style.height = rect.height + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    redraw();
  }

  function redraw() {
    const rect = wrap.getBoundingClientRect();
    ctx.clearRect(0, 0, rect.width, rect.height);
    for (const stroke of strokes) {
      drawStroke(ctx, stroke);
    }
  }

  function drawStroke(context, stroke) {
    if (stroke.points.length === 0) return;
    context.save();
    context.lineCap = "round";
    context.lineJoin = "round";
    context.lineWidth = stroke.size;
    context.globalCompositeOperation = stroke.isEraser ? "destination-out" : "source-over";
    context.strokeStyle = stroke.color;
    context.beginPath();
    context.moveTo(stroke.points[0].x, stroke.points[0].y);
    if (stroke.points.length === 1) {
      // Bitta nuqta (bosib-qo'yib yuborish) — kichik doira sifatida chizamiz.
      context.lineTo(stroke.points[0].x + 0.01, stroke.points[0].y + 0.01);
    }
    for (let i = 1; i < stroke.points.length; i++) {
      context.lineTo(stroke.points[i].x, stroke.points[i].y);
    }
    context.stroke();
    context.restore();
  }

  function getPos(evt) {
    const rect = canvas.getBoundingClientRect();
    const point = evt.touches ? evt.touches[0] : evt;
    return { x: point.clientX - rect.left, y: point.clientY - rect.top };
  }

  function startDraw(evt) {
    evt.preventDefault();
    const pos = getPos(evt);
    currentStroke = {
      color: currentColor,
      size: parseInt(brushSizeInput.value, 10),
      isEraser: isEraser,
      points: [pos],
    };
    redoStack = []; // yangi chizish redo tarixini bekor qiladi
    updateUndoRedoButtons();
  }

  function moveDraw(evt) {
    if (!currentStroke) return;
    evt.preventDefault();
    const pos = getPos(evt);
    currentStroke.points.push(pos);
    drawStroke(ctx, { ...currentStroke, points: currentStroke.points.slice(-2) });
  }

  function endDraw() {
    if (!currentStroke) return;
    strokes.push(currentStroke);
    currentStroke = null;
    updateUndoRedoButtons();
  }

  canvas.addEventListener("mousedown", startDraw);
  canvas.addEventListener("mousemove", moveDraw);
  window.addEventListener("mouseup", endDraw);

  canvas.addEventListener("touchstart", startDraw, { passive: false });
  canvas.addEventListener("touchmove", moveDraw, { passive: false });
  canvas.addEventListener("touchend", endDraw);
  canvas.addEventListener("touchcancel", endDraw);

  window.addEventListener("resize", resizeCanvas);
  resizeCanvas();

  // --- Asboblar ---
  brushSizeInput.addEventListener("input", () => {});

  eraserBtn.addEventListener("click", () => {
    isEraser = !isEraser;
    eraserBtn.classList.toggle("active", isEraser);
  });

  undoBtn.addEventListener("click", () => {
    if (strokes.length === 0) return;
    redoStack.push(strokes.pop());
    redraw();
    updateUndoRedoButtons();
  });

  redoBtn.addEventListener("click", () => {
    if (redoStack.length === 0) return;
    strokes.push(redoStack.pop());
    redraw();
    updateUndoRedoButtons();
  });

  clearBtn.addEventListener("click", () => {
    if (strokes.length === 0) return;
    if (tg && tg.showConfirm) {
      tg.showConfirm("Butun rasmni tozalaymi?", (ok) => {
        if (ok) {
          strokes = [];
          redoStack = [];
          redraw();
          updateUndoRedoButtons();
        }
      });
    } else {
      strokes = [];
      redoStack = [];
      redraw();
      updateUndoRedoButtons();
    }
  });

  function updateUndoRedoButtons() {
    undoBtn.style.opacity = strokes.length === 0 ? "0.4" : "1";
    redoBtn.style.opacity = redoStack.length === 0 ? "0.4" : "1";
  }
  updateUndoRedoButtons();

  // --- Yuborish ---
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

    // Oq fon bilan yakuniy PNG tayyorlaymiz (checkerboard preview emas).
    const rect = wrap.getBoundingClientRect();
    const exportCanvas = document.createElement("canvas");
    const dpr = window.devicePixelRatio || 1;
    exportCanvas.width = rect.width * dpr;
    exportCanvas.height = rect.height * dpr;
    const exportCtx = exportCanvas.getContext("2d");
    exportCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    exportCtx.fillStyle = "#ffffff";
    exportCtx.fillRect(0, 0, rect.width, rect.height);
    for (const stroke of strokes) {
      drawStroke(exportCtx, stroke);
    }

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
