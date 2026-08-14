from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.blog.models import (
    AuthorProfile,
    BlogCalloutBlock,
    BlogCategory,
    BlogChecklistBlock,
    BlogCodeBlock,
    BlogEmbedSharingBlock,
    BlogFAQBlock,
    BlogHeadingBlock,
    BlogImage,
    BlogImageBlock,
    BlogInternalLinkBlock,
    BlogLinkGroupBlock,
    BlogPost,
    BlogPostPublication,
    BlogRichTextBlock,
    BlogSourceLinkBlock,
    BlogTag,
)
from apps.blog.filters import FilterState
from apps.blog.content_text import normalize_reader_text
from apps.blog.rendering import get_reading_time_minutes
from apps.blog.selectors import get_public_posts
from apps.core.sites import EASY_MEALS_SITE, PERSONAL_SITE, VANTA_SITE

from .site_settings import BLOG_ENABLED_SITE_DEFINITIONS


class BlogSearchTestMixin:
    def create_post(
        self,
        *,
        slug,
        title=None,
        summary='Ordinary summary',
        category=None,
        tags=(),
        author=None,
        site_slug=PERSONAL_SITE,
        status=BlogPost.Status.PUBLISHED,
        published_at=None,
        body='Ordinary body',
        post_type=BlogPost.Type.ARTICLE,
    ):
        category = category or BlogCategory.objects.create(
            name=f'Category {slug}',
            slug=f'category-{slug}',
        )
        post = BlogPost.objects.create(
            status=status,
            type=post_type,
            title=title or slug,
            slug=slug,
            summary=summary,
            published_at=published_at or timezone.now(),
            canonical_site_slug=site_slug,
            category=category,
            author=author,
        )
        BlogPostPublication.objects.create(post=post, site_slug=site_slug)
        BlogRichTextBlock.objects.create(
            parent=post,
            region='main',
            body=f'<p>{body}</p>',
        )
        post.tags.add(*tags)
        post.refresh_from_db()
        return post


