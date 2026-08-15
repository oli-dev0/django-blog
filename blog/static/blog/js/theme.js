(function () {
  "use strict";

  const storageKey = "statusAppearance";
  const allowedThemes = new Set(["light", "dark"]);
  const root = document.documentElement;
  const control = document.querySelector("[data-status-appearance-toggle]");
  const compactHeaderMedia = window.matchMedia("(max-width: 934px)");

  function readStoredTheme() {
    try {
      return window.localStorage.getItem(storageKey);
    } catch (error) {
      return null;
    }
  }

  function storeTheme(theme) {
    try {
      window.localStorage.setItem(storageKey, theme);
    } catch (error) {
      return;
    }
  }

  function applyTheme(theme) {
    const selectedTheme = allowedThemes.has(theme) ? theme : "dark";
    root.setAttribute("data-status-theme", selectedTheme);
    if (control) {
      const input = control.querySelector(`input[value="${selectedTheme}"]`);
      if (input) {
        input.checked = true;
      }
    }
  }

  applyTheme(readStoredTheme() || "dark");

  if (control) {
    control.addEventListener("change", function (event) {
      if (!event.target.matches("input[name='status-appearance']")) {
        return;
      }
      applyTheme(event.target.value);
      storeTheme(event.target.value);
    });
    control.hidden = false;
  }

  const header = document.querySelector(".site-header");
  const headerNav = header ? header.querySelector(".site-nav") : null;
  if (!header || !headerNav) {
    return;
  }

  let lastScrollY = window.scrollY;
  let scrollDirection = null;
  let directionStartY = lastScrollY;
  let expandedHeaderHeight = header.getBoundingClientRect().height;
  let ticking = false;
  const expandAtTopThreshold = 12;
  const compactOnDownDistance = 24;
  const expandOnUpDistance = 40;

  function setCompactHeader(isCompact) {
    header.classList.toggle("site-header--compact", isCompact);
  }

  function updateCompactHeader() {
    const currentScrollY = Math.max(window.scrollY, 0);
    const delta = currentScrollY - lastScrollY;

    if (!header.classList.contains("site-header--compact")) {
      expandedHeaderHeight = header.getBoundingClientRect().height;
    }

    if (!compactHeaderMedia.matches || currentScrollY <= expandAtTopThreshold) {
      setCompactHeader(false);
      scrollDirection = null;
      directionStartY = currentScrollY;
      lastScrollY = currentScrollY;
      ticking = false;
      return;
    }

    if (Math.abs(delta) < 2) {
      lastScrollY = currentScrollY;
      ticking = false;
      return;
    }

    const nextDirection = delta > 0 ? "down" : "up";
    if (nextDirection !== scrollDirection) {
      scrollDirection = nextDirection;
      directionStartY = currentScrollY;
    }

    const directionDistance = Math.abs(currentScrollY - directionStartY);
    if (scrollDirection === "down" && currentScrollY > expandedHeaderHeight && directionDistance >= compactOnDownDistance) {
      setCompactHeader(true);
    } else if (scrollDirection === "up" && directionDistance >= expandOnUpDistance) {
      setCompactHeader(false);
    }

    lastScrollY = currentScrollY;
    ticking = false;
  }

  function requestCompactHeaderUpdate() {
    if (ticking) {
      return;
    }
    ticking = true;
    window.requestAnimationFrame(updateCompactHeader);
  }

  window.addEventListener("scroll", requestCompactHeaderUpdate, {passive: true});
  compactHeaderMedia.addEventListener("change", requestCompactHeaderUpdate);
})();
