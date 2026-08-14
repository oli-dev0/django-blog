from django.http import Http404
from django.test import TestCase
from django.utils import timezone

from apps.blog.models import BlogCategory, BlogPost, BlogPostPublication, BlogRichTextBlock
from apps.blog.selectors import (
    get_compatible_related_posts,
    get_public_post_by_slug,
    get_public_posts,
    get_related_public_posts,
)
from apps.core.sites import EASY_MEALS_SITE, PERSONAL_SITE, VANTA_SITE


class BlogSelectorTests(TestCase):
    def create_post(self, *, slug, status=BlogPost.Status.PUBLISHED, published_at=None, site_slug=PERSONAL_SITE):
        category, _created = BlogCategory.objects.get_or_create(
            name='General',
            defaults={'slug': 'general'},
        )
        post = BlogPost.objects.create(
            status=status,
            title=slug.replace('-', ' ').title(),
            slug=slug,
            summary='Summary',
            published_at=published_at or timezone.now(),
            canonical_site_slug=site_slug,
            category=category,
        )
        BlogPostPublication.objects.create(post=post, site_slug=site_slug)
        BlogRichTextBlock.objects.create(parent=post, region='main', body='<p>Body</p>')
        return post

    def test_public_posts_are_site_scoped_and_newest_first(self):
        older = self.create_post(slug='older', published_at=timezone.now() - timezone.timedelta(days=2))
        newer = self.create_post(slug='newer')
        self.create_post(slug='easy-meals', site_slug=EASY_MEALS_SITE)

        self.assertEqual(list(get_public_posts(site_slug=PERSONAL_SITE)), [newer, older])
        self.assertEqual(list(get_public_posts(site_slug=EASY_MEALS_SITE)), [BlogPost.objects.get(slug='easy-meals')])

    def test_future_schedule_and_private_states_are_not_public(self):
        now = timezone.now()
        due = self.create_post(slug='due', status=BlogPost.Status.SCHEDULED, published_at=now - timezone.timedelta(minutes=1))
        self.create_post(slug='future', status=BlogPost.Status.SCHEDULED, published_at=now + timezone.timedelta(days=1))
        self.create_post(slug='draft', status=BlogPost.Status.DRAFT)
        self.create_post(slug='unpublished', status=BlogPost.Status.UNPUBLISHED)

        self.assertEqual(list(get_public_posts(site_slug=PERSONAL_SITE, now=now)), [due])

    def test_detail_selector_returns_same_public_contract(self):
        post = self.create_post(slug='public-article')

        self.assertEqual(get_public_post_by_slug(slug='public-article', site_slug=PERSONAL_SITE), post)
        with self.assertRaises(Http404):
            get_public_post_by_slug(slug='missing', site_slug=PERSONAL_SITE)

    def test_related_selector_omits_private_targets(self):
        post = self.create_post(slug='main')
        public_related = self.create_post(slug='public-related')
        private_related = self.create_post(slug='private-related', status=BlogPost.Status.UNPUBLISHED)
        post.related_posts.add(public_related, through_defaults={'position': 1})
        post.related_posts.add(private_related, through_defaults={'position': 2})

        self.assertEqual(list(get_related_public_posts(post=post, site_slug=PERSONAL_SITE)), [public_related])

    def test_compatible_related_selector_requires_every_source_site(self):
        source = self.create_post(slug='multi-source')
        BlogPostPublication.objects.create(post=source, site_slug=VANTA_SITE)
        personal_only = self.create_post(slug='personal-only')
        both_sites = self.create_post(slug='both-sites')
        BlogPostPublication.objects.create(post=both_sites, site_slug=VANTA_SITE)

        self.assertEqual(
            list(get_compatible_related_posts(source_post=source)),
            [both_sites],
        )
        self.assertNotIn(personal_only, get_compatible_related_posts(source_post=source))

    def test_public_related_selector_fails_closed_for_stale_cross_site_links(self):
        source = self.create_post(slug='multi-source-public')
        BlogPostPublication.objects.create(post=source, site_slug=VANTA_SITE)
        stale_target = self.create_post(slug='stale-target')
        valid_target = self.create_post(slug='valid-target')
        BlogPostPublication.objects.create(post=valid_target, site_slug=VANTA_SITE)
        post = source
        post.related_posts.add(stale_target, through_defaults={'position': 1})
        post.related_posts.add(valid_target, through_defaults={'position': 2})

        self.assertEqual(
            list(get_related_public_posts(post=post, site_slug=PERSONAL_SITE)),
            [valid_target],
        )
        self.assertEqual(
            list(get_related_public_posts(post=post, site_slug=VANTA_SITE)),
            [valid_target],
        )
