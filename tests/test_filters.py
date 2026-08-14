from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.blog.filters import (
    FilterOption,
    FilterOptions,
    FilterState,
    SEARCH_MAX_TERMS,
    normalize_search_query,
    parse_filter_state,
    relative_date_bounds,
    serialize_filter_state,
)
from apps.blog.models import (
    AuthorProfile,
    BlogCategory,
    BlogPost,
    BlogPostPublication,
    BlogRichTextBlock,
    BlogTag,
)
from apps.blog.selectors import get_public_filter_options, get_public_posts
from apps.core.sites import EASY_MEALS_SITE, PERSONAL_SITE, VANTA_SITE

from .site_settings import BLOG_ENABLED_SITE_DEFINITIONS


BLOG_LIST_JS = Path(__file__).resolve().parents[2] / 'apps/blog/static/blog/js/list.js'


class BlogFilterJavascriptTests(TestCase):
    def test_only_tag_changes_remember_the_open_dropdown(self):
        javascript = BLOG_LIST_JS.read_text(encoding='utf-8')

        self.assertIn(
            '''if (control.matches("input[name='tag']")) {
          const dropdown = control.closest("[data-blog-filter-dropdown]");
          if (dropdown) {
            rememberDropdown(dropdown);
          }
        }
        form.requestSubmit();''',
            javascript,
        )


class FilterStateTests(TestCase):
    def setUp(self):
        self.options = FilterOptions(
            article_types=(
                FilterOption('article', 'Article'),
                FilterOption('guide', 'Guide'),
                FilterOption('comparison', 'Comparison'),
            ),
            categories=(FilterOption('django', 'Django'),),
            authors=(FilterOption('example-author', 'Example Author'),),
            tags=(FilterOption('django', 'Django'), FilterOption('python', 'Python')),
            years=(FilterOption('2026', '2026'), FilterOption('2025', '2025')),
        )

    def test_parser_uses_one_article_type_and_serializes_in_stable_order(self):
        query = QueryDict(
            'tag=python&type=guide&tag=django&type=guide&type=article&'
            'category=invalid&category=django&author=example-author&year=2026&unknown=value'
        )

        state = parse_filter_state(query, self.options)

        self.assertEqual(
            state,
            FilterState(
                article_type='guide',
                category_slug='django',
                author_slug='example-author',
                year=2026,
                tag_slugs=('django', 'python'),
            ),
        )
        self.assertEqual(
            serialize_filter_state(state),
            'type=guide&category=django&author=example-author&year=2026&tag=django&tag=python',
        )

    def test_search_query_is_normalized_limited_and_serialized_once(self):
        query = QueryDict('', mutable=True)
        query.setlist('q', ['   ', '  Django   search  ', 'ignored duplicate'])
        query['category'] = 'django'

        state = parse_filter_state(query, self.options)

        self.assertEqual(state.search_query, 'Django search')
        self.assertEqual(
            serialize_filter_state(state),
            'q=Django+search&category=django',
        )
        self.assertEqual(
            normalize_search_query(' word ' * 100),
            ('word ' * SEARCH_MAX_TERMS).rstrip(),
        )
        self.assertEqual(normalize_search_query('x' * 300), 'x' * 200)

    def test_relative_date_wins_over_year_and_invalid_values_are_ignored(self):
        query = QueryDict('date=past_30_days&date=unsupported&year=2025&tag=stale')

        state = parse_filter_state(query, self.options)

        self.assertEqual(state.date_preset, 'past_30_days')
        self.assertIsNone(state.year)
        self.assertEqual(state.tag_slugs, ())

    def test_date_parameter_accepts_a_publication_year(self):
        state = parse_filter_state(QueryDict('date=2025'), self.options)

        self.assertIsNone(state.date_preset)
        self.assertEqual(state.year, 2025)

    def test_relative_date_bounds_use_the_defined_rolling_window(self):
        now = timezone.make_aware(timezone.datetime(2026, 8, 2, 12, 0))

        start, end = relative_date_bounds(now, 'past_3_months')

        self.assertEqual(start, now - timedelta(days=90))
        self.assertEqual(end, now)


