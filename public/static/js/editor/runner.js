/* Run, Restart and Stop, and the console that goes with them.

   The editor never talks to the bot itself. It asks the site to start the bot, and then asks (about once a second while
   the bot is running) for the state of the run and for the lines the bot wrote since the last answer:

     POST /api/bots/{id}/run        start it (nothing happens if it is already running)
     POST /api/bots/{id}/restart    stop it if it is running, then start it again
     POST /api/bots/{id}/stop       stop it
     GET  /api/bots/{id}/run?after=N&run=ID    what happened after line N of run ID

   An answer is { run, state, reason, ready, started_at, ended_at, now, lines: [{ n, t, level, source, text, hint }],
   next, skipped, reset }. `state` is none | starting | running | stopping | stopped | crashed. A bot keeps running when
   this page is closed, so a page that is opened later picks the run up again. */
(function () {
  "use strict";

  var E = window.sffEditor;
  var sff = window.sff;

  var POLL_MS = 1000;
  var POLL_HIDDEN_MS = 8000;
  var OFF_NOTE = "Running bots isn't switched on for this site. You can still download your code and run it yourself.";

  var cfg = null;
  var refs = {};
  var base = ""; /* /api/bots/{id}  ->  /run (start, and read), /restart, /stop */
  var s = {
    mode: "off",
    state: "none",
    run: null,
    after: 0,
    busy: false,
    pending: "",
    epoch: 0,
    timer: null,
    ticker: null,
    polling: false,
    failures: 0,
    unreachable: false,
    reason: "",
    elapsed: 0,
    received: 0,
  };

  function live() {
    return s.state === "starting" || s.state === "running";
  }

  function polled() {
    return live() || s.state === "stopping";
  }

  // ------------------------------------------------------------------------------------------------ what the buttons say
  function duration(seconds) {
    var total = Math.max(0, Math.floor(seconds));
    var hours = Math.floor(total / 3600);
    var minutes = Math.floor((total % 3600) / 60);
    var rest = total % 60;
    if (hours) return hours + "h " + (minutes < 10 ? "0" : "") + minutes + "m";
    if (minutes) return minutes + "m " + (rest < 10 ? "0" : "") + rest + "s";
    return rest + "s";
  }

  function label() {
    if (s.busy) return { name: "starting", text: s.pending };
    if (s.unreachable && polled()) return { name: "error", text: "Can't reach the runner. Trying again…" };
    switch (s.state) {
      case "starting":
        return { name: "starting", text: "Starting…" };
      case "running": {
        var seconds = s.elapsed + (performance.now() - s.received) / 1000;
        return { name: "running", text: "Running · " + duration(seconds) };
      }
      case "stopping":
        return { name: "stopping", text: "Stopping…" };
      case "stopped":
        return { name: "stopped", text: "Stopped" };
      case "crashed":
        return { name: "crashed", text: "Crashed" };
      default:
        return { name: "idle", text: "Not running" };
    }
  }

  function updateLabel() {
    var now = label();
    E.console.setState(now.name, now.text, s.busy ? "" : s.reason);
  }

  /* Screen readers hear what happened, not the clock that counts up. */
  var ANNOUNCE = {
    starting: "Your bot is starting.",
    running: "Your bot is running.",
    stopped: "Your bot stopped.",
    crashed: "Your bot stopped because of a problem. Read the console.",
  };

  function announce(from, to) {
    if (from === to || !ANNOUNCE[to] || !refs.announce) return;
    refs.announce.textContent = ANNOUNCE[to];
  }

  function render() {
    var off = s.mode === "off";
    var compile = cfg && cfg.compile();
    var blocked = !!compile && !compile.ok;
    refs.run.disabled = off || s.busy || live() || s.state === "stopping";
    refs.restart.disabled = off || s.busy || !live();
    refs.stop.disabled = off || s.busy || !live();
    refs.run.classList.toggle("is-blocked", blocked);
    refs.restart.classList.toggle("is-blocked", blocked);
    refs.run.title = off ? OFF_NOTE : blocked ? "Fix the problems listed in the editor first" : "Run your bot";
    refs.restart.title = off ? OFF_NOTE : blocked ? "Fix the problems listed in the editor first" : "Stop the bot and start it again with your latest blocks";
    refs.stop.title = off ? OFF_NOTE : "Stop your bot";
    updateLabel();
    if (s.state === "running" && !s.busy) {
      if (!s.ticker) s.ticker = setInterval(updateLabel, 1000);
    } else if (s.ticker) {
      clearInterval(s.ticker);
      s.ticker = null;
    }
  }

  // ------------------------------------------------------------------------------------------------ "Show block"
  function hashOf(text) {
    var h = 5381;
    for (var i = 0; i < text.length; i += 1) h = ((h << 5) + h + text.charCodeAt(i)) | 0;
    return h + ":" + text.length;
  }

  function remember(run, compile) {
    if (compile && compile.code) E.store.set("run-" + cfg.botId, { run: run, code: hashOf(compile.code) });
  }

  /* The block that wrote line `line` of main.py, as long as the blocks still make the same code the run started with
     (once they have changed, the line numbers of the error mean nothing to them). */
  function locate(line) {
    var compile = cfg && cfg.compile();
    var saved = E.store.get("run-" + cfg.botId, null);
    if (!compile || !compile.lines || !compile.code || !saved || saved.run !== s.run || saved.code !== hashOf(compile.code)) {
      return null;
    }
    var best = null;
    var size = Infinity;
    Object.keys(compile.lines).forEach(function (id) {
      var range = compile.lines[id];
      if (line >= range[0] && line <= range[1] && range[1] - range[0] < size) {
        best = id;
        size = range[1] - range[0];
      }
    });
    return best;
  }

  // ------------------------------------------------------------------------------------------------ what the server says
  function toEntry(line) {
    return { level: line.level, text: line.text, time: line.t * 1000, source: line.source, hint: line.hint };
  }

  function apply(view) {
    if (view.state === "none") {
      if (s.run) {
        E.console.add({
          level: "system",
          text: "The runner no longer has this run. It was probably restarted, and that stops every bot.",
        });
      }
      s.run = null;
      s.state = "none";
      s.reason = "";
      render();
      return;
    }
    if (view.run !== s.run || view.reset) {
      E.console.clear();
      s.after = 0;
      s.run = view.run;
    }
    var fresh = view.lines.filter(function (line) {
      return line.n > s.after;
    });
    if (view.skipped > 0 && s.after === 0 && fresh.length) {
      E.console.add({
        level: "system",
        text: "The first " + E.plural(view.skipped, "message") + " of this run are not shown. The console keeps the latest 1000.",
      });
    } else if (view.skipped > 0 && s.after > 0) {
      E.console.add({ level: "system", text: E.plural(view.skipped, "message") + " were left out because the bot wrote too many." });
    }
    E.console.addMany(fresh.map(toEntry));
    s.after = Math.max(s.after, view.next);

    var was = s.state;
    s.state = view.state;
    announce(was, view.state);
    s.reason = view.reason || "";
    if (typeof view.now === "number" && typeof view.started_at === "number") {
      s.elapsed = Math.max(0, view.now - view.started_at);
      s.received = performance.now();
    }
    if ((was === "starting" || was === "running" || was === "stopping") && view.state === "crashed") {
      sff.toast("Your bot stopped because of a problem. The console says what happened.", "error", 6500);
    }
    render();
  }

  function problem(error, fallback) {
    var message = (error && error.message) || fallback;
    if (error && error.name === "ApiError" && error.status === 429) {
      message = "That was too many presses in a short time. Wait a few seconds and try again.";
    }
    sff.toast(message, "error", 6500);
    E.console.add({ level: "error", source: "runner", text: message });
  }

  // ------------------------------------------------------------------------------------------------ polling
  function delay() {
    if (s.unreachable) return Math.min(15000, POLL_MS * Math.pow(2, Math.min(s.failures, 4)));
    return document.hidden ? POLL_HIDDEN_MS : POLL_MS;
  }

  function schedule(ms) {
    clearTimeout(s.timer);
    s.timer = setTimeout(poll, ms);
  }

  async function poll() {
    if (s.polling || s.busy) return;
    s.polling = true;
    var epoch = s.epoch;
    var again = true;
    try {
      var query = "?after=" + s.after + (s.run ? "&run=" + encodeURIComponent(s.run) : "");
      var view = await sff.api(base + "/run" + query);
      if (epoch !== s.epoch) return; /* a press of Run or Stop came in meanwhile: that answer is newer */
      s.failures = 0;
      s.unreachable = false;
      apply(view);
    } catch (error) {
      if (epoch !== s.epoch) return;
      if (error && error.status === 404) {
        again = false;
        return;
      }
      s.failures += 1;
      s.unreachable = true;
      if (error && error.status === 429) s.failures = Math.max(s.failures, 3);
      render();
    } finally {
      s.polling = false;
      if (again && epoch === s.epoch && polled()) schedule(delay());
    }
  }

  // ------------------------------------------------------------------------------------------------ the three buttons
  async function start(restart) {
    if (s.busy || s.mode === "off") return;
    var compile = cfg.compile();
    if (compile && !compile.ok) {
      sff.toast("Fix the problems listed in the editor first. Blocks with errors can't run.", "warn", 5000);
      cfg.showProblems();
      return;
    }
    s.busy = true;
    s.epoch += 1;
    s.pending = restart && live() ? "Restarting…" : "Starting…";
    render();
    try {
      var saved = await cfg.flush();
      if (!saved) {
        sff.toast("Your latest changes couldn't be saved yet, so the bot wasn't started. Check the label at the top and try again.", "warn", 6500);
        return;
      }
      compile = cfg.compile();
      if (compile && !compile.ok) {
        sff.toast("Fix the problems listed in the editor first. Blocks with errors can't run.", "warn", 5000);
        cfg.showProblems();
        return;
      }
      var view = await sff.api(base + (restart ? "/restart" : "/run"), { method: "POST" });
      if (!view.already_running) remember(view.run, compile);
      cfg.showConsole();
      s.unreachable = false;
      s.failures = 0;
      apply(view);
    } catch (error) {
      problem(error, "The bot couldn't be started.");
    } finally {
      s.busy = false;
      s.epoch += 1;
      render();
      if (polled()) schedule(POLL_MS);
    }
  }

  async function stop() {
    if (s.busy || s.mode === "off") return;
    s.busy = true;
    s.epoch += 1;
    s.pending = "Stopping…";
    render();
    try {
      apply(await sff.api(base + "/stop", { method: "POST" }));
    } catch (error) {
      problem(error, "The bot couldn't be stopped.");
    } finally {
      s.busy = false;
      s.epoch += 1;
      render();
      if (polled()) schedule(POLL_MS);
    }
  }

  /* A bot started earlier (here, in another tab, or before this page was loaded) shows up in the console again. */
  async function attach() {
    try {
      var view = await sff.api(base + "/run");
      if (s.busy || s.run) return;
      if (view.state !== "none") apply(view);
      if (polled()) schedule(POLL_MS);
    } catch (_) {
      /* The runner can't be reached right now. Run says so if it is pressed. */
    }
  }

  function init(options) {
    cfg = options;
    base = "/api/bots/" + encodeURIComponent(cfg.botId);
    s.mode = cfg.mode === "off" ? "off" : "on";
    refs = {
      run: document.getElementById("run-btn"),
      restart: document.getElementById("restart-btn"),
      stop: document.getElementById("stop-btn"),
      announce: document.getElementById("run-announce"),
    };
    refs.run.addEventListener("click", function () { start(false); });
    refs.restart.addEventListener("click", function () { start(true); });
    refs.stop.addEventListener("click", stop);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && polled() && !s.busy) schedule(0);
    });
    E.console.setLocator(locate);
    if (s.mode === "off") E.console.setHint(OFF_NOTE + " Messages from your bot would show up here.");
    render();
    if (s.mode !== "off") attach();
  }

  E.runner = {
    init: init,
    refresh: function () {
      if (cfg) render();
    },
    state: function () {
      return s.state;
    },
  };
})();