class BlogSearchIndexTests(BlogSearchTestMixin, TestCase):
    def test_reader_facing_blocks_are_indexed_and_non_reader_metadata_is_excluded(self):
        post = self.create_post(slug='reader-content')
        BlogHeadingBlock.objects.create(
            parent=post,
            region='main',
            text='Heading Needle',
            anchor='heading-needle',
        )
        BlogRichTextBlock.objects.create(
            parent=post,
            region='main',
            body='<p>Rich &amp; Needle</p>',
        )
        BlogFAQBlock.objects.create(
            parent=post,
            region='main',
            items=[{'question': 'FAQ Needle?', 'answer': '<p>FAQ answer Needle</p>'}],
        )
        BlogChecklistBlock.objects.create(
            parent=post,
            region='main',
            items=['Checklist Needle'],
        )
        BlogCalloutBlock.objects.create(
            parent=post,
            region='main',
            title='Callout Needle',
            body='<p>Callout body Needle</p>',
        )
        BlogSourceLinkBlock.objects.create(
            parent=post,
            region='main',
            label='Source Needle',
            url='https://example.com/source-only-needle',
            note='Source note Needle',
        )
        BlogLinkGroupBlock.objects.create(
            parent=post,
            region='main',
            label='Links Needle',
            links=[{'label': 'Link label Needle', 'url': 'https://example.com/link-only-needle'}],
        )
        BlogInternalLinkBlock.objects.create(
            parent=post,
            region='main',
            destination_key='non-searchable-destination',
            label='Internal label Needle',
            note='Internal note Needle',
        )
        BlogCodeBlock.objects.create(
            parent=post,
            region='main',
            code='CodeOnlyNeedle',
            caption='CodeCaptionOnlyNeedle',
        )
        image = BlogImage.objects.create(
            name='ImageNameOnlyNeedle',
            original='blog/originals/image.png',
            alt_text='ImageAltOnlyNeedle',
            caption_title='ImageCaptionOnlyNeedle',
        )
        BlogImageBlock.objects.create(parent=post, region='main', image=image)
        post.refresh_from_db()

        for query in (
            'Heading Needle',
            'Rich & Needle',
            'FAQ answer Needle',
            'Checklist Needle',
            'Callout body Needle',
            'Source note Needle',
            'Link label Needle',
            'Internal note Needle',
        ):
            with self.subTest(query=query):
                self.assertIn(
                    post,
                    get_public_posts(
                        site_slug=PERSONAL_SITE,
                        filters=FilterState(search_query=query),
                    ),
                )
        self.assertNotIn('<p>', post.search_body_text)
        self.assertIn('Rich & Needle', post.search_body_text)
        for excluded_query in (
            'CodeOnlyNeedle',
            'CodeCaptionOnlyNeedle',
            'ImageNameOnlyNeedle',
            'ImageAltOnlyNeedle',
            'ImageCaptionOnlyNeedle',
            'source-only-needle',
            'link-only-needle',
            'non-searchable-destination',
        ):
            with self.subTest(excluded_query=excluded_query):
                self.assertNotIn(
                    post,
                    get_public_posts(
                        site_slug=PERSONAL_SITE,
                        filters=FilterState(search_query=excluded_query),
                    ),
                )

    def test_block_create_update_and_delete_refresh_search_body(self):
        post = self.create_post(slug='signal-refresh')
        block = BlogHeadingBlock.objects.create(
            parent=post,
            region='main',
            text='Created Needle',
            anchor='created-needle',
        )
        post.refresh_from_db()
        self.assertIn('Created Needle', post.search_body_text)

        block.text = 'Updated Needle'
        block.save()
        post.refresh_from_db()
        self.assertNotIn('Created Needle', post.search_body_text)
        self.assertIn('Updated Needle', post.search_body_text)

        block.delete()
        post.refresh_from_db()
        self.assertNotIn('Updated Needle', post.search_body_text)

    def test_embed_caption_is_indexed_but_provider_metadata_is_not(self):
        post = self.create_post(slug='embed-caption-search')
        block = BlogEmbedSharingBlock.objects.create(
            parent=post,
            region='main',
            platform=BlogEmbedSharingBlock.Platform.YOUTUBE,
            url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            caption='Caption Needle',
        )
        post.refresh_from_db()

        self.assertIn('Caption Needle', post.search_body_text)
        self.assertNotIn('youtube', post.search_body_text.casefold())
        self.assertNotIn('dQw4w9WgXcQ', post.search_body_text)
        self.assertIn(
            post,
            get_public_posts(
                site_slug=PERSONAL_SITE,
                filters=FilterState(search_query='Caption Needle'),
            ),
        )
        for excluded_query in ('youtube', 'dQw4w9WgXcQ', 'provider title'):
            with self.subTest(excluded_query=excluded_query):
                self.assertNotIn(
                    post,
                    get_public_posts(
                        site_slug=PERSONAL_SITE,
                        filters=FilterState(search_query=excluded_query),
                    ),
                )
        block.caption = 'Updated Caption'
        block.save(update_fields=['caption'])
        post.refresh_from_db()
        self.assertNotIn('Caption Needle', post.search_body_text)
        self.assertIn('Updated Caption', post.search_body_text)

    def test_embed_caption_contributes_to_reading_time(self):
        post = self.create_post(
            slug='embed-reading-time',
            body=' '.join(['bodyword'] * 160),
        )
        self.assertEqual(get_reading_time_minutes(post), 1)

        BlogEmbedSharingBlock.objects.create(
            parent=post,
            region='main',
            platform=BlogEmbedSharingBlock.Platform.YOUTUBE,
            url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            caption=' '.join(['captionword'] * 25),
        )

        self.assertEqual(get_reading_time_minutes(post), 2)

    def test_html_boundaries_remain_searchable_word_boundaries(self):
        post = self.create_post(
            slug='html-boundaries',
            body=(
                '<p>Alpha</p><p>Beta</p><ul><li>Gam<strong>ma</strong></li><li>Delta</li></ul>'
                'Epsilon<br>Zeta &amp; Eta'
            ),
        )

        self.assertIn(
            'Alpha Beta Gamma Delta Epsilon Zeta & Eta',
            post.search_body_text,
        )
        self.assertEqual(
            normalize_reader_text('&amp;lt;Theta&amp;gt;'),
            '&lt;Theta&gt;',
        )
        self.assertNotIn(
            post,
            get_public_posts(
                site_slug=PERSONAL_SITE,
                filters=FilterState(search_query='AlphaBeta'),
            ),
        )