class BlogFilterSelectorTests(TestCase):
    def create_post(
        self,
        *,
        slug,
        category,
        status=BlogPost.Status.PUBLISHED,
        site_slug=PERSONAL_SITE,
        published_at=None,
        author=None,
        post_type=BlogPost.Type.ARTICLE,
        tags=(),
    ):
        post = BlogPost.objects.create(
            status=status,
            type=post_type,
            title=slug,
            slug=slug,
            summary='Summary',
            published_at=published_at or timezone.now(),
            canonical_site_slug=site_slug,
            author=author,
            category=category,
        )
        BlogPostPublication.objects.create(post=post, site_slug=site_slug)
        BlogRichTextBlock.objects.create(parent=post, region='main', body='<p>Body</p>')
        post.tags.add(*tags)
        return post

    def test_article_type_is_exact_and_tags_use_and_semantics(self):
        category = BlogCategory.objects.create(name='Django', slug='django')
        first_tag = BlogTag.objects.create(name='Django', slug='django')
        second_tag = BlogTag.objects.create(name='Python', slug='python')
        article = self.create_post(
            slug='article',
            category=category,
            post_type=BlogPost.Type.ARTICLE,
            tags=(first_tag, second_tag),
        )
        guide = self.create_post(
            slug='guide',
            category=category,
            post_type=BlogPost.Type.GUIDE,
            tags=(first_tag,),
        )
        comparison = self.create_post(
            slug='comparison',
            category=category,
            post_type=BlogPost.Type.COMPARISON,
            tags=(second_tag,),
        )

        article_type_state = FilterState(article_type='guide')
        tag_state = FilterState(tag_slugs=('django', 'python'))

        self.assertEqual(
            list(get_public_posts(site_slug=PERSONAL_SITE, filters=article_type_state)),
            [guide],
        )
        self.assertEqual(
            list(get_public_posts(site_slug=PERSONAL_SITE, filters=tag_state)),
            [article],
        )
        self.assertNotIn(article, get_public_posts(site_slug=PERSONAL_SITE, filters=article_type_state))
        self.assertNotIn(comparison, get_public_posts(site_slug=PERSONAL_SITE, filters=article_type_state))

    def test_category_author_date_and_tag_dimensions_combine(self):
        now = timezone.make_aware(timezone.datetime(2026, 8, 2, 12, 0))
        category = BlogCategory.objects.create(name='Django', slug='django')
        other_category = BlogCategory.objects.create(name='Other', slug='other')
        tag = BlogTag.objects.create(name='Python', slug='python')
        author = AuthorProfile.objects.create(
            user=get_user_model().objects.create_user(username='filter-author'),
            public_author_name='Filter Author',
            slug='filter-author',
        )
        other_author = AuthorProfile.objects.create(
            user=get_user_model().objects.create_user(username='other-author'),
            public_author_name='Other Author',
            slug='other-author',
        )
        matching = self.create_post(
            slug='matching-combination',
            category=category,
            author=author,
            post_type=BlogPost.Type.GUIDE,
            tags=(tag,),
            published_at=now - timedelta(days=10),
        )
        self.create_post(
            slug='wrong-author',
            category=category,
            author=other_author,
            post_type=BlogPost.Type.GUIDE,
            tags=(tag,),
            published_at=now - timedelta(days=10),
        )
        self.create_post(
            slug='wrong-category',
            category=other_category,
            author=author,
            post_type=BlogPost.Type.GUIDE,
            tags=(tag,),
            published_at=now - timedelta(days=10),
        )
        self.create_post(
            slug='outside-date-window',
            category=category,
            author=author,
            post_type=BlogPost.Type.GUIDE,
            tags=(tag,),
            published_at=now - timedelta(days=31),
        )

        state = FilterState(
            search_query='Body',
            article_type=BlogPost.Type.GUIDE,
            category_slug=category.slug,
            author_slug=author.slug,
            date_preset='past_30_days',
            tag_slugs=(tag.slug,),
        )

        self.assertEqual(
            list(get_public_posts(site_slug=PERSONAL_SITE, now=now, filters=state)),
            [matching],
        )

    def test_year_filter_and_relative_date_boundary(self):
        now = timezone.make_aware(timezone.datetime(2026, 8, 2, 12, 0))
        category = BlogCategory.objects.create(name='Django', slug='django')
        boundary = self.create_post(
            slug='boundary',
            category=category,
            published_at=now - timedelta(days=30),
        )
        current_year = self.create_post(
            slug='current-year',
            category=category,
            published_at=now - timedelta(days=60),
        )
        self.create_post(
            slug='future-current-year',
            category=category,
            published_at=now + timedelta(days=1),
        )
        previous_year = self.create_post(
            slug='previous-year',
            category=category,
            published_at=timezone.make_aware(timezone.datetime(2025, 6, 1, 12, 0)),
        )

        self.assertEqual(
            list(
                get_public_posts(
                    site_slug=PERSONAL_SITE,
                    now=now,
                    filters=FilterState(date_preset='past_30_days'),
                )
            ),
            [boundary],
        )
        self.assertEqual(
            list(
                get_public_posts(
                    site_slug=PERSONAL_SITE,
                    now=now,
                    filters=FilterState(year=2025),
                )
            ),
            [previous_year],
        )
        self.assertEqual(
            list(
                get_public_posts(
                    site_slug=PERSONAL_SITE,
                    now=now,
                    filters=FilterState(year=2026),
                )
            ),
            [boundary, current_year],
        )

    def test_filtered_results_keep_publication_and_site_scope(self):
        now = timezone.make_aware(timezone.datetime(2026, 8, 2, 12, 0))
        category = BlogCategory.objects.create(name='Django', slug='django')
        visible = self.create_post(
            slug='visible',
            category=category,
            published_at=now - timedelta(days=2),
        )
        due = self.create_post(
            slug='due',
            category=category,
            status=BlogPost.Status.SCHEDULED,
            published_at=now - timedelta(days=1),
        )
        self.create_post(
            slug='future',
            category=category,
            status=BlogPost.Status.SCHEDULED,
            published_at=now + timedelta(days=1),
        )
        self.create_post(
            slug='draft',
            category=category,
            status=BlogPost.Status.DRAFT,
            published_at=now,
        )
        self.create_post(
            slug='wrong-site',
            category=category,
            site_slug=EASY_MEALS_SITE,
            published_at=now,
        )

        self.assertEqual(
            list(
                get_public_posts(
                    site_slug=PERSONAL_SITE,
                    now=now,
                    filters=FilterState(category_slug=category.slug),
                )
            ),
            [due, visible],
        )

    def test_filter_options_only_contain_visible_active_site_values(self):
        visible_category = BlogCategory.objects.create(name='Visible', slug='visible')
        wrong_site_category = BlogCategory.objects.create(name='Wrong site', slug='wrong-site')
        empty_category = BlogCategory.objects.create(name='Empty', slug='empty')
        visible_tag = BlogTag.objects.create(name='Visible', slug='visible')
        wrong_site_tag = BlogTag.objects.create(name='Wrong site', slug='wrong-site')
        empty_tag = BlogTag.objects.create(name='Empty', slug='empty')
        visible_author = AuthorProfile.objects.create(
            user=get_user_model().objects.create_user(username='visible-author'),
            public_author_name='Visible Author',
            slug='visible-author',
        )
        wrong_site_author = AuthorProfile.objects.create(
            user=get_user_model().objects.create_user(username='wrong-site-author'),
            public_author_name='Wrong Site Author',
            slug='wrong-site-author',
        )
        private_author = AuthorProfile.objects.create(
            user=get_user_model().objects.create_user(username='private-author'),
            public_author_name='Private Author',
            slug='private-author',
        )
        visible = self.create_post(
            slug='visible',
            category=visible_category,
            author=visible_author,
            tags=(visible_tag,),
            published_at=timezone.now() - timedelta(days=2),
        )
        self.create_post(
            slug='wrong-site',
            category=wrong_site_category,
            author=wrong_site_author,
            tags=(wrong_site_tag,),
            site_slug=EASY_MEALS_SITE,
        )
        self.create_post(
            slug='future',
            category=BlogCategory.objects.create(name='Future', slug='future'),
            status=BlogPost.Status.SCHEDULED,
            published_at=timezone.now() + timedelta(days=2),
        )
        self.create_post(
            slug='private',
            category=BlogCategory.objects.create(name='Private', slug='private'),
            status=BlogPost.Status.DRAFT,
            author=private_author,
        )

        options = get_public_filter_options(site_slug=PERSONAL_SITE)

        self.assertEqual([option.value for option in options.categories], ['visible'])
        self.assertEqual([option.value for option in options.authors], ['visible-author'])
        self.assertEqual([option.value for option in options.tags], ['visible'])
        self.assertEqual([option.value for option in options.years], [str(visible.published_at.year)])
        self.assertNotIn(empty_category.slug, options.category_values)
        self.assertNotIn(empty_tag.slug, options.tag_values)


