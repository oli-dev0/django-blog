from pathlib import Path

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase


class BlogEmbedSharingStylesheetContractTests(SimpleTestCase):
    stylesheet_paths = (
        'blog/css/article.css',
        'vanta_site/css/blog.css',
    )

    def stylesheet(self, path):
        resolved = finders.find(path)
        self.assertIsNotNone(resolved, f'Static asset does not resolve: {path}')
        return Path(resolved).read_text(encoding='utf-8')

    def test_both_stylesheet_owners_cover_the_responsive_embed_contract(self):
        required_rules = (
            '.blog-article__body figure.blog-embed',
            '.blog-embed__youtube { display: block; width: 100%; height: auto; '
            'aspect-ratio: 16 / 9;',
            '.blog-embed__x-target',
            '.blog-embed > .reddit-embed-bq',
            'max-width: 100% !important',
            'overflow-wrap: anywhere',
            'width: min(100%, 30rem);',
            'background: transparent;',
            'border-radius: 12px;',
            'overflow: hidden;',
            '.blog-embed__x-target iframe',
            'clip-path: inset(0 round 12px);',
            'zoom: .9;',
            '.blog-embed__fallback[hidden] { display: none; }',
            '.blog-embed__youtube:focus-visible',
            '.blog-article .blog-embed__source-link:focus-visible',
            '.blog-article .blog-embed__source-link { display: inline-flex;',
            '.blog-article .blog-embed__source-link svg',
            '.blog-embed__caption { min-width: 0; }',
            '.blog-embed[data-blog-embed-platform="x"] > .blog-embed__footer',
            '.blog-embed[data-blog-embed-platform="reddit"] > iframe',
            '.blog-embed[data-blog-embed-platform="reddit"] > .blog-embed__footer',
            'width: min(100%, 640px);',
            'justify-content: space-between;',
            '@media (forced-colors: active)',
        )

        for path in self.stylesheet_paths:
            with self.subTest(path=path):
                stylesheet = self.stylesheet(path)
                for rule in required_rules:
                    self.assertIn(rule, stylesheet)

    def test_print_rules_keep_first_party_links_and_captions_without_provider_frames(self):
        for path in self.stylesheet_paths:
            with self.subTest(path=path):
                stylesheet = self.stylesheet(path)
                print_rules = stylesheet[stylesheet.index('@media print {'):]

                self.assertIn('.blog-embed > iframe', print_rules)
                self.assertIn('.blog-embed__x-target', print_rules)
                self.assertIn('.blog-embed > .reddit-embed-bq', print_rules)
                self.assertIn('.blog-embed__fallback', print_rules)
                self.assertIn('display: none !important', print_rules)
                self.assertIn('.blog-article .blog-embed__source-link', print_rules)
                self.assertIn('display: inline-block !important', print_rules)
                self.assertIn('.blog-embed > figcaption', print_rules)

                self.assertNotIn(
                    '.blog-embed > iframe { display: block; width: 100%; '
                    'height: auto; aspect-ratio:',
                    stylesheet,
                )
                self.assertNotIn('.reddit-embed-bq iframe', stylesheet)