class BlogSearchSelectorTests(BlogSearchTestMixin, TestCase):
    def test_search_term_predicates_are_deduplicated_and_body_text_is_deferred(self):
        self.create_post(slug='bounded-query', title='Needle')

        queryset = get_public_posts(
            site_slug=PERSONAL_SITE,
            filters=FilterState(search_query='Needle needle NEEDLE'),
        )
        result = queryset.get()

        self.assertEqual(str(queryset.query).count('EXISTS'), 2)
        self.assertIn('search_body_text', result.get_deferred_fields())

    def test_searches_metadata_case_insensitively_and_requires_every_word(self):
        category = BlogCategory.objects.create(name='Django Development', slug='django-development')
        tag = BlogTag.objects.create(name='Admin UX', slug='admin-ux')
        title = self.create_post(slug='title-match', title='SEARCHABLE title')
        summary = self.create_post(slug='summary-match', summary='Searchable summary')
        taxonomy = self.create_post(slug='taxonomy-match', category=category, tags=(tag,))
        body = self.create_post(slug='body-match', body='Searchable body')

        self.assertEqual(
            set(get_public_posts(site_slug=PERSONAL_SITE, filters=FilterState(search_query='searchable'))),
            {title, summary, body},
        )
        self.assertEqual(
            list(get_public_posts(site_slug=PERSONAL_SITE, filters=FilterState(search_query='django development'))),
            [taxonomy],
        )
        self.assertEqual(
            list(get_public_posts(site_slug=PERSONAL_SITE, filters=FilterState(search_query='admin ux'))),
            [taxonomy],
        )
        self.assertEqual(
            list(get_public_posts(site_slug=PERSONAL_SITE, filters=FilterState(search_query='searchable missing'))),
            [],
        )

    def test_relevance_then_publication_date_and_primary_key_control_order(self):
        now = timezone.now()
        old_title = self.create_post(
            slug='old-title',
            title='Needle Phrase title',
            published_at=now - timedelta(days=2),
        )
        new_title = self.create_post(
            slug='new-title',
            title='Needle Phrase newer title',
            published_at=now - timedelta(days=1),
        )
        taxonomy = self.create_post(
            slug='taxonomy',
            category=BlogCategory.objects.create(
                name='Needle Phrase taxonomy',
                slug='needle-phrase-taxonomy',
            ),
            published_at=now,
        )
        summary = self.create_post(
            slug='summary',
            summary='Needle Phrase summary',
            published_at=now,
        )
        body = self.create_post(
            slug='body',
            body='Needle Phrase body',
            published_at=now,
        )

        results = list(
            get_public_posts(
                site_slug=PERSONAL_SITE,
                filters=FilterState(search_query='needle phrase'),
            )
        )

        self.assertEqual(results, [new_title, old_title, taxonomy, summary, body])

    def test_search_retains_publication_status_and_site_boundaries(self):
        now = timezone.now()
        visible = self.create_post(slug='visible', title='Scoped Needle')
        due = self.create_post(
            slug='due',
            title='Scoped Needle due',
            status=BlogPost.Status.SCHEDULED,
            published_at=now - timedelta(minutes=1),
        )
        self.create_post(
            slug='future',
            title='Scoped Needle future',
            status=BlogPost.Status.SCHEDULED,
            published_at=now + timedelta(days=1),
        )
        self.create_post(
            slug='draft',
            title='Scoped Needle draft',
            status=BlogPost.Status.DRAFT,
        )
        self.create_post(
            slug='other-site',
            title='Scoped Needle other',
            site_slug=EASY_MEALS_SITE,
        )

        self.assertEqual(
            list(
                get_public_posts(
                    site_slug=PERSONAL_SITE,
                    now=now,
                    filters=FilterState(search_query='scoped needle'),
                )
            ),
            [visible, due],
        )


