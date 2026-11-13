/* Sharing a bot. The owner makes invite links, sees who joined, ticks what each person may do and removes people.
   A person the bot is shared with can leave it. Everything the server says is put in the page as text only. */
(function () {
  "use strict";

  var E = window.sffEditor;
  var sff = window.sff;
  var el = E.el;
  var root = document.getElementById("editor");
  if (!root) return;

  var botId = root.dataset.botId;
  var base = "/api/bots/" + encodeURIComponent(botId);

  var EXPIRY_LABELS = { "1h": "1 hour", "1d": "1 day", "7d": "7 days", "30d": "30 days", never: "Never ends" };

  function until(iso) {
    var then = Date.parse(iso);
    if (isNaN(then)) return "";
    var seconds = (then - Date.now()) / 1000;
    if (seconds <= 0) return "ended";
    if (seconds < 3600) return "ends in " + E.plural(Math.max(1, Math.round(seconds / 60)), "minute");
    if (seconds < 86400) return "ends in " + E.plural(Math.round(seconds / 3600), "hour");
    return "ends in " + E.plural(Math.round(seconds / 86400), "day");
  }

  function names(info, keys) {
    var out = [];
    info.permissions.forEach(function (item) {
      if (keys.indexOf(item.key) !== -1) out.push(item.name.toLowerCase());
    });
    return out.length ? out.join(", ") : "look only";
  }

  /* A row of ticks for the things a person may do. `onChange(keys)` is called with the ticked ones. */
  function checklist(info, ticked, onChange, name) {
    var boxes = [];
    var list = el("div", { class: "share-checks", role: "group" }, [
      el("label", { class: "share-check is-fixed" }, [
        el("input", { type: "checkbox", checked: true, disabled: true }),
        el("span", {}, [el("strong", { text: "Look at the blocks and files" }), el("small", { text: "Always allowed." })]),
      ]),
    ]);
    info.permissions.forEach(function (item) {
      var box = el("input", { type: "checkbox", name: name || "perm", value: item.key });
      box.checked = ticked.indexOf(item.key) !== -1;
      box.addEventListener("change", function () {
        onChange(
          boxes
            .filter(function (b) { return b.checked; })
            .map(function (b) { return b.value; })
        );
      });
      boxes.push(box);
      list.appendChild(el("label", { class: "share-check" }, [box, el("span", {}, [el("strong", { text: item.name }), el("small", { text: item.what })])]));
    });
    return list;
  }

  async function openShare() {
    var info;
    try {
      info = await sff.api(base + "/sharing");
    } catch (error) {
      sff.toast(error.message, "error", 5000);
      return;
    }
    var chosen = ["edit"];

    return E.dialogs.open(function (dialog, close) {
      dialog.classList.add("dialog-wide", "share-dialog");
      dialog.setAttribute("aria-labelledby", "share-title");
      var linksBox = el("div", { class: "share-list" });
      var peopleBox = el("div", { class: "share-list" });
      var made = el("div", { class: "share-made", hidden: true, role: "status" });
      var error = el("p", { class: "field-error", role: "alert", hidden: true });

      function fail(problem) {
        error.textContent = problem.message || String(problem);
        error.hidden = false;
      }

      async function reload() {
        try {
          info = await sff.api(base + "/sharing");
          drawLists();
        } catch (problem) {
          fail(problem);
        }
      }

      function drawLists() {
        E.clear(linksBox);
        var live = info.invites.filter(function (invite) { return invite.state === "active"; });
        if (!live.length) linksBox.appendChild(el("p", { class: "share-empty", text: "No links yet." }));
        live.forEach(function (invite) {
          var uses = invite.max_uses === null ? E.plural(invite.uses, "person", "people") + " joined" : invite.uses + " of " + invite.max_uses + " used";
          var ends = invite.expires_at ? until(invite.expires_at) : "never ends";
          linksBox.appendChild(
            el("div", { class: "share-row" }, [
              E.icon("link"),
              el("div", { class: "share-row-main" }, [
                el("strong", { text: "Link ending in " + invite.hint }),
                el("small", { text: names(info, invite.perms) + " · " + uses + " · " + ends }),
              ]),
              el("button", {
                class: "btn btn-ghost btn-sm",
                type: "button",
                text: "Cancel link",
                on: {
                  click: async function () {
                    try {
                      await sff.api(base + "/invites/" + encodeURIComponent(invite.id), { method: "DELETE" });
                      await reload();
                    } catch (problem) {
                      fail(problem);
                    }
                  },
                },
              }),
            ])
          );
        });

        E.clear(peopleBox);
        if (!info.members.length) peopleBox.appendChild(el("p", { class: "share-empty", text: "Nobody has joined yet." }));
        info.members.forEach(function (member) {
          var checks = checklist(info, member.perms, async function (keys) {
            try {
              await sff.api(base + "/members/" + encodeURIComponent(member.id), { method: "PATCH", body: { perms: keys } });
              member.perms = keys;
              sff.toast("Saved what " + member.name + " can do.", "ok", 2000);
            } catch (problem) {
              fail(problem);
              await reload();
            }
          }, "perm-" + member.id);
          peopleBox.appendChild(
            el("details", { class: "share-person" }, [
              el("summary", {}, [
                el("img", { class: "avatar", src: member.avatar_url, alt: "", width: "28", height: "28", referrerpolicy: "no-referrer" }),
                el("span", { class: "share-person-name" }, [el("strong", { text: member.name }), el("small", { text: "@" + member.username })]),
                el("span", { class: "share-person-can", text: names(info, member.perms) }),
              ]),
              checks,
              el("div", { class: "share-person-actions" }, [
                el("button", {
                  class: "btn btn-danger btn-sm",
                  type: "button",
                  text: "Remove " + member.name,
                  on: {
                    click: async function () {
                      var yes = await E.dialogs.confirm({
                        title: "Remove " + member.name + "?",
                        message: "They lose access to this bot straight away. They can join again only with a new link.",
                        confirmLabel: "Remove",
                        danger: true,
                      });
                      if (!yes) return;
                      try {
                        await sff.api(base + "/members/" + encodeURIComponent(member.id), { method: "DELETE" });
                        await reload();
                      } catch (problem) {
                        fail(problem);
                      }
                    },
                  },
                }),
              ]),
            ])
          );
        });
      }

      var expires = el("select", { class: "select", id: "share-expires", "aria-label": "How long the link lasts" });
      info.expiries.forEach(function (key) {
        var option = el("option", { value: key, text: EXPIRY_LABELS[key] || key });
        if (key === "7d") option.selected = true;
        expires.appendChild(option);
      });
      var uses = el("select", { class: "select", id: "share-uses", "aria-label": "How many people can use it" });
      info.use_choices.forEach(function (count) {
        uses.appendChild(el("option", { value: String(count), text: count === 1 ? "1 person" : count + " people" }));
      });
      uses.appendChild(el("option", { value: "", text: "Anyone with the link (up to " + info.limits.members + " people on the bot)" }));

      var create = el("button", { class: "btn btn-primary", type: "button", text: "Create link" });
      create.addEventListener("click", async function () {
        error.hidden = true;
        create.disabled = true;
        try {
          var result = await sff.api(base + "/invites", {
            method: "POST",
            body: { perms: chosen, expires: expires.value, max_uses: uses.value === "" ? null : Number(uses.value) },
          });
          E.clear(made);
          var field = el("input", { class: "input input-mono", type: "text", readonly: true, value: result.url, "aria-label": "Your invite link" });
          made.appendChild(el("p", { class: "share-made-title" }, [E.icon("check-circle"), el("strong", { text: "Your link is ready" })]));
          made.appendChild(
            el("div", { class: "input-group" }, [
              field,
              el("button", {
                class: "btn btn-primary",
                type: "button",
                text: "Copy",
                on: {
                  click: function () {
                    sff.copyText(result.url).then(function () { sff.toast("Link copied.", "ok", 1800); });
                  },
                },
              }),
            ])
          );
          made.appendChild(el("p", { class: "hint", text: "Copy it now. For safety it is not shown again, but you can make another any time." }));
          made.hidden = false;
          field.focus();
          field.select();
          await reload();
        } catch (problem) {
          fail(problem);
        } finally {
          create.disabled = false;
        }
      });

      drawLists();
      dialog.appendChild(
        el("div", { class: "dialog-body" }, [
          el("div", { class: "dialog-head" }, [
            el("h2", { id: "share-title", text: "Share this bot" }),
            el("button", { class: "ed-icon-btn", type: "button", "aria-label": "Close", on: { click: function () { close(); } } }, [E.icon("x")]),
          ]),
          el("p", { class: "dialog-text", text: "Send a link to the people you want to work with. Nobody but you can ever see your bot's token." }),
          el("h3", { text: "Make a link" }),
          el("p", { class: "hint", text: "What can people who use this link do?" }),
          checklist(info, chosen, function (keys) { chosen = keys; }),
          el("div", { class: "share-options" }, [
            el("div", { class: "field" }, [el("label", { for: "share-expires", text: "The link lasts" }), expires]),
            el("div", { class: "field" }, [el("label", { for: "share-uses", text: "It can be used by" }), uses]),
          ]),
          el("div", { class: "dialog-actions share-create" }, [create]),
          made,
          error,
          el("h3", { text: "Links" }),
          linksBox,
          el("h3", { text: "People on this bot" }),
          peopleBox,
        ])
      );
    }, undefined);
  }

  var button = document.getElementById("share-btn");
  if (button) button.addEventListener("click", openShare);

  var leave = document.getElementById("leave-btn");
  if (leave) {
    leave.addEventListener("click", async function () {
      var yes = await E.dialogs.confirm({
        title: "Leave this bot?",
        message: "You won't be able to open it any more, unless the owner sends you a new link.",
        confirmLabel: "Leave",
        danger: true,
      });
      if (!yes) return;
      try {
        var result = await sff.api(base + "/membership", { method: "DELETE" });
        window.location.href = result.redirect || "/bots";
      } catch (problem) {
        sff.toast(problem.message, "error", 5000);
      }
    });
  }

  E.share = { open: openShare };
})();
