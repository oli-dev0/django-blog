(function () {
  "use strict";

  const filteredReturnKey = "blog-filtered-return";

  function restoreFilteredReturnLinks() {
    let remembered;

    try {
      remembered = window.sessionStorage.getItem(filteredReturnKey);
      window.sessionStorage.removeItem(filteredReturnKey);
    } catch {
      return;
    }

    if (!remembered) {
      return;
    }

    try {
      const state = JSON.parse(remembered);
      const returnUrl = new URL(state.returnUrl, window.location.href);
      if (
        state.articlePath !== window.location.pathname
        || returnUrl.origin !== window.location.origin
        || returnUrl.pathname !== state.listPath
        || !returnUrl.search
      ) {
        return;
      }

      document.querySelectorAll("[data-blog-return-link]").forEach(function (link) {
        link.href = returnUrl.pathname + returnUrl.search;
      });
    } catch {
      // Direct article visits keep their normal Blog links when state is invalid.
    }
  }

  restoreFilteredReturnLinks();

  function copyText(value) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(value);
    }

    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.top = "0";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();

    try {
      document.execCommand("copy");
      return Promise.resolve();
    } catch (error) {
      return Promise.reject(error);
    } finally {
      document.body.removeChild(textarea);
    }
  }

  function initializeShare() {
    const share = document.querySelector("[data-blog-share]");
    const shareButton = share && share.querySelector("[data-blog-share-button]");
    const shareMenu = share && share.querySelector("[data-blog-share-menu]");
    const copyLinkButton = share && share.querySelector("[data-blog-copy-link]");
    const copyLabel = copyLinkButton && copyLinkButton.querySelector("[data-blog-copy-label]");
    const titleElement = document.querySelector(".blog-article__header h1");

    if (!share || !shareButton || !shareMenu || !copyLinkButton || !copyLabel) {
      return;
    }

    const articleTitle = titleElement ? titleElement.textContent.trim() : document.title;
    const articleUrl = window.location.href;
    const shareLinks = [...shareMenu.querySelectorAll("[data-blog-share-platform]")];
    const defaultCopyLabel = "Copy link";
    let resetTimer = null;

    function shareUrl(base, parameters) {
      return base + "?" + new URLSearchParams(parameters).toString();
    }

    const platformUrls = {
      x: shareUrl("https://twitter.com/intent/tweet", {text: articleTitle, url: articleUrl}),
      facebook: shareUrl("https://www.facebook.com/sharer/sharer.php", {u: articleUrl}),
      linkedin: shareUrl("https://www.linkedin.com/feed/", {
        shareActive: "true",
        shareUrl: articleUrl,
        text: articleTitle,
      }),
      reddit: shareUrl("https://www.reddit.com/submit", {url: articleUrl, title: articleTitle}),
      whatsapp: shareUrl("https://wa.me/", {text: articleTitle + " " + articleUrl}),
      email: shareUrl("mailto:", {subject: articleTitle, body: articleUrl}),
    };

    shareLinks.forEach(function (link) {
      link.href = platformUrls[link.dataset.blogSharePlatform];
      link.addEventListener("click", function () {
        setMenuOpen(false);
        shareButton.focus({preventScroll: true});
      });
    });

    function setMenuOpen(open) {
      shareMenu.hidden = !open;
      shareButton.setAttribute("aria-expanded", String(open));
    }

    function openFallbackMenu() {
      setMenuOpen(true);
    }

    copyLinkButton.addEventListener("click", function () {
      copyText(articleUrl).then(function () {
        copyLinkButton.classList.add("is-copied");
        copyLinkButton.setAttribute("aria-label", "Article link copied");
        copyLabel.textContent = "Article link copied";
        window.clearTimeout(resetTimer);
        resetTimer = window.setTimeout(function () {
          copyLinkButton.classList.remove("is-copied");
          copyLinkButton.setAttribute("aria-label", defaultCopyLabel);
          copyLabel.textContent = defaultCopyLabel;
        }, 1800);
      }).catch(function () {
        copyLinkButton.classList.remove("is-copied");
        copyLinkButton.setAttribute("aria-label", "Copy failed");
        copyLabel.textContent = "Copy failed";
        window.clearTimeout(resetTimer);
        resetTimer = window.setTimeout(function () {
          copyLinkButton.setAttribute("aria-label", defaultCopyLabel);
          copyLabel.textContent = defaultCopyLabel;
        }, 1800);
      });
    });

    shareButton.addEventListener("click", function () {
      const touchFirst = typeof window.matchMedia === "function"
        && window.matchMedia("(pointer: coarse)").matches;

      if (touchFirst && window.isSecureContext && typeof navigator.share === "function") {
        setMenuOpen(false);
        try {
          navigator.share({title: articleTitle, url: articleUrl}).catch(function (error) {
            if (error && error.name === "AbortError") {
              return;
            }
            openFallbackMenu();
          });
        } catch {
          openFallbackMenu();
        }
        return;
      }

      setMenuOpen(shareMenu.hidden);
    });

    document.addEventListener("click", function (event) {
      if (!shareMenu.hidden && !share.contains(event.target)) {
        setMenuOpen(false);
      }
    });

    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || shareMenu.hidden) {
        return;
      }
      setMenuOpen(false);
      shareButton.focus({preventScroll: true});
    });

    shareButton.hidden = false;
  }

  initializeShare();

  function initializePrint() {
    const printButton = document.querySelector("[data-blog-print]");

    if (!printButton || typeof window.print !== "function") {
      return;
    }

    let printState = null;

    function prepareForPrint() {
      if (printState) {
        return;
      }

      const article = document.querySelector("[data-blog-read-mode-root]");
      const details = article ? [...article.querySelectorAll(".blog-faq__item")] : [];
      const images = article ? [...article.querySelectorAll("img[loading='lazy']")] : [];
      printState = {
        details: details.map(function (detail) {
          const wasOpen = detail.open;
          detail.open = true;
          return [detail, wasOpen];
        }),
        images: images.map(function (image) {
          const loading = image.getAttribute("loading");
          image.setAttribute("loading", "eager");
          return [image, loading];
        }),
      };
    }

    function restoreAfterPrint() {
      if (!printState) {
        return;
      }

      printState.details.forEach(function ([detail, wasOpen]) {
        detail.open = wasOpen;
      });
      printState.images.forEach(function ([image, loading]) {
        if (loading) {
          image.setAttribute("loading", loading);
        } else {
          image.removeAttribute("loading");
        }
      });
      printState = null;
    }

    function waitForImages() {
      if (!printState) {
        return Promise.resolve();
      }

      return Promise.all(printState.images.map(function ([image]) {
        if (image.complete) {
          return Promise.resolve();
        }
        return new Promise(function (resolve) {
          image.addEventListener("load", resolve, {once: true});
          image.addEventListener("error", resolve, {once: true});
        });
      }));
    }

    printButton.hidden = false;
    window.addEventListener("beforeprint", prepareForPrint);
    window.addEventListener("afterprint", restoreAfterPrint);
    printButton.addEventListener("click", function () {
      prepareForPrint();
      waitForImages().then(function () {
        window.print();
      });
    });
  }

  initializePrint();

  function initializeReadMode() {
    const article = document.querySelector("[data-blog-read-mode-root]");
    const entryButton = article && article.querySelector("[data-blog-read-mode-entry]");
    const exitButton = article && article.querySelector("[data-blog-read-mode-exit]");
    const toolbar = article && article.querySelector("[data-blog-read-mode-toolbar]");
    const progress = article && article.querySelector("[data-blog-read-mode-progress]");

    if (
      !article
      || !entryButton
      || !exitButton
      || !toolbar
      || !progress
      || typeof window.requestAnimationFrame !== "function"
    ) {
      return;
    }
    if (article.dataset.blogReadModeInitialized === "true") {
      return;
    }

    article.dataset.blogReadModeInitialized = "true";
    const root = document.documentElement;
    let active = false;
    let frameId = null;

    function cancelFrame() {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId);
        frameId = null;
      }
    }

    function articleMetrics() {
      const rect = article.getBoundingClientRect();
      const height = Math.max(rect.height, article.scrollHeight);
      const start = rect.top + window.scrollY;
      return {
        start: start,
        end: start + height,
        scrollable: Math.max(0, height - window.innerHeight),
      };
    }

    function articleProgress() {
      const metrics = articleMetrics();
      if (metrics.scrollable === 0) {
        return window.scrollY + window.innerHeight >= metrics.end ? 100 : 0;
      }

      const value = ((window.scrollY - metrics.start) / metrics.scrollable) * 100;
      return Math.min(100, Math.max(0, value));
    }

    function restoreArticlePosition(value) {
      const metrics = articleMetrics();
      const target = metrics.scrollable === 0
        ? metrics.start
        : metrics.start + (metrics.scrollable * value) / 100;
      window.scrollTo(0, Math.max(0, target));
    }

    function updateProgress() {
      progress.value = articleProgress();
    }

    function queueProgressUpdate() {
      if (!active || frameId !== null) {
        return;
      }
      frameId = window.requestAnimationFrame(function () {
        frameId = null;
        if (active) {
          updateProgress();
        }
      });
    }

    function restoreAfterLayout(value, callback) {
      cancelFrame();
      frameId = window.requestAnimationFrame(function () {
        frameId = null;
        restoreArticlePosition(value);
        if (callback) {
          callback();
        }
        if (active) {
          updateProgress();
        }
      });
    }

    function enterReadMode() {
      if (active) {
        return;
      }

      const value = articleProgress();
      active = true;
      root.classList.add("blog-read-mode-active");
      entryButton.hidden = true;
      toolbar.hidden = false;
      exitButton.hidden = false;
      progress.hidden = false;
      restoreAfterLayout(value, function () {
        exitButton.focus({preventScroll: true});
      });
    }

    function entryButtonIsVisible() {
      const rect = entryButton.getBoundingClientRect();
      return rect.bottom > 0 && rect.top < window.innerHeight;
    }

    function exitReadMode() {
      if (!active) {
        return;
      }

      const value = articleProgress();
      active = false;
      cancelFrame();
      root.classList.remove("blog-read-mode-active");
      toolbar.hidden = true;
      exitButton.hidden = true;
      progress.hidden = true;
      entryButton.hidden = false;
      restoreAfterLayout(value, function () {
        if (entryButtonIsVisible()) {
          entryButton.focus({preventScroll: true});
        } else {
          article.focus({preventScroll: true});
        }
      });
    }

    entryButton.hidden = false;
    entryButton.addEventListener("click", enterReadMode);
    exitButton.addEventListener("click", exitReadMode);
    window.addEventListener("scroll", queueProgressUpdate, {passive: true});
    window.addEventListener("resize", queueProgressUpdate, {passive: true});
    window.addEventListener("pagehide", cancelFrame);
  }

  initializeReadMode();

  const dialog = document.querySelector("#blog-image-dialog");
  const dialogImage = dialog && dialog.querySelector("[data-blog-dialog-image]");
  const stage = dialog && dialog.querySelector("[data-blog-dialog-stage]");
  const previousButton = dialog && dialog.querySelector("[data-blog-dialog-previous]");
  const nextButton = dialog && dialog.querySelector("[data-blog-dialog-next]");
  const loadingStatus = dialog && dialog.querySelector("[data-blog-dialog-loading]");
  const errorStatus = dialog && dialog.querySelector("[data-blog-dialog-error]");
  const details = dialog && dialog.querySelector("[data-blog-dialog-details]");
  const positionStatus = dialog && dialog.querySelector("[data-blog-dialog-position]");
  const caption = dialog && dialog.querySelector("[data-blog-dialog-caption]");
  const captionTitle = dialog && dialog.querySelector("[data-blog-dialog-caption-title]");
  const captionText = dialog && dialog.querySelector("[data-blog-dialog-caption-text]");
  const triggers = document.querySelectorAll("[data-blog-image-dialog]");

  if (!dialog || !dialogImage || typeof dialog.showModal !== "function") {
    return;
  }

  let trigger = null;
  let group = [];
  let currentIndex = 0;
  let comparison = false;
  let ownedHistory = false;
  let scrollPosition = 0;
  let closingFromHistory = false;
  let touchStart = null;

  function setHidden(element, hidden) {
    if (element) {
      element.hidden = hidden;
    }
  }

  function resetDialog() {
    dialogImage.removeAttribute("src");
    dialogImage.alt = "";
    dialogImage.onerror = null;
    setHidden(loadingStatus, true);
    setHidden(errorStatus, true);
    setHidden(previousButton, true);
    setHidden(nextButton, true);
    setHidden(details, true);
    setHidden(caption, true);
  }

  function restoreFocus() {
    if (trigger) {
      trigger.focus({preventScroll: true});
    }
    window.scrollTo(0, scrollPosition);
    trigger = null;
    group = [];
    comparison = false;
  }

  function finishClose() {
    const shouldReturn = !closingFromHistory && ownedHistory;
    ownedHistory = false;
    resetDialog();
    restoreFocus();
    if (shouldReturn) {
      window.history.back();
    }
    closingFromHistory = false;
  }

  function closeDialog(fromHistory) {
    closingFromHistory = Boolean(fromHistory);
    if (dialog.open) {
      dialog.close();
    } else {
      finishClose();
    }
  }

  function updateNavigation() {
    setHidden(previousButton, !comparison);
    setHidden(nextButton, !comparison);
    if (!comparison) {
      setHidden(details, true);
      return;
    }

    previousButton.disabled = currentIndex === 0;
    nextButton.disabled = currentIndex === group.length - 1;
    setHidden(details, false);
    positionStatus.textContent = `${positionStatus.dataset.positionLabel} ${currentIndex + 1} ${positionStatus.dataset.positionOf} ${group.length}`;
  }

  function showLoadError() {
    setHidden(loadingStatus, true);
    setHidden(errorStatus, false);
  }

  function loadImage(link) {
    const image = link.querySelector("img");
    const figure = link.closest("[data-blog-image-comparison]");
    const alt = image?.alt || link.getAttribute("aria-label") || "";
    dialogImage.removeAttribute("src");
    dialogImage.alt = alt;
    setHidden(loadingStatus, false);
    setHidden(errorStatus, true);
    dialogImage.onerror = function () {
      showLoadError();
    };
    dialogImage.onload = function () {
      setHidden(loadingStatus, true);
      setHidden(errorStatus, true);
    };

    if (comparison && figure) {
      const title = figure.dataset.captionTitle || "";
      const text = figure.dataset.captionText || "";
      captionTitle.textContent = title;
      captionText.textContent = text;
      setHidden(caption, !(title || text));
    }
    dialogImage.src = link.href;
  }

  function openDialog(link) {
    const figure = link.closest("[data-blog-image-comparison]");
    const localGroup = figure ? [...figure.querySelectorAll("a[data-blog-image-dialog]")] : [link];
    if (!localGroup.length) {
      return false;
    }

    trigger = link;
    group = localGroup;
    currentIndex = Math.max(0, group.indexOf(link));
    comparison = Boolean(figure);
    scrollPosition = window.scrollY;
    resetDialog();
    updateNavigation();
    dialog.showModal();
    loadImage(group[currentIndex]);
    window.history.pushState(
      {blogImageDialog: true},
      "",
      window.location.pathname + window.location.search + "#blog-image-dialog",
    );
    ownedHistory = true;
    const closeButton = dialog.querySelector("form button");
    if (closeButton) {
      closeButton.focus();
    }
    return true;
  }

  function move(delta) {
    if (!comparison) {
      return;
    }
    const nextIndex = currentIndex + delta;
    if (nextIndex < 0 || nextIndex >= group.length) {
      return;
    }
    currentIndex = nextIndex;
    updateNavigation();
    loadImage(group[currentIndex]);
  }

  triggers.forEach(function (link) {
    link.addEventListener("click", function (event) {
      if (openDialog(link)) {
        event.preventDefault();
      }
    });
  });

  previousButton.addEventListener("click", function () {
    move(-1);
  });
  nextButton.addEventListener("click", function () {
    move(1);
  });

  dialog.addEventListener("cancel", function (event) {
    event.preventDefault();
    closeDialog(false);
  });

  dialog.addEventListener("close", function () {
    finishClose();
  });

  dialog.addEventListener("click", function (event) {
    const clickedControl = event.target.closest("button, form, [data-blog-dialog-details]");
    if (!clickedControl) {
      closeDialog(false);
    }
  });

  dialog.addEventListener("keydown", function (event) {
    if (!comparison) {
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      move(-1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      move(1);
    }
  });

  stage.addEventListener("touchstart", function (event) {
    if (event.touches.length === 1) {
      touchStart = event.touches[0];
    } else {
      touchStart = null;
    }
  }, {passive: true});

  stage.addEventListener("touchend", function (event) {
    if (!comparison || !touchStart || event.changedTouches.length !== 1) {
      touchStart = null;
      return;
    }
    const touch = event.changedTouches[0];
    const deltaX = touch.clientX - touchStart.clientX;
    const deltaY = touch.clientY - touchStart.clientY;
    touchStart = null;
    if (Math.abs(deltaX) < 48 || Math.abs(deltaX) <= Math.abs(deltaY)) {
      return;
    }
    event.preventDefault();
    move(deltaX < 0 ? 1 : -1);
  }, {passive: false});

  window.addEventListener("popstate", function () {
    if (ownedHistory) {
      closeDialog(true);
    }
  });
}());
