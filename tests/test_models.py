from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.blog.models import (
    BLOG_BLOCK_MODELS,
    BlogCalloutBlock,
    BlogChecklistBlock,
    BlogCategory,
    BlogCategorySite,
    BlogCodeBlock,
    BlogEmbedSharingBlock,
    BlogFAQBlock,
    BlogHeadingBlock,
    BlogImage,
    BlogImageBlock,
    BlogImageComparison,
    BlogImageComparisonBlock,
    BlogPost,
    BlogPostPublication,
    BlogPostRelated,
    BlogRichTextBlock,
    BlogSourceLinkBlock,
    BlogSite,
    BlogTag,
    BlogTagSite,
    AuthorProfile,
)
from apps.core.sites import EASY_MEALS_SITE, PERSONAL_SITE, VANTA_SITE


class BlogAssignmentModelTests(TestCase):
    def test_taxonomy_site_assignments_are_unique_and_protect_used_sites(self):
        site, _created = BlogSite.objects.get_or_create(slug=VANTA_SITE)
        category = BlogCategory.objects.create(name='Tools', slug='tools')
        assignment = BlogCategorySite.objects.create(taxonomy=category, site=site)
        post = BlogPost.objects.create(title='Tool', slug='tool', category=category)
        BlogPostPublication.objects.create(post=post, site_slug=VANTA_SITE)

        with self.assertRaises(ValidationError):
            with transaction.atomic():
                assignment.delete()
        with self.assertRaises(ValidationError):
            with transaction.atomic():
                category.websites.remove(site)
        with self.assertRaises(ValidationError):
            with transaction.atomic():
                BlogCategorySite.objects.filter(pk=assignment.pk).delete()
        with self.assertRaises(ValidationError):
            BlogCategorySite(taxonomy=category, site=site).validate_constraints()

    def test_tag_site_assignments_are_unique(self):
        site, _created = BlogSite.objects.get_or_create(slug=VANTA_SITE)
        tag = BlogTag.objects.create(name='JavaScript', slug='javascript')
        BlogTagSite.objects.create(taxonomy=tag, site=site)

        with self.assertRaises(ValidationError):
            BlogTagSite(taxonomy=tag, site=site).validate_constraints()


