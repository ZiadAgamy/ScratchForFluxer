/* My bots: rename, replace token, remove, delete my data. */
(function () {
  "use strict";

  var sff = window.sff;
  var currentRow = null;

  function dialog(id) {
    return document.getElementById(id);
  }

  function resetError(dlg) {
    var error = dlg.querySelector(".field-error");
    if (error) {
      error.hidden = true;
      error.textContent = "";
    }
  }

  function openDialog(id, row) {
    var dlg = dialog(id);
    currentRow = row || null;
    resetError(dlg);
    dlg.showModal();
    var field = dlg.querySelector("input");
    if (field) {
      field.focus();
      field.select();
    }
    return dlg;
  }

  function closeMenus() {
    document.querySelectorAll("details.menu[open]").forEach(function (menu) {
      menu.removeAttribute("open");
    });
  }

  function updateCount() {
    var rows = document.querySelectorAll(".bot-row").length;
    var note = document.querySelector(".list-count");
    if (note) note.textContent = note.textContent.replace(/^\d+/, String(rows));
  }

  /* Open a dialog from the row menu or the "Delete my data" button. */
  document.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-action]");
    if (!trigger) return;
    var row = trigger.closest(".bot-row");
    var action = trigger.getAttribute("data-action");
    closeMenus();

    if (action === "rename" && row) {
      var rename = openDialog("dlg-rename", row);
      var field = rename.querySelector("#rename-input");
      field.value = row.getAttribute("data-bot-name") || "";
      field.select();
    } else if (action === "token" && row) {
      var replace = openDialog("dlg-token", row);
      replace.querySelector("input").value = "";
    } else if (action === "remove" && row) {
      var remove = openDialog("dlg-remove", row);
      remove.querySelector('[data-role="remove-name"]').textContent = row.getAttribute("data-bot-name") || "this bot";
    } else if (action === "delete-account") {
      var account = openDialog("dlg-account");
      account.querySelector("input").value = "";
    }
  });

  /* Close buttons and clicks on the dimmed backdrop. */
  document.querySelectorAll("dialog.dialog").forEach(function (dlg) {
    dlg.addEventListener("click", function (event) {
      if (event.target === dlg || event.target.closest("[data-close]")) dlg.close();
    });
  });

  var handlers = {
    rename: async function (form) {
      var data = await sff.api("/api/bots/" + encodeURIComponent(currentRow.dataset.botId), {
        method: "PATCH",
        body: { name: form.elements.name.value },
      });
      currentRow.dataset.botName = data.bot.name;
      currentRow.querySelector(".bot-name").textContent = data.bot.name;
      sff.toast("Renamed to " + data.bot.name, "ok");
    },

    token: async function (form) {
      var data = await sff.api("/api/bots/" + encodeURIComponent(currentRow.dataset.botId) + "/token", {
        method: "PUT",
        body: { token: form.elements.token.value },
      });
      form.elements.token.value = "";
      var last4 = currentRow.querySelector('[data-role="last4"]');
      if (last4) last4.textContent = data.bot.token_last4;
      sff.toast("Token replaced", "ok");
    },

    remove: async function () {
      await sff.api("/api/bots/" + encodeURIComponent(currentRow.dataset.botId), { method: "DELETE" });
      currentRow.remove();
      if (!document.querySelector(".bot-row")) {
        window.location.reload();
        return;
      }
      updateCount();
      sff.toast("Bot removed", "ok");
    },

    account: async function (form) {
      var data = await sff.api("/api/me", { method: "DELETE", body: { confirm: form.elements.confirm.value } });
      window.location.href = data.redirect || "/";
    },
  };

  document.querySelectorAll("form[data-dialog-form]").forEach(function (form) {
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      var dlg = form.closest("dialog");
      var submit = form.querySelector('[type="submit"]');
      var error = form.querySelector(".field-error");
      resetError(dlg);
      submit.setAttribute("aria-busy", "true");
      try {
        await handlers[form.getAttribute("data-dialog-form")](form);
        dlg.close();
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
      } finally {
        submit.removeAttribute("aria-busy");
      }
    });
  });
})();
