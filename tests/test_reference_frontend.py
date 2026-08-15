import json
import re

from django.test import TestCase, override_settings
from django.templatetags.static import static
from django.utils import timezone

from apps.blog.models import BlogCategory, BlogPost, BlogPostPublication, BlogRichTextBlock
from apps.core.sites import VANTA_SITE

from .site_settings import BLOG_ENABLED_SITE_DEFINITIONS, REFERENCE_BLOG_SITE_DEFINITIONS


@override_settings(SITE_DEFINITIONS=REFERENCE_BLOG_SITE_DEFINITIONS)
class ReferenceBlogFrontendIntegrationTests(TestCase):
    host = 'vanta.localhost'

    def create_post(self, slug='reference-article'):
        category, _created = BlogCategory.objects.get_or_create(
            name='General',
            defaults={'slug': 'general'},
        )
        post = BlogPost.objects.create(
            status=BlogPost.Status.PUBLISHED,
            title='Reference article',
            slug=slug,
            summary='A neutral reference article.',
            published_at=timezone.now(),
            canonical_site_slug=VANTA_SITE,
            category=category,
        )
        BlogPostPublication.objects.create(post=post, site_slug=VANTA_SITE)
        BlogRichTextBlock.objects.create(parent=post, region='main', body='<p>Reference body.</p>')
        return post

    def test_empty_list_uses_the_opt_in_reference_shell(self):
        response = self.client.get('/en/blog/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'blog/list.html')
        self.assertContains(response, 'No articles have been published yet.')
        self.assertContains(response, static('blog/css/shell.css'))
        self.assertContains(response, static('blog/css/article.css'))
        self.assertContains(response, 'Vanta Admin Blog')
        self.assertEqual(response.headers['Content-Language'], 'en')

    def test_list_and_detail_preserve_metadata_rss_and_json_ld(self):
        post = self.create_post()

        list_response = self.client.get('/en/blog/', HTTP_HOST=self.host)
        detail_response = self.client.get(f'/en/blog/{post.slug}/', HTTP_HOST=self.host)

        self.assertTemplateUsed(list_response, 'blog/list.html')
        self.assertTemplateUsed(detail_response, 'blog/detail.html')
        self.assertContains(list_response, 'application/rss+xml')
        self.assertContains(list_response, 'name="robots" content="index, follow"')
        self.assertContains(detail_response, '<link rel="canonical"', html=False)
        self.assertContains(detail_response, 'name="description"', html=False)
        schemas = re.findall(
            rb'<script type="application/ld\+json">(.*?)</script>',
            detail_response.content,
            flags=re.DOTALL,
        )
        self.assertEqual(len(schemas), 2)
        self.assertEqual({json.loads(schema)['@type'] for schema in schemas}, {'BlogPosting', 'BreadcrumbList'})

    def test_filtered_reference_list_remains_noindex(self):
        self.create_post()

        response = self.client.get('/en/blog/?q=does-not-match', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['X-Robots-Tag'], 'noindex, follow')
        self.assertContains(response, 'name="robots" content="noindex,follow"')

    def test_missing_reference_template_namespace_still_reports_e003(self):
        broken_sites = {
            **REFERENCE_BLOG_SITE_DEFINITIONS,
            VANTA_SITE: {
                **REFERENCE_BLOG_SITE_DEFINITIONS[VANTA_SITE],
                'template_namespace': 'missing_reference_shell',
            },
        }

        with override_settings(SITE_DEFINITIONS=broken_sites):
            from blog.checks import check_blog_site_definitions

            errors = check_blog_site_definitions(None)

        self.assertIn('blog.E003', {error.id for error in errors})


@override_settings(SITE_DEFINITIONS=BLOG_ENABLED_SITE_DEFINITIONS)
class ReferenceFrontendCompatibilityTests(TestCase):
    def test_reference_namespace_resolves_app_owned_templates(self):
        from apps.blog.views import get_blog_template
        from apps.core.sites import get_site_definition

        with override_settings(SITE_DEFINITIONS=REFERENCE_BLOG_SITE_DEFINITIONS):
            self.assertEqual(
                get_blog_template(get_site_definition(VANTA_SITE), 'list'),
                'blog/list.html',
            )

    def test_existing_site_owned_templates_remain_selected(self):
        from apps.blog.views import get_blog_template
        from apps.core.sites import get_site_definition

        self.assertEqual(
            get_blog_template(get_site_definition(VANTA_SITE), 'list'),
            'vanta_site/blog/list.html',
        )
