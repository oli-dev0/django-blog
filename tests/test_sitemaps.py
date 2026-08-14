from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from django.contrib.auth import get_user_model

from apps.blog.models import AuthorProfile, BlogCategory, BlogPost, BlogPostPublication, BlogRichTextBlock, BlogTag
from apps.blog.sitemaps import get_sitemap_entries
from apps.core.sites import EASY_MEALS_SITE, PERSONAL_SITE, VANTA_SITE, get_site_definition

from .site_settings import BLOG_ENABLED_SITE_DEFINITIONS


@override_settings(SITE_DEFINITIONS=BLOG_ENABLED_SITE_DEFINITIONS)
class BlogSitemapTests(TestCase):
    def create_post(self, *, slug, canonical_site=PERSONAL_SITE):
        category, _created = BlogCategory.objects.get_or_create(
            name='General',
            defaults={'slug': 'general'},
        )
        post = BlogPost.objects.create(
            status=BlogPost.Status.PUBLISHED,
            title='Published article',
            slug=slug,
            summary='Summary',
            published_at=timezone.now(),
            canonical_site_slug=canonical_site,
            category=category,
        )
        BlogPostPublication.objects.create(post=post, site_slug=PERSONAL_SITE)
        BlogPostPublication.objects.create(post=post, site_slug=EASY_MEALS_SITE)
        BlogRichTextBlock.objects.create(parent=post, region='main', body='<p>Body</p>')
        return post

    def create_author_profile(self, *, public_author_name, username, slug=''):
        return AuthorProfile.objects.create(
            user=get_user_model().objects.create_user(username=username),
            public_author_name=public_author_name,
            slug=slug,
        )

    def test_sitemap_contains_english_list_and_only_canonical_detail(self):
        post = self.create_post(slug='canonical-article')
        post.content_updated_at = timezone.now()
        post.save(update_fields=['content_updated_at'])
        request = RequestFactory().get('/sitemap.xml', HTTP_HOST='testserver')

        personal_entries = get_sitemap_entries(
            request=request,
            site=get_site_definition(PERSONAL_SITE),
            languages=['en', 'fr', 'nl'],
        )
        easy_entries = get_sitemap_entries(
            request=request,
            site=get_site_definition(EASY_MEALS_SITE),
            languages=['en', 'fr', 'nl'],
        )

        personal_urls = [entry['loc'] for entry in personal_entries]
        easy_urls = [entry['loc'] for entry in easy_entries]
        self.assertIn('http://personal.example.com/blog/', personal_urls)
        self.assertIn('http://personal.example.com/blog/canonical-article/', personal_urls)
        self.assertIn('http://recipes.example.com/en/blog/', easy_urls)
        self.assertNotIn('http://recipes.example.com/en/blog/canonical-article/', easy_urls)
        self.assertEqual(post.last_reviewed_on, None)
        detail_entry = next(
            entry
            for entry in personal_entries
            if entry['loc'] == 'http://personal.example.com/blog/canonical-article/'
        )
        self.assertEqual(detail_entry['lastmod'], post.content_updated_at.isoformat())

    def test_noncanonical_site_does_not_get_detail_entry(self):
        self.create_post(slug='easy-canonical', canonical_site=EASY_MEALS_SITE)
        request = RequestFactory().get('/sitemap.xml', HTTP_HOST='recipes.example.com')
        entries = get_sitemap_entries(
            request=request,
            site=get_site_definition(EASY_MEALS_SITE),
            languages=['en'],
        )
        self.assertIn('http://recipes.example.com/en/blog/easy-canonical/', [entry['loc'] for entry in entries])

    def test_vanta_sitemap_includes_enabled_blog(self):
        request = RequestFactory().get('/sitemap.xml', HTTP_HOST='admin-theme.example.com')

        entries = get_sitemap_entries(
            request=request,
            site=get_site_definition(VANTA_SITE),
            languages=['en'],
        )

        self.assertIn('http://admin-theme.example.com/en/blog/', [entry['loc'] for entry in entries])

    def test_sitemap_includes_indexable_tag_archive(self):
        tag = BlogTag.objects.create(name='Django', slug='django')
        sparse_tag = BlogTag.objects.create(name='Sparse', slug='sparse')
        first = self.create_post(slug='tag-first')
        second = self.create_post(slug='tag-second')
        first.tags.add(tag)
        first.tags.add(sparse_tag)
        second.tags.add(tag)
        request = RequestFactory().get('/sitemap.xml', HTTP_HOST='testserver')

        entries = get_sitemap_entries(
            request=request,
            site=get_site_definition(PERSONAL_SITE),
            languages=['en'],
        )

        urls = [entry['loc'] for entry in entries]
        self.assertIn('http://personal.example.com/blog/tag/django/', urls)
        self.assertNotIn('http://personal.example.com/blog/tag/sparse/', urls)

    def test_sitemap_includes_indexable_category_archive(self):
        category = BlogCategory.objects.create(name='Django', slug='django')
        sparse_category = BlogCategory.objects.create(name='Sparse', slug='sparse')
        first = self.create_post(slug='category-first')
        second = self.create_post(slug='category-second')
        first.category = category
        second.category = category
        sparse = self.create_post(slug='category-sparse')
        sparse.category = sparse_category
        BlogPost.objects.bulk_update((first, second, sparse), ['category'])
        request = RequestFactory().get('/sitemap.xml', HTTP_HOST='testserver')

        entries = get_sitemap_entries(
            request=request,
            site=get_site_definition(PERSONAL_SITE),
            languages=['en'],
        )

        urls = [entry['loc'] for entry in entries]
        self.assertIn('http://personal.example.com/blog/category/django/', urls)
        self.assertNotIn('http://personal.example.com/blog/category/sparse/', urls)

    def test_sitemap_includes_indexable_author_archive(self):
        first = self.create_post(slug='oli-first')
        second = self.create_post(slug='oli-second')
        sparse = self.create_post(slug='sparse-author')
        first.author = self.create_author_profile(
            public_author_name='Example Author',
            username='oli',
            slug='example-author',
        )
        second.author = first.author
        sparse.author = self.create_author_profile(public_author_name='Sparse author', username='sparse')
        BlogPost.objects.bulk_update((first, second, sparse), ['author'])
        request = RequestFactory().get('/sitemap.xml', HTTP_HOST='testserver')

        entries = get_sitemap_entries(
            request=request,
            site=get_site_definition(PERSONAL_SITE),
            languages=['en'],
        )

        urls = [entry['loc'] for entry in entries]
        self.assertIn('http://personal.example.com/blog/author/example-author/', urls)
        self.assertNotIn('http://personal.example.com/blog/author/sparse-author/', urls)
