from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.blog import embed_sharing
from apps.blog.rendering import build_preview_warnings
from apps.blog.models import (
    BlogCategory,
    BlogEmbedSharingBlock,
    BlogFAQBlock,
    BlogImage,
    BlogImageComparison,
    BlogImageComparisonBlock,
    BlogPost,
    BlogPostPublication,
    BlogRichTextBlock,
    BlogSite,
)
from apps.blog.services import (
    BlogWorkflowError,
    mark_post_ready,
    mark_post_reviewed,
    publish_post_now,
    schedule_post,
    unpublish_post,
    validate_post_for_publication,
)
from apps.core.sites import PERSONAL_SITE


class BlogServiceTests(TestCase):
    def setUp(self):
        BlogSite.objects.get_or_create(slug=PERSONAL_SITE)
        self.publisher = get_user_model().objects.create_superuser(
            username='publisher',
            email='publisher@example.com',
            password='test-password',
        )
        self.author = get_user_model().objects.create_user(
            username='author',
            password='test-password',
        )

    def create_post(self, *, status=BlogPost.Status.DRAFT, slug='service-article'):
        category, _created = BlogCategory.objects.get_or_create(
            name='General',
            defaults={'slug': 'general'},
        )
        category.websites.add(PERSONAL_SITE)
        post = BlogPost.objects.create(
            status=status,
            title='Service article',
            slug=slug,
            summary='A service summary.',
            published_at=timezone.now(),
            canonical_site_slug=PERSONAL_SITE,
            category=category,
        )
        BlogPostPublication.objects.create(post=post, site_slug=PERSONAL_SITE)
        BlogRichTextBlock.objects.create(parent=post, region='main', body='<p>Service body.</p>')
        return post

    def add_embed(self, post, *, ordering=20, caption=''):
        return BlogEmbedSharingBlock.objects.create(
            parent=post,
            region='main',
            ordering=ordering,
            platform=BlogEmbedSharingBlock.Platform.YOUTUBE,
            url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            caption=caption,
        )

    @staticmethod
    def verified_embeds(blocks):
        return tuple(
            embed_sharing.VerifiedEmbed(
                block.pk,
                embed_sharing.normalize_embed_reference(block.platform, block.url),
            )
            for block in blocks
        )

    def test_author_cannot_publish(self):
        post = self.create_post(status=BlogPost.Status.READY)

        with self.assertRaises(BlogWorkflowError):
            publish_post_now(post, actor=self.author)

        self.assertEqual(BlogPost.objects.get(pk=post.pk).status, BlogPost.Status.READY)

    def test_ready_publish_and_unpublish_transitions_are_explicit(self):
        post = self.create_post()

        mark_post_ready(post, actor=self.publisher)
        post.refresh_from_db()
        self.assertEqual(post.status, BlogPost.Status.READY)

        publish_post_now(post, actor=self.publisher)
        post.refresh_from_db()
        self.assertEqual(post.status, BlogPost.Status.PUBLISHED)
        original_published_at = post.published_at

        unpublish_post(post, actor=self.publisher)
        post.refresh_from_db()
        self.assertEqual(post.status, BlogPost.Status.UNPUBLISHED)
        self.assertEqual(post.published_at, original_published_at)

    def test_publish_now_sets_missing_publication_time(self):
        post = self.create_post()
        post.published_at = None
        post.save(update_fields=['published_at'])

        publish_post_now(post, actor=self.publisher)
        post.refresh_from_db()

        self.assertEqual(post.status, BlogPost.Status.PUBLISHED)
        self.assertIsNotNone(post.published_at)

    def test_schedule_requires_future_time_and_remains_scheduled(self):
        post = self.create_post()
        publish_at = timezone.now() + timezone.timedelta(days=1)

        schedule_post(post, publish_at=publish_at, actor=self.publisher)
        post.refresh_from_db()

        self.assertEqual(post.status, BlogPost.Status.SCHEDULED)
        self.assertEqual(post.published_at, publish_at)

        with self.assertRaises(BlogWorkflowError):
            schedule_post(post, publish_at=timezone.now() - timezone.timedelta(minutes=1), actor=self.publisher)

    def test_mark_reviewed_does_not_change_updated_at(self):
        post = self.create_post(status=BlogPost.Status.PUBLISHED)
        before = post.updated_at

        mark_post_reviewed(post, reviewed_on=timezone.localdate(), actor=self.publisher)
        post.refresh_from_db()

        self.assertEqual(post.last_reviewed_on, timezone.localdate())
        self.assertEqual(post.updated_at, before)
        self.assertIsNone(post.content_updated_at)

    def test_incomplete_draft_cannot_be_marked_ready(self):
        post = self.create_post()
        post.summary = ''
        post.save(update_fields=['summary'])

        with self.assertRaises(BlogWorkflowError) as error:
            mark_post_ready(post, actor=self.publisher)

        self.assertIn('Add a summary before publishing.', error.exception.messages)

    def test_complete_faq_is_meaningful_but_empty_or_invalid_faq_is_not(self):
        post = self.create_post()
        BlogRichTextBlock.objects.filter(parent=post).delete()
        faq = BlogFAQBlock.objects.create(
            parent=post,
            region='main',
            items=[{'question': 'How?', 'answer': '<p>Like this.</p>'}],
        )

        mark_post_ready(post, actor=self.publisher)
        post.refresh_from_db()
        self.assertEqual(post.status, BlogPost.Status.READY)

        post.status = BlogPost.Status.DRAFT
        post.save(update_fields=['status'])
        faq.items = []
        faq.save(update_fields=['items'])
        with self.assertRaises(BlogWorkflowError) as empty_error:
            mark_post_ready(post, actor=self.publisher)
        self.assertIn('Add meaningful article content before publishing.', empty_error.exception.messages)

        faq.items = [{'question': 'Broken?', 'answer': ''}]
        faq.save(update_fields=['items'])
        with self.assertRaises(BlogWorkflowError) as invalid_error:
            mark_post_ready(post, actor=self.publisher)
        self.assertIn('Question 1: Enter an answer.', invalid_error.exception.messages)

    def test_faq_internal_links_must_match_publication_sites(self):
        post = self.create_post()
        BlogRichTextBlock.objects.filter(parent=post).delete()
        BlogFAQBlock.objects.create(
            parent=post,
            region='main',
            items=[{
                'question': 'Where?',
                'answer': '<p><a data-blog-internal-key="vanta-home">Vanta</a></p>',
            }],
        )

        with self.assertRaises(BlogWorkflowError) as error:
            mark_post_ready(post, actor=self.publisher)

        self.assertIn(
            'Choose a destination available on every selected publication website.',
            error.exception.messages,
        )

    def test_ready_does_not_require_publication_time(self):
        post = self.create_post()
        post.published_at = None
        post.save(update_fields=['published_at'])

        mark_post_ready(post, actor=self.publisher)
        post.refresh_from_db()

        self.assertEqual(post.status, BlogPost.Status.READY)
        self.assertIsNone(post.published_at)

    def test_republishing_public_article_preserves_original_publication_time(self):
        original_published_at = timezone.now() - timezone.timedelta(days=30)
        post = self.create_post(status=BlogPost.Status.PUBLISHED)
        post.published_at = original_published_at
        post.save(update_fields=['published_at'])

        publish_post_now(post, actor=self.publisher)
        post.refresh_from_db()

        self.assertEqual(post.published_at, original_published_at)

    def test_missing_stored_image_files_block_publication(self):
        post = self.create_post()
        post.featured_image = BlogImage.objects.create(
            name='Missing image',
            original='blog/originals/missing.png',
            rendition_480='blog/renditions/missing-480.webp',
            rendition_800='blog/renditions/missing-800.webp',
            rendition_1200='blog/renditions/missing-1200.webp',
            width=1200,
            height=675,
            alt_text='Missing image alt text',
            processing_status=BlogImage.ProcessingStatus.READY,
        )
        post.save(update_fields=['featured_image'])

        with self.assertRaises(BlogWorkflowError) as error:
            publish_post_now(post, actor=self.publisher)

        self.assertIn('Featured image is missing one or more stored image files.', error.exception.messages)

    def test_comparison_publication_reports_each_unready_side(self):
        post = self.create_post()
        comparison = BlogImageComparison.objects.create(
            name='Incomplete comparison',
            first_original='blog/comparisons/originals/first.png',
            first_rendition_480='blog/comparisons/renditions/first-480.webp',
            first_rendition_800='blog/comparisons/renditions/first-800.webp',
            first_rendition_1200='blog/comparisons/renditions/first-1200.webp',
            first_width=800,
            first_height=450,
            first_alt_text='First view',
            first_processing_status=BlogImageComparison.ProcessingStatus.READY,
            second_original='blog/comparisons/originals/second.png',
            second_alt_text='Second view',
            second_processing_status=BlogImageComparison.ProcessingStatus.FAILED,
        )
        BlogImageComparisonBlock.objects.create(parent=post, region='main', comparison=comparison)

        with self.assertRaises(BlogWorkflowError) as error:
            publish_post_now(post, actor=self.publisher)

        self.assertIn('First comparison image is missing one or more stored image files.', error.exception.messages)
        self.assertIn('Second comparison image is not ready for publication.', error.exception.messages)

    def test_valid_embed_only_article_is_meaningful_without_provider_io(self):
        post = self.create_post(slug='embed-only-service')
        BlogRichTextBlock.objects.filter(parent=post).delete()
        self.add_embed(post)

        with patch.object(embed_sharing, 'build_opener') as build_opener:
            messages = validate_post_for_publication(post)

        self.assertNotIn('Add meaningful article content before publishing.', messages)
        self.assertNotIn('Embed block 1:', messages)
        build_opener.assert_not_called()

    def test_ready_publish_and_schedule_verify_embeds_before_transition(self):
        post = self.create_post(slug='embed-transitions')
        BlogRichTextBlock.objects.filter(parent=post).delete()
        first = self.add_embed(post, ordering=10)
        second = self.add_embed(post, ordering=20, caption='Relevant context')

        def fake_verification(blocks):
            self.assertEqual([block.pk for block in blocks], [first.pk, second.pk])
            return self.verified_embeds(blocks)

        with patch('apps.blog.services.verify_article_embeds', side_effect=fake_verification) as verify:
            mark_post_ready(post, actor=self.publisher)
            self.assertEqual(verify.call_count, 1)

            post.status = BlogPost.Status.READY
            post.save(update_fields=['status'])
            publish_post_now(post, actor=self.publisher)
            self.assertEqual(verify.call_count, 2)

        scheduled_post = self.create_post(slug='embed-scheduled')
        BlogRichTextBlock.objects.filter(parent=scheduled_post).delete()
        self.add_embed(scheduled_post)
        publish_at = timezone.now() + timezone.timedelta(days=1)
        with patch(
            'apps.blog.services.verify_article_embeds',
            side_effect=lambda blocks: self.verified_embeds(blocks),
        ) as verify:
            schedule_post(scheduled_post, publish_at=publish_at, actor=self.publisher)

        self.assertEqual(verify.call_count, 1)
        self.assertEqual(
            BlogPost.objects.get(pk=scheduled_post.pk).status,
            BlogPost.Status.SCHEDULED,
        )

    def test_embed_verification_failures_report_position_and_preserve_draft(self):
        post = self.create_post(slug='embed-failure')
        BlogRichTextBlock.objects.filter(parent=post).delete()
        block = self.add_embed(post)
        original_url = block.url

        for error in (
            embed_sharing.UnsupportedEmbedItem(block_id=block.pk),
            embed_sharing.EmbedVerificationUnavailable(block_id=block.pk),
        ):
            with self.subTest(error=type(error).__name__):
                post.status = BlogPost.Status.DRAFT
                post.save(update_fields=['status'])
                with patch('apps.blog.services.verify_article_embeds', side_effect=error):
                    with self.assertRaises(BlogWorkflowError) as raised:
                        mark_post_ready(post, actor=self.publisher)

                self.assertIn('Embed block 1:', raised.exception.messages[0])
                self.assertIn(str(error), raised.exception.messages[0])
                post.refresh_from_db()
                block.refresh_from_db()
                self.assertEqual(post.status, BlogPost.Status.DRAFT)
                self.assertEqual(block.url, original_url)

    def test_locked_transition_rejects_embed_fingerprint_changed_after_verification(self):
        post = self.create_post(slug='embed-concurrent-edit')
        BlogRichTextBlock.objects.filter(parent=post).delete()
        block = self.add_embed(post)
        verified_url = block.url

        def verify_then_edit(blocks):
            verified = self.verified_embeds(blocks)
            BlogEmbedSharingBlock.objects.filter(pk=block.pk).update(
                url='https://www.youtube.com/watch?v=9bZkp7q19f0',
            )
            return verified

        with patch('apps.blog.services.verify_article_embeds', side_effect=verify_then_edit):
            with self.assertRaises(BlogWorkflowError) as raised:
                mark_post_ready(post, actor=self.publisher)

        self.assertIn('changed while it was being verified', str(raised.exception))
        post.refresh_from_db()
        block.refresh_from_db()
        self.assertEqual(post.status, BlogPost.Status.DRAFT)
        self.assertEqual(block.url, 'https://www.youtube.com/watch?v=9bZkp7q19f0')
        self.assertNotEqual(block.url, verified_url)

    def test_locked_transition_rejects_embed_order_changed_after_verification(self):
        post = self.create_post(slug='embed-concurrent-reorder')
        BlogRichTextBlock.objects.filter(parent=post).delete()
        first = self.add_embed(post, ordering=10)
        second = self.add_embed(post, ordering=20)

        def verify_then_reorder(blocks):
            verified = self.verified_embeds(blocks)
            BlogEmbedSharingBlock.objects.filter(pk=first.pk).update(ordering=30)
            return verified

        with patch('apps.blog.services.verify_article_embeds', side_effect=verify_then_reorder):
            with self.assertRaises(BlogWorkflowError) as raised:
                mark_post_ready(post, actor=self.publisher)

        self.assertIn('changed while it was being verified', str(raised.exception))
        post.refresh_from_db()
        self.assertEqual(post.status, BlogPost.Status.DRAFT)
        self.assertEqual(
            list(
                BlogEmbedSharingBlock.objects.filter(parent=post)
                .order_by('ordering', 'pk')
                .values_list('pk', flat=True)
            ),
            [second.pk, first.pk],
        )

    def test_preview_warnings_remain_provider_free_for_embeds(self):
        post = self.create_post(slug='embed-preview')
        self.add_embed(post)

        with patch.object(embed_sharing, 'build_opener') as build_opener:
            build_preview_warnings(post)

        build_opener.assert_not_called()
