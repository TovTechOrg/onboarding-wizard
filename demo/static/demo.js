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

  document.addEventListener("DOMContentLoaded", function () {
    addBanner();
    apply();
    // applyLanguage() rewrites every [data-i18n] node, restoring the
    // hardcoded numbers, so renumber again after a language switch.
    document.addEventListener("click", function (event) {
      if (event.target.closest("[data-lang-option], #langToggleBtn")) {
        setTimeout(apply, 0);
      }
    });
  });
})();