class BlogModelTests(TestCase):
    def create_post(self, *, status=BlogPost.Status.DRAFT, slug='article'):
        category, _created = BlogCategory.objects.get_or_create(
            name='General',
            defaults={'slug': 'general'},
        )
        return BlogPost.objects.create(
            status=status,
            title='An article',
            slug=slug,
            summary='A useful summary.',
            published_at=timezone.now(),
            canonical_site_slug=PERSONAL_SITE,
            category=category,
        )

    def test_article_requires_category(self):
        field = BlogPost._meta.get_field('category')

        self.assertFalse(field.blank)
        self.assertFalse(field.null)

    def add_publication_and_body(self, post, site_slug=PERSONAL_SITE):
        BlogPostPublication.objects.create(post=post, site_slug=site_slug)
        BlogRichTextBlock.objects.create(parent=post, region='main', body='<p>Body</p>')

    def test_author_profile_extends_a_user_once(self):
        user = get_user_model().objects.create_user(username='author')
        profile = AuthorProfile.objects.create(user=user, public_author_name='Author')

        self.assertIs(user.author_profile, profile)
        self.assertEqual(str(profile), 'Author')
        self.assertEqual(AuthorProfile._meta.verbose_name, 'author')
        self.assertEqual(AuthorProfile._meta.verbose_name_plural, 'authors')

    def test_author_slug_is_generated_uniquely_and_remains_stable(self):
        first = AuthorProfile.objects.create(
            user=get_user_model().objects.create_user(username='first-author'),
            public_author_name='Jane Doe!',
        )
        second = AuthorProfile.objects.create(
            user=get_user_model().objects.create_user(username='second-author'),
            public_author_name='Jâne Döe',
        )

        self.assertEqual(first.slug, 'jane-doe')
        self.assertEqual(second.slug, 'jane-doe-2')

        first.public_author_name = 'Renamed Author'
        first.save()
        first.refresh_from_db()

        self.assertEqual(first.slug, 'jane-doe')

    def test_author_slug_rejects_invalid_case_and_is_unique(self):
        first = AuthorProfile.objects.create(
            user=get_user_model().objects.create_user(username='slug-owner'),
            public_author_name='Slug Owner',
            slug='slug-owner',
        )
        invalid = AuthorProfile(
            user=get_user_model().objects.create_user(username='invalid-slug'),
            public_author_name='Invalid Slug',
            slug='Invalid_Slug',
        )

        with self.assertRaises(ValidationError):
            invalid.full_clean()

        duplicate = AuthorProfile(
            user=get_user_model().objects.create_user(username='duplicate-slug'),
            public_author_name='Duplicate Slug',
            slug=first.slug,
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_source_link_defaults_to_source_label(self):
        self.assertEqual(BlogSourceLinkBlock().label, 'Source:')

    def test_publication_requires_configured_site_slug(self):
        publication = BlogPostPublication(post=self.create_post(), site_slug='typo_site')

        with self.assertRaises(ValidationError):
            publication.full_clean()

    def test_published_post_requires_publication_and_body(self):
        post = self.create_post(status=BlogPost.Status.PUBLISHED)

        with self.assertRaises(ValidationError) as error:
            post.full_clean()

        self.assertIn('Published articles must be assigned to at least one site.', error.exception.messages)
        self.assertIn('Published articles must contain body content.', error.exception.messages)

    def test_published_post_canonical_site_must_match_publication(self):
        post = self.create_post(status=BlogPost.Status.PUBLISHED)
        self.add_publication_and_body(post, EASY_MEALS_SITE)

        with self.assertRaises(ValidationError) as error:
            post.full_clean()

        self.assertIn('canonical_site_slug', error.exception.message_dict)

    def test_related_post_cannot_reference_itself(self):
        post = self.create_post()
        related = BlogPostRelated(post=post, related_post=post)

        with self.assertRaises(ValidationError):
            related.full_clean()

    def test_related_post_must_be_available_on_every_source_site(self):
        source = self.create_post(slug='multi-site-source')
        self.add_publication_and_body(source, PERSONAL_SITE)
        BlogPostPublication.objects.create(post=source, site_slug=VANTA_SITE)
        target = self.create_post(slug='single-site-target')
        self.add_publication_and_body(target, PERSONAL_SITE)
        related = BlogPostRelated(post=source, related_post=target)

        with self.assertRaises(ValidationError) as error:
            related.full_clean()

        self.assertEqual(
            error.exception.message_dict['related_post'],
            ['Choose an article available on the same website.'],
        )

    def test_related_post_requires_a_source_publication_site(self):
        source = self.create_post(slug='unassigned-source')
        target = self.create_post(slug='assigned-target')
        self.add_publication_and_body(target)
        related = BlogPostRelated(post=source, related_post=target)

        with self.assertRaises(ValidationError) as error:
            related.full_clean()

        self.assertEqual(
            error.exception.message_dict['related_post'],
            ['Choose an article available on the same website.'],
        )

    def test_string_values_are_readable(self):
        post = self.create_post()
        publication = BlogPostPublication.objects.create(post=post, site_slug=PERSONAL_SITE)

        self.assertEqual(str(post), 'An article')
        self.assertEqual(str(publication), f'{post.pk} on {PERSONAL_SITE}')

    def test_translated_block_labels_are_concrete_strings(self):
        post = self.create_post()
        blocks = (
            BlogRichTextBlock(parent=post),
            BlogChecklistBlock(parent=post),
            BlogCodeBlock(parent=post),
            BlogCalloutBlock(parent=post),
            BlogImageBlock(parent=post),
            BlogImageComparisonBlock(parent=post),
        )

        for block in blocks:
            with self.subTest(block=block.__class__.__name__):
                self.assertIsInstance(str(block), str)

    def test_embed_block_normalizes_and_persists_supported_values(self):
        post = self.create_post()
        block = BlogEmbedSharingBlock(
            parent=post,
            region='main',
            ordering=3,
            platform=BlogEmbedSharingBlock.Platform.YOUTUBE,
            url=' https://youtu.be/dQw4w9WgXcQ?si=tracking-value ',
            caption='  A useful caption  ',
        )

        block.full_clean()
        block.save()
        block.refresh_from_db()

        self.assertEqual(block.platform, 'youtube')
        self.assertEqual(block.url, 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        self.assertEqual(block.caption, 'A useful caption')
        self.assertEqual(block.region, 'main')
        self.assertEqual(block.ordering, 3)
        self.assertEqual(str(block), 'Embed sharing')
        self.assertIn(BlogEmbedSharingBlock, BLOG_BLOCK_MODELS)

    def test_embed_block_rejects_invalid_fields_without_persisting(self):
        post = self.create_post()
        invalid_cases = (
            ({'platform': '', 'url': ''}, {'platform', 'url'}),
            ({'platform': 'unsupported', 'url': 'https://example.com/item'}, {'platform'}),
            ({'platform': 'youtube', 'url': 'not-a-url'}, {'url'}),
            ({'platform': 'youtube', 'url': 'https://x.com/example/status/123456789'}, {'url'}),
            (
                {'platform': 'youtube', 'url': 'https://www.youtube.com/playlist?list=PL1234567890'},
                {'url'},
            ),
            (
                {
                    'platform': 'youtube',
                    'url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                    'caption': 'x' * 301,
                },
                {'caption'},
            ),
            (
                {
                    'platform': 'youtube',
                    'url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                    'caption': '<strong>Unsafe</strong>',
                },
                {'caption'},
            ),
        )

        for values, expected_error_fields in invalid_cases:
            with self.subTest(values=values):
                block = BlogEmbedSharingBlock(parent=post, region='main', **values)
                with self.assertRaises(ValidationError) as error:
                    block.full_clean()

                self.assertEqual(set(error.exception.message_dict), expected_error_fields)
                self.assertEqual(BlogEmbedSharingBlock.objects.count(), 0)

    def test_embed_block_normalizes_whitespace_caption_to_empty(self):
        post = self.create_post()
        block = BlogEmbedSharingBlock(
            parent=post,
            region='main',
            platform='x',
            url='https://x.com/example/status/123456789',
            caption=' \t\n ',
        )

        block.full_clean()

        self.assertEqual(block.caption, '')

    def test_comparison_has_separate_verbose_name_and_requires_two_meaningful_images(self):
        self.assertEqual(BlogImageComparison._meta.verbose_name_plural, 'comparison images')
        comparison = BlogImageComparison(
            name='Pair',
            first_original='blog/comparisons/originals/first.png',
            second_original='blog/comparisons/originals/second.png',
            first_alt_text='   ',
            second_alt_text='Second image',
        )

        with self.assertRaises(ValidationError) as error:
            comparison.full_clean()

        self.assertIn('first_alt_text', error.exception.message_dict)

    def test_comparison_block_protects_reusable_pair(self):
        comparison = BlogImageComparison.objects.create(
            name='Protected pair',
            first_original='blog/comparisons/originals/first.png',
            second_original='blog/comparisons/originals/second.png',
            first_alt_text='First image',
            second_alt_text='Second image',
        )
        post = self.create_post()
        BlogImageComparisonBlock.objects.create(parent=post, comparison=comparison, region='main')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                comparison.delete()

    def test_slug_is_globally_unique(self):
        self.create_post(slug='shared-slug')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_post(slug='shared-slug')

    def test_heading_anchor_is_unique_per_article(self):
        post = self.create_post()
        BlogHeadingBlock.objects.create(parent=post, region='main', text='One', anchor='same')
        duplicate = BlogHeadingBlock(parent=post, region='main', text='Two', anchor='same')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                duplicate.save()

    def test_decorative_images_must_have_empty_alternative_text(self):
        image = BlogImage(name='Test image', original='blog/originals/image.png', is_decorative=True, alt_text='Not empty')

        with self.assertRaises(ValidationError):
            image.full_clean()

    def test_image_string_uses_manual_name(self):
        image = BlogImage(name='Dashboard screenshot', original='blog/originals/image.png')

        self.assertEqual(str(image), 'Dashboard screenshot')

    def test_rich_text_sanitizer_adds_safe_link_relations(self):
        post = self.create_post()
        block = BlogRichTextBlock(
            parent=post,
            region='main',
            body=(
                '<p><a href="https://example.com" target="_blank">External</a> '
                '<a href="javascript:alert(1)">Unsafe</a></p>'
            ),
        )

        block.full_clean()

        self.assertIn('target="_blank" rel="noopener noreferrer"', block.body)
        self.assertNotIn('javascript:', block.body)

    def test_internal_link_metadata_is_limited_to_rich_text(self):
        post = self.create_post()
        html = (
            '<p><a href="/projects/" '
            'data-blog-internal-key="personal-projects">Projects</a></p>'
        )
        rich_text = BlogRichTextBlock(parent=post, region='main', body=html)
        callout = BlogCalloutBlock(parent=post, region='main', body=html)

        rich_text.full_clean()
        callout.full_clean()

        self.assertIn('data-blog-internal-key="personal-projects"', rich_text.body)
        self.assertNotIn('data-blog-internal-key', callout.body)

    def test_faq_normalizes_order_and_uses_the_rich_text_sanitizer(self):
        post = self.create_post()
        answer = (
            '<p><a href="https://example.com" target="_blank">External</a> '
            '<a href="javascript:alert(1)">Unsafe</a> '
            '<a data-blog-internal-key="personal-projects">Projects</a></p>'
        )
        block = BlogFAQBlock(
            parent=post,
            region='main',
            items=[
                {'question': '  First question?  ', 'answer': answer},
                {'question': 'Second question?', 'answer': '<p>Second answer.</p>'},
            ],
        )

        block.full_clean()

        self.assertEqual(
            [item['question'] for item in block.items],
            ['First question?', 'Second question?'],
        )
        self.assertIn('rel="noopener noreferrer"', block.items[0]['answer'])
        self.assertNotIn('javascript:', block.items[0]['answer'])
        self.assertIn('data-blog-internal-key="personal-projects"', block.items[0]['answer'])

    def test_faq_accepts_an_empty_draft_and_rejects_invalid_items(self):
        post = self.create_post()
        empty = BlogFAQBlock(parent=post, region='main', items=[])
        empty.full_clean()

        invalid_items = (
            'not-a-list',
            ['not-a-dictionary'],
            [{'question': 'Missing answer'}],
            [{'question': 'Question?', 'answer': '<p>Answer</p>', 'extra': 'no'}],
            [{'question': 1, 'answer': '<p>Answer</p>'}],
            [{'question': '   ', 'answer': '<p>Answer</p>'}],
            [{'question': '<strong>Markup?</strong>', 'answer': '<p>Answer</p>'}],
            [{'question': 'x' * 301, 'answer': '<p>Answer</p>'}],
            [{'question': 'Question?', 'answer': '<p> </p>'}],
        )
        for items in invalid_items:
            with self.subTest(items=items), self.assertRaises(ValidationError):
                BlogFAQBlock(parent=post, region='main', items=items).full_clean()

        self.assertEqual(str(empty), 'FAQ')
        self.assertIn(BlogFAQBlock, BLOG_BLOCK_MODELS)
