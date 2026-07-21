/**
 * ReNest embeddable listing widget.
 *
 * Usage:
 *   <div data-renest-campus="usc" data-renest-limit="6"></div>
 *   <script src="https://your-domain/embed.js" defer></script>
 *
 * The script finds every [data-renest-campus] element and renders
 * a live listing grid inside it. No authentication required.
 */
(function () {
  "use strict";

  var API_BASE = (function () {
    var scripts = document.getElementsByTagName("script");
    var src = scripts[scripts.length - 1].src || "";
    var m = src.match(/^(https?:\/\/[^/]+)/);
    return m ? m[1] : "";
  })();

  var CARD_CSS = [
    ".dc-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;font-family:system-ui,sans-serif}",
    ".dc-card{border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;text-decoration:none;color:inherit;display:block;transition:box-shadow .15s}",
    ".dc-card:hover{box-shadow:0 4px 12px rgba(0,0,0,.1)}",
    ".dc-img{width:100%;aspect-ratio:4/3;object-fit:cover;background:#f3f4f6}",
    ".dc-body{padding:10px 12px}",
    ".dc-title{font-size:13px;font-weight:600;margin:0 0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
    ".dc-meta{font-size:11px;color:#6b7280;display:flex;gap:6px;align-items:center}",
    ".dc-cat{background:#f3f4f6;border-radius:20px;padding:2px 8px}",
    ".dc-empty{font-size:13px;color:#9ca3af;text-align:center;padding:24px}",
  ].join("");

  function injectStyles() {
    if (document.getElementById("dc-embed-css")) return;
    var s = document.createElement("style");
    s.id = "dc-embed-css";
    s.textContent = CARD_CSS;
    document.head.appendChild(s);
  }

  function renderGrid(container, data, baseUrl) {
    var items = data.results || [];
    if (!items.length) {
      container.innerHTML = '<p class="dc-empty">No items available right now.</p>';
      return;
    }
    var limit = parseInt(container.dataset.renestLimit, 10) || 6;
    items = items.slice(0, limit);
    var html = '<div class="dc-grid">';
    items.forEach(function (item) {
      var price = item.price_type === "free" ? "Free" : ("$" + parseFloat(item.price_amount || 0).toFixed(0));
      var img = item.image_cdn_url
        ? '<img class="dc-img" src="' + item.image_cdn_url + '" alt="" loading="lazy">'
        : '<div class="dc-img"></div>';
      html += [
        '<a class="dc-card" href="' + baseUrl + '/listings/' + item.id + '" target="_blank" rel="noopener">',
        img,
        '<div class="dc-body">',
        '<p class="dc-title">' + escHtml(item.title) + "</p>",
        '<div class="dc-meta">',
        '<span class="dc-cat">' + escHtml(item.category || "") + "</span>",
        "<span>" + escHtml(price) + "</span>",
        "</div></div></a>",
      ].join("");
    });
    html += "</div>";
    container.innerHTML = html;
  }

  function escHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function init() {
    injectStyles();
    var containers = document.querySelectorAll("[data-renest-campus]");
    containers.forEach(function (el) {
      var slug = el.dataset.renestCampus;
      if (!slug) return;
      el.innerHTML = '<p class="dc-empty">Loading…</p>';
      fetch(API_BASE + "/api/embed/" + encodeURIComponent(slug) + "/listings")
        .then(function (r) { return r.json(); })
        .then(function (data) { renderGrid(el, data, API_BASE); })
        .catch(function () {
          el.innerHTML = '<p class="dc-empty">Could not load listings.</p>';
        });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
