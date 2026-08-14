from pathlib import Path

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase
from fontTools.ttLib import TTFont


class BlogFontTests(SimpleTestCase):
    font_paths = (
        'blog/fonts/Literata[opsz,wght].woff2',
        'blog/fonts/Literata-Italic[opsz,wght].woff2',
    )
    stylesheet_paths = (
        'blog/css/article.css',
        'vanta_site/css/blog.css',
    )

    def static_path(self, path):
        resolved = finders.find(path)
        self.assertIsNotNone(resolved, f'Static asset does not resolve: {path}')
        return Path(resolved)

    def test_literata_faces_are_local_woff2_variable_fonts(self):
        for path in self.font_paths:
            with self.subTest(path=path), TTFont(self.static_path(path)) as font:
                self.assertEqual(font.flavor, 'woff2')
                axes = {axis.axisTag: axis for axis in font['fvar'].axes}
                self.assertEqual((axes['opsz'].minValue, axes['opsz'].maxValue), (7, 72))
                self.assertEqual((axes['wght'].minValue, axes['wght'].maxValue), (200, 900))

    def test_literata_license_and_paired_stylesheet_contract_are_discoverable(self):
        license_path = self.static_path('blog/fonts/OFL.txt')
        self.assertIn('SIL OPEN FONT LICENSE', license_path.read_text(encoding='utf-8'))

        for path in self.stylesheet_paths:
            content = self.static_path(path).read_text(encoding='utf-8')
            with self.subTest(path=path):
                self.assertEqual(content.count('@font-face'), 2)
                self.assertEqual(content.count('font-family: "Literata";'), 2)
                self.assertEqual(content.count('font-display: swap;'), 2)
                self.assertEqual(content.count('font-weight: 200 900;'), 2)
                for selector in (
                    'blog-read-mode-active',
                    '.blog-read-mode__entry',
                    '.blog-read-mode__exit',
                    '.blog-read-mode__progress',
                ):
                    self.assertIn(selector, content)
