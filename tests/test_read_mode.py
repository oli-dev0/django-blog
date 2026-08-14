from pathlib import Path

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase


class BlogReadModeStaticContractTests(SimpleTestCase):
    stylesheet_paths = (
        'blog/css/article.css',
        'vanta_site/css/blog.css',
    )

    def static_text(self, path):
        resolved = finders.find(path)
        self.assertIsNotNone(resolved, f'Static asset does not resolve: {path}')
        return Path(resolved).read_text(encoding='utf-8')

    def test_javascript_preserves_reading_state_and_accessible_focus(self):
        javascript = self.static_text('blog/js/article.js')
        read_mode = javascript[
            javascript.index('  function initializeReadMode() {') : javascript.index('  initializeReadMode();')
        ]

        for behavior in (
            'Math.min(100, Math.max(0, value))',
            'window.scrollTo(0, Math.max(0, target));',
            'root.classList.add("blog-read-mode-active");',
            'root.classList.remove("blog-read-mode-active");',
            'entryButton.hidden = true;',
            'entryButton.hidden = false;',
            'toolbar.hidden = false;',
            'toolbar.hidden = true;',
            'exitButton.focus({preventScroll: true});',
            'article.focus({preventScroll: true});',
            'window.addEventListener("scroll", queueProgressUpdate, {passive: true});',
            'window.addEventListener("resize", queueProgressUpdate, {passive: true});',
            'window.addEventListener("pagehide", cancelFrame);',
        ):
            with self.subTest(behavior=behavior):
                self.assertIn(behavior, read_mode)

        self.assertIn('window.requestAnimationFrame(function () {', read_mode)
        self.assertIn('window.cancelAnimationFrame(frameId);', read_mode)
        self.assertNotIn('window.history.', read_mode)
        self.assertNotIn('window.localStorage', read_mode)
        self.assertNotIn('window.sessionStorage', read_mode)

    def test_javascript_reveals_print_action_and_opens_native_print_preview(self):
        javascript = self.static_text('blog/js/article.js')
        print_action = javascript[
            javascript.index('  function initializePrint() {') : javascript.index('  initializePrint();')
        ]

        for behavior in (
            'document.querySelector("[data-blog-print]")',
            'typeof window.print !== "function"',
            'printButton.hidden = false;',
            'window.addEventListener("beforeprint", prepareForPrint);',
            'window.addEventListener("afterprint", restoreAfterPrint);',
            'detail.open = true;',
            'image.setAttribute("loading", "eager");',
            'return Promise.all(printState.images.map(function ([image]) {',
            'image.addEventListener("load", resolve, {once: true});',
            'detail.open = wasOpen;',
            'image.removeAttribute("loading");',
            'printButton.addEventListener("click", function () {',
            'window.print();',
        ):
            with self.subTest(behavior=behavior):
                self.assertIn(behavior, print_action)

        self.assertLess(
            javascript.index('  initializePrint();'),
            javascript.index('  function initializeReadMode() {'),
        )

    def test_javascript_uses_native_share_on_touch_and_falls_back_to_the_platform_menu(self):
        javascript = self.static_text('blog/js/article.js')
        sharing = javascript[
            javascript.index('  function copyText(value) {') : javascript.index('  function initializePrint() {')
        ]

        for behavior in (
            'navigator.clipboard.writeText(value)',
            'document.execCommand("copy")',
            'share.querySelector("[data-blog-share-button]")',
            'share.querySelector("[data-blog-share-menu]")',
            'share.querySelector("[data-blog-copy-link]")',
            'new URLSearchParams(parameters).toString()',
            'https://twitter.com/intent/tweet',
            'https://www.facebook.com/sharer/sharer.php',
            'https://www.linkedin.com/feed/',
            'https://www.reddit.com/submit',
            'https://wa.me/',
            'mailto:',
            '{text: articleTitle, url: articleUrl}',
            '{u: articleUrl}',
            'shareActive: "true"',
            'shareUrl: articleUrl',
            '{url: articleUrl, title: articleTitle}',
            '{text: articleTitle + " " + articleUrl}',
            '{subject: articleTitle, body: articleUrl}',
            'window.matchMedia("(pointer: coarse)").matches',
            'window.isSecureContext',
            'typeof navigator.share === "function"',
            'navigator.share({title: articleTitle, url: articleUrl})',
            'error.name === "AbortError"',
            'openFallbackMenu();',
            'shareMenu.hidden = !open;',
            'shareButton.setAttribute("aria-expanded", String(open));',
            'setMenuOpen(shareMenu.hidden);',
            'shareButton.hidden = false;',
            'link.addEventListener("click", function () {',
            'document.addEventListener("click", function (event) {',
            '!share.contains(event.target)',
            'document.addEventListener("keydown", function (event) {',
            'event.key !== "Escape"',
            'shareButton.focus({preventScroll: true});',
            'copyText(articleUrl)',
            'copyLinkButton.classList.add("is-copied")',
            'copyLinkButton.setAttribute("aria-label", "Article link copied")',
            'copyLinkButton.setAttribute("aria-label", "Copy failed")',
            'copyLabel.textContent = "Article link copied";',
            'copyLabel.textContent = "Copy failed";',
            '}, 1800);',
        ):
            with self.subTest(behavior=behavior):
                self.assertIn(behavior, sharing)

        self.assertNotIn('function initializeCopyLink()', sharing)
        self.assertEqual(sharing.count('shareButton.focus({preventScroll: true});'), 2)

    def test_paired_stylesheets_keep_the_focused_layout_contract(self):
        for path in self.stylesheet_paths:
            stylesheet = self.static_text(path)
            read_mode = stylesheet[
                stylesheet.index('/* Article actions stay client-side presentation controls') :
            ]

            with self.subTest(path=path):
                for behavior in (
                    'min-height: 44px;',
                    'min-height: 32px;',
                    'background: transparent;',
                    '.blog-share-menu {',
                    'bottom: calc(100% + .5rem);',
                    'right: 0;',
                    'min-width: 10.5rem;',
                    'max-height: calc(100vh - 2rem);',
                    'overflow-y: auto;',
                    '.blog-share-menu ul {',
                    'display: grid;',
                    'background: color-mix(in srgb',
                    '.blog-share-menu a:focus-visible,',
                    '.blog-share-menu__icon--x-light { display: none; }',
                    '.blog-share-menu__icon--email { filter: invert(1); }',
                    '.blog-read-mode__exit-icon',
                    'display: block;',
                    'stroke-width: 5;',
                    'paint-order: stroke fill;',
                    'position: fixed;',
                    'position: sticky;',
                    'position: fixed;',
                    'inset-inline-end: calc(50% + 22rem + 1rem);',
                    'max-width: 44rem;',
                    'width: 100%;',
                    'overflow-wrap: anywhere;',
                    'scroll-margin-top: 5.5rem;',
                    '@media (forced-colors: active)',
                    '@media (prefers-reduced-motion: reduce)',
                    '@media print',
                ):
                    self.assertIn(behavior, read_mode)

                if path == 'blog/css/article.css':
                    self.assertIn('border-radius: var(--blog-radius);', read_mode)
                    self.assertIn('background: var(--blog-surface);', read_mode)
                else:
                    self.assertIn('border-radius: var(--radius-md);', read_mode)
                    self.assertIn('background: var(--surface);', read_mode)

                popup_start = read_mode.index('.blog-share-menu {')
                popup_rule = read_mode[popup_start : read_mode.index('\n}', popup_start)]
                self.assertNotIn('box-shadow', popup_rule)

                self.assertIn(
                    '''html.blog-read-mode-active .blog-article__body table,
html.blog-read-mode-active .blog-article__body img,
html.blog-read-mode-active .blog-article__body svg,
html.blog-read-mode-active .blog-article__body video {
  max-width: 100%;
}''',
                    read_mode,
                )

    def test_paired_stylesheets_keep_share_print_and_document_contracts(self):
        for path in self.stylesheet_paths:
            stylesheet = self.static_text(path)
            article_actions = stylesheet[
                stylesheet.index('/* Article actions stay client-side presentation controls') :
            ]
            print_styles = stylesheet[stylesheet.rindex('@media print {') :]
            hidden_surfaces = print_styles[
                print_styles.index('  .site-header,') : print_styles.index('    display: none !important;')
            ]

            with self.subTest(path=path):
                for behavior in (
                    '.blog-article__actions',
                    '.blog-print-action::after',
                    '.blog-share-action::after',
                    '.blog-read-mode__entry::after',
                    'content: attr(aria-label);',
                    'font-size: .75rem;',
                    'border-radius: 4px;',
                    '.blog-print-action:hover::after,',
                    '.blog-share-action:hover::after,',
                    '.blog-print-action:focus-visible::after,',
                    '.blog-read-mode__entry:hover::after,',
                    '.blog-read-mode__entry:focus-visible::after',
                    'html.blog-read-mode-active .blog-share,',
                    'html.blog-read-mode-active .blog-print-action,',
                    '.blog-share-action[hidden],',
                    '.blog-share-menu[hidden],',
                    '.blog-share-menu__copy,',
                    '@media (forced-colors: active)',
                    '@media (prefers-reduced-motion: reduce)',
                    '@page',
                    'margin: 16mm 14mm;',
                ):
                    self.assertIn(behavior, article_actions)

                for excluded_surface in (
                    '.site-header,',
                    '.site-footer,',
                    '.skip-link,',
                    'body > .section-shell,',
                    '.blog-article__breadcrumb,',
                    '.blog-article__tags-row,',
                    '.blog-article__related,',
                    '.blog-article__back,',
                    '.blog-image-dialog,',
                    '.blog-share,',
                    '.blog-print-action,',
                ):
                    self.assertIn(excluded_surface, hidden_surfaces)

                self.assertNotIn('.blog-article__toc,', hidden_surfaces)
                for print_behavior in (
                    'color-scheme: light !important;',
                    'font-family: "Literata", Georgia, serif;',
                    '.blog-article__body,\n  .blog-article__toc {',
                    '.blog-figure--featured img {',
                    '.blog-rich-text table {',
                    'white-space: pre-wrap !important;',
                    'break-inside: avoid-page;',
                    'page-break-inside: avoid;',
                ):
                    self.assertIn(print_behavior, print_styles)
