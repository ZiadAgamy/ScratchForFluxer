/* Scratch For Fluxer - small shared helpers (no dependencies).
   Exposes window.sff = { api, toast, copyText, csrf }. */
(function () {
  "use strict";

  var meta = document.querySelector('meta[name="csrf-token"]');
  var csrf = meta ? meta.getAttribute("content") || "" : "";

  function apiError(status, message, data) {
    var err = new Error(message);
    err.name = "ApiError";
    err.status = status;
    err.data = data || null;
    return err;
  }

  /**
   * fetch() wrapper for our JSON API.
   * Adds the CSRF header to state-changing calls, sends/reads JSON, and turns failures into
   * an ApiError whose .message is safe to show to the person using the site.
   */
  async function api(path, opts) {
    opts = opts || {};
    var method = (opts.method || "GET").toUpperCase();
    var headers = Object.assign({ Accept: "application/json" }, opts.headers || {});
    var init = { method: method, headers: headers, credentials: "same-origin", signal: opts.signal };
    if (opts.keepalive) init.keepalive = true; /* lets a last save finish while the page is closing */

    if (method !== "GET" && method !== "HEAD") headers["X-CSRF-Token"] = csrf;
    if (opts.body !== undefined) {
      if (opts.body instanceof FormData || opts.body instanceof Blob || typeof opts.body === "string") {
        init.body = opts.body;
      } else {
        headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(opts.body);
      }
    }

    var res;
    try {
      res = await fetch(path, init);
    } catch (err) {
      if (err && err.name === "AbortError") throw err;
      throw apiError(0, "Can't reach the server. Check your connection and try again.");
    }

    var data = null;
    if ((res.headers.get("content-type") || "").indexOf("application/json") !== -1) {
      try {
        data = await res.json();
      } catch (_) {
        data = null;
      }
    }

    if (!res.ok) {
      if (res.status === 401) {
        window.location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
        throw apiError(401, "Please log in again.", data);
      }
      var message = data && typeof data.detail === "string" ? data.detail : "Something went wrong (" + res.status + ").";
      throw apiError(res.status, message, data);
    }
    return data;
  }

  var toastRegion = null;
  var TOAST_ICONS = { ok: "ph-check-circle", error: "ph-warning-circle", warn: "ph-warning", info: "ph-info" };

  function toast(message, kind, ms) {
    kind = TOAST_ICONS[kind] ? kind : "info";
    if (!toastRegion) {
      toastRegion = document.createElement("div");
      toastRegion.className = "toasts";
      toastRegion.setAttribute("role", "status");
      toastRegion.setAttribute("aria-live", "polite");
      document.body.appendChild(toastRegion);
    }
    var el = document.createElement("div");
    el.className = "toast toast-" + kind;
    var icon = document.createElement("i");
    icon.className = "ph-fill " + TOAST_ICONS[kind];
    icon.setAttribute("aria-hidden", "true");
    var text = document.createElement("span");
    text.textContent = message;
    el.appendChild(icon);
    el.appendChild(text);
    toastRegion.appendChild(el);
    setTimeout(function () {
      el.classList.add("is-leaving");
      setTimeout(function () {
        el.remove();
      }, 260);
    }, ms || 4200);
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (_) {
      /* fall through to the textarea fallback */
    }
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (_) {
      ok = false;
    }
    area.remove();
    return ok;
  }

  /* Account menu: close on outside click or Escape. */
  document.addEventListener("click", function (event) {
    document.querySelectorAll("details.menu[open]").forEach(function (menu) {
      if (!menu.contains(event.target)) menu.removeAttribute("open");
    });
  });
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    document.querySelectorAll("details.menu[open]").forEach(function (menu) {
      menu.removeAttribute("open");
      var summary = menu.querySelector("summary");
      if (summary) summary.focus();
    });
  });

  /* Avatars come from Fluxer's CDN. If one can't load, swap in a plain placeholder (once). */
  document.addEventListener(
    "error",
    function (event) {
      var img = event.target;
      if (!(img instanceof HTMLImageElement) || !img.hasAttribute("data-fallback") || img.dataset.fellBack) return;
      img.dataset.fellBack = "1";
      img.src = "/static/img/avatar-fallback.svg";
    },
    true
  );

  /* <button data-copy="text"> copies that text. */
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy]");
    if (!button) return;
    copyText(button.getAttribute("data-copy") || "").then(function (ok) {
      toast(ok ? "Copied" : "Couldn't copy. Select the text and copy it by hand.", ok ? "ok" : "error", 1800);
    });
  });

  window.sff = { api: api, toast: toast, copyText: copyText, csrf: csrf };
})();
