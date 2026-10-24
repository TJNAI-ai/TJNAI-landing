/* Team of Bots signup form (double opt-in). Used by index.html (footer) and newsletter.html.
 *
 * ONE config constant: set NEWSLETTER_ENDPOINT to the deployed backend's base URL
 * (e.g. "https://tob-newsletter.<account>.workers.dev"). While it's empty, the form runs in
 * MOCK mode: it shows the thank-you state and sends nothing anywhere.
 * Add ?nl_mock=1 to any page URL to force mock mode for previews.
 */
const NEWSLETTER_ENDPOINT = "";

(function () {
  var mock = !NEWSLETTER_ENDPOINT || /[?&]nl_mock=1\b/.test(location.search);
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

  document.querySelectorAll("[data-newsletter]").forEach(function (box) {
    var form = box.querySelector("form");
    var thanks = box.querySelector(".nl-thanks");
    var err = box.querySelector(".nl-error");
    var btn = form.querySelector("button[type=submit]");
    var started = Date.now();

    function showError(msg) { err.textContent = msg; err.hidden = false; }
    function done(email) {
      var slot = thanks.querySelector("[data-nl-email]");
      if (slot) slot.textContent = email;
      var note = thanks.querySelector(".nl-mock");
      if (note) note.hidden = !mock;
      form.hidden = true;
      thanks.hidden = false;
      thanks.focus();
    }

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      err.hidden = true;
      var email = form.email.value.trim();
      if (!EMAIL_RE.test(email)) { showError("Please enter a valid email address."); form.email.focus(); return; }
      var payload = {
        email: email,
        first_name: form.first_name.value.trim(),
        company: form.company.value, // honeypot: real people never see or fill this
        elapsed_ms: Date.now() - started
      };
      if (mock) { done(email); return; }
      btn.disabled = true;
      fetch(NEWSLETTER_ENDPOINT.replace(/\/+$/, "") + "/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      }).then(function (res) {
        if (res.ok) { done(email); return; }
        if (res.status === 400) showError("Please check your email address and try again.");
        else showError("Something went wrong. Please try again in a minute, or email info@tjnai.ai.");
      }).catch(function () {
        showError("Couldn't reach the signup service. Please try again in a minute.");
      }).finally(function () { btn.disabled = false; });
    });
  });
})();
