/* Add a bot: paste a token, check it with Fluxer, name the bot, save it. */
(function () {
  "use strict";

  var sff = window.sff;
  var $ = function (id) {
    return document.getElementById(id);
  };

  var input = $("token-input");
  var toggle = $("token-toggle");
  var checkBtn = $("check-btn");
  var statePaste = $("state-paste");
  var stateConfirm = $("state-confirm");
  var flash = $("flash");
  var nameField = $("name-field");
  var nameInput = $("bot-name-input");
  var saveBtn = $("save-btn");
  var retryBtn = $("retry-btn");
  var alreadyBox = $("already-box");

  var TOKEN_SHAPE = /^[0-9]{1,25}\.[A-Za-z0-9_\-.~+/=]{8,256}$/;

  function clean(value) {
    var v = (value || "").trim().replace(/^["'`]+|["'`]+$/g, "").trim();
    if (/^bot\s+/i.test(v)) v = v.slice(4).trim();
    return v;
  }

  function showError(message) {
    flash.textContent = message || "";
    flash.hidden = !message;
  }

  function setBusy(button, busy) {
    if (busy) button.setAttribute("aria-busy", "true");
    else button.removeAttribute("aria-busy");
  }

  function showPaste() {
    stateConfirm.hidden = true;
    statePaste.hidden = false;
    alreadyBox.hidden = true;
    nameField.hidden = false;
    saveBtn.hidden = false;
    showError("");
    input.focus();
  }

  function showConfirm(data) {
    var bot = data.bot;
    $("bot-avatar").src = bot.avatar_url;
    $("bot-app-name").textContent = bot.application_name || bot.bot_username;
    $("bot-username").textContent = "@" + bot.bot_username;
    nameInput.value = bot.default_name;

    var already = data.already_added;
    alreadyBox.hidden = !already;
    nameField.hidden = !!already;
    saveBtn.hidden = !!already;
    if (already) {
      $("already-name").textContent = already.name;
      $("already-link").href = "/bots/" + encodeURIComponent(already.id) + "/edit";
    }

    statePaste.hidden = true;
    stateConfirm.hidden = false;
    if (already) retryBtn.focus();
    else {
      nameInput.focus();
      nameInput.select();
    }
  }

  async function check() {
    var token = clean(input.value);
    if (!token) return;
    showError("");
    setBusy(checkBtn, true);
    try {
      showConfirm(await sff.api("/api/bots/verify", { method: "POST", body: { token: token } }));
    } catch (err) {
      showError(err.message);
    } finally {
      setBusy(checkBtn, false);
    }
  }

  async function save() {
    showError("");
    setBusy(saveBtn, true);
    try {
      var result = await sff.api("/api/bots", {
        method: "POST",
        body: { token: clean(input.value), name: nameInput.value },
      });
      input.value = ""; // the token only ever lived in this field
      window.location.href = result.redirect;
    } catch (err) {
      showError(err.message);
      setBusy(saveBtn, false);
    }
  }

  input.addEventListener("input", function () {
    checkBtn.disabled = !input.value.trim();
    showError("");
  });

  // Pasting something that looks like a token checks it straight away.
  input.addEventListener("paste", function () {
    setTimeout(function () {
      checkBtn.disabled = !input.value.trim();
      if (TOKEN_SHAPE.test(clean(input.value))) check();
    }, 0);
  });

  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      if (!checkBtn.disabled) check();
    }
  });

  toggle.addEventListener("click", function () {
    var show = input.type === "password";
    input.type = show ? "text" : "password";
    toggle.setAttribute("aria-pressed", String(show));
    toggle.setAttribute("aria-label", show ? "Hide token" : "Show token");
    var icon = toggle.querySelector("i");
    icon.className = "ph-fill " + (show ? "ph-eye-slash" : "ph-eye");
  });

  nameInput.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      save();
    }
  });

  checkBtn.addEventListener("click", check);
  saveBtn.addEventListener("click", save);
  retryBtn.addEventListener("click", function () {
    input.value = "";
    checkBtn.disabled = true;
    showPaste();
  });
})();
