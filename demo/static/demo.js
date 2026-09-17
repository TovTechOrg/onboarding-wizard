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
    // font-size/line-height shrink at narrow widths so the banner text
    // never wraps to a second line down to 320px (measured) -- keeps its
    // height small and predictable instead of doubling on every mobile
    // width this frame is reviewed at.
    el.style.cssText =
      "padding:.4rem 1rem;text-align:center;background:#f5c518;color:#1a1a2e;" +
      "font-weight:600;font-size:.8rem;line-height:1.3;position:sticky;top:0;z-index:50";
    document.body.prepend(el);
    return el;
  }

  // header.topbar (the theme/language toggle row, real markup from
  // index.html) sits immediately after #demoBanner in the DOM with no
  // margin of its own. #demoBanner is `position: sticky; top: 0`, so once
  // the page scrolls past the topbar's own rest position by more than the
  // banner's height, the topbar scrolls UNDER the still-pinned banner and
  // gets visually clipped -- reproducible with a scroll as small as ~12px
  // at some widths, i.e. any normal reading scroll or a mobile keyboard
  // auto-scrolling a focused input into view.
  //
  // Shrinking the banner (above) narrows the danger zone but can't remove
  // it -- the topbar would still eventually scroll under any nonzero-height
  // sticky banner above it. The robust fix is making the topbar sticky too,
  // pinned directly below the banner's own (measured, not assumed) height,
  // so the two form one continuous sticky stack and the topbar can never be
  // scrolled underneath the banner at all. Needs its own opaque background
  // (topbar has none of its own -- it normally just shows body's) since it
  // now has to occlude content scrolling up behind it.
  function pinTopbarBelowBanner(bannerEl) {
    var header = document.querySelector("header.topbar");
    if (!header || !bannerEl) return function () {};
    function apply() {
      header.style.position = "sticky";
      header.style.top = bannerEl.offsetHeight + "px";
      header.style.zIndex = "49";
      header.style.background = "var(--bg)";
    }
    apply();
    window.addEventListener("resize", apply);
    return apply;
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

  // The render-service frame is hidden (COLLAPSED_FRAMES above), but its
  // data is NOT synthetic the way dashboard-auth/supabase/uptime-pinger's
  // is (demo/session_store.py's _PRESEEDED_FRAMES) -- github-app's own
  // validateGithubApp() reads readStoredRenderService().service_url from
  // sessionStorage before ever calling the relay, and uptime-pinger reads
  // the same record's URL later, so *something* has to actually call
  // POST /api/render/create-service (demo/render_client.py's mock still
  // answers it) once render-key is done, the same way a visitor would by
  // hand in the real wizard. Watching this frame's own `data-locked`
  // attribute (flipped by the page's own completeFrame() chain right
  // after render-key finishes) and driving its already-existing
  // prefillRenderServiceDefaults()/createRenderService() functions
  // (global, since index.html's own <script> is a classic script, not a
  // module) reuses the real machinery exactly like addShortcut() does for
  // github-app, rather than reimplementing the create-service call here.
  function autoCreateRenderService() {
    var frame = document.getElementById("frame-render-service");
    if (!frame) return;
    var triggered = false;
    function maybeTrigger() {
      if (frame.getAttribute("data-locked") === "true") {
        // The frame can legitimately relock (e.g. "Change" on render-key
        // via beginChange -> relockDownstreamOf -> lockFrame). Reset the
        // latch so a later unlock auto-drives again instead of dead-ending
        // the demo permanently.
        triggered = false;
        return;
      }
      if (triggered) return;
      triggered = true;
      if (typeof prefillRenderServiceDefaults === "function") prefillRenderServiceDefaults();
      if (typeof createRenderService === "function") createRenderService();
    }
    new MutationObserver(maybeTrigger).observe(frame, {
      attributes: true, attributeFilter: ["data-locked"]
    });
    maybeTrigger();
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

  // Was `position: fixed; bottom: 1rem` -- floating independently of
  // document flow, it sat on top of whatever frame content happened to be
  // at that fixed viewport position (confirmed via elementFromPoint/
  // elementsFromPoint: a validated frame's collapsed detail line, e.g.
  // "-- account: bot-demo", at every mobile width from 390 down to 320px,
  // in both LTR and RTL). Adding bottom padding to `main` can't fix this:
  // the overlap happens with whichever frame is currently laid out under
  // that fixed point, not necessarily the last one on the page, so no
  // amount of trailing whitespace after the final frame keeps it clear.
  // Placing it inside `header.topbar` instead makes it a normal in-flow
  // sibling of the theme/language toggles -- it can never sit on top of
  // frame content again, and it inherits the topbar's own sticky-below-the-
  // banner fix above for free.
  function addStartOver() {
    var header = document.querySelector("header.topbar");
    var link = document.createElement("button");
    link.id = "demoStartOver";
    link.type = "button";
    link.className = "control";
    link.textContent = lang() === "he"
      ? "התחל מחדש"
      : "Start over";
    link.style.cssText = "font-size:.85rem";
    link.addEventListener("click", function () {
      fetch("/api/session/reset", { method: "POST" }).then(function () {
        location.href = location.pathname;
      });
    });
    if (header) header.appendChild(link);
    else document.body.appendChild(link);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var bannerEl = addBanner();
    var reapplyTopbarOffset = pinTopbarBelowBanner(bannerEl);
    apply();
    addShortcut();
    addStartOver();
    wireServiceLink();
    autoCreateRenderService();
    // applyLanguage() rewrites every [data-i18n] node, restoring the
    // hardcoded numbers, so renumber again after a language switch. The
    // banner's own text also swaps (EN/HE differ in length/line count), so
    // the topbar's sticky offset -- measured off the banner's live height
    // -- is recomputed too, not just assumed to be unchanged.
    document.addEventListener("click", function (event) {
      if (event.target.closest("[data-lang-option], #langToggleBtn")) {
        setTimeout(function () {
          apply();
          reapplyTopbarOffset();
        }, 0);
      }
    });
  });
})();