@override_settings(SITE_DEFINITIONS=BLOG_ENABLED_SITE_DEFINITIONS)
class BlogSearchViewTests(BlogSearchTestMixin, TestCase):
    def test_search_markup_results_links_pagination_clear_and_seo(self):
        category = BlogCategory.objects.create(name='Django', slug='django')
        for index in range(13):
            self.create_post(
                slug=f'needle-{index:02d}',
                title=f'Needle article {index:02d}',
                category=category,
                post_type=BlogPost.Type.GUIDE,
                published_at=timezone.now() - timedelta(minutes=index),
            )

        response = self.client.get('/en/blog/?q=Needle&type=guide&category=django')
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['X-Robots-Tag'], 'noindex, follow')
        self.assertContains(response, '<meta name="robots" content="noindex,follow">', html=True)
        self.assertContains(response, '<link rel="canonical" href="http://personal.example.com/en/blog/">', html=True)
        self.assertContains(response, 'role="search"', html=False)
        self.assertContains(response, 'type="search" name="q" value="Needle"', html=False)
        self.assertContains(response, 'placeholder="Search"', html=False)
        self.assertContains(response, 'aria-label="Search articles"', count=1)
        self.assertContains(response, '>Clear</a>', html=False)
        self.assertLess(content.index('blog-filters__toolbar'), content.index('blog-filters__search-form'))
        self.assertLess(content.index('blog-filters__search-form'), content.index('blog-filters__types-row'))
        self.assertContains(
            response,
            'href="/en/blog/?q=Needle&amp;type=article&amp;category=django"',
            html=False,
        )
        self.assertContains(
            response,
            'href="/en/blog/?q=Needle&amp;type=guide&amp;category=django&amp;page=2"',
            html=False,
        )
        self.assertContains(response, '<input type="hidden" name="q" value="Needle">', html=True)
        self.assertContains(
            response,
            '<a class="blog-filters__search-clear" href="/en/blog/?type=guide&amp;category=django">Clear</a>',
            html=True,
        )
        self.assertContains(
            response,
            '<a class="blog-filters__clear" href="/en/blog/">',
            html=False,
        )

    def test_query_normalization_and_search_empty_state(self):
        category = BlogCategory.objects.create(name='General', slug='general')
        self.create_post(slug='article', category=category)

        response = self.client.get(
            '/en/blog/?q=%20%20No%20%20matches%20%20&q=ignored&unknown=value&page=2'
        )
        self.assertRedirects(
            response,
            '/en/blog/?q=No+matches&page=2',
            fetch_redirect_response=False,
        )
        self.assertRedirects(
            self.client.get('/en/blog/?q=%20%20%20'),
            '/en/blog/',
            fetch_redirect_response=False,
        )

        response = self.client.get('/en/blog/?q=No+matches&category=general')
        self.assertContains(response, 'No articles match your search')
        self.assertContains(response, 'Try a different search or clear it to see more articles.')
        self.assertContains(response, 'href="/en/blog/?category=general">Clear search</a>', html=False)
        self.assertContains(response, 'href="/en/blog/">Clear all filters</a>', html=False)

    def test_archive_searches_redirect_to_combined_homepage_and_forms_preserve_archive(self):
        category = BlogCategory.objects.create(name='Django', slug='django')
        tag = BlogTag.objects.create(name='Python', slug='python')
        author = AuthorProfile.objects.create(
            user=get_user_model().objects.create_user(username='search-author'),
            public_author_name='Search Author',
            slug='search-author',
        )
        self.create_post(
            slug='archive-post',
            category=category,
            tags=(tag,),
            author=author,
        )

        cases = (
            ('/en/blog/category/django/', '/en/blog/?q=Needle&category=django', 'category', 'django'),
            ('/en/blog/tag/python/', '/en/blog/?q=Needle&tag=python', 'tag', 'python'),
            ('/en/blog/author/search-author/', '/en/blog/?q=Needle&author=search-author', 'author', 'search-author'),
        )
        for archive_url, target, parameter, value in cases:
            with self.subTest(archive_url=archive_url):
                self.assertRedirects(
                    self.client.get(f'{archive_url}?q=Needle'),
                    target,
                    fetch_redirect_response=False,
                )
                response = self.client.get(archive_url)
                self.assertContains(response, 'class="blog-filters__search-form"', html=False)
                self.assertContains(
                    response,
                    f'<input type="hidden" name="{parameter}" value="{value}">',
                    html=True,
                )
                self.assertNotContains(response, 'blog-filters__search-clear', html=False)

    def test_search_control_renders_in_every_blog_shell(self):
        cases = (
            (PERSONAL_SITE, 'testserver'),
            (EASY_MEALS_SITE, 'recipes.example.com'),
            (VANTA_SITE, 'admin-theme.example.com'),
        )
        for index, (site_slug, host) in enumerate(cases):
            with self.subTest(site_slug=site_slug):
                self.create_post(slug=f'shell-{index}', site_slug=site_slug)
                response = self.client.get('/en/blog/', HTTP_HOST=host)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'class="blog-filters__search-form"', html=False)
                self.assertContains(response, 'id="blog-search-input"', html=False)
