/* Saving. Every change to the blocks is saved a moment later, and the server only accepts a save that starts from the
   version it has (so two tabs can't quietly overwrite each other). This file decides when to save, retries when the
   network or the server hiccups, and reports what is going on with a short status. */
(function () {
  "use strict";

  var E = window.sffEditor;
  var sff = window.sff;

  /* options:
       botId, version
       serialize()              -> the workspace JSON to save
       onSaved(result, newer)   -> after a save; `newer` is true if the person kept editing while it was saving
       onConflict(serverVersion)-> promise of "keep" (save mine over theirs) or "load" (take theirs) or null (later)
       onStatus(state, detail)  -> "saved" | "dirty" | "saving" | "offline" | "error" | "conflict" | "limited"
  */
  function create(options) {
    var version = options.version;
    var delay = options.delay || 900;
    var timer = null;
    var retryTimer = null;
    var running = null;
    var dirty = false;
    var paused = false;
    var failures = 0;
    var conflictVersion = null;
    var path = "/api/bots/" + encodeURIComponent(options.botId) + "/workspace";

    function status(name, detail) {
      options.onStatus(name, detail || "");
    }

    function schedule(ms) {
      clearTimeout(timer);
      timer = setTimeout(function () {
        run();
      }, ms);
    }

    function retryIn(ms) {
      clearTimeout(retryTimer);
      retryTimer = setTimeout(function () {
        run();
      }, ms);
    }

    async function ask(serverVersion) {
      paused = true;
      status("conflict");
      var choice = await options.onConflict(serverVersion);
      if (choice === "keep") {
        version = serverVersion;
        paused = false;
        dirty = true;
        return run();
      }
      if (choice === "load") {
        paused = false;
        dirty = false;
        status("saved");
        return true;
      }
      return false; /* left for later: the status stays "changed somewhere else" and clicking it asks again */
    }

    function failed(error) {
      dirty = true;
      var code = error.status;
      if (code === 409) {
        conflictVersion = error.data && typeof error.data.version === "number" ? error.data.version : version;
        ask(conflictVersion);
        return false;
      }
      if (code === 0 || code >= 500) {
        failures += 1;
        status("offline");
        retryIn(Math.min(30000, 2000 * Math.pow(2, failures - 1)));
        return false;
      }
      if (code === 429) {
        status("limited");
        retryIn(6000);
        return false;
      }
      /* 400, 413, 422...: these blocks can't be saved as they are. A later change tries again. */
      status("error", error.message);
      return false;
    }

    async function save(keepalive) {
      clearTimeout(timer);
      clearTimeout(retryTimer);
      dirty = false;
      status("saving");
      var workspace = null;
      try {
        workspace = options.serialize();
      } catch (error) {
        return failed(error);
      }
      if (!workspace) {
        /* Nothing the server could keep: an editor with no blocks at all (not even the two it always has) means
           something went wrong here. Saving it would replace the person's real blocks, so nothing is sent. */
        status("error", "The editor has lost its blocks, so nothing was saved and your saved blocks are untouched. Reload the page.");
        return false;
      }
      try {
        var result = await sff.api(path, { method: "PUT", body: { version: version, workspace: workspace }, keepalive: !!keepalive });
        version = result.version;
        failures = 0;
        var newer = dirty;
        try {
          options.onSaved(result, newer);
        } catch (error) {
          /* The save worked. A problem showing its result is not a failed save (and must not be saved again). */
          if (window.console) console.error("[editor] after saving", error);
        }
        if (newer) {
          status("dirty");
          schedule(300);
        } else {
          status("saved");
        }
        return true;
      } catch (error) {
        if (error.name === "AbortError") return false;
        return failed(error);
      }
    }

    function run(keepalive) {
      if (paused) return Promise.resolve(false);
      if (running) return running;
      if (!dirty) return Promise.resolve(true);
      running = save(keepalive).then(
        function (ok) {
          running = null;
          return ok;
        },
        function (error) {
          running = null;
          throw error;
        }
      );
      return running;
    }

    /* The blocks changed. */
    function touch() {
      dirty = true;
      if (paused) return;
      status("dirty");
      schedule(delay);
    }

    /* Save right now and say whether it worked. */
    function flush() {
      if (running) {
        return running.then(function () {
          return dirty ? run() : true;
        });
      }
      return run();
    }

    window.addEventListener("online", function () {
      if (dirty && !paused) run();
    });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden" && dirty && !paused && !running) run(true);
    });
    window.addEventListener("pagehide", function () {
      if (dirty && !paused && !running) run(true);
    });
    window.addEventListener("beforeunload", function (event) {
      if (dirty || running) {
        event.preventDefault();
        event.returnValue = "";
      }
    });

    return {
      touch: touch,
      flush: flush,
      version: function () {
        return version;
      },
      setVersion: function (next) {
        version = next;
      },
      isDirty: function () {
        return dirty || !!running;
      },
      /* Ask again (the "changed somewhere else" status is clickable). */
      retry: function () {
        if (paused) return ask(conflictVersion === null ? version : conflictVersion);
        dirty = true;
        return run();
      },
      clean: function () {
        dirty = false;
        clearTimeout(timer);
        clearTimeout(retryTimer);
      },
    };
  }

  E.autosave = { create: create };
})();
