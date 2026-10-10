/* The block editor: small helpers the other editor scripts share.
   Every editor script adds itself to window.sffEditor. Text only ever goes into the page through textContent,
   so a bot name, a file name or a log line can never become markup. */
(function () {
  "use strict";

  var E = (window.sffEditor = window.sffEditor || {});

  /* el("div", { class: "x", text: "hi", data: { id: "1" }, on: { click: fn } }, [child, "text"]) */
  E.el = function (tag, props, children) {
    var node = document.createElement(tag);
    var attrs = props || {};
    Object.keys(attrs).forEach(function (key) {
      var value = attrs[key];
      if (value === undefined || value === null || value === false) return;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "data") Object.keys(value).forEach(function (name) { node.dataset[name] = value[name]; });
      else if (key === "on") Object.keys(value).forEach(function (name) { node.addEventListener(name, value[name]); });
      else if (key === "style") node.style.cssText = value;
      else if (value === true) node.setAttribute(key, "");
      else node.setAttribute(key, String(value));
    });
    (children || []).forEach(function (child) {
      if (child === null || child === undefined || child === false) return;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return node;
  };

  /* A Phosphor Fill icon: E.icon("check") -> <i class="ph-fill ph-check">. */
  E.icon = function (name, extra) {
    var node = document.createElement("i");
    node.className = "ph-fill ph-" + name + (extra ? " " + extra : "");
    node.setAttribute("aria-hidden", "true");
    return node;
  };

  E.clear = function (node) {
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  };

  E.debounce = function (fn, ms) {
    var timer = null;
    function run() {
      var args = arguments;
      var self = this;
      clearTimeout(timer);
      timer = setTimeout(function () {
        timer = null;
        fn.apply(self, args);
      }, ms);
    }
    run.cancel = function () {
      clearTimeout(timer);
      timer = null;
    };
    return run;
  };

  E.plural = function (count, one, many) {
    return count + " " + (count === 1 ? one : many || one + "s");
  };

  E.formatBytes = function (bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 102.4) / 10 + " KB";
    return Math.round(bytes / (1024 * 102.4)) / 10 + " MB";
  };

  E.timeAgo = function (iso) {
    var then = Date.parse(iso);
    if (isNaN(then)) return "";
    var seconds = Math.max(0, (Date.now() - then) / 1000);
    if (seconds < 45) return "just now";
    if (seconds < 3600) return E.plural(Math.max(1, Math.round(seconds / 60)), "minute") + " ago";
    if (seconds < 86400) return E.plural(Math.round(seconds / 3600), "hour") + " ago";
    return E.plural(Math.round(seconds / 86400), "day") + " ago";
  };

  /* Remembered choices (panel sizes, the open tab). Per person and per browser, and the editor works without it. */
  E.store = {
    get: function (key, fallback) {
      try {
        var raw = window.localStorage.getItem("sff." + key);
        return raw === null ? fallback : JSON.parse(raw);
      } catch (_) {
        return fallback;
      }
    },
    set: function (key, value) {
      try {
        window.localStorage.setItem("sff." + key, JSON.stringify(value));
      } catch (_) {
        /* private window or storage turned off: nothing to remember with */
      }
    },
  };

  /* A very small event bus: E.bus.on("select", fn); E.bus.emit("select", id). */
  var listeners = {};
  E.bus = {
    on: function (name, fn) {
      (listeners[name] = listeners[name] || []).push(fn);
    },
    emit: function (name, payload) {
      (listeners[name] || []).slice().forEach(function (fn) {
        try {
          fn(payload);
        } catch (error) {
          if (window.console) console.error("[editor] a " + name + " listener failed", error);
        }
      });
    },
  };

  /* Wait for the fonts the blocks are measured with. Blockly sizes a block from its text when it draws it, so a font
     that arrives later would leave every block the wrong width. */
  E.fontsReady = function () {
    if (!document.fonts || !document.fonts.load) return Promise.resolve();
    var wanted = [
      '600 12px "IBM Plex Sans"',
      '400 12px "IBM Plex Sans"',
      '400 12px "IBM Plex Mono"',
      '400 12px "Phosphor-Fill"',
    ];
    var loads = wanted.map(function (font) {
      return document.fonts.load(font).catch(function () {});
    });
    var timeout = new Promise(function (resolve) { setTimeout(resolve, 3000); });
    return Promise.race([Promise.all(loads).then(function () { return document.fonts.ready; }), timeout]);
  };
})();
