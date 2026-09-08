/**
 * ReNest embeddable listing widget.
 *
 * Usage:
 *   <div data-renest-campus="usc" data-renest-limit="6"></div>
 *   <script src="https://your-domain/embed.js" defer></script>
 *
 * The script finds every [data-renest-campus] element and renders
 * a live listing grid inside it. No authentication required.
 *
 * This runs on third-party pages, so every value from the API is inserted
 * through DOM APIs (textContent / setAttribute) rather than by building an
 * HTML string. Concatenating listing text or image URLs into innerHTML would
 * make any listing a stored-XSS vector on every partner site.
 */
(function () {
  "use strict";

  var API_BASE = (function () {
    // document.currentScript is the script being executed, which is correct
    // whether the tag is deferred, async or inline. Reading the *last* script
    // in the document (the previous approach) resolves to an unrelated script
    // under the documented `defer` usage.
    var self = document.currentScript;
    var src = (self && self.src) || "";
    var match = src.match(/^(https?:\/\/[^/]+)/);
    return match ? match[1] : "";
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

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function setMessage(container, text) {
    container.textContent = "";
    container.appendChild(el("p", "dc-empty", text));
  }

  /** Only http(s) image URLs are allowed; anything else renders a placeholder. */
  function safeImageUrl(value) {
    if (typeof value !== "string" || !value) return "";
    try {
      var parsed = new URL(value, API_BASE || window.location.origin);
      return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : "";
    } catch (err) {
      return "";
    }
  }

  function renderGrid(container, data, baseUrl) {
    var items = (data && data.results) || [];
    if (!items.length) {
      setMessage(container, "No items available right now.");
      return;
    }
    var limit = parseInt(container.dataset.renestLimit, 10) || 6;
    items = items.slice(0, limit);

    var grid = el("div", "dc-grid");
    items.forEach(function (item) {
      var id = parseInt(item.id, 10);
      if (!id) return;

      var price =
        item.price_type === "free"
          ? "Free"
          : "$" + (parseFloat(item.price_amount) || 0).toFixed(0);

      var card = el("a", "dc-card");
      card.href = baseUrl + "/listings/" + id;
      card.target = "_blank";
      card.rel = "noopener";

      var imageUrl = safeImageUrl(item.image_url || item.image_cdn_url);
      if (imageUrl) {
        var img = el("img", "dc-img");
        img.src = imageUrl;
        img.alt = "";
        img.loading = "lazy";
        card.appendChild(img);
      } else {
        card.appendChild(el("div", "dc-img"));
      }

      var body = el("div", "dc-body");
      body.appendChild(el("p", "dc-title", item.title || "Untitled"));
      var meta = el("div", "dc-meta");
      meta.appendChild(el("span", "dc-cat", item.category || ""));
      meta.appendChild(el("span", null, price));
      body.appendChild(meta);
      card.appendChild(body);
      grid.appendChild(card);
    });

    container.textContent = "";
    container.appendChild(grid);
  }

  function init() {
    injectStyles();
    var containers = document.querySelectorAll("[data-renest-campus]");
    Array.prototype.forEach.call(containers, function (node) {
      var slug = node.dataset.renestCampus;
      if (!slug) return;
      setMessage(node, "Loading…");
      fetch(API_BASE + "/api/embed/" + encodeURIComponent(slug) + "/listings")
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) { renderGrid(node, data, API_BASE); })
        .catch(function () {
          setMessage(node, "Could not load listings.");
        });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
