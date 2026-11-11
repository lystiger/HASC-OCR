// The site's own messages, field checks and loading screen, in Vietnamese, in place of the
// browser's alert(), English validation bubbles and silent waits. Without JavaScript every form
// still works and the browser's own checks apply (ADR 7); this script only adds `novalidate`.
(function () {
  "use strict";

  var region = null;

  function toastRegion() {
    if (!region) {
      region = document.createElement("div");
      region.className = "toast-region";
      region.setAttribute("aria-live", "polite");
      document.body.appendChild(region);
    }
    return region;
  }

  // kind: "info", "success", "warning" or "error".
  function toast(text, kind) {
    var item = document.createElement("div");
    item.className = "toast toast--" + (kind || "info");
    item.setAttribute("role", kind === "error" || kind === "warning" ? "alert" : "status");
    var message = document.createElement("span");
    message.textContent = text;
    var close = document.createElement("button");
    close.type = "button";
    close.className = "toast__close";
    close.setAttribute("aria-label", "Đóng");
    close.textContent = "×";
    close.addEventListener("click", function () { item.remove(); });
    item.appendChild(message);
    item.appendChild(close);
    toastRegion().appendChild(item);
    setTimeout(function () { item.remove(); }, kind === "error" ? 8000 : 5000);
  }

  var loadingTimer = null;

  function showLoading(text) {
    var overlay = document.querySelector(".loading-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "loading-overlay";
      overlay.setAttribute("role", "status");
      overlay.innerHTML = '<div class="loading-box"><span class="spinner" aria-hidden="true"></span><span class="loading-text"></span></div>';
      document.body.appendChild(overlay);
    }
    overlay.querySelector(".loading-text").textContent = text || "Đang xử lý...";
    overlay.hidden = false;
    // A hung network must not lock the page for good.
    clearTimeout(loadingTimer);
    loadingTimer = setTimeout(function () {
      hideLoading();
      toast("Mạng chậm hoặc mất kết nối. Xem danh sách đã gửi trước khi gửi lại.", "warning");
    }, 90000);
  }

  function hideLoading() {
    clearTimeout(loadingTimer);
    var overlay = document.querySelector(".loading-overlay");
    if (overlay) overlay.hidden = true;
  }

  function fieldName(field) {
    var label = field.id && document.querySelector('label[for="' + field.id + '"]');
    return label ? label.textContent.trim() : (field.getAttribute("aria-label") || "Trường này");
  }

  function problem(field) {
    var v = field.validity;
    if (v.valueMissing) return "Chưa nhập " + fieldName(field).toLowerCase();
    if (v.rangeUnderflow) return fieldName(field) + " phải từ " + field.min + " trở lên";
    if (v.rangeOverflow) return fieldName(field) + " tối đa là " + field.max;
    if (v.badInput || v.typeMismatch || v.stepMismatch) return fieldName(field) + " không hợp lệ";
    return "";
  }

  function clearError(field) {
    field.classList.remove("is-invalid");
    field.removeAttribute("aria-invalid");
    field.removeAttribute("aria-describedby");
    var note = field.parentElement.querySelector(".field-error");
    if (note) note.remove();
  }

  function markError(target, text) {
    target.classList.add("is-invalid");
    target.setAttribute("aria-invalid", "true");
    var note = target.parentElement.querySelector(".field-error");
    if (!note) {
      note = document.createElement("div");
      note.className = "field-error";
      note.id = "field-error-" + Math.random().toString(36).slice(2, 8);
      target.insertAdjacentElement("afterend", note);
    }
    note.textContent = text;
    target.setAttribute("aria-describedby", note.id);
  }

  // A form may add its own check: form.hascCheck = function () { return [element, message] or null }.
  function check(form) {
    var first = null;
    form.querySelectorAll("input, select, textarea").forEach(function (field) {
      if (!field.willValidate || field.type === "file") return;
      clearError(field);
      var text = problem(field);
      if (text) {
        markError(field, text);
        first = first || [field, text];
      }
    });
    if (typeof form.hascCheck === "function") {
      var extra = form.hascCheck();
      if (extra) {
        markError(extra[0], extra[1]);
        first = first || extra;
      }
    }
    return first;
  }

  // Says why the form cannot be sent yet, at its first wrong field; false when it can be sent.
  function refuse(form) {
    var bad = check(form);
    if (!bad) return false;
    toast(bad[1], "warning");
    // A group (the photo buttons) cannot take focus itself: focus its first control.
    var focusable = bad[0].matches("input, select, textarea, button") ? bad[0] : bad[0].querySelector("input, select, textarea, button");
    if (focusable) focusable.focus();
    return true;
  }

  // Runs on the page and again on every part htmx swaps in (the accounts screen after an add), which
  // would otherwise lose the Vietnamese checks and messages. Each box and form is set up once.
  function setUp(root) {
    // A message the server put on the page also pops up; the page copy stays for reading later.
    root.querySelectorAll("[data-toast]").forEach(function (box) {
      if (box.hascShown) return;
      box.hascShown = true;
      toast(box.textContent.replace(/\s+/g, " ").trim(), box.getAttribute("data-toast"));
    });
    root.querySelectorAll("form").forEach(function (form) {
      if (form.hascReady) return;
      form.hascReady = true;
      form.setAttribute("novalidate", "");
      function clearFor(event) { clearError(event.target.closest(".file-pick") || event.target); }
      form.addEventListener("input", clearFor);
      form.addEventListener("change", clearFor);
      form.addEventListener("submit", function (event) {
        // htmx has already sent (or, at htmx:confirm below, refused) its own forms by now.
        if (form.hasAttribute("hx-post")) {
          if (!check(form) && !form.hasAttribute("data-no-loading")) showLoading(form.getAttribute("data-loading"));
          return;
        }
        // Cancel in an onsubmit confirm() stops the submit: no loading screen.
        if (event.defaultPrevented) return;
        if (refuse(form)) {
          event.preventDefault();
          return;
        }
        // A form that navigates gets the loading screen; a download (data-no-loading) does not.
        if (!form.hasAttribute("data-no-loading")) showLoading(form.getAttribute("data-loading"));
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () { setUp(document); });
  document.addEventListener("htmx:load", function (event) { setUp(event.detail.elt); });
  // htmx sends its forms from a submit listener that runs before the one above, so an empty field
  // would reach the server: check here, where htmx still lets the request be cancelled.
  document.addEventListener("htmx:confirm", function (event) {
    var form = event.detail.elt;
    if (form.tagName === "FORM" && form.hascReady && refuse(form)) event.preventDefault();
  });

  // Coming back with the browser's Back button must not leave the loading screen up.
  window.addEventListener("pageshow", hideLoading);
  // An htmx form (adding an account) swaps the page in place instead of navigating: hide it when that
  // post ends. Only a post: the pages' polling (GET) must not close the screen of a running upload.
  // (detail.elt is not reliably the form: htmx may name the swap target instead, so use requestConfig.)
  function htmxPost(event, path) {
    var config = event.detail.requestConfig;
    return !!config && config.verb === "post" && (!path || config.path === path);
  }
  document.addEventListener("htmx:afterRequest", function (event) {
    if (htmxPost(event)) hideLoading();
  });
  // On the accounts screen a 400 is the page with the reason (a username already taken): show it.
  // htmx skips 4xx by default, which left the last temporary password on screen as if the new account
  // had been made. Elsewhere a 400 is JSON, which must not be swapped into the page.
  document.addEventListener("htmx:beforeSwap", function (event) {
    if (event.detail.xhr.status === 400 && htmxPost(event, "/admin/accounts")) {
      event.detail.shouldSwap = true;
      event.detail.isError = false;
    }
  });

  // After an htmx post the page is replaced where it stands; on a phone the add form is far down, so
  // the result (the one-time password, or the error) would sit off-screen above. Bring it into view.
  document.addEventListener("htmx:afterSettle", function (event) {
    if (!htmxPost(event)) return;
    var box = event.detail.target.querySelector(".temp-password-box, .alert");
    if (box) box.scrollIntoView({ block: "start" });
  });

  window.hascUI = { toast: toast, showLoading: showLoading, hideLoading: hideLoading };
})();
