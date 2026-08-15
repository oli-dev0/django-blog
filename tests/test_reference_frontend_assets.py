import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = ROOT / "blog" / "templates" / "blog"
STATIC_ROOT = ROOT / "blog" / "static" / "blog"


class ReferenceFrontendAssetTests(unittest.TestCase):
    def read_template(self, name):
        return (TEMPLATE_ROOT / name).read_text(encoding="utf-8")

    def read_static(self, name):
        return (STATIC_ROOT / name).read_text(encoding="utf-8")

    def test_reference_shell_assets_exist(self):
        required_templates = ("base.html", "list.html", "detail.html")
        required_static = (
            "css/shell.css",
            "css/article.css",
            "js/theme.js",
            "js/list.js",
            "js/tags.js",
            "js/article.js",
            "js/embed-sharing.js",
            "fonts/inter-variable.woff2",
            "fonts/inter-italic-variable.woff2",
            "fonts/Literata[opsz,wght].woff2",
            "fonts/Literata-Italic[opsz,wght].woff2",
            "fonts/OFL-Inter.txt",
            "fonts/OFL.txt",
            "img/icons/x-dark.svg",
            "img/icons/x-light.svg",
            "img/icons/facebook.svg",
            "img/icons/linkedin.svg",
            "img/icons/reddit.svg",
            "img/icons/whatsapp.svg",
            "img/icons/email.svg",
            "img/icons/rss.svg",
        )

        for name in required_templates:
            with self.subTest(path=f"templates/blog/{name}"):
                self.assertTrue((TEMPLATE_ROOT / name).is_file())
        for name in required_static:
            with self.subTest(path=f"static/blog/{name}"):
                self.assertTrue((STATIC_ROOT / name).is_file())

    def test_blog_owned_frontend_has_no_omitted_shell_dependencies(self):
        files = tuple(TEMPLATE_ROOT.rglob("*.html")) + tuple(
            path for path in STATIC_ROOT.rglob("*") if path.is_file() and path.suffix in {".css", ".js"}
        )
        for path in files:
            with self.subTest(path=path.relative_to(ROOT)):
                content = path.read_text(encoding="utf-8")
                self.assertNotIn("vanta_site", content)
                self.assertNotIn("core/", content)

    def test_template_static_references_stay_in_blog_namespace(self):
        static_references = re.compile(r"\{%\s*static\s+['\"]([^'\"]+)")
        for path in TEMPLATE_ROOT.rglob("*.html"):
            content = path.read_text(encoding="utf-8")
            for static_path in static_references.findall(content):
                with self.subTest(path=path.name, static_path=static_path):
                    self.assertTrue(static_path.startswith("blog/"))

    def test_font_urls_are_relative_to_the_blog_stylesheet(self):
        for stylesheet in ("css/shell.css", "css/article.css"):
            content = self.read_static(stylesheet)
            urls = re.findall(r"url\((?:\"|')?([^\)\"']+)", content)
            font_urls = [url for url in urls if url.endswith(".woff2")]
            with self.subTest(stylesheet=stylesheet):
                self.assertTrue(font_urls)
                self.assertTrue(all(not url.startswith("/") for url in font_urls))
                self.assertTrue(all("blog/" not in url and "core/" not in url for url in font_urls))

    def test_theme_control_is_hidden_until_safe_theme_initialization(self):
        template = self.read_template("base.html")
        script = self.read_static("js/theme.js")

        self.assertRegex(template, r"data-status-appearance-toggle[^>]*\bhidden\b")
        for behavior in (
            'const storageKey = "statusAppearance"',
            'new Set(["light", "dark"])',
            "window.localStorage.getItem(storageKey)",
            "window.localStorage.setItem(storageKey, theme)",
            'root.setAttribute("data-status-theme", selectedTheme)',
            "try {",
            "catch (error)",
            "control.hidden = false",
            "input[name='status-appearance']",
        ):
            with self.subTest(behavior=behavior):
                self.assertIn(behavior, script)

    def test_shell_loads_expected_progressive_enhancement_assets(self):
        base = self.read_template("base.html")
        listing = self.read_template("list.html")
        detail = self.read_template("detail.html")

        for static_path in ("blog/css/shell.css", "blog/css/article.css", "blog/js/theme.js"):
            self.assertIn(static_path, base)
        for static_path in ("blog/js/tags.js", "blog/js/list.js"):
            self.assertIn(static_path, listing)
        for static_path in ("blog/js/article.js", "blog/js/tags.js", "blog/js/embed-sharing.js"):
            self.assertIn(static_path, detail)
        self.assertIn("{% if has_embed_sharing %}", detail)
        self.assertLess(detail.index("{% if has_embed_sharing %}"), detail.index("blog/js/embed-sharing.js"))

    def test_shell_and_fragments_keep_neutral_branding_and_existing_contracts(self):
        base = self.read_template("base.html")
        listing = self.read_template("list.html")
        detail = self.read_template("detail.html")
        article_fragment = self.read_template("article_content.html")
        filters_fragment = self.read_template("list_filters.html")

        self.assertIn("{{ site_name }}", base)
        self.assertIn("{{ site_name }}", listing)
        self.assertIn("{{ site_name }}", detail)
        self.assertIn("seo.article_schema_json", detail)
        self.assertIn("seo.breadcrumb_schema_json", detail)
        self.assertIn("rss_feed_url", listing)
        self.assertIn("{% if seo_og_image_url %}", listing)
        self.assertIn("{% if seo.og_image_url %}", detail)
        self.assertIn("blog/img/icons/", article_fragment)
        self.assertIn("blog/img/icons/rss.svg", filters_fragment)
        self.assertNotIn("core/img/icons/", article_fragment + filters_fragment)


if __name__ == "__main__":
    unittest.main()
