(function () {
  "use strict";

  const widgetTimeoutMilliseconds = 10000;
  const providerScripts = Object.create(null);
  const providerSources = {
    x: "https://platform.x.com/widgets.js",
    reddit: "https://embed.reddit.com/widgets.js",
  };
  const youtubeApiSource = "https://www.youtube.com/iframe_api";
  let youtubeApiPromise;

  function showFallback(root) {
    const fallback = root.querySelector("[data-blog-embed-fallback]");
    if (fallback) {
      fallback.hidden = false;
    }
  }

  function loadProviderScript(provider) {
    if (providerScripts[provider]) {
      return providerScripts[provider];
    }

    const source = providerSources[provider];
    if (!source) {
      return Promise.reject(new Error("Unsupported embed provider"));
    }

    providerScripts[provider] = new Promise(function (resolve, reject) {
      const script = document.createElement("script");
      let timer;

      function cleanup() {
        window.clearTimeout(timer);
        script.removeEventListener("load", loaded);
        script.removeEventListener("error", failed);
      }

      function loaded() {
        cleanup();
        resolve();
      }

      function failed() {
        cleanup();
        reject(new Error("Embed provider script failed"));
      }

      script.src = source;
      script.defer = true;
      script.dataset.blogEmbedProvider = provider;
      script.addEventListener("load", loaded);
      script.addEventListener("error", failed);
      timer = window.setTimeout(failed, widgetTimeoutMilliseconds);
      document.head.appendChild(script);
    });

    return providerScripts[provider];
  }

  function waitForWidget(root) {
    return new Promise(function (resolve, reject) {
      let observer;
      let timer;

      function widget() {
        return root.querySelector("iframe");
      }

      function cleanup() {
        window.clearTimeout(timer);
        if (observer) {
          observer.disconnect();
        }
      }

      function confirm() {
        if (!widget()) {
          return false;
        }
        cleanup();
        resolve();
        return true;
      }

      if (confirm()) {
        return;
      }
      if (window.MutationObserver) {
        observer = new MutationObserver(confirm);
        observer.observe(root, {childList: true, subtree: true});
      }
      timer = window.setTimeout(function () {
        cleanup();
        reject(new Error("Embed widget timed out"));
      }, widgetTimeoutMilliseconds);
    });
  }

  function loadYouTubeApi() {
    if (window.YT && typeof window.YT.Player === "function") {
      return Promise.resolve(window.YT);
    }
    if (youtubeApiPromise) {
      return youtubeApiPromise;
    }

    youtubeApiPromise = new Promise(function (resolve, reject) {
      const previousReady = window.onYouTubeIframeAPIReady;
      const script = document.createElement("script");
      let timer;

      function cleanup() {
        window.clearTimeout(timer);
        script.removeEventListener("error", failed);
        if (window.onYouTubeIframeAPIReady === ready) {
          window.onYouTubeIframeAPIReady = previousReady;
        }
      }

      function ready() {
        cleanup();
        if (window.YT && typeof window.YT.Player === "function") {
          resolve(window.YT);
        } else {
          reject(new Error("YouTube iframe API unavailable"));
        }
        if (typeof previousReady === "function") {
          previousReady();
        }
      }

      function failed() {
        cleanup();
        reject(new Error("YouTube iframe API failed"));
      }

      window.onYouTubeIframeAPIReady = ready;
      script.src = youtubeApiSource;
      script.defer = true;
      script.addEventListener("error", failed);
      timer = window.setTimeout(failed, widgetTimeoutMilliseconds);
      document.head.appendChild(script);
    });

    return youtubeApiPromise;
  }

  function initializeX(root, target, itemId) {
    return loadProviderScript("x").then(function () {
      const widgets = window.twttr && window.twttr.widgets;
      if (!widgets || typeof widgets.createTweet !== "function") {
        throw new Error("X widget factory unavailable");
      }
      const factoryResult = widgets.createTweet(itemId, target, {dnt: true});
      const widgetReady = waitForWidget(root);
      const factoryReady = Promise.resolve(factoryResult)
        .then(function (widget) {
          if (!widget) {
            throw new Error("X widget unavailable");
          }
          return widgetReady;
        });
      return Promise.race([factoryReady, widgetReady]);
    });
  }

  function initializeReddit(root) {
    return loadProviderScript("reddit").then(function () {
      return waitForWidget(root);
    });
  }

  function initializeYouTube(root, iframe) {
    iframe.addEventListener("error", function () {
      showFallback(root);
    }, {once: true});

    loadYouTubeApi().then(function (YT) {
      new YT.Player(iframe, {
        events: {
          onError: function () {
            showFallback(root);
          },
        },
      });
    }).catch(function () {
      showFallback(root);
    });
  }

  function initializeEmbed(root) {
    if (root.dataset.blogEmbedInitialized) {
      return;
    }
    root.dataset.blogEmbedInitialized = "true";

    const provider = root.dataset.blogEmbedPlatform;
    const itemId = root.dataset.blogEmbedId;
    const target = root.querySelector("[data-blog-embed-target]");

    if (provider === "youtube") {
      const iframe = root.querySelector("iframe[src^='https://www.youtube-nocookie.com/embed/']");
      if (iframe) {
        initializeYouTube(root, iframe);
      } else {
        showFallback(root);
      }
      return;
    }

    let initialization;
    if (provider === "x" && /^\d+$/.test(itemId) && target) {
      initialization = initializeX(root, target, itemId);
    } else if (provider === "reddit" && target && target.classList.contains("reddit-embed-bq")) {
      initialization = initializeReddit(root);
    } else {
      showFallback(root);
      return;
    }

    initialization.catch(function () {
      showFallback(root);
    });
  }

  document.querySelectorAll("[data-blog-embed]").forEach(initializeEmbed);
}());
