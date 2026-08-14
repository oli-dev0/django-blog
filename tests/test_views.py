import json
import re
from io import BytesIO
from datetime import datetime, timezone as datetime_timezone
from html.parser import HTMLParser
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.templatetags.static import static
from django.utils import timezone
from PIL import Image

from apps.blog.models import (
    BlogCalloutBlock,
    BlogChecklistBlock,
    BlogCodeBlock,
    BlogCategory,
    BlogEmbedSharingBlock,
    BlogFAQBlock,
    BlogImage,
    BlogImageBlock,
    BlogLinkGroupBlock,
    BlogPost,
    BlogPostPublication,
    BlogPostRelated,
    BlogImageComparison,
    BlogImageComparisonBlock,
    BlogRichTextBlock,
    BlogSourceLinkBlock,
    BlogTag,
    AuthorProfile,
)
from apps.blog.image_services import process_comparison_image
from apps.blog.feeds import BlogFeed
from apps.blog.rendering import build_article_context
from apps.core.sites import EASY_MEALS_SITE, PERSONAL_SITE, VANTA_SITE

from .site_settings import BLOG_ENABLED_SITE_DEFINITIONS


class RSSAutodiscoveryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_head = False
        self.links = []
        self.rss_anchors = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'head':
            self.in_head = True
        elif tag == 'link' and attributes.get('type') == 'application/rss+xml':
            self.links.append((self.in_head, attributes))
        elif tag == 'a' and 'blog-rss__link' in attributes.get('class', '').split():
            self.rss_anchors.append(attributes)

    def handle_endtag(self, tag):
        if tag == 'head':
            self.in_head = False


