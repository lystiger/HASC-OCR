// Review screen (P154): photo viewer, tabs, the editable table, and Lưu / Xác nhận.
(function () {
  "use strict";
  var root = document.querySelector("[data-review]");
  if (!root) return;
  var ui = window.hascUI || { toast: function () {}, showLoading: function () {}, hideLoading: function () {} };

  // --- Photo viewer: zoom (buttons, wheel), drag to pan, rotate, switch photos -------------------
  var stage = root.querySelector("[data-stage]");
  var img = root.querySelector("[data-photo]");
  var label = root.querySelector("[data-zoom-label]");
  var view = { scale: 1, fit: 1, cover: 1, rotate: 0, x: 0, y: 0 };
  var MIN = 0.25, MAX = 6;  // of the default (filled) view

  function size() {  // the photo's on-screen size at the current scale and turn
    var turned = view.rotate % 180 !== 0;
    return { w: (turned ? img.naturalHeight : img.naturalWidth) * view.scale,
             h: (turned ? img.naturalWidth : img.naturalHeight) * view.scale };
  }
  function clamp() {
    // No empty space inside the frame: a photo larger than the frame keeps its edges at the frame's
    // edges; a smaller one (Vừa khung, or zoomed out) sits in the middle.
    var shown = size(), sw = stage.clientWidth, sh = stage.clientHeight;
    var cx = view.x + img.naturalWidth / 2, cy = view.y + img.naturalHeight / 2;
    cx = shown.w <= sw ? sw / 2 : Math.min(shown.w / 2, Math.max(sw - shown.w / 2, cx));
    cy = shown.h <= sh ? sh / 2 : Math.min(shown.h / 2, Math.max(sh - shown.h / 2, cy));
    view.x = cx - img.naturalWidth / 2;
    view.y = cy - img.naturalHeight / 2;
  }
  function draw() {
    clamp();
    img.style.transform = "translate(" + view.x + "px," + view.y + "px) rotate(" + view.rotate + "deg) scale(" + view.scale + ")";
    if (label) label.textContent = Math.round(view.scale / view.cover * 100) + "%";
  }
  function measure() {
    var turned = view.rotate % 180 !== 0;
    var w = turned ? img.naturalHeight : img.naturalWidth;
    var h = turned ? img.naturalWidth : img.naturalHeight;
    root.style.setProperty("--photo-ratio", w / h);  // the frame takes the photo's shape (CSS), then we size to it
    view.fit = Math.min(stage.clientWidth / w, stage.clientHeight / h);
    view.cover = Math.max(stage.clientWidth / w, stage.clientHeight / h);
  }
  function fill() {  // the default: the photo fills the frame, starting at the sheet's top-left
    if (!stage || !img.naturalWidth) return;
    measure();
    view.scale = view.cover;
    view.x = 1e6;  // clamp() moves it back to the left and top edges
    view.y = 1e6;
    draw();
  }
  function fit() {  // Vừa khung: the whole page in view
    if (!stage || !img.naturalWidth) return;
    measure();
    view.scale = view.fit;
    draw();
  }
  function zoomAt(factor, cx, cy) {
    var next = Math.min(MAX * view.cover, Math.max(Math.min(MIN * view.cover, view.fit), view.scale * factor));
    var ratio = next / view.scale;
    // Keep the point under the cursor in place: the image's centre moves away from it by `ratio`.
    var centreX = view.x + img.naturalWidth / 2, centreY = view.y + img.naturalHeight / 2;
    view.x += (centreX - cx) * (ratio - 1);
    view.y += (centreY - cy) * (ratio - 1);
    view.scale = next;
    draw();
  }
  if (stage && img) {
    img.addEventListener("load", fill);
    if (img.complete) fill();
    window.addEventListener("resize", fill);
    root.querySelectorAll("[data-zoom]").forEach(function (button) {
      button.addEventListener("click", function () {
        var kind = button.getAttribute("data-zoom");
        if (kind === "fit") return fit();
        zoomAt(kind === "in" ? 1.25 : 0.8, stage.clientWidth / 2, stage.clientHeight / 2);
      });
    });
    root.querySelector("[data-rotate]").addEventListener("click", function () {
      view.rotate = (view.rotate + 90) % 360;
      fill();
    });
    stage.addEventListener("wheel", function (event) {
      event.preventDefault();
      var box = stage.getBoundingClientRect();
      zoomAt(event.deltaY < 0 ? 1.15 : 1 / 1.15, event.clientX - box.left, event.clientY - box.top);
    }, { passive: false });
    var drag = null;
    stage.addEventListener("pointerdown", function (event) {
      drag = { x: event.clientX - view.x, y: event.clientY - view.y };
      stage.setPointerCapture(event.pointerId);
      stage.classList.add("is-dragging");
    });
    stage.addEventListener("pointermove", function (event) {
      if (!drag) return;
      view.x = event.clientX - drag.x;
      view.y = event.clientY - drag.y;
      draw();
    });
    function endDrag() { drag = null; stage.classList.remove("is-dragging"); }
    stage.addEventListener("pointerup", endDrag);
    stage.addEventListener("pointercancel", endDrag);
    stage.addEventListener("keydown", function (event) {  // keyboard: arrows pan, +/- zoom, 0 back to the default view
      var step = 40, keys = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
      if (keys[event.key]) { view.x += keys[event.key][0]; view.y += keys[event.key][1]; draw(); }
      else if (event.key === "+" || event.key === "=") zoomAt(1.25, stage.clientWidth / 2, stage.clientHeight / 2);
      else if (event.key === "-") zoomAt(0.8, stage.clientWidth / 2, stage.clientHeight / 2);
      else if (event.key === "0") fill();
      else return;
      event.preventDefault();
    });
    root.querySelectorAll("[data-photo-src]").forEach(function (button) {
      button.addEventListener("click", function () {
        root.querySelectorAll("[data-photo-src]").forEach(function (other) {
          other.classList.toggle("is-active", other === button);
          other.setAttribute("aria-pressed", other === button ? "true" : "false");
        });
        view.rotate = 0;
        img.src = button.getAttribute("data-photo-src");
      });
    });
  }

  // --- Tabs ------------------------------------------------------------------------------------
  var tabs = Array.prototype.slice.call(document.querySelectorAll('[role="tab"]'));
  function select(tab) {
    tabs.forEach(function (other) {
      var on = other === tab;
      other.setAttribute("aria-selected", on ? "true" : "false");
      other.tabIndex = on ? 0 : -1;
      document.getElementById(other.getAttribute("aria-controls")).hidden = !on;
    });
    var rollsOnly = document.querySelector("[data-rolls-only]");  // legend and flag jump belong to the table
    if (rollsOnly) rollsOnly.hidden = tab.id !== "tab-rolls";
    tab.focus();
  }
  tabs.forEach(function (tab, index) {
    tab.addEventListener("click", function () { select(tab); });
    tab.addEventListener("keydown", function (event) {
      var move = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (!move) return;
      event.preventDefault();
      select(tabs[(index + move + tabs.length) % tabs.length]);
    });
  });

  // --- Table: edits, number checks, Enter moves down, F8 jumps to the next flagged cell -----------
  var inputs = Array.prototype.slice.call(root.querySelectorAll("input.cell"));
  var flagged = inputs.filter(function (input) { return input.hasAttribute("data-flag"); });
  var counter = root.querySelector("[data-flag-count]");
  var NUMBER = /^-?\d+([.,]\d+)?$/;

  function edited(input) { return input.value !== input.getAttribute("data-orig"); }
  function dirty() { return inputs.some(edited); }
  function updateCount() {
    if (!counter) return;
    var left = flagged.filter(function (input) { return !input.classList.contains("is-seen") && !edited(input); }).length;
    counter.textContent = left + "/" + flagged.length;
    counter.setAttribute("aria-label", "Còn " + left + " trên " + flagged.length + " ô cần kiểm tra");
  }
  function check(input, confirming) {
    // An untouched mark the AI left (its unreadable "?") may stay for Lưu; Xác nhận needs a number.
    var bad = input.hasAttribute("data-numeric") && input.value.trim() !== "" && !NUMBER.test(input.value.trim())
      && (confirming || edited(input));
    input.classList.toggle("is-invalid", bad);
    input.setAttribute("aria-invalid", bad ? "true" : "false");
    return !bad;
  }
  inputs.forEach(function (input, index) {
    input.addEventListener("input", function () {
      input.classList.toggle("is-edited", edited(input));
      // A typed value replaces a copied-down one, so it stops looking grey.
      input.classList.toggle("is-inherited", input.classList.contains("was-inherited") && !edited(input));
      check(input);
      updateCount();
    });
    if (input.classList.contains("is-inherited")) input.classList.add("was-inherited");
    input.addEventListener("focus", function () {
      if (input.hasAttribute("data-flag")) { input.classList.add("is-seen"); updateCount(); }
    });
    input.addEventListener("keydown", function (event) {
      if (event.key !== "Enter") return;
      event.preventDefault();
      var columns = input.closest("tr").querySelectorAll("input.cell").length;
      var target = inputs[index + (event.shiftKey ? -columns : columns)];
      if (target) target.focus();
    });
  });
  function nextFlag() {
    if (!flagged.length) return;
    var current = flagged.indexOf(document.activeElement);
    for (var step = 1; step <= flagged.length; step++) {
      var candidate = flagged[(current + step) % flagged.length];
      if (!candidate.classList.contains("is-seen") || step === flagged.length) {
        select(document.getElementById("tab-rolls"));
        candidate.focus();
        candidate.select();
        candidate.scrollIntoView({ block: "nearest", inline: "nearest" });
        return;
      }
    }
  }
  var nextButton = root.querySelector("[data-next-flag]") || document.querySelector("[data-next-flag]");
  if (nextButton) nextButton.addEventListener("click", nextFlag);
  document.addEventListener("keydown", function (event) {
    if (event.key === "F8") { event.preventDefault(); nextFlag(); }
  });
  updateCount();

  // --- Lưu / Xác nhận ----------------------------------------------------------------------------
  var saving = false;
  window.addEventListener("beforeunload", function (event) {
    if (!saving && dirty()) { event.preventDefault(); event.returnValue = ""; }
  });
  function focusProblem(message) {
    // Server messages name the cell as "dòng 3 · Dày/mic"; the inputs' labels start with "Dòng 3 · Dày/mic".
    var match = /dòng (\d+) · ([^"]+?) phải/i.exec(message);
    if (!match) return;
    var want = ("dòng " + match[1] + " · " + match[2]).toLowerCase();
    var target = inputs.filter(function (input) { return input.getAttribute("aria-label").toLowerCase().indexOf(want) === 0; })[0];
    if (target) { select(document.getElementById("tab-rolls")); target.focus(); check(target, true); }
  }
  function send(confirm) {
    var bad = inputs.filter(function (input) { return !check(input, confirm); });
    if (bad.length) {
      ui.toast("Ô " + bad[0].getAttribute("aria-label") + " phải là số.", "error");
      select(document.getElementById("tab-rolls"));
      bad[0].focus();
      return;
    }
    var keys = [], rows = [];
    root.querySelectorAll("tbody tr[data-row-key]").forEach(function (tr) {
      keys.push(tr.getAttribute("data-row-key"));
      var row = {};
      tr.querySelectorAll("input.cell").forEach(function (input) { row[input.getAttribute("data-field")] = input.value; });
      rows.push(row);
    });
    var buttons = document.querySelectorAll("[data-review-save], [data-review-confirm]");
    buttons.forEach(function (b) { b.disabled = true; });
    ui.showLoading(confirm ? "Đang xác nhận..." : "Đang lưu...");
    fetch("/api/sheets/" + root.getAttribute("data-sheet-id") + "/review", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ version: Number(root.getAttribute("data-version")), row_keys: keys, rows: rows, confirm: confirm })
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) { return { ok: response.ok, status: response.status, body: body }; });
    }).then(function (result) {
      if (result.ok) {
        saving = true;
        window.location.href = window.location.pathname + (confirm ? "?confirmed=1" : "?saved=" + result.body.changed);
        return;
      }
      ui.hideLoading();
      buttons.forEach(function (b) { b.disabled = false; });
      var message = (result.body.error && result.body.error.message) || result.body.message || result.body.detail || "Không lưu được. Hãy thử lại.";
      if (result.status === 409 && /thay đổi/.test(message)) {
        message = "Biểu mẫu đã được người khác hoặc AI thay đổi từ lúc bạn mở trang. Hãy tải lại trang (các ô bạn vừa sửa sẽ mất).";
      }
      ui.toast(message, "error");
      if (result.status === 422) focusProblem(message);
    }).catch(function () {
      ui.hideLoading();
      buttons.forEach(function (b) { b.disabled = false; });
      ui.toast("Mất kết nối tới máy chủ. Chưa lưu được; các ô đã sửa vẫn còn trên trang.", "error");
    });
  }
  var save = document.querySelector("[data-review-save]");
  if (save) save.addEventListener("click", function () { send(false); });
  var confirmForm = document.querySelector("[data-review-confirm-form]");
  if (confirmForm) confirmForm.addEventListener("submit", function (event) {
    // Edited or not, Xác nhận goes through the same save-and-confirm; the plain form is the no-script fallback.
    event.preventDefault();
    event.stopImmediatePropagation();  // ui.js would show its own loading for the plain post
    send(true);
  }, true);
})();
