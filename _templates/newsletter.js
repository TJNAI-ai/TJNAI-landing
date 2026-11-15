/* Team of Bots sign-up form (store-only). The Worker saves the address in D1 and sends no email.
 * The endpoint is chosen by site; any other host (local preview, file://) runs in mock mode and sends nothing. */
(function () {
  var ENDPOINTS = {
    "tjnai.ai": "https://tob-newsletter.tjnai.workers.dev",
    "tjnai-relaunch-preview.tjnai.workers.dev": "https://tob-newsletter.tjnai.workers.dev"
  };
  var ENDPOINT = ENDPOINTS[location.hostname] || "";
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

  document.querySelectorAll("[data-newsletter]").forEach(function (box) {
    var form = box.querySelector("form");
    var thanks = box.querySelector(".nl-thanks");
    var err = box.querySelector(".nl-error");
    var btn = form.querySelector("button[type=submit]");
    var consent = box.querySelector("#nl-consent").textContent.replace(/\s+/g, " ").trim();
    var started = Date.now();

    function showError(msg) { err.textContent = msg; err.hidden = false; }
    function done(email) {
      thanks.querySelector("[data-nl-email]").textContent = email;
      form.hidden = true;
      thanks.hidden = false;
      thanks.focus();
    }

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      err.hidden = true;
      var email = form.email.value.trim();
      if (!EMAIL_RE.test(email)) { showError("Please enter a valid email address."); form.email.focus(); return; }
      if (!ENDPOINT) { done(email); return; }
      btn.disabled = true;
      fetch(ENDPOINT + "/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: email,
          first_name: form.first_name.value.trim(),
          company: form.company.value,
          elapsed_ms: Date.now() - started,
          source_page: location.pathname.replace(/\.html$/, ""),
          consent_text: consent
        })
      }).then(function (res) {
        if (res.ok) { done(email); return; }
        if (res.status === 400) showError("Please check your email address and try again.");
        else showError("Something went wrong. Please try again in a minute, or email info@tjnai.ai.");
      }).catch(function () {
        showError("We couldn't reach the sign-up service. Please try again in a minute.");
      }).then(function () { btn.disabled = false; });
    });
  });
})();
