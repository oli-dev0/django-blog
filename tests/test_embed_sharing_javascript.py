from pathlib import Path

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase


class BlogEmbedSharingJavaScriptContractTests(SimpleTestCase):
    def javascript(self):
        resolved = finders.find('blog/js/embed-sharing.js')
        self.assertIsNotNone(resolved, 'Embed sharing JavaScript does not resolve')
        return Path(resolved).read_text(encoding='utf-8')

    def test_uses_exact_provider_loaders_once_and_initializes_blocks_once(self):
        javascript = self.javascript()

        self.assertIn('x: "https://platform.x.com/widgets.js"', javascript)
        self.assertIn('reddit: "https://embed.reddit.com/widgets.js"', javascript)
        self.assertIn('if (providerScripts[provider])', javascript)
        self.assertIn('providerScripts[provider] = new Promise', javascript)
        self.assertIn('if (root.dataset.blogEmbedInitialized)', javascript)
        self.assertIn('root.dataset.blogEmbedInitialized = "true"', javascript)
        self.assertIn('document.querySelectorAll("[data-blog-embed]").forEach(initializeEmbed)', javascript)

    def test_uses_native_targets_and_validates_browser_values(self):
        javascript = self.javascript()

        self.assertIn('widgets.createTweet(itemId, target, {dnt: true})', javascript)
        self.assertIn('/^\\d+$/.test(itemId)', javascript)
        self.assertIn('target.classList.contains("reddit-embed-bq")', javascript)
        self.assertIn("iframe[src^='https://www.youtube-nocookie.com/embed/']", javascript)
        self.assertIn('const youtubeApiSource = "https://www.youtube.com/iframe_api"', javascript)
        self.assertIn('if (youtubeApiPromise)', javascript)
        self.assertIn('new YT.Player(iframe, {', javascript)
        self.assertIn('onError: function () {', javascript)

    def test_failures_are_local_and_observers_and_timers_are_bounded(self):
        javascript = self.javascript()

        self.assertIn('const fallback = root.querySelector("[data-blog-embed-fallback]")', javascript)
        self.assertIn('fallback.hidden = false', javascript)
        self.assertIn('observer.disconnect()', javascript)
        self.assertIn('window.clearTimeout(timer)', javascript)
        self.assertIn('window.setTimeout', javascript)
        self.assertIn('script.removeEventListener("load", loaded)', javascript)
        self.assertIn('script.removeEventListener("error", failed)', javascript)

    def test_avoids_unsafe_or_authoritative_browser_behavior(self):
        javascript = self.javascript()

        for forbidden in (
            'innerHTML',
            'outerHTML',
            'insertAdjacentHTML',
            'eval(',
            'oembed',
            'playVideo(',
            'play(',
            'postMessage(',
            'onclick=',
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, javascript)
