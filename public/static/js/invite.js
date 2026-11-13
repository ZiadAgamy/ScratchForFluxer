/* The invite page: joining a bot someone shared. */
(function () {
  "use strict";

  var sff = window.sff;
  var button = document.getElementById("invite-join");
  if (!button) return;
  var error = document.getElementById("invite-error");

  button.addEventListener("click", async function () {
    button.disabled = true;
    error.hidden = true;
    try {
      var result = await sff.api("/api/invites/accept", { method: "POST", body: { code: button.dataset.code } });
      window.location.href = result.redirect;
    } catch (problem) {
      error.textContent = problem.message;
      error.hidden = false;
      button.disabled = false;
    }
  });
})();