@override_settings(SITE_DEFINITIONS=BLOG_ENABLED_SITE_DEFINITIONS)
class BlogFilterViewTests(TestCase):
    def create_post(
        self,
        *,
        slug,
        category,
        site_slug=PERSONAL_SITE,
        post_type=BlogPost.Type.ARTICLE,
        tags=(),
        published_at=None,
        author=None,
        status=BlogPost.Status.PUBLISHED,
    ):
        post = BlogPost.objects.create(
            status=status,
            type=post_type,
            title=slug,
            slug=slug,
            summary='Summary',
            published_at=published_at or timezone.now(),
            canonical_site_slug=site_slug,
            category=category,
            author=author,
        )
        BlogPostPublication.objects.create(post=post, site_slug=site_slug)
        BlogRichTextBlock.objects.create(parent=post, region='main', body='<p>Body</p>')
        post.tags.add(*tags)
        return post

    def test_homepage_filter_renders_active_state_and_filtered_seo(self):
        category = BlogCategory.objects.create(name='Django', slug='django')
        tag = BlogTag.objects.create(name='Python', slug='python')
        matching = self.create_post(slug='matching', category=category, tags=(tag,))
        self.create_post(slug='other', category=BlogCategory.objects.create(name='Other', slug='other'))

        response = self.client.get('/en/blog/?category=django&tag=python')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, matching.title)
        self.assertNotContains(response, '<h2><a href="/en/blog/other/">', html=False)
        self.assertContains(response, 'blog-filters__icon', html=False)
        self.assertContains(response, 'blog-filters__chevron', html=False)
        self.assertContains(response, 'blog-filters__toggle--active', html=False)
        self.assertContains(response, 'blog-filters__active-count', html=False)
        self.assertNotContains(response, 'blog-filters__count-separator', html=False)
        self.assertContains(response, '>Filters<', html=False)
        self.assertNotContains(response, 'Article filters')
        self.assertNotContains(response, 'Additional filters')
        self.assertNotContains(response, 'Open filters')
        self.assertNotContains(response, 'Close filters')
        self.assertContains(response, 'Category: Django')
        self.assertContains(response, 'Tag: Python')
        self.assertContains(response, 'Past 12 months')
        self.assertContains(response, 'value="2026"', html=False)
        self.assertNotContains(response, 'blog-filter-year', html=False)
        self.assertContains(response, 'data-blog-filter-group="tag"', html=False)
        self.assertContains(response, 'Match all selected tags')
        self.assertContains(response, '<a class="blog-filters__tag-clear" href="/en/blog/?category=django">Clear</a>', html=True)
        self.assertContains(response, 'data-blog-filter-active="true"', html=False)
        self.assertContains(response, 'data-blog-article-link', html=False)
        self.assertContains(response, 'data-blog-dropdown-toggle', count=4, html=False)
        self.assertContains(response, 'data-blog-dropdown-panel', count=4, html=False)
        self.assertContains(response, 'blog-filters__dropdown--single', count=3, html=False)
        self.assertContains(response, 'blog-filters__options--scroll', count=1, html=False)
        self.assertNotContains(response, '<select', html=False)
        self.assertContains(response, 'type="radio" name="category"', html=False)
        self.assertContains(response, 'type="radio" name="author"', html=False)
        self.assertContains(response, 'type="radio" name="date"', html=False)
        self.assertContains(response, 'name="tag"', html=False)
        self.assertContains(response, 'href="/en/blog/?tag=python"', html=False)
        self.assertContains(response, 'href="/en/blog/?category=django"', html=False)
        self.assertContains(response, '<link rel="canonical" href="http://personal.example.com/en/blog/">', html=True)
        self.assertContains(response, '<meta name="robots" content="noindex,follow">', html=True)
        self.assertEqual(response.headers['X-Robots-Tag'], 'noindex, follow')
        self.assertEqual(response.headers['Content-Language'], 'en')

    def test_article_type_row_is_single_value_and_preserves_additional_filters(self):
        category = BlogCategory.objects.create(name='Django', slug='django')
        guide = self.create_post(
            slug='guide',
            category=category,
            post_type=BlogPost.Type.GUIDE,
        )
        self.create_post(
            slug='article',
            category=category,
            post_type=BlogPost.Type.ARTICLE,
        )
        self.create_post(
            slug='showcase',
            category=category,
            post_type=BlogPost.Type.SHOWCASE,
        )

        response = self.client.get('/en/blog/?type=guide&category=django')

        self.assertContains(response, guide.title)
        self.assertNotContains(response, '<h2><a href="/en/blog/article/">', html=False)
        self.assertContains(
            response,
            'href="/en/blog/?type=guide&amp;category=django" aria-current="true"',
            html=False,
        )
        self.assertContains(
            response,
            'href="/en/blog/?type=article&amp;category=django"',
            html=False,
        )
        self.assertContains(response, '>All</a>', html=False)
        self.assertContains(response, '>Articles</a>', html=False)
        self.assertContains(response, 'data-blog-type-nav', html=False)
        self.assertContains(response, 'data-blog-type-scroll', html=False)
        self.assertContains(response, 'data-blog-type-previous', html=False)
        self.assertContains(response, 'data-blog-type-next', html=False)
        self.assertContains(response, 'aria-label="Show previous article types"', html=False)
        self.assertContains(response, 'aria-label="Show more article types"', html=False)
        self.assertContains(response, 'blog-filters__types-chevron', count=2)
        self.assertContains(response, 'blog-filters__types-separator', html=False)
        self.assertNotContains(
            response,
            'blog-filters__types-separator" aria-hidden="true">|',
            html=False,
        )
        self.assertContains(response, '>Guides</a>', html=False)
        self.assertNotContains(response, '>Comparisons</a>', html=False)
        self.assertNotContains(response, '>Top lists</a>', html=False)
        self.assertNotContains(response, '>Showcases</a>', html=False)
        self.assertNotContains(response, '<h3 id="blog-filter-type-title">', html=False)
        self.assertNotContains(response, 'Article type: Guide', html=False)
        self.assertContains(response, 'blog-filters__clear', html=False)
        content = response.content.decode()
        self.assertLess(content.index('>All</a>'), content.index('data-blog-type-scroll'))
        self.assertGreater(content.index('>Articles</a>'), content.index('data-blog-type-scroll'))
        self.assertGreater(content.index('class="blog-rss__link"'), content.index('data-blog-type-next'))
        self.assertLess(content.index('>Filters<'), content.index('blog-filters__types'))
        self.assertContains(response, '<input type="hidden" name="type" value="guide">', html=True)
        self.assertNotContains(response, 'type="checkbox" name="type"', html=False)

        type_only_response = self.client.get('/en/blog/?type=guide')
        self.assertNotContains(type_only_response, 'Article type: Guide', html=False)
        self.assertNotContains(type_only_response, 'blog-filters__clear', html=False)

    def test_invalid_filter_values_redirect_to_normalized_homepage(self):
        tag = BlogTag.objects.create(name='Python', slug='python')
        self.create_post(slug='tagged', category=BlogCategory.objects.create(name='Django', slug='django'), tags=(tag,))

        response = self.client.get(
            '/en/blog/?type=stale&type=guide&type=article&tag=stale&tag=python&'
            'tag=python&category=wrong&unsupported=value'
        )

        self.assertRedirects(response, '/en/blog/?type=guide&tag=python')

    def test_filtered_pagination_and_category_archive_keep_their_distinct_urls(self):
        category = BlogCategory.objects.create(name='Django', slug='django')
        for index in range(13):
            self.create_post(slug=f'post-{index}', category=category)

        response = self.client.get('/en/blog/?category=django&page=2')
        archive_response = self.client.get('/en/blog/category/django/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="blog-pagination__input"', html=False)
        self.assertContains(response, 'value="2"', html=False)
        self.assertContains(response, 'max="2"', html=False)
        self.assertContains(
            response,
            '<input type="hidden" name="category" value="django">',
            html=True,
        )
        self.assertContains(response, 'href="/en/blog/?category=django&amp;page=1"', html=False)
        self.assertContains(response, 'href="http://personal.example.com/en/blog/"', html=False)
        self.assertContains(response, 'noindex,follow')
        self.assertContains(
            response,
            'href="/en/blog/?type=guide&amp;category=django"',
            html=False,
        )
        self.assertContains(archive_response, '>Filters<', html=False)
        self.assertContains(archive_response, 'Category: Django')
        self.assertContains(
            archive_response,
            '<form action="/en/blog/" method="get" data-blog-filter-form>',
            html=False,
        )
        self.assertContains(
            archive_response,
            '<input type="radio" name="category" value="django" checked data-blog-filter-single>',
            html=True,
        )
        self.assertContains(
            archive_response,
            'href="/en/blog/?type=guide&amp;category=django"',
            html=False,
        )
        self.assertContains(
            archive_response,
            'href="/en/blog/category/django/?page=2"',
            html=False,
        )
        self.assertContains(
            archive_response,
            'href="http://personal.example.com/en/blog/category/django/"',
            html=False,
        )
        self.assertNotContains(archive_response, 'noindex,follow')

        out_of_range_response = self.client.get('/en/blog/?category=django&page=3')
        self.assertEqual(out_of_range_response.status_code, 404)

    def test_tag_archive_initializes_tag_filter_and_additional_filters_use_homepage(self):
        category = BlogCategory.objects.create(name='Django', slug='django')
        tag = BlogTag.objects.create(name='Admin UX', slug='admin-ux')
        for index in range(2):
            self.create_post(slug=f'tagged-{index}', category=category, tags=(tag,))

        response = self.client.get('/en/blog/tag/admin-ux/')

        self.assertContains(response, 'Tag: Admin UX')
        self.assertContains(
            response,
            '<input type="checkbox" name="tag" value="admin-ux" checked>',
            html=True,
        )
        self.assertContains(
            response,
            'href="/en/blog/?type=guide&amp;tag=admin-ux"',
            html=False,
        )
        self.assertContains(response, 'href="/en/blog/"', html=False)
        self.assertNotContains(response, 'noindex,follow')

    def test_author_archive_initializes_author_filter(self):
        category = BlogCategory.objects.create(name='Django', slug='django')
        user = get_user_model().objects.create_user(username='oli')
        author = AuthorProfile.objects.create(
            user=user,
            public_author_name='Oli',
            slug='oli',
        )
        for index in range(2):
            self.create_post(slug=f'authored-{index}', category=category, author=author)

        response = self.client.get('/en/blog/author/oli/')

        self.assertContains(response, 'Author: Oli')
        self.assertContains(
            response,
            '<input type="radio" name="author" value="oli" checked data-blog-filter-single>',
            html=True,
        )
        self.assertContains(
            response,
            'href="/en/blog/?type=guide&amp;author=oli"',
            html=False,
        )
        self.assertNotContains(response, 'noindex,follow')

    def test_relative_date_wins_over_year_in_normalized_view_url(self):
        category = BlogCategory.objects.create(name='Django', slug='django')
        recent = self.create_post(
            slug='recent',
            category=category,
            published_at=timezone.now() - timedelta(days=2),
        )
        self.create_post(
            slug='old',
            category=category,
            published_at=timezone.now() - timedelta(days=400),
        )

        response = self.client.get('/en/blog/?date=past_30_days&year=2025')

        self.assertRedirects(response, '/en/blog/?date=past_30_days')
        filtered_response = self.client.get('/en/blog/?date=past_30_days')
        self.assertContains(filtered_response, recent.title)
        self.assertNotContains(filtered_response, '<h2><a href="/en/blog/old/">', html=False)

    def test_filtered_empty_state_has_recovery_copy(self):
        category = BlogCategory.objects.create(name='Django', slug='django')
        other_category = BlogCategory.objects.create(name='Other', slug='other')
        tag = BlogTag.objects.create(name='Python', slug='python')
        self.create_post(slug='article', category=category)
        self.create_post(slug='other-article', category=other_category, tags=(tag,))

        empty_response = self.client.get('/en/blog/?category=django&tag=python')
        self.assertContains(empty_response, 'No articles match these filters')
        self.assertContains(empty_response, 'Try removing a filter or clear all filters to see more articles.')
        self.assertContains(empty_response, 'Clear all filters')

    def test_all_blog_shells_include_the_shared_filter_and_list_asset(self):
        cases = (
            (PERSONAL_SITE, 'testserver'),
            (EASY_MEALS_SITE, 'recipes.example.com'),
            (VANTA_SITE, 'admin-theme.example.com'),
        )
        for index, (site_slug, host) in enumerate(cases):
            with self.subTest(site_slug=site_slug):
                self.create_post(
                    slug=f'shell-{index}',
                    category=BlogCategory.objects.create(name=f'Category {index}', slug=f'category-{index}'),
                    site_slug=site_slug,
                )
                response = self.client.get('/en/blog/', HTTP_HOST=host)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, '>Filters<', html=False)
                self.assertContains(response, '/static/blog/js/list')
                self.assertContains(response, '?v=tag-filter-restore')
