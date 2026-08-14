(function () {
  const filterRestoreKey = "blog-filter-restore";
  const filteredReturnKey = "blog-filtered-return";

  function setupMetadataRows(root) {
    const cards = Array.from(root.querySelectorAll(":scope > .blog-list__item"));
    let frame;

    function getLineCount(meta) {
      const lineTops = [];
      const items = Array.from(meta.children).filter(function (item) {
        return item.classList.contains("blog-list__meta-item");
      });

      items.forEach(function (item) {
        const top = item.getBoundingClientRect().top;
        if (!lineTops.some(function (lineTop) { return Math.abs(lineTop - top) <= 2; })) {
          lineTops.push(top);
        }
      });
      return lineTops.length;
    }

    function measure() {
      const cardRows = [];

      cards.forEach(function (card) {
        const meta = card.querySelector(".blog-list__meta");
        if (!meta) {
          return;
        }
        meta.classList.remove("blog-list__meta--two-rows");

        const top = card.getBoundingClientRect().top;
        let cardRow = cardRows.find(function (candidate) {
          return Math.abs(candidate.top - top) <= 2;
        });
        if (!cardRow) {
          cardRow = { top: top, metadata: [] };
          cardRows.push(cardRow);
        }
        cardRow.metadata.push(meta);
      });

      cardRows.forEach(function (cardRow) {
        const lineCounts = cardRow.metadata.map(getLineCount);
        if (Math.max.apply(null, lineCounts) !== 2) {
          return;
        }
        cardRow.metadata.forEach(function (meta, index) {
          if (lineCounts[index] === 1) {
            meta.classList.add("blog-list__meta--two-rows");
          }
        });
      });
    }

    function scheduleMeasure() {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(measure);
    }

    measure();
    if (window.ResizeObserver) {
      new ResizeObserver(scheduleMeasure).observe(root);
    } else {
      window.addEventListener("resize", scheduleMeasure);
    }
    if (document.fonts) {
      document.fonts.ready.then(scheduleMeasure);
    }
  }

  function setupTypeScroller(root) {
    const scroller = root.querySelector("[data-blog-type-scroll]");
    const previous = root.querySelector("[data-blog-type-previous]");
    const next = root.querySelector("[data-blog-type-next]");
    const selected = scroller && scroller.querySelector('[aria-current="true"]');
    const overflowTolerance = 1;
    let frame;

    if (!scroller || !previous || !next) {
      return;
    }

    function updateControls() {
      const maximumScroll = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
      previous.hidden = scroller.scrollLeft <= overflowTolerance;
      next.hidden = scroller.scrollLeft >= maximumScroll - overflowTolerance;
    }

    function scheduleUpdate() {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(updateControls);
    }

    function revealSelected() {
      if (!selected) {
        updateControls();
        return;
      }

      const edgeClearance = 44;
      const scrollerRect = scroller.getBoundingClientRect();
      const selectedRect = selected.getBoundingClientRect();
      const visibleStart = scrollerRect.left + edgeClearance;
      const visibleEnd = scrollerRect.right - edgeClearance;
      if (selectedRect.left < visibleStart) {
        scroller.scrollLeft -= visibleStart - selectedRect.left;
      } else if (selectedRect.right > visibleEnd) {
        scroller.scrollLeft += selectedRect.right - visibleEnd;
      }
      updateControls();
    }

    function scrollTypes(direction) {
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const items = Array.from(scroller.querySelectorAll("li"));
      const scrollerRect = scroller.getBoundingClientRect();
      const edgeClearance = 44;
      const visibleStart = scrollerRect.left + (previous.hidden ? 0 : edgeClearance);
      const firstVisibleIndex = items.findIndex(function (item) {
        return item.getBoundingClientRect().right > visibleStart + overflowTolerance;
      });
      const currentIndex = firstVisibleIndex === -1 ? items.length - 1 : firstVisibleIndex;
      const targetIndex = Math.max(0, Math.min(items.length - 1, currentIndex + direction));
      const targetRect = items[targetIndex].getBoundingClientRect();
      const targetStart = targetIndex === 0 ? scrollerRect.left : scrollerRect.left + edgeClearance;

      scroller.scrollBy({
        left: targetRect.left - targetStart,
        behavior: reducedMotion ? "auto" : "smooth",
      });
    }

    previous.addEventListener("click", function () {
      scrollTypes(-1);
    });
    next.addEventListener("click", function () {
      scrollTypes(1);
    });
    scroller.addEventListener("scroll", scheduleUpdate, { passive: true });

    revealSelected();
    if (window.ResizeObserver) {
      new ResizeObserver(revealSelected).observe(scroller);
    } else {
      window.addEventListener("resize", revealSelected);
    }
    if (document.fonts) {
      document.fonts.ready.then(revealSelected);
    }
  }

  function setupFilters(root) {
    const form = root.querySelector("[data-blog-filter-form]");
    const panel = root.querySelector("[data-blog-filter-panel]");
    const toggle = root.querySelector("[data-blog-filter-toggle]");
    const dropdowns = Array.from(root.querySelectorAll("[data-blog-filter-dropdown]"));
    const results = document.querySelector("[data-blog-filter-results]");
    const status = root.querySelector("[data-blog-filter-status]") || document.querySelector("[data-blog-filter-status]");

    if (!form || !panel || !toggle) {
      return;
    }

    root.classList.add("blog-filters--enhanced");

    function updateFilteredReturnLinks() {
      let returnUrl = form.action;

      if (root.dataset.blogFilterActive === "true") {
        returnUrl = window.location.pathname + window.location.search;
      } else {
        try {
          window.sessionStorage.removeItem(filteredReturnKey);
        } catch {
          // Direct Blog visits must keep working when browser storage is unavailable.
        }
      }

      document.querySelectorAll("[data-blog-return-link]").forEach(function (link) {
        link.href = returnUrl;
      });
    }

    function rememberFilteredReturn(event) {
      const link = event.target.closest("[data-blog-article-link]");
      if (
        !link
        || root.dataset.blogFilterActive !== "true"
        || event.defaultPrevented
        || event.button !== 0
        || event.ctrlKey
        || event.metaKey
        || event.shiftKey
        || event.altKey
        || link.target === "_blank"
      ) {
        return;
      }

      try {
        const destination = new URL(link.href, window.location.href);
        const listUrl = new URL(form.action, window.location.href);
        window.sessionStorage.setItem(filteredReturnKey, JSON.stringify({
          articlePath: destination.pathname,
          listPath: listUrl.pathname,
          returnUrl: window.location.pathname + window.location.search,
        }));
      } catch {
        // Article navigation must keep working when browser storage is unavailable.
      }
    }

    function setPanelOpen(open) {
      panel.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
    }

    function setDropdownOpen(dropdown, open) {
      const dropdownToggle = dropdown.querySelector("[data-blog-dropdown-toggle]");
      const dropdownPanel = dropdown.querySelector("[data-blog-dropdown-panel]");
      dropdownPanel.hidden = !open;
      dropdownToggle.setAttribute("aria-expanded", String(open));
    }

    function closeDropdowns(except) {
      dropdowns.forEach(function (dropdown) {
        if (dropdown !== except) {
          setDropdownOpen(dropdown, false);
        }
      });
    }

    function markUpdating() {
      if (results) {
        results.setAttribute("aria-busy", "true");
      }
      if (status) {
        status.textContent = root.dataset.blogFilterUpdating || "Updating articles…";
      }
    }

    function rememberDropdown(dropdown) {
      const dropdownPanel = dropdown.querySelector("[data-blog-dropdown-panel]");
      const scrollableOptions = dropdown.querySelector(".blog-filters__options--scroll");

      if (!dropdownPanel || !dropdownPanel.id) {
        return;
      }

      try {
        window.sessionStorage.setItem(filterRestoreKey, JSON.stringify({
          path: new URL(form.action, window.location.href).pathname,
          dropdownPanelId: dropdownPanel.id,
          scrollTop: scrollableOptions ? scrollableOptions.scrollTop : 0,
        }));
      } catch {
        // Filtering must keep working when browser storage is unavailable.
      }
    }

    function takeRememberedDropdown() {
      let remembered;

      try {
        remembered = window.sessionStorage.getItem(filterRestoreKey);
        window.sessionStorage.removeItem(filterRestoreKey);
      } catch {
        return null;
      }

      if (!remembered) {
        return null;
      }

      try {
        const state = JSON.parse(remembered);
        if (state.path !== window.location.pathname || typeof state.dropdownPanelId !== "string") {
          return null;
        }
        return state;
      } catch {
        return null;
      }
    }

    function restoreDropdown(state) {
      const dropdown = dropdowns.find(function (candidate) {
        const dropdownPanel = candidate.querySelector("[data-blog-dropdown-panel]");
        return dropdownPanel && dropdownPanel.id === state.dropdownPanelId;
      });

      if (!dropdown) {
        return false;
      }

      setPanelOpen(true);
      closeDropdowns(dropdown);
      setDropdownOpen(dropdown, true);

      window.requestAnimationFrame(function () {
        const scrollableOptions = dropdown.querySelector(".blog-filters__options--scroll");

        if (scrollableOptions && Number.isFinite(state.scrollTop)) {
          scrollableOptions.scrollTop = state.scrollTop;
        }
      });

      return true;
    }

    toggle.addEventListener("click", function () {
      const opening = panel.hidden;
      setPanelOpen(opening);
    });

    dropdowns.forEach(function (dropdown) {
      const dropdownToggle = dropdown.querySelector("[data-blog-dropdown-toggle]");
      const dropdownPanel = dropdown.querySelector("[data-blog-dropdown-panel]");
      dropdownToggle.addEventListener("click", function () {
        const opening = dropdownPanel.hidden;
        closeDropdowns(dropdown);
        setDropdownOpen(dropdown, opening);
      });
    });

    form.addEventListener("change", function (event) {
      const control = event.target;
      if (control.matches("[data-blog-filter-single], [data-blog-filter-date], input[name='tag']")) {
        if (control.matches("input[name='tag']")) {
          const dropdown = control.closest("[data-blog-filter-dropdown]");
          if (dropdown) {
            rememberDropdown(dropdown);
          }
        }
        form.requestSubmit();
      }
    });

    form.addEventListener("submit", function () {
      markUpdating();
      setPanelOpen(false);
      closeDropdowns();
    });

    document.addEventListener("click", rememberFilteredReturn);
    updateFilteredReturnLinks();

    const rememberedDropdown = takeRememberedDropdown();
    if (!rememberedDropdown || !restoreDropdown(rememberedDropdown)) {
      setPanelOpen(false);
    }
  }

  const metadataRows = document.querySelectorAll(".blog-list .stack-list");
  metadataRows.forEach(setupMetadataRows);

  const typeNavigations = document.querySelectorAll("[data-blog-type-nav]");
  typeNavigations.forEach(setupTypeScroller);

  const filterRoots = document.querySelectorAll("[data-blog-filter-root]");
  filterRoots.forEach(setupFilters);

  document.addEventListener("click", function (event) {
    filterRoots.forEach(function (root) {
      root.querySelectorAll("[data-blog-filter-dropdown]").forEach(function (dropdown) {
        if (!dropdown.contains(event.target)) {
          const dropdownToggle = dropdown.querySelector("[data-blog-dropdown-toggle]");
          const dropdownPanel = dropdown.querySelector("[data-blog-dropdown-panel]");
          dropdownPanel.hidden = true;
          dropdownToggle.setAttribute("aria-expanded", "false");
        }
      });
    });

  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
      return;
    }
    filterRoots.forEach(function (root) {
      const openToggle = root.querySelector('[data-blog-dropdown-toggle][aria-expanded="true"]');
      if (!openToggle) return;
      const dropdown = openToggle.closest("[data-blog-filter-dropdown]");
      dropdown.querySelector("[data-blog-dropdown-panel]").hidden = true;
      openToggle.setAttribute("aria-expanded", "false");
      openToggle.focus();
    });
  });
}());