@override_settings(SITE_DEFINITIONS=BLOG_ENABLED_SITE_DEFINITIONS)
class BlogViewTests(TestCase):
    def test_personal_blog_detail_uses_only_unprefixed_blog_urls(self):
        self.create_post(slug='unprefixed-personal-blog')

        response = self.client.get(
            '/blog/unprefixed-personal-blog/',
            HTTP_HOST='personal.example.com',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'href="http://personal.example.com/blog/unprefixed-personal-blog/"',
            html=False,
        )
        self.assertContains(response, 'href="/blog/"', html=False)
        self.assertNotContains(response, '/en/blog/', html=False)

    def test_missing_personal_blog_routes_return_direct_404_responses(self):
        self.create_post()

        for path in (
            '/blog/missing/',
            '/blog/tag/missing/',
            '/blog/category/missing/',
            '/blog/author/missing/',
            '/blog/?page=2',
        ):
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST='personal.example.com')

                self.assertEqual(response.status_code, 404)
                self.assertNotIn('Location', response)

        localized_response = self.client.get(
            '/blog/missing/',
            HTTP_HOST='vanta.localhost',
        )
        self.assertEqual(localized_response.status_code, 302)
        self.assertEqual(localized_response['Location'], '/en/blog/missing/')

    def comparison_upload(self, *, color, name):
        output = BytesIO()
        Image.new('RGB', (800, 450), color).save(output, format='PNG')
        return SimpleUploadedFile(name, output.getvalue(), content_type='image/png')

    def rendered_json_ld(self, response):
        scripts = re.findall(
            rb'<script type="application/ld\+json">(.*?)</script>',
            response.content,
            flags=re.DOTALL,
        )
        self.assertEqual(len(scripts), 2)
        return [json.loads(script) for script in scripts]

    def create_post(
        self,
        *,
        slug='english-post',
        status=BlogPost.Status.PUBLISHED,
        site_slug=PERSONAL_SITE,
        canonical_site_slug=None,
        published_at=None,
        author=None,
    ):
        category, _created = BlogCategory.objects.get_or_create(
            name='General',
            defaults={'slug': 'general'},
        )
        post = BlogPost.objects.create(
            status=status,
            title='English post',
            slug=slug,
            summary='English summary',
            published_at=published_at or timezone.now(),
            canonical_site_slug=canonical_site_slug or site_slug,
            author=author,
            category=category,
        )
        BlogPostPublication.objects.create(post=post, site_slug=site_slug)
        BlogRichTextBlock.objects.create(parent=post, region='main', body='<p>Rich article body.</p>')
        return post

    def create_author_profile(self, *, public_author_name, username=None, profile_picture=None, slug=''):
        user = get_user_model().objects.create_user(username=username or public_author_name)
        return AuthorProfile.objects.create(
            user=user,
            public_author_name=public_author_name,
            profile_picture=profile_picture,
            slug=slug,
        )

    def test_blog_list_renders_empty_state(self):
        response = self.client.get('/en/blog/')
        parser = RSSAutodiscoveryParser()
        parser.feed(response.content.decode())

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'my_website/blog/list.html')
        self.assertContains(response, 'No articles have been published yet.')
        self.assertEqual(len(parser.links), 1)
        self.assertEqual(response.headers['Content-Language'], 'en')

    def test_vanta_blog_title_links_to_blog_homepage(self):
        response = self.client.get('/en/blog/', HTTP_HOST='vanta.localhost')

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<h1 id="blog-title"><a href="/en/blog/" data-blog-return-link>Vanta Admin blog</a></h1>',
            html=True,
        )

    def test_vanta_blog_loads_only_required_shell_styles(self):
        self.create_post(site_slug=VANTA_SITE)

        for path in ('/en/blog/', '/en/blog/english-post/'):
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST='vanta.localhost')

                self.assertNotContains(response, static('vanta_site/css/styles.css'))
                for stylesheet in (
                    'core/css/fonts.css',
                    'vanta_site/css/base.css',
                    'vanta_site/css/layout.css',
                    'vanta_site/css/footer-newsletter.css',
                    'vanta_site/css/responsive-shared.css',
                    'vanta_site/css/responsive-final.css',
                    'vanta_site/css/blog.css',
                ):
                    self.assertContains(response, static(stylesheet))

    def test_vanta_blog_prioritizes_only_first_list_image_and_uses_grid_sizes(self):
        self.create_post(slug='first-image', site_slug=VANTA_SITE)
        self.create_post(slug='second-image', site_slug=VANTA_SITE)
        source_data = {
            'original': '/media/original.webp',
            'src': '/media/rendition.webp',
            'srcset': '/media/480.webp 480w, /media/800.webp 800w',
            'sizes': '',
            'width': 1600,
            'height': 900,
            'alt': 'Article image',
            'caption_title': '',
            'caption_text': '',
        }

        def list_image_sources(_image, *, sizes=None):
            return {**source_data, 'sizes': sizes}

        with patch('apps.blog.views.image_sources', side_effect=list_image_sources) as sources:
            response = self.client.get('/en/blog/', HTTP_HOST='vanta.localhost')

        expected_sizes = (
            '(min-width: 1024px) calc((min(100vw, 1440px) - 7rem) / 3), '
            '(min-width: 640px) calc((100vw - 5.5rem) / 2), '
            'calc(100vw - 3rem)'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode().count('fetchpriority="high"'), 1)
        self.assertEqual(response.content.decode().count('loading="eager"'), 1)
        self.assertEqual(response.content.decode().count('loading="lazy"'), 1)
        self.assertContains(response, f'sizes="{expected_sizes}"', count=2, html=False)
        self.assertEqual(sources.call_count, 2)
        for call in sources.call_args_list:
            self.assertEqual(call.kwargs['sizes'], expected_sizes)

    def test_vanta_blog_prioritizes_article_featured_image(self):
        post = self.create_post(site_slug=VANTA_SITE)
        post.featured_image = BlogImage.objects.create(name='Featured', original='featured.png')
        post.save(update_fields=['featured_image'])
        source_data = {
            'original': '/media/original.webp',
            'src': '/media/rendition.webp',
            'srcset': '/media/480.webp 480w, /media/800.webp 800w',
            'sizes': '(min-width: 900px) 820px, calc(100vw - 3rem)',
            'width': 1600,
            'height': 900,
            'alt': 'Article image',
            'caption_title': '',
            'caption_text': '',
        }

        with patch('apps.blog.rendering.image_sources', return_value=source_data):
            response = self.client.get('/en/blog/english-post/', HTTP_HOST='vanta.localhost')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'src="/media/rendition.webp"', html=False)
        self.assertContains(
            response,
            '<meta property="og:image" content="http://admin-theme.example.com/media/rendition.webp">',
            html=True,
        )
        self.assertContains(
            response,
            '<meta name="twitter:image" content="http://admin-theme.example.com/media/rendition.webp">',
            html=True,
        )
        self.assertContains(response, '<meta property="og:image:alt" content="Article image">', html=True)
        self.assertEqual(response.content.decode().count('fetchpriority="high"'), 1)
        self.assertEqual(response.content.decode().count('loading="eager"'), 1)

    def test_personal_and_easy_meals_lists_prioritize_only_the_first_image(self):
        source_data = {
            'original': '/media/original.webp',
            'src': '/media/rendition.webp',
            'srcset': '',
            'sizes': '',
            'width': 1200,
            'height': 675,
            'alt': 'Article image',
            'caption_title': '',
            'caption_text': '',
        }
        cases = (
            (PERSONAL_SITE, 'personal.example.com', '/blog/'),
            (EASY_MEALS_SITE, 'recipes.example.com', '/en/blog/'),
        )
        for site_slug, host, path in cases:
            with self.subTest(site_slug=site_slug):
                self.create_post(slug=f'{site_slug}-first', site_slug=site_slug)
                self.create_post(slug=f'{site_slug}-second', site_slug=site_slug)
                with patch('apps.blog.views.image_sources', return_value=source_data):
                    response = self.client.get(path, HTTP_HOST=host)

                self.assertEqual(response.content.decode().count('fetchpriority="high"'), 1)
                self.assertEqual(response.content.decode().count('loading="eager"'), 1)
                self.assertEqual(response.content.decode().count('loading="lazy"'), 1)

    def test_tag_overflow_control_accessible_name_contains_visible_counter(self):
        tag = BlogTag.objects.create(name='Django', slug='django')
        post = self.create_post(site_slug=VANTA_SITE)
        post.tags.add(tag)

        response = self.client.get('/en/blog/', HTTP_HOST='vanta.localhost')

        self.assertContains(
            response,
            '<summary>+<span data-blog-tags-count>0</span>'
            '<span class="visually-hidden"> Show remaining tags</span></summary>',
            html=True,
        )
        self.assertNotContains(response, 'aria-label="Show all tags"')

    def test_non_english_blog_paths_return_404(self):
        BlogTag.objects.create(name='Django', slug='django')
        response = self.client.get('/fr/blog/')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get('/fr/blog/tag/django/').status_code, 404)
        self.assertEqual(self.client.get('/fr/blog/author/example-author/').status_code, 404)

    def test_detail_renders_shared_blocks_and_article_metadata(self):
        self.create_post()

        response = self.client.get('/en/blog/english-post/')

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'my_website/blog/detail.html')
        self.assertContains(response, 'Rich article body.')
        self.assertContains(response, '<p class="blog-article__type">Article</p>', html=True)
        self.assertContains(response, 'BlogPosting')
        self.assertContains(response, '<meta property="og:type" content="article">', html=True)
        self.assertContains(response, 'plausible.personal.example.com')
        self.assertNotContains(response, 'hreflang=')
        self.assertNotContains(response, 'href="/fr/blog/')
        self.assertContains(response, static('my_website/css/blog.css'))
        self.assertEqual(response.headers['Content-Language'], 'en')

    def test_detail_renders_native_embeds_in_saved_order_without_provider_html(self):
        post = self.create_post(slug='native-embeds')
        BlogEmbedSharingBlock.objects.create(
            parent=post,
            region='main',
            ordering=10,
            platform=BlogEmbedSharingBlock.Platform.YOUTUBE,
            url='https://youtu.be/dQw4w9WgXcQ?si=tracking',
            caption='Video caption',
        )
        BlogEmbedSharingBlock.objects.create(
            parent=post,
            region='main',
            ordering=20,
            platform=BlogEmbedSharingBlock.Platform.X,
            url='https://twitter.com/example/status/123456789?s=20',
        )
        BlogEmbedSharingBlock.objects.create(
            parent=post,
            region='main',
            ordering=30,
            platform=BlogEmbedSharingBlock.Platform.REDDIT,
            url='https://www.reddit.com/r/python/comments/abc123/example-post/',
            caption='Reddit caption',
        )

        response = self.client.get('/en/blog/native-embeds/')
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(content.count('<figure class="blog-embed"'), 3)
        self.assertEqual(content.count('class="blog-embed__source-link"'), 3)
        self.assertEqual(content.count('class="blog-embed__footer"'), 3)
        self.assertEqual(content.count('class="blog-embed__caption"'), 2)
        self.assertLess(
            content.index('youtube-nocookie.com'),
            content.index('blog-embed__x-target'),
        )
        self.assertLess(
            content.index('blog-embed__x-target'),
            content.index('reddit-embed-bq'),
        )
        self.assertContains(response, 'data-blog-embed-platform="youtube"', html=False)
        self.assertContains(response, 'data-blog-embed-id="dQw4w9WgXcQ"', html=False)
        self.assertContains(
            response,
            'src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?autoplay=0&amp;playsinline=1&amp;enablejsapi=1&amp;origin=http%3A%2F%2Ftestserver"',
            html=False,
        )
        self.assertContains(response, 'title="Embedded YouTube video"', html=False)
        self.assertContains(
            response,
            'allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture"',
            html=False,
        )
        self.assertContains(response, 'allowfullscreen', html=False)
        self.assertContains(response, 'referrerpolicy="strict-origin-when-cross-origin"', html=False)
        self.assertNotContains(response, 'allow="autoplay', html=False)
        self.assertContains(response, 'class="blog-embed__x-target"', html=False)
        self.assertNotContains(response, 'class="twitter-tweet"', html=False)
        self.assertContains(response, 'href="https://x.com/example/status/123456789"', html=False)
        self.assertContains(response, 'class="reddit-embed-bq"', html=False)
        self.assertContains(
            response,
            'href="https://www.reddit.com/r/python/comments/abc123/"',
            html=False,
        )
        self.assertContains(response, 'Video caption')
        self.assertContains(response, 'Reddit caption')
        self.assertNotContains(response, '<script>untrusted provider markup</script>', html=False)
        self.assertNotContains(response, 'VideoObject', html=False)
        self.assertNotContains(response, 'SocialMediaPosting', html=False)
        self.assertContains(
            response,
            '<link rel="canonical" href="http://personal.example.com/blog/native-embeds/">',
            html=True,
        )
        self.assertContains(
            response,
            '<meta property="og:url" content="http://personal.example.com/blog/native-embeds/">',
            html=True,
        )
        schemas = self.rendered_json_ld(response)
        self.assertEqual({schema['@type'] for schema in schemas}, {'BlogPosting', 'BreadcrumbList'})

    def test_embed_captions_are_escaped_and_empty_captions_are_omitted(self):
        post = self.create_post(slug='escaped-embed-caption')
        BlogEmbedSharingBlock.objects.create(
            parent=post,
            region='main',
            ordering=10,
            platform=BlogEmbedSharingBlock.Platform.YOUTUBE,
            url='https://youtu.be/dQw4w9WgXcQ',
            caption='<script>alert(1)</script>',
        )
        BlogEmbedSharingBlock.objects.create(
            parent=post,
            region='main',
            ordering=20,
            platform=BlogEmbedSharingBlock.Platform.X,
            url='https://x.com/example/status/123456789',
            caption='   ',
        )

        response = self.client.get('/en/blog/escaped-embed-caption/')

        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;', html=False)
        self.assertNotContains(response, '<script>alert(1)</script>', html=False)
        self.assertEqual(response.content.count(b'class="blog-embed__caption"'), 1)

    def test_embed_script_is_conditionally_loaded_in_every_detail_shell(self):
        script_path = static('blog/js/embed-sharing.js')
        cases = (
            (PERSONAL_SITE, 'testserver'),
            (EASY_MEALS_SITE, 'recipes.example.com'),
            (VANTA_SITE, 'admin-theme.example.com'),
        )

        for index, (site_slug, host) in enumerate(cases):
            with self.subTest(site_slug=site_slug):
                plain_post = self.create_post(slug=f'plain-script-{index}', site_slug=site_slug)
                embed_post = self.create_post(slug=f'embed-script-{index}', site_slug=site_slug)
                BlogEmbedSharingBlock.objects.create(
                    parent=embed_post,
                    region='main',
                    ordering=10,
                    platform=BlogEmbedSharingBlock.Platform.X,
                    url='https://x.com/example/status/123456789',
                )

                plain_response = self.client.get(f'/en/blog/{plain_post.slug}/', HTTP_HOST=host)
                embed_response = self.client.get(f'/en/blog/{embed_post.slug}/', HTTP_HOST=host)

                self.assertNotContains(plain_response, script_path)
                self.assertContains(embed_response, script_path)
                self.assertTrue(embed_response.context['has_embed_sharing'])
                self.assertFalse(plain_response.context['has_embed_sharing'])

    def test_youtube_embed_uses_the_request_origin_in_every_detail_shell(self):
        cases = (
            (PERSONAL_SITE, 'testserver'),
            (EASY_MEALS_SITE, 'recipes.example.com'),
            (VANTA_SITE, 'admin-theme.example.com'),
        )

        for index, (site_slug, host) in enumerate(cases):
            with self.subTest(site_slug=site_slug):
                post = self.create_post(slug=f'youtube-origin-{index}', site_slug=site_slug)
                BlogEmbedSharingBlock.objects.create(
                    parent=post,
                    region='main',
                    ordering=10,
                    platform=BlogEmbedSharingBlock.Platform.YOUTUBE,
                    url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                )

                response = self.client.get(f'/en/blog/{post.slug}/', HTTP_HOST=host)

                encoded_origin = f'http%3A%2F%2F{host}'
                self.assertContains(response, f'&amp;origin={encoded_origin}', html=False)
                self.assertEqual(response.context['embed_origin'], f'http://{host}')

    def test_invalid_stored_embed_fails_closed_without_using_its_url(self):
        post = self.create_post(slug='invalid-stored-embed')
        BlogEmbedSharingBlock.objects.create(
            parent=post,
            region='main',
            ordering=10,
            platform=BlogEmbedSharingBlock.Platform.YOUTUBE,
            url='https://attacker.example/embed?video=dQw4w9WgXcQ',
            caption='Still useful context',
        )

        response = self.client.get('/en/blog/invalid-stored-embed/')
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This content is currently unavailable.')
        self.assertContains(response, 'Still useful context')
        self.assertNotIn('attacker.example', content)
        self.assertNotIn('data-blog-embed', content)
        self.assertNotIn('youtube-nocookie.com', content)
        self.assertNotIn('<iframe', content)

    def test_public_detail_exposes_sharing_and_read_mode_on_all_site_shells(self):
        cases = (
            (PERSONAL_SITE, 'testserver', 'my_website/css/blog.css'),
            (EASY_MEALS_SITE, 'recipes.example.com', 'blog/css/article.css'),
            (VANTA_SITE, 'admin-theme.example.com', 'vanta_site/css/blog.css'),
        )

        for index, (site_slug, host, stylesheet) in enumerate(cases):
            with self.subTest(site_slug=site_slug):
                slug = f'read-mode-shell-{index}'
                self.create_post(slug=slug, site_slug=site_slug)

                response = self.client.get(f'/en/blog/{slug}/', HTTP_HOST=host)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, static(stylesheet))
                self.assertContains(response, 'data-blog-read-mode-root', html=False)
                self.assertContains(response, 'data-blog-read-mode-toolbar', html=False)
                self.assertContains(response, 'data-blog-read-mode-entry', html=False)
                self.assertContains(response, 'data-blog-read-mode-exit', html=False)
                self.assertContains(response, 'data-blog-read-mode-progress', html=False)
                self.assertContains(response, 'class="blog-article__actions"', html=False)
                self.assertContains(response, 'data-blog-share-button', html=False)
                self.assertContains(response, 'aria-label="Share"', html=False)
                self.assertContains(response, 'aria-expanded="false"', html=False)
                self.assertContains(response, 'aria-controls="blog-share-menu"', html=False)
                self.assertContains(response, 'id="blog-share-menu"', html=False)
                self.assertContains(response, 'data-blog-copy-link', html=False)
                self.assertContains(response, 'aria-label="Copy link"', html=False)
                self.assertContains(response, 'class="blog-share-action__icon"', html=False)
                self.assertContains(response, '<circle cx="18" cy="5" r="2.25" fill="currentColor"></circle>', html=False)
                self.assertContains(response, 'stroke-width="1.8"', html=False)
                self.assertContains(response, 'stroke="currentColor"', html=False)
                self.assertContains(response, 'data-blog-print', html=False)
                self.assertContains(response, 'aria-label="Print"', html=False)
                self.assertContains(response, 'class="blog-print-action__icon"', html=False)
                self.assertContains(response, 'viewBox="0 0 24 24"', html=False)
                self.assertContains(response, 'Read mode')
                self.assertContains(response, 'Exit read mode')
                self.assertContains(response, 'blog-read-mode__exit-icon', html=False)
                self.assertContains(response, 'Reading progress')
                self.assertContains(response, f'{static("blog/js/article.js")}?v=social-share')
                content = response.content.decode()
                self.assertNotIn('blog-copy-link-action', content)
                self.assertLess(content.index('data-blog-share-button'), content.index('data-blog-print'))
                self.assertLess(content.index('data-blog-print'), content.index('data-blog-read-mode-entry'))
                platform_order = ('x', 'facebook', 'linkedin', 'reddit', 'whatsapp', 'email')
                platform_positions = [
                    content.index(f'data-blog-share-platform="{platform}"')
                    for platform in platform_order
                ]
                self.assertEqual(platform_positions, sorted(platform_positions))
                self.assertLess(platform_positions[-1], content.index('data-blog-copy-link'))
                for label in ('X', 'Facebook', 'LinkedIn', 'Reddit', 'WhatsApp', 'Email'):
                    self.assertIn(f'<span>{label}</span>', content)
                self.assertIn('<span data-blog-copy-label>Copy link</span>', content)
                for icon in (
                    'x-dark.svg',
                    'x-light.svg',
                    'facebook.svg',
                    'linkedin.svg',
                    'reddit.svg',
                    'whatsapp.svg',
                    'email.svg',
                ):
                    self.assertIn(static(f'core/img/icons/{icon}'), content)
                share_menu = content[
                    content.index('id="blog-share-menu"') : content.index('data-blog-print')
                ]
                self.assertEqual(share_menu.count('target="_blank" rel="noopener noreferrer"'), 6)
                self.assertEqual(response.content.count(b'<h1>'), 1)

    def test_detail_opens_article_body_links_without_changing_surrounding_links(self):
        post = self.create_post()
        related = self.create_post(slug='related-article')
        BlogPostRelated.objects.create(post=post, related_post=related)
        BlogRichTextBlock.objects.filter(parent=post).update(
            body='<p><a href="https://example.com">Article link</a></p>'
        )

        response = self.client.get('/en/blog/english-post/')

        self.assertContains(
            response,
            '<a href="https://example.com" target="_blank" rel="noopener noreferrer">Article link</a>',
            html=True,
        )
        self.assertContains(
            response,
            '<a href="/en/blog/" data-blog-return-link><span>Blog</span></a>',
            html=False,
        )
        self.assertContains(
            response,
            '<a href="/en/blog/related-article/">',
            html=False,
        )
        self.assertNotContains(
            response,
            '<a href="/en/blog/related-article/" target="_blank"',
            html=False,
        )

    def test_detail_renders_callout_title_without_callout_type_prefix(self):
        post = self.create_post(slug='callout-article')
        BlogCalloutBlock.objects.create(
            parent=post,
            region='main',
            callout_type=BlogCalloutBlock.CalloutType.TIP,
            title='Keep the change focused',
            body='<p>Short advice.</p>',
        )

        response = self.client.get('/en/blog/callout-article/')

        self.assertContains(
            response,
            '<p class="blog-callout__title">Keep the change focused</p>',
            html=True,
        )
        self.assertNotContains(response, 'Tip: Keep the change focused')

    def test_detail_renders_read_mode_action_without_tags(self):
        self.create_post()

        response = self.client.get('/en/blog/english-post/')
        content = response.content.decode()

        self.assertIn('blog-article__tags-row', content)
        self.assertNotIn('class="blog-article__tags"', content)
        self.assertIn('class="blog-article__actions"', content)
        self.assertIn('data-blog-share-button', content)
        self.assertIn('data-blog-print', content)
        self.assertIn('data-blog-read-mode-entry', content)
        self.assertIn('class="blog-read-mode__icon"', content)
        self.assertIn('aria-label="Read mode"', content)
        self.assertNotIn('Focus</button>', content)
        self.assertNotIn('class="blog-rss__link"', content)

    def test_detail_renders_tags_and_read_mode_action_in_the_same_row(self):
        tag = BlogTag.objects.create(name='Django', slug='django')
        post = self.create_post()
        post.tags.add(tag)

        response = self.client.get('/en/blog/english-post/')
        content = response.content.decode()

        self.assertIn('blog-article__tags-row', content)
        self.assertIn('class="blog-article__tags"', content)
        self.assertIn('class="blog-article__actions"', content)
        self.assertIn('data-blog-share-button', content)
        self.assertIn('data-blog-print', content)
        self.assertIn('data-blog-read-mode-entry', content)
        self.assertNotIn('class="blog-rss__link"', content)

    def test_detail_renders_a_comparison_as_one_pair_with_shared_caption(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            post = self.create_post(slug='comparison-article')
            comparison = BlogImageComparison.objects.create(
                name='Comparison pair',
                first_original=self.comparison_upload(color='red', name='first.png'),
                first_alt_text='First view',
                second_original=self.comparison_upload(color='blue', name='second.png'),
                second_alt_text='Second view',
                caption_title='Shared title',
                caption_text='Shared explanation.',
            )
            process_comparison_image(comparison, 'first')
            process_comparison_image(comparison, 'second')
            BlogImageComparisonBlock.objects.create(parent=post, region='main', comparison=comparison)

            response = self.client.get('/en/blog/comparison-article/')

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'blog-image-comparison')
            self.assertContains(response, 'Shared title')
            self.assertContains(response, 'Shared explanation.')
            self.assertEqual(response.content.count(b'data-blog-image-dialog'), 2)
            self.assertContains(response, 'data-blog-image-comparison')
            self.assertContains(
                response,
                'sizes="(min-width: 940px) 462px, (min-width: 640px) '
                'calc((100vw - 3rem) / 2), calc(100vw - 3rem)"',
                html=False,
            )

    def test_detail_renders_reading_time_from_article_body(self):
        post = self.create_post()
        BlogRichTextBlock.objects.filter(parent=post).update(body=f'<p>{"word " * 180}</p>')

        response = self.client.get('/en/blog/english-post/')

        self.assertContains(response, '1 min read')

    def test_blog_list_renders_reading_time_from_article_body(self):
        post = self.create_post()
        BlogRichTextBlock.objects.filter(parent=post).update(body=f'<p>{"word " * 180}</p>')

        response = self.client.get('/en/blog/')

        self.assertContains(response, '1 min read')

    def test_detail_renders_compact_three_letter_date_format(self):
        self.create_post(published_at=datetime(2026, 8, 1, 12, tzinfo=datetime_timezone.utc))

        response = self.client.get('/en/blog/english-post/')

        self.assertContains(response, '1 Aug 2026')

    def test_detail_renders_category_metadata_and_breadcrumb(self):
        category = BlogCategory.objects.create(name='Django', slug='django')
        post = self.create_post()
        post.category = category
        post.save(update_fields=['category'])

        response = self.client.get('/en/blog/english-post/')

        self.assertContains(response, 'Django')
        self.assertContains(response, 'href="/en/blog/category/django/"', html=False)
        self.assertEqual(response.content.count(b'href="/en/blog/category/django/"'), 1)
        eyebrow = response.content.find(b'class="blog-list__eyebrow"')
        title = response.content.find(b'<h1>English post</h1>')
        self.assertLess(eyebrow, title)
        breadcrumb = response.content.find(b'class="blog-article__breadcrumb"')
        title = response.content.find(b'<h1>English post</h1>')
        self.assertLess(breadcrumb, title)
        self.assertContains(response, '<li><a href="/en/blog/" data-blog-return-link><span>Blog</span></a></li>', html=False)
        self.assertContains(response, 'class="blog-article__back-link" href="/en/blog/" data-blog-return-link', html=False)
        self.assertContains(response, '<li aria-current="page"><span>English post</span></li>', html=False)

    def test_detail_exposes_matching_breadcrumb_schema(self):
        category = BlogCategory.objects.create(name='Django', slug='django')
        post = self.create_post(slug='schema-article')
        post.category = category
        post.save(update_fields=['category'])

        response = self.client.get('/en/blog/schema-article/')
        schema = response.context['seo']['breadcrumb_schema_json']

        self.assertIn('"@type":"BreadcrumbList"', schema)
        self.assertIn('"name":"Blog","item":"http://personal.example.com/en/blog/"', schema)
        self.assertIn(
            '"name":"Django","item":"http://personal.example.com/en/blog/category/django/"',
            schema,
        )
        self.assertIn(
            '"name":"English post","item":"http://personal.example.com/blog/schema-article/"',
            schema,
        )

    @override_settings(SEO_CANONICAL_SCHEME='https')
    def test_detail_article_schema_omits_unavailable_optional_facts(self):
        post = self.create_post()
        post.title = 'English </script><script>alert(1)</script> post'
        post.save(update_fields=['title'])

        response = self.client.get('/en/blog/english-post/')
        schema, breadcrumb_schema = self.rendered_json_ld(response)

        self.assertEqual(schema['headline'], post.title)
        self.assertEqual(schema['inLanguage'], 'en')
        self.assertEqual(schema['articleSection'], 'General')
        self.assertNotIn('author', schema)
        self.assertNotIn('keywords', schema)
        self.assertNotIn('image', schema)
        self.assertNotIn('dateModified', schema)
        self.assertEqual(breadcrumb_schema['@type'], 'BreadcrumbList')
        self.assertEqual(breadcrumb_schema['itemListElement'][-1]['name'], post.title)

    def test_detail_uses_visible_title_and_content_update_timestamp_for_schema(self):
        post = self.create_post()
        post.seo_title = 'Search result title'
        post.content_updated_at = timezone.now()
        post.save(update_fields=['seo_title', 'content_updated_at'])

        response = self.client.get('/en/blog/english-post/')
        schema, _breadcrumb_schema = self.rendered_json_ld(response)

        self.assertEqual(schema['headline'], post.title)
        self.assertEqual(schema['dateModified'], post.content_updated_at.isoformat())
        self.assertContains(
            response,
            (
                '<meta property="article:modified_time" '
                f'content="{timezone.localtime(post.content_updated_at).isoformat()}">'
            ),
            html=True,
        )

    def test_detail_rounds_reading_time_up_and_excludes_code(self):
        post = self.create_post()
        BlogRichTextBlock.objects.filter(parent=post).update(body=f'<p>{"word " * 201}</p>')
        BlogCodeBlock.objects.create(parent=post, region='main', code='code ' * 1000)

        response = self.client.get('/en/blog/english-post/')

        self.assertContains(response, '2 min read')

    def test_detail_highlights_code_using_selected_language(self):
        post = self.create_post()
        BlogCodeBlock.objects.create(
            parent=post,
            region='main',
            language=BlogCodeBlock.Language.PYTHON,
            code='<script>\ndef greet(name):\n    return f"Hello {name}"\n',
        )

        response = self.client.get('/en/blog/english-post/')

        self.assertContains(response, '<span class="k">def</span>', html=False)
        self.assertContains(response, '<span class="nf">greet</span>', html=False)
        self.assertContains(response, '&lt;', html=False)
        self.assertContains(response, 'script', html=False)

    def test_detail_uses_180_words_per_minute(self):
        post = self.create_post()
        BlogRichTextBlock.objects.filter(parent=post).update(body=f'<p>{"word " * 181}</p>')

        response = self.client.get('/en/blog/english-post/')

        self.assertContains(response, '2 min read')

    def test_detail_renders_square_checklist_marker(self):
        post = self.create_post()
        BlogChecklistBlock.objects.create(
            parent=post,
            region='main',
            marker=BlogChecklistBlock.Marker.SQUARE,
            items=['First item'],
        )

        response = self.client.get('/en/blog/english-post/')

        self.assertContains(response, 'blog-checklist blog-checklist--square')

    def test_detail_renders_arrow_checklist_marker(self):
        post = self.create_post()
        BlogChecklistBlock.objects.create(
            parent=post,
            region='main',
            marker=BlogChecklistBlock.Marker.ARROW,
            items=['First item'],
        )

        response = self.client.get('/en/blog/english-post/')

        self.assertContains(response, 'blog-checklist blog-checklist--arrow')

    def test_detail_renders_grouped_external_links(self):
        post = self.create_post()
        BlogLinkGroupBlock.objects.create(
            parent=post,
            region='main',
            label='Evaluation links',
            links=[
                {'label': 'Review features', 'url': 'https://example.com/features'},
                {'label': 'Try the demo', 'url': 'https://example.com/demo'},
            ],
        )

        response = self.client.get('/en/blog/english-post/')

        self.assertContains(response, 'blog-link-group')
        self.assertContains(response, 'aria-label="Evaluation links"', html=False)
        self.assertContains(response, 'href="https://example.com/features"', html=False)
        self.assertContains(response, 'target="_blank" rel="noopener noreferrer"', html=False)

    def test_detail_renders_source_label_as_plain_text_and_links_note(self):
        post = self.create_post()
        BlogSourceLinkBlock.objects.create(
            parent=post,
            region='main',
            url='https://example.com/source',
            note='Read the source',
        )

        response = self.client.get('/en/blog/english-post/')

        self.assertContains(response, '<span>Source:</span>', html=True)
        self.assertContains(
            response,
            '<a href="https://example.com/source" target="_blank" rel="noopener noreferrer">Read the source</a>',
            html=True,
        )
        self.assertNotContains(
            response,
            '<a href="https://example.com/source" target="_blank" rel="noopener noreferrer">Source:</a>',
            html=True,
        )

    def test_detail_uses_source_url_as_link_text_when_note_is_empty(self):
        post = self.create_post()
        BlogSourceLinkBlock.objects.create(
            parent=post,
            region='main',
            url='https://example.com/source',
        )

        response = self.client.get('/en/blog/english-post/')

        self.assertContains(
            response,
            '<a href="https://example.com/source" target="_blank" rel="noopener noreferrer">https://example.com/source</a>',
            html=True,
        )

    def test_detail_renders_public_tag_links(self):
        post = self.create_post()
        tag = BlogTag.objects.create(name='Django', slug='django')
        post.tags.add(tag)

        response = self.client.get('/en/blog/english-post/')

        self.assertContains(response, 'Tags')
        self.assertContains(response, 'href="/en/blog/tag/django/"', html=False)
        self.assertContains(response, 'Django')

    def test_detail_links_author_to_the_internal_author_archive(self):
        self.create_post(
            author=self.create_author_profile(
                public_author_name='Example Author',
                slug='example-author',
            )
        )

        response = self.client.get('/en/blog/english-post/')

        self.assertContains(
            response,
            '<a href="/en/blog/author/example-author/" rel="author">Example Author</a>',
            html=True,
        )

    @override_settings(SEO_CANONICAL_SCHEME='https')
    def test_detail_renders_author_profile_picture(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            author = self.create_author_profile(
                public_author_name='Author',
                profile_picture=SimpleUploadedFile(
                    'author.png',
                    b'profile-picture',
                    content_type='image/png',
                ),
            )
            self.create_post(author=author)

            response = self.client.get('/en/blog/english-post/')

            self.assertContains(response, 'class="blog-article__author-picture"', html=False)
            self.assertContains(response, 'alt="Author"', html=False)
            self.assertContains(response, 'blog/authors/', html=False)
            schema, _breadcrumb_schema = self.rendered_json_ld(response)
            self.assertEqual(schema['author']['name'], 'Author')
            self.assertEqual(
                schema['author']['url'],
                f'https://personal.example.com/en/blog/author/{author.slug}/',
            )
            self.assertTrue(schema['author']['image'].startswith('https://personal.example.com/media/blog/authors/'))
            self.assertNotIn('username', schema['author'])
            self.assertNotIn('email', schema['author'])

    def test_detail_uses_default_author_profile_picture_when_not_set(self):
        self.create_post(author=self.create_author_profile(public_author_name='Author'))

        response = self.client.get('/en/blog/english-post/')

        self.assertContains(response, 'blog-article__author-picture--default', html=False)
        self.assertContains(response, 'default-author.', html=False)

    def test_author_archive_filters_public_posts_and_is_indexable_with_two_posts(self):
        author = self.create_author_profile(public_author_name='example-author')
        self.create_post(slug='oli-first', author=author)
        self.create_post(slug='oli-second', author=author)
        self.create_post(slug='someone-else', author=self.create_author_profile(public_author_name='Another author'))

        response = self.client.get('/en/blog/author/example-author/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1 id="blog-title">Articles by example-author</h1>', html=True)
        self.assertContains(response, 'Product showcases and practical guides.')
        self.assertContains(response, '/en/blog/oli-first/')
        self.assertContains(response, '/en/blog/oli-second/')
        self.assertNotContains(response, '/en/blog/someone-else/')
        self.assertContains(response, 'href="http://personal.example.com/en/blog/author/example-author/"', html=False)
        self.assertNotContains(response, 'noindex')

    def test_single_article_author_archive_is_noindex_and_missing_author_is_404(self):
        self.create_post(slug='oli-only', author=self.create_author_profile(public_author_name='example-author'))

        response = self.client.get('/en/blog/author/example-author/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['X-Robots-Tag'], 'noindex, follow')
        self.assertContains(response, 'noindex,follow')
        self.assertEqual(self.client.get('/en/blog/author/missing/').status_code, 404)

    def test_tag_archive_filters_public_posts_and_is_indexable_with_two_posts(self):
        tag = BlogTag.objects.create(name='Django', slug='django')
        first = self.create_post(slug='django-first')
        second = self.create_post(slug='django-second')
        self.create_post(slug='unrelated')
        first.tags.add(tag)
        second.tags.add(tag)

        response = self.client.get('/en/blog/tag/django/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1 id="blog-title">Django articles</h1>', html=True)
        self.assertContains(response, 'Product showcases and practical guides.')
        self.assertContains(response, '/en/blog/django-first/')
        self.assertContains(response, '/en/blog/django-second/')
        self.assertNotContains(response, '/en/blog/unrelated/')
        self.assertEqual(response.content.count(b'blog-list__category'), 2)
        self.assertContains(response, 'href="http://personal.example.com/en/blog/tag/django/"', html=False)
        self.assertNotContains(response, 'noindex')

    def test_single_article_tag_archive_is_noindex_and_empty_tag_is_404(self):
        tag = BlogTag.objects.create(name='Django', slug='django')
        BlogTag.objects.create(name='Empty', slug='empty')
        post = self.create_post(slug='django-only')
        post.tags.add(tag)

        response = self.client.get('/en/blog/tag/django/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['X-Robots-Tag'], 'noindex, follow')
        self.assertContains(response, 'noindex,follow')
        self.assertEqual(self.client.get('/en/blog/tag/missing/').status_code, 404)
        self.assertEqual(self.client.get('/en/blog/tag/empty/').status_code, 404)

    def test_tag_archive_is_site_scoped(self):
        tag = BlogTag.objects.create(name='Django', slug='django')
        personal_post = self.create_post(slug='personal-django')
        easy_post = self.create_post(slug='easy-django', site_slug=EASY_MEALS_SITE)
        personal_post.tags.add(tag)
        easy_post.tags.add(tag)

        response = self.client.get('/en/blog/tag/django/', HTTP_HOST='recipes.example.com')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/en/blog/easy-django/')
        self.assertNotContains(response, '/en/blog/personal-django/')

    def test_tag_archive_paginates_and_keeps_its_canonical_url(self):
        tag = BlogTag.objects.create(name='Django', slug='django')
        for index in range(13):
            post = self.create_post(slug=f'tagged-{index}')
            post.tags.add(tag)

        response = self.client.get('/en/blog/tag/django/?page=2')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="blog-pagination__input"', html=False)
        self.assertContains(response, 'value="2"', html=False)
        self.assertContains(response, 'max="2"', html=False)
        self.assertContains(
            response,
            'href="http://personal.example.com/en/blog/tag/django/?page=2"',
            html=False,
        )

    def test_category_archive_filters_public_posts_and_is_indexable_with_two_posts(self):
        category = BlogCategory.objects.create(name='Django', slug='django')
        first = self.create_post(slug='django-first')
        second = self.create_post(slug='django-second')
        self.create_post(slug='unrelated')
        first.category = category
        second.category = category
        BlogPost.objects.bulk_update((first, second), ['category'])

        response = self.client.get('/en/blog/category/django/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1 id="blog-title">Django articles</h1>', html=True)
        self.assertContains(response, 'Product showcases and practical guides.')
        self.assertContains(response, '/en/blog/django-first/')
        self.assertContains(response, '/en/blog/django-second/')
        self.assertNotContains(response, '/en/blog/unrelated/')
        self.assertEqual(response.content.count(b'blog-list__category'), 2)
        self.assertContains(response, 'href="http://personal.example.com/en/blog/category/django/"', html=False)
        self.assertNotContains(response, 'noindex')

    def test_single_article_category_archive_is_noindex_and_empty_category_is_404(self):
        category = BlogCategory.objects.create(name='Django', slug='django')
        BlogCategory.objects.create(name='Empty', slug='empty')
        post = self.create_post(slug='django-only')
        post.category = category
        post.save(update_fields=['category'])

        response = self.client.get('/en/blog/category/django/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['X-Robots-Tag'], 'noindex, follow')
        self.assertContains(response, 'noindex,follow')
        self.assertEqual(self.client.get('/en/blog/category/missing/').status_code, 404)
        self.assertEqual(self.client.get('/en/blog/category/empty/').status_code, 404)

    def test_category_archive_is_site_scoped(self):
        category = BlogCategory.objects.create(name='Django', slug='django')
        personal_post = self.create_post(slug='personal-django')
        easy_post = self.create_post(slug='easy-django', site_slug=EASY_MEALS_SITE)
        personal_post.category = category
        easy_post.category = category
        BlogPost.objects.bulk_update((personal_post, easy_post), ['category'])

        response = self.client.get('/en/blog/category/django/', HTTP_HOST='recipes.example.com')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/en/blog/easy-django/')
        self.assertNotContains(response, '/en/blog/personal-django/')

    def test_category_archive_paginates_and_keeps_its_canonical_url(self):
        category = BlogCategory.objects.create(name='Django', slug='django')
        posts = [self.create_post(slug=f'categorized-{index}') for index in range(13)]
        for post in posts:
            post.category = category
        BlogPost.objects.bulk_update(posts, ['category'])

        response = self.client.get('/en/blog/category/django/?page=2')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="blog-pagination__input"', html=False)
        self.assertContains(response, 'value="2"', html=False)
        self.assertContains(response, 'max="2"', html=False)
        self.assertContains(
            response,
            'href="http://personal.example.com/en/blog/category/django/?page=2"',
            html=False,
        )

    def test_future_scheduled_article_is_unavailable(self):
        self.create_post(status=BlogPost.Status.SCHEDULED, published_at=timezone.now() + timezone.timedelta(days=1))

        self.assertEqual(self.client.get('/en/blog/english-post/').status_code, 404)
        self.assertNotContains(self.client.get('/en/blog/'), 'English post')

    def test_invalid_and_out_of_range_pages_return_404(self):
        self.create_post()

        self.assertEqual(self.client.get('/en/blog/?page=not-a-number').status_code, 404)
        self.assertEqual(self.client.get('/en/blog/?page=2').status_code, 404)

    def test_list_paginates_after_twelve_articles(self):
        for index in range(13):
            self.create_post(slug=f'post-{index}')

        first_page = self.client.get('/en/blog/')
        second_page = self.client.get('/en/blog/?page=2')

        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(second_page.status_code, 200)
        self.assertContains(first_page, 'class="blog-pagination__jump"', html=False)
        self.assertContains(first_page, 'value="1"', html=False)
        self.assertContains(first_page, 'max="2"', html=False)
        self.assertContains(first_page, '>Go</button>', html=False)
        self.assertContains(second_page, 'value="2"', html=False)
        self.assertContains(second_page, '<title>Blog | example-author — Page 2</title>', html=True)
        self.assertContains(
            second_page,
            '<meta property="og:title" content="Blog | example-author — Page 2">',
            html=True,
        )
        self.assertContains(
            second_page,
            '<meta name="twitter:title" content="Blog | example-author — Page 2">',
            html=True,
        )
        self.assertContains(second_page, 'lessons learned while shipping on the web. Page 2.')
        self.assertNotContains(first_page, '— Page 1')
        self.assertContains(first_page, 'Next →', html=False)
        self.assertContains(second_page, '← Previous', html=False)

    def test_featured_image_makes_first_body_image_lazy(self):
        featured_image = BlogImage.objects.create(name='Featured', original='featured.png')
        body_image = BlogImage.objects.create(name='Body', original='body.png')
        post = self.create_post(slug='image-loading')
        post.featured_image = featured_image
        post.save(update_fields=['featured_image'])
        BlogImageBlock.objects.create(
            parent=post,
            region='main',
            image=body_image,
        )
        source_data = {
            'original': '/media/original.png',
            'src': '/media/rendition.webp',
            'srcset': '',
            'sizes': '',
            'width': 1200,
            'height': 675,
            'alt': 'Image',
            'caption_title': '',
            'caption_text': '',
        }

        with patch('apps.blog.rendering.image_sources', return_value=source_data):
            context = build_article_context(post, site_slug=PERSONAL_SITE)

        body_block = next(
            block
            for block in context['rendered_blocks']
            if isinstance(block.item, BlogImageBlock)
        )
        self.assertEqual(body_block.loading, 'lazy')

    def test_secondary_site_uses_its_shell_and_primary_canonical(self):
        self.create_post(site_slug=EASY_MEALS_SITE, canonical_site_slug=PERSONAL_SITE)

        response = self.client.get('/en/blog/english-post/', HTTP_HOST='recipes.example.com')

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'easy_meals/blog/detail.html')
        self.assertContains(response, 'http://personal.example.com/blog/english-post/')

    @override_settings(SEO_CANONICAL_SCHEME='https')
    def test_article_seo_ignores_request_scheme_and_alternate_publication_host(self):
        self.create_post(site_slug=EASY_MEALS_SITE, canonical_site_slug=PERSONAL_SITE)

        plain_response = self.client.get('/en/blog/english-post/', HTTP_HOST='recipes.example.com')
        forwarded_response = self.client.get(
            '/en/blog/english-post/',
            HTTP_HOST='recipes.example.com',
            HTTP_X_FORWARDED_PROTO='https',
        )
        canonical_url = 'https://personal.example.com/blog/english-post/'
        social_image_url = f'https://recipes.example.com{static("easy_meals/img/logo.png")}'

        for response in (plain_response, forwarded_response):
            with self.subTest(forwarded=response.wsgi_request.is_secure()):
                self.assertContains(response, f'<link rel="canonical" href="{canonical_url}">', html=True)
                self.assertContains(response, f'<meta property="og:url" content="{canonical_url}">', html=True)
                self.assertContains(
                    response,
                    f'<meta property="og:image" content="{social_image_url}">',
                    html=True,
                )
                self.assertContains(
                    response,
                    f'<meta name="twitter:image" content="{social_image_url}">',
                    html=True,
                )
                self.assertEqual(response.context['seo']['canonical_url'], canonical_url)
                self.assertIn(canonical_url, response.context['seo']['article_schema_json'])
                self.assertNotContains(response, 'https://recipes.example.com/en/blog/english-post/')

    @override_settings(SEO_CANONICAL_SCHEME='https')
    def test_all_three_sites_serve_blog_with_site_owned_social_images(self):
        tag = BlogTag.objects.create(name='Shared', slug='shared')
        cases = (
            (
                PERSONAL_SITE,
                'testserver',
                'my_website/blog/detail.html',
                'my_website/img/avatar.png',
                'my_website/css/blog.css',
                {'@type': 'Person', 'name': 'Oli', 'url': 'https://personal.example.com/'},
                ('image', 'my_website/img/avatar.png'),
            ),
            (
                EASY_MEALS_SITE,
                'recipes.example.com',
                'easy_meals/blog/detail.html',
                'easy_meals/img/logo.png',
                'blog/css/article.css',
                {'@type': 'Organization', 'name': 'Easy Meals', 'url': 'https://recipes.example.com/'},
                ('logo', 'easy_meals/img/logo.png'),
            ),
            (
                VANTA_SITE,
                'admin-theme.example.com',
                'vanta_site/blog/detail.html',
                'vanta_site/img/social-preview.png',
                'vanta_site/css/blog.css',
                {'@type': 'Organization', 'name': 'Vanta Admin', 'url': 'https://admin-theme.example.com/en/'},
                ('logo', 'vanta_site/img/logo.png'),
            ),
        )
        for index, (site_slug, host, template, social_image, stylesheet, publisher, publisher_image) in enumerate(cases):
            with self.subTest(site_slug=site_slug):
                slug = f'site-article-{index}'
                post = self.create_post(slug=slug, site_slug=site_slug)
                post.author = self.create_author_profile(
                    public_author_name='example-author',
                    username=f'oli-{index}',
                )
                post.save(update_fields=['author'])
                post.tags.add(tag)
                post.last_reviewed_on = timezone.localdate()
                post.save(update_fields=['last_reviewed_on'])

                list_response = self.client.get('/en/blog/', HTTP_HOST=host)
                detail_response = self.client.get(f'/en/blog/{slug}/', HTTP_HOST=host)
                tag_response = self.client.get('/en/blog/tag/shared/', HTTP_HOST=host)
                author_response = self.client.get(
                    f'/en/blog/author/{post.author.slug}/',
                    HTTP_HOST=host,
                )

                self.assertEqual(list_response.status_code, 200)
                self.assertEqual(detail_response.status_code, 200)
                self.assertEqual(tag_response.status_code, 200)
                self.assertEqual(author_response.status_code, 200)
                self.assertTemplateUsed(detail_response, template)
                self.assertTemplateUsed(tag_response, template.replace('detail.html', 'list.html'))
                self.assertTemplateUsed(author_response, template.replace('detail.html', 'list.html'))
                self.assertContains(tag_response, '>Shared articles</h1>', html=False)
                self.assertContains(author_response, '>Articles by example-author</h1>', html=False)
                self.assertContains(detail_response, static(stylesheet))
                self.assertContains(list_response, static(stylesheet))
                self.assertContains(tag_response, static(stylesheet))
                self.assertContains(author_response, static(stylesheet))
                self.assertContains(detail_response, static(social_image))
                self.assertContains(detail_response, 'plausible.personal.example.com')
                self.assertNotContains(detail_response, 'content="None"')
                self.assertNotContains(
                    detail_response,
                    f'<img src="{static(social_image)}',
                    html=False,
                )
                article_schema, breadcrumb_schema = self.rendered_json_ld(detail_response)
                site_origin = publisher['url'].split('/en/')[0].rstrip('/')
                blog_prefix = '/blog/' if site_slug == PERSONAL_SITE else '/en/blog/'
                expected_article_url = f'{site_origin}{blog_prefix}{slug}/'
                expected_author_url = f'{site_origin}/en/blog/author/{post.author.slug}/'

                self.assertEqual(article_schema['mainEntityOfPage']['@id'], expected_article_url)
                self.assertEqual(article_schema['inLanguage'], 'en')
                self.assertEqual(article_schema['articleSection'], 'General')
                self.assertEqual(article_schema['keywords'], ['Shared'])
                self.assertNotIn('dateModified', article_schema)
                self.assertEqual(
                    article_schema['author'],
                    {'@type': 'Person', 'name': 'example-author', 'url': expected_author_url},
                )
                self.assertNotIn('image', article_schema)
                self.assertEqual(
                    {key: article_schema['publisher'][key] for key in ('@type', 'name', 'url')},
                    publisher,
                )
                image_key, image_path = publisher_image
                image_url = f'{site_origin}{static(image_path)}'
                publisher_image_value = article_schema['publisher'][image_key]
                if image_key == 'logo':
                    self.assertEqual(publisher_image_value, {'@type': 'ImageObject', 'url': image_url})
                else:
                    self.assertEqual(publisher_image_value, image_url)
                for unsupported_property in ('aggregateRating', 'review', 'offers', 'price', 'faq'):
                    self.assertNotIn(unsupported_property, article_schema)
                self.assertEqual(breadcrumb_schema['@type'], 'BreadcrumbList')

    def test_blog_stylesheet_falls_back_for_unknown_site(self):
        from apps.blog.rendering import get_blog_stylesheet

        self.assertEqual(get_blog_stylesheet('unknown'), 'blog/css/article.css')
        self.assertEqual(get_blog_stylesheet(PERSONAL_SITE), 'my_website/css/blog.css')
        self.assertEqual(get_blog_stylesheet(EASY_MEALS_SITE), 'blog/css/article.css')
        self.assertEqual(get_blog_stylesheet(VANTA_SITE), 'vanta_site/css/blog.css')

    def test_standalone_blog_lists_describe_social_images(self):
        cases = (
            (EASY_MEALS_SITE, 'recipes.example.com', 'Easy Meals logo'),
            (VANTA_SITE, 'admin-theme.example.com', 'Vanta Admin interface preview'),
        )
        for index, (site_slug, host, image_alt) in enumerate(cases):
            with self.subTest(site_slug=site_slug):
                self.create_post(slug=f'social-list-{index}', site_slug=site_slug)

                response = self.client.get('/en/blog/', HTTP_HOST=host)

                self.assertContains(
                    response,
                    f'<meta property="og:image:alt" content="{image_alt}">',
                    html=True,
                )
                self.assertContains(
                    response,
                    f'<meta name="twitter:image:alt" content="{image_alt}">',
                    html=True,
                )

    @override_settings(SEO_CANONICAL_SCHEME='https')
    def test_all_three_sites_advertise_their_own_english_feed_only_on_list(self):
        cases = (
            (PERSONAL_SITE, 'personal.example.com', 'example-author Blog RSS'),
            (EASY_MEALS_SITE, 'recipes.example.com', 'Easy Meals Blog RSS'),
            (VANTA_SITE, 'admin-theme.example.com', 'Vanta Admin Blog RSS'),
        )
        all_feed_urls = {f'https://{host}/en/blog/rss/' for _site_slug, host, _title in cases}

        for index, (site_slug, host, title) in enumerate(cases):
            with self.subTest(site_slug=site_slug):
                slug = f'rss-article-{index}'
                self.create_post(slug=slug, site_slug=site_slug)
                feed_url = f'https://{host}/en/blog/rss/'
                canonical_feed_url = (
                    'https://personal.example.com/blog/rss/'
                    if site_slug == PERSONAL_SITE
                    else feed_url
                )
                canonical_blog_prefix = '/blog/' if site_slug == PERSONAL_SITE else '/en/blog/'
                expected_attrs = {
                    'rel': 'alternate',
                    'type': 'application/rss+xml',
                    'title': title,
                    'href': feed_url,
                }

                response = self.client.get(
                    '/en/blog/',
                    HTTP_HOST=host,
                    HTTP_X_FORWARDED_HOST='attacker.example',
                )
                parser = RSSAutodiscoveryParser()
                parser.feed(response.content.decode())

                self.assertEqual(response.status_code, 200)
                self.assertEqual(parser.links, [(True, expected_attrs)])
                self.assertEqual(len(parser.rss_anchors), 1)
                self.assertEqual(parser.rss_anchors[0]['href'], feed_url)
                self.assertContains(response, 'aria-label="RSS feed"', html=False)
                self.assertNotContains(response, '<span>RSS feed</span>', html=False)
                for other_feed_url in all_feed_urls - {feed_url}:
                    self.assertNotContains(response, other_feed_url)

                detail_response = self.client.get(
                    f'/en/blog/{slug}/',
                    HTTP_HOST=host,
                    HTTP_X_FORWARDED_HOST='attacker.example',
                )
                detail_parser = RSSAutodiscoveryParser()
                detail_parser.feed(detail_response.content.decode())

                self.assertEqual(detail_response.status_code, 200)
                self.assertEqual(detail_parser.links, [])
                self.assertEqual(detail_parser.rss_anchors, [])
                self.assertNotContains(detail_response, '<span>RSS feed</span>', html=False)

                feed_response = self.client.get('/en/blog/rss/', HTTP_HOST=host)
                self.assertEqual(feed_response.status_code, 200)
                self.assertEqual(feed_response['Content-Type'], 'application/rss+xml; charset=utf-8')
                self.assertContains(
                    feed_response,
                    f'<atom:link href="{canonical_feed_url}" rel="self"/>',
                    html=False,
                )
                self.assertContains(
                    feed_response,
                    f'<guid>https://{host}{canonical_blog_prefix}{slug}/</guid>',
                    html=False,
                )

    @override_settings(SEO_CANONICAL_SCHEME='https')
    def test_blog_archives_advertise_rss_but_non_blog_pages_do_not(self):
        author = self.create_author_profile(public_author_name='Archive Author')
        tag = BlogTag.objects.create(name='Archive Tag', slug='archive-tag')
        post = self.create_post(author=author)
        post.tags.add(tag)

        for path in (
            '/en/blog/category/general/',
            '/en/blog/tag/archive-tag/',
            f'/en/blog/author/{author.slug}/',
        ):
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST='personal.example.com')
                parser = RSSAutodiscoveryParser()
                parser.feed(response.content.decode())

                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(parser.links), 1)
                self.assertEqual(parser.links[0][1]['href'], 'https://personal.example.com/en/blog/rss/')

        home_response = self.client.get('/en/', HTTP_HOST='personal.example.com', follow=True)
        parser = RSSAutodiscoveryParser()
        parser.feed(home_response.content.decode())

        self.assertEqual(home_response.status_code, 200)
        self.assertEqual(parser.links, [])

    def test_feed_contains_only_public_english_articles(self):
        self.create_post(slug='feed-article')
        self.create_post(slug='feed-future', status=BlogPost.Status.SCHEDULED, published_at=timezone.now() + timezone.timedelta(days=1))

        response = self.client.get('/en/blog/rss/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'feed-article')
        self.assertNotContains(response, 'feed-future')
        self.assertContains(response, 'http://personal.example.com/blog/feed-article/')
        self.assertContains(response, '<language>en</language>')

    def test_feed_uses_content_update_timestamp_not_review_date(self):
        post = self.create_post(slug='updated-feed-article')
        post.content_updated_at = timezone.now()
        post.last_reviewed_on = timezone.localdate() - timezone.timedelta(days=30)
        post.save(update_fields=['content_updated_at', 'last_reviewed_on'])

        self.assertEqual(BlogFeed().item_updateddate(post), post.content_updated_at)

    def test_feed_is_pretty_printed(self):
        self.create_post(slug='feed-article')

        response = self.client.get('/en/blog/rss/')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'\n<rss ', response.content)
        self.assertIn(b'\n  <channel>', response.content)

    @override_settings(SEO_CANONICAL_SCHEME='https')
    def test_feed_uses_configured_https_origin_without_forwarded_proto(self):
        self.create_post(slug='feed-article')

        response = self.client.get('/en/blog/rss/', HTTP_HOST='personal.example.com')

        self.assertContains(response, '<link>https://personal.example.com/blog/</link>', html=False)
        self.assertContains(response, '<atom:link href="https://personal.example.com/blog/rss/" rel="self"/>', html=False)
        self.assertContains(response, '<guid>https://personal.example.com/blog/feed-article/</guid>', html=False)

    def test_non_english_feed_returns_404(self):
        self.assertEqual(self.client.get('/fr/blog/rss/').status_code, 404)

    def test_faq_renders_ordered_collapsed_crawlable_content_and_reading_time(self):
        post = self.create_post(slug='faq-article')
        BlogRichTextBlock.objects.filter(parent=post).update(body='<p>short</p>')
        faq = BlogFAQBlock.objects.create(
            parent=post,
            region='main',
            ordering=10,
            items=[
                {'question': 'First question?', 'answer': f'<p>{"answer " * 180}</p>'},
                {'question': 'Second question?', 'answer': '<ul><li>Second answer.</li></ul>'},
            ],
        )

        response = self.client.get('/en/blog/faq-article/')
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn(f'aria-labelledby="blog-faq-{faq.pk}-title"', content)
        self.assertIn('Frequently asked questions', content)
        self.assertEqual(content.count('class="blog-faq__item"'), 2)
        self.assertNotIn('<details class="blog-faq__item" open', content)
        self.assertLess(content.index('First question?'), content.index('Second question?'))
        self.assertIn('<ul><li>Second answer.</li></ul>', content)
        self.assertContains(response, '2 min read')
        schemas = self.rendered_json_ld(response)
        self.assertNotIn('FAQPage', {schema.get('@type') for schema in schemas})

    def test_empty_or_malformed_faq_is_omitted_without_breaking_the_article(self):
        post = self.create_post(slug='invalid-faq')
        BlogFAQBlock.objects.create(parent=post, region='main', items=[])
        BlogFAQBlock.objects.create(
            parent=post,
            region='main',
            items=[{'question': '<script>Unsafe</script>', 'answer': '<script>alert(1)</script>'}],
        )

        response = self.client.get('/en/blog/invalid-faq/')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'class="blog-faq"', html=False)
        self.assertNotContains(response, '<script>Unsafe</script>', html=False)

    def test_faq_internal_links_resolve_and_multiple_blocks_have_unique_headings(self):
        post = self.create_post(slug='linked-faq')
        answer = '<p><a data-blog-internal-key="personal-projects">Projects</a></p>'
        first = BlogFAQBlock.objects.create(
            parent=post,
            region='main',
            items=[{'question': 'First?', 'answer': answer}],
        )
        second = BlogFAQBlock.objects.create(
            parent=post,
            region='main',
            items=[{'question': 'Second?', 'answer': '<p>Answer.</p>'}],
        )
        BlogFAQBlock.objects.create(
            parent=post,
            region='main',
            items=[{
                'question': 'Stale?',
                'answer': '<p><a data-blog-internal-key="missing">Stale link</a></p>',
            }],
        )

        response = self.client.get('/en/blog/linked-faq/')

        self.assertContains(
            response,
            '<a href="/projects/" target="_blank" rel="noopener noreferrer">Projects</a>',
            html=True,
        )
        self.assertContains(response, f'id="blog-faq-{first.pk}-title"', html=False)
        self.assertContains(response, f'id="blog-faq-{second.pk}-title"', html=False)
        self.assertContains(
            response,
            '<a target="_blank" rel="noopener noreferrer">Stale link</a>',
            html=True,
        )
        self.assertNotContains(response, 'data-blog-internal-key="missing"', html=False)

    def test_faq_uses_shared_markup_and_each_site_blog_stylesheet(self):
        cases = (
            ('personal.example.com', 'my_website/css/blog.css'),
            ('easymeals.localhost', 'blog/css/article.css'),
            ('vanta.localhost', 'vanta_site/css/blog.css'),
        )
        for index, (host, stylesheet) in enumerate(cases):
            slug = f'faq-shell-{index}'
            site_slug = (PERSONAL_SITE, EASY_MEALS_SITE, VANTA_SITE)[index]
            post = self.create_post(slug=slug, site_slug=site_slug)
            BlogFAQBlock.objects.create(
                parent=post,
                region='main',
                items=[{'question': 'Shared question?', 'answer': '<p>Shared answer.</p>'}],
            )

            with self.subTest(host=host):
                response = self.client.get(f'/en/blog/{slug}/', HTTP_HOST=host)
                self.assertContains(response, 'class="blog-faq"', html=False)
                self.assertContains(response, static(stylesheet))
