/**
 * Applies the saved (or OS) colour theme before first paint.
 *
 * Loaded as a same-origin classic script rather than an inline <script> so it
 * runs under a strict `script-src 'self'` Content-Security-Policy. As an inline
 * script it was silently blocked in production, which reset every user's dark
 * mode to light on each page load.
 */
(function () {
  try {
    var theme = localStorage.getItem("renest-theme");
    // No saved choice yet: follow the OS preference.
    if (!theme && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      theme = "dark";
    }
    if (theme === "dark") document.documentElement.classList.add("dark");
  } catch (e) {
    /* Private mode or blocked storage: fall back to the light theme. */
  }
})();
