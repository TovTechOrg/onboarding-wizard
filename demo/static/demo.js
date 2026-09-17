// Demo-only chrome. Hides the frames the trimmed flow collapses, renumbers
// the four that remain, and adds the banner.
//
// Step numbers live in the i18n strings, and the key names do NOT match
// their positions (frame2_title is "4. GitHub App"), so the visible titles
// are renumbered by DOM position here rather than by editing those strings.
(function () {
  var COLLAPSED_FRAMES = [
    "render-service", "dashboard-auth", "supabase", "uptime-pinger"
  ];
  var KEPT_FRAMES = ["render-key", "github-app", "llm-provider", "render-deploy"];

  var BANNER = {
    en: "Demo — mock data. No real Render, GitHub or LLM calls.",
    he: "דמו — נתונים מדומיים."
  };

  function lang() {
    return document.documentElement.lang === "he" ? "he" : "en";
  }

  function addBanner() {
    var el = document.createElement("div");
    el.id = "demoBanner";
    el.setAttribute("data-demo-banner", "");
    el.textContent = BANNER[lang()];
    el.style.cssText =
      "padding:.5rem 1rem;text-align:center;background:#f5c518;color:#1a1a2e;" +
      "font-weight:600;position:sticky;top:0;z-index:50";
    document.body.prepend(el);
  }

  function hideCollapsedFrames() {
    COLLAPSED_FRAMES.forEach(function (id) {
      var el = document.getElementById("frame-" + id);
      if (el) el.hidden = true;
    });
  }

  function renumberKeptFrames() {
    KEPT_FRAMES.forEach(function (id, index) {
      var el = document.getElementById("frame-" + id);
      if (!el) return;
      var title = el.querySelector(".frame-title");
      if (!title) return;
      // Replace a leading "<digits>. " with this frame's visible position.
      title.textContent = title.textContent.replace(
        /^\s*\d+\.\s*/, String(index + 1) + ". "
      );
    });
  }

  function apply() {
    hideCollapsedFrames();
    renumberKeptFrames();
  }

  var DEMO_APP_ID = "900001";
  // Body of the synthetic .pem the file input receives. Not a key of any
  // kind -- demo/github_client.py never reads it.
  var DEMO_PEM_BODY =
    "-----BEGIN RSA PRIVATE KEY-----\ndemo-not-a-real-key\n-----END RSA PRIVATE KEY-----\n";

  // Fill the real App-id/key-file fields and let the page's own submit path
  // run. The real GitHub-App-validation relay endpoint still gets called;
  // the demo's mock client is what makes it succeed, so the frame's own
  // machinery advances normally instead of being bypassed.
  function addShortcut() {
    var frame = document.getElementById("frame-github-app");
    if (!frame) return;
    var body = frame.querySelector(".frame-body");
    if (!body) return;

    var button = document.createElement("button");
    button.id = "demoUseCredentials";
    button.type = "button";
    button.textContent =
      lang() === "he"
        ? "השתמש בפרטי דמו"
        : "Use demo credentials";
    button.style.cssText = "margin-bottom:.75rem;font-weight:600";

    // The private key field is a FILE input (accept=".pem"), not a text box:
    // assigning .value to it is forbidden by every browser. A DataTransfer
    // is the supported way to hand it a synthetic file, and the change event
    // must be dispatched explicitly because assigning .files fires none.
    button.addEventListener("click", function () {
      var appId = document.getElementById("github-app-id-input");
      var keyFile = document.getElementById("github-app-key-file-input");
      if (appId) appId.value = DEMO_APP_ID;
      if (keyFile) {
        var transfer = new DataTransfer();
        transfer.items.add(
          new File([DEMO_PEM_BODY], "demo-app.pem", {
            type: "application/x-pem-file"
          })
        );
        keyFile.files = transfer.files;
        keyFile.dispatchEvent(new Event("change", { bubbles: true }));
      }
      // Every button on this page is type="button"; there is no submit.
      var submit = document.getElementById("github-app-validate-submit");
      if (submit) submit.click();
    });

    body.prepend(button);
  }

  function chosenProvider() {
    var checked = document.querySelector(
      'input[name="llm-provider-choice"]:checked'
    );
    return checked ? checked.value : null;
  }

  function wireServiceLink() {
    // finishRenderDeploy() sets this href to the created service's URL, which
    // demo/render_client.py returns as the bot demo. Append the reader's
    // provider choice so the review they land on reports it.
    var observer = new MutationObserver(function () {
      var link = document.getElementById("render-deploy-service-link");
      if (!link || !link.href || link.dataset.demoWired) return;
      var provider = chosenProvider();
      if (provider) {
        link.href =
          link.href + (link.href.indexOf("?") === -1 ? "?" : "&") +
          "provider=" + encodeURIComponent(provider);
      }
      link.dataset.demoWired = "1";
    });
    observer.observe(document.body, { attributes: true, childList: true, subtree: true });
  }

  function addStartOver() {
    var link = document.createElement("button");
    link.id = "demoStartOver";
    link.type = "button";
    link.textContent = lang() === "he"
      ? "התחל מחדש"
      : "Start over";
    link.style.cssText =
      "position:fixed;bottom:1rem;inset-inline-end:1rem;z-index:60;font-size:.85rem";
    link.addEventListener("click", function () {
      fetch("/api/session/reset", { method: "POST" }).then(function () {
        location.href = location.pathname;
      });
    });
    document.body.appendChild(link);
  }

  document.addEventListener("DOMContentLoaded", function () {
    addBanner();
    apply();
    addShortcut();
    addStartOver();
    wireServiceLink();
    // applyLanguage() rewrites every [data-i18n] node, restoring the
    // hardcoded numbers, so renumber again after a language switch.
    document.addEventListener("click", function (event) {
      if (event.target.closest("[data-lang-option], #langToggleBtn")) {
        setTimeout(apply, 0);
      }
    });
  });
})();
