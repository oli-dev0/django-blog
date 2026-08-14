from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from threading import Event, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, IntegrityError, connection, close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from PIL import Image

from apps.blog import import_services
from apps.blog.import_services import (
    BlogImportUnavailable,
    BlogImportValidationError,
    ReviewedImportReferences,
    cleanup_staged_imports,
    create_blog_post_from_import,
    get_blog_import_review,
    validate_and_stage_blog_import,
)
from apps.blog.models import (
    AuthorProfile,
    BlogArticleImport,
    BlogHeadingBlock,
    BlogImage,
    BlogImageBlock,
    BlogImageComparison,
    BlogImageComparisonBlock,
    BlogPost,
    BlogPostPublication,
    BlogRichTextBlock,
    BlogCategory,
    BlogSite,
)


@contextmanager
def import_workspace():
    with TemporaryDirectory() as media_root, TemporaryDirectory() as import_root:
        with override_settings(MEDIA_ROOT=media_root, BLOG_IMPORT_ROOT=import_root):
            yield Path(media_root), Path(import_root)


def source_file(payload):
    return SimpleUploadedFile(
        'article.json',
        json.dumps(payload).encode('utf-8'),
        content_type='application/json',
    )


def image_file(
    name='hero.png',
    *,
    size=(1600, 900),
    image_format='PNG',
    orientation=None,
):
    output = BytesIO()
    image = Image.new('RGB', size, 'white')
    save_kwargs = {}
    if orientation is not None:
        exif = image.getexif()
        exif[274] = orientation
        save_kwargs['exif'] = exif
    image.save(output, format=image_format, **save_kwargs)
    return SimpleUploadedFile(
        name,
        output.getvalue(),
        content_type=f'image/{image_format.lower()}',
    )


def import_payload(*, title='Imported article', slug='imported-article', blocks=None):
    article = {
        'title': title,
        'summary': 'A useful imported draft.',
        'author': {'slug': 'oli'},
        'category': {'slug': 'development'},
        'tags': [],
        'publication_sites': ['vanta_admin'],
        'canonical_site': 'vanta_admin',
        'related_articles': [],
        'blocks': blocks if blocks is not None else [
            {'type': 'heading', 'level': 2, 'text': 'A section'},
        ],
        'type': 'guide',
        'seo': {
            'title': 'Imported search title',
            'description': 'Imported search description.',
        },
    }
    if slug is not None:
        article['slug'] = slug
    return {
        'format': 'blog-article-import',
        'version': 1,
        'article': article,
        'assets': [],
        'comparisons': [],
    }


class ImportMediaFixtureMixin:
    def setUp(self):
        super().setUp()
        user_model = get_user_model()
        self.actor = user_model.objects.create_user(username='import-media-owner')
        self.author = AuthorProfile.objects.create(
            user=user_model.objects.create_user(username='import-media-author'),
            public_author_name='Oli',
            slug='oli',
        )
        self.category = BlogCategory.objects.create(name='Development', slug='development')
        site, _created = BlogSite.objects.get_or_create(slug='vanta_admin')
        self.category.websites.add(site)

    def allow_permissions(self, actor=None):
        return patch.object(actor or self.actor, 'has_perm', return_value=True)

    def reviewed_references(self):
        return ReviewedImportReferences(
            author=self.author,
            category=self.category,
            tags=(),
            publication_sites=('vanta_admin',),
            canonical_site='vanta_admin',
        )

    def stage(self, payload, *, image_files):
        with self.allow_permissions():
            return validate_and_stage_blog_import(
                source_file(payload),
                list(image_files),
                self.actor,
            )

    def confirm(self, session, *, actor=None):
        actor = actor or self.actor
        with self.allow_permissions(actor):
            with self.captureOnCommitCallbacks(execute=True):
                return create_blog_post_from_import(
                    session,
                    self.reviewed_references(),
                    actor,
                )

    @staticmethod
    def media_files(media_root):
        return [path for path in media_root.rglob('*') if path.is_file()]

    def assert_normal_image_ready(self, image, *, size=(1600, 900)):
        self.assertEqual(image.processing_status, BlogImage.ProcessingStatus.READY)
        self.assertEqual((image.width, image.height), size)
        with Image.open(image.original.path) as original:
            self.assertEqual(original.format, 'PNG')
            self.assertEqual(original.size, size)
        expected_sizes = {
            'rendition_480': (480, round(size[1] * 480 / size[0])),
            'rendition_800': (800, round(size[1] * 800 / size[0])),
            'rendition_1200': (1200, round(size[1] * 1200 / size[0])),
            'rendition_1600': (1600, round(size[1] * 1600 / size[0])),
        }
        for field_name, expected_size in expected_sizes.items():
            field = getattr(image, field_name)
            self.assertTrue(field.name)
            self.assertTrue(field.storage.exists(field.name))
            with Image.open(field.path) as rendition:
                self.assertEqual(rendition.format, 'WEBP')
                self.assertEqual(rendition.size, expected_size)
        self.assertTrue(image.has_publication_files())


class BlogImportMediaTests(ImportMediaFixtureMixin, TestCase):
    def test_all_referenced_media_is_ready_and_blocks_keep_source_order(self):
        payload = import_payload(
            blocks=[
                {'type': 'image', 'asset_id': 'body', 'is_expandable': False},
                {'type': 'heading', 'level': 2, 'text': 'A heading'},
                {'type': 'image_comparison', 'comparison_id': 'before-after'},
                {'type': 'rich_text', 'body': '<p>Article text.</p>'},
            ]
        )
        payload['article']['featured_image'] = 'hero'
        payload['assets'] = [
            {
                'id': 'hero',
                'file': 'images/hero.png',
                'name': 'Hero image',
                'alt_text': 'The hero image',
                'is_feature': True,
                'caption_title': 'Hero',
                'caption_text': 'The article hero.',
            },
            {
                'id': 'body',
                'file': 'images/body.png',
                'name': 'Body image',
                'alt_text': 'The body image',
            },
        ]
        payload['comparisons'] = [
            {
                'id': 'before-after',
                'name': 'Before and after',
                'first': {'file': 'images/before.png', 'alt_text': 'Before image'},
                'second': {'file': 'images/after.png', 'alt_text': 'After image'},
                'caption_title': 'Comparison',
                'caption_text': 'The two states.',
            }
        ]

        with import_workspace() as (media_root, import_root):
            session = self.stage(
                payload,
                image_files=[
                    image_file('hero.png'),
                    image_file('body.png'),
                    image_file('before.png'),
                    image_file('after.png'),
                ],
            )
            post = self.confirm(session)

            post.refresh_from_db()
            images = {
                image.name: image
                for image in BlogImage.objects.filter(created_by=self.actor)
            }
            self.assertEqual(set(images), {'Hero image', 'Body image'})
            self.assertEqual(post.featured_image_id, images['Hero image'].pk)
            self.assertTrue(images['Hero image'].is_feature)
            self.assertFalse(images['Body image'].is_decorative)
            self.assertEqual(images['Hero image'].alt_text, 'The hero image')
            self.assertEqual(images['Body image'].alt_text, 'The body image')
            for image in images.values():
                self.assertEqual(image.created_by_id, self.actor.pk)
                self.assert_normal_image_ready(image)

            comparison = BlogImageComparison.objects.get(created_by=self.actor)
            self.assertEqual(comparison.name, 'Before and after')
            self.assertEqual(comparison.caption_title, 'Comparison')
            self.assertEqual(comparison.caption_text, 'The two states.')
            self.assertEqual(
                (comparison.first_width, comparison.first_height),
                (1600, 900),
            )
            self.assertEqual(
                (comparison.second_width, comparison.second_height),
                (1600, 900),
            )
            self.assertEqual(
                comparison.first_processing_status,
                BlogImageComparison.ProcessingStatus.READY,
            )
            self.assertEqual(
                comparison.second_processing_status,
                BlogImageComparison.ProcessingStatus.READY,
            )
            self.assertEqual(comparison.first_alt_text, 'Before image')
            self.assertEqual(comparison.second_alt_text, 'After image')
            for side in ('first', 'second'):
                self.assertTrue(comparison.has_publication_files(side))
                original_field = getattr(comparison, f'{side}_original')
                self.assertTrue(original_field.name)
                self.assertTrue(original_field.storage.exists(original_field.name))
                with Image.open(original_field.path) as original:
                    self.assertEqual(original.format, 'PNG')
                    self.assertEqual(original.size, (1600, 900))
                for field_name, expected_size in (
                    ('rendition_480', (480, 270)),
                    ('rendition_800', (800, 450)),
                    ('rendition_1200', (1200, 675)),
                    ('rendition_1600', (1600, 900)),
                ):
                    field = getattr(comparison, f'{side}_{field_name}')
                    self.assertTrue(field.name)
                    self.assertTrue(field.storage.exists(field.name))
                    with Image.open(field.path) as rendition:
                        self.assertEqual(rendition.format, 'WEBP')
                        self.assertEqual(rendition.size, expected_size)

            content_blocks = []
            for model in (
                BlogImageBlock,
                BlogHeadingBlock,
                BlogImageComparisonBlock,
                BlogRichTextBlock,
            ):
                content_blocks.extend(model.objects.filter(parent=post))
            content_blocks.sort(key=lambda block: block.ordering)
            self.assertEqual(
                [(type(block), block.ordering) for block in content_blocks],
                [
                    (BlogImageBlock, 10),
                    (BlogHeadingBlock, 20),
                    (BlogImageComparisonBlock, 30),
                    (BlogRichTextBlock, 40),
                ],
            )
            self.assertEqual(content_blocks[0].image_id, images['Body image'].pk)
            self.assertFalse(content_blocks[0].is_expandable)
            self.assertEqual(content_blocks[2].comparison_id, comparison.pk)
            self.assertEqual(post.status, BlogPost.Status.DRAFT)
            self.assertIsNone(post.published_at)
            self.assertIsNone(post.last_reviewed_on)
            self.assertIsNone(post.content_updated_at)
            self.assertFalse(BlogArticleImport.objects.filter(pk=session.pk).exists())
            self.assertEqual(list(import_root.rglob('*')), [])
            self.assertEqual(len(self.media_files(media_root)), 20)

    def test_import_normalizes_original_orientation_and_rendition_dimensions(self):
        payload = import_payload(blocks=[{'type': 'heading', 'level': 2, 'text': 'Heading'}])
        payload['article']['featured_image'] = 'hero'
        payload['assets'] = [{
            'id': 'hero',
            'file': 'hero.jpg',
            'name': 'Rotated hero',
            'alt_text': 'A rotated hero image',
        }]

        with import_workspace() as (media_root, _import_root):
            session = self.stage(
                payload,
                image_files=[image_file('hero.jpg', image_format='JPEG', orientation=6)],
            )
            post = self.confirm(session)

            image = post.featured_image
            image.refresh_from_db()
            self.assertEqual((image.width, image.height), (900, 1600))
            with Image.open(image.original.path) as original:
                self.assertEqual(original.format, 'JPEG')
                self.assertEqual(original.size, (900, 1600))
            for field_name, expected_size in (
                ('rendition_480', (480, 853)),
                ('rendition_800', (800, 1422)),
                ('rendition_1200', (900, 1600)),
                ('rendition_1600', (900, 1600)),
            ):
                with Image.open(getattr(image, field_name).path) as rendition:
                    self.assertEqual(rendition.size, expected_size)
            self.assertTrue(image.has_publication_files())
            self.assertTrue(self.media_files(media_root))

    def test_only_referenced_definitions_create_media_and_unused_definitions_warn(self):
        payload = import_payload(
            blocks=[
                {'type': 'image', 'asset_id': 'used'},
                {'type': 'image_comparison', 'comparison_id': 'used-comparison'},
            ]
        )
        payload['assets'] = [
            {
                'id': 'used',
                'file': 'used.png',
                'name': 'Used image',
                'alt_text': 'The used image',
            },
            {
                'id': 'unused',
                'file': 'unused.png',
                'name': 'Unused image',
                'alt_text': 'The unused image',
            },
        ]
        payload['comparisons'] = [
            {
                'id': 'used-comparison',
                'name': 'Used comparison',
                'first': {'file': 'first.png', 'alt_text': 'First view'},
                'second': {'file': 'second.png', 'alt_text': 'Second view'},
            },
            {
                'id': 'unused-comparison',
                'name': 'Unused comparison',
                'first': {'file': 'unused-first.png', 'alt_text': 'Unused first view'},
                'second': {'file': 'unused-second.png', 'alt_text': 'Unused second view'},
            },
        ]

        with import_workspace() as (_media_root, import_root):
            session = self.stage(
                payload,
                image_files=[
                    image_file('used.png'),
                    image_file('unused.png'),
                    image_file('first.png'),
                    image_file('second.png'),
                    image_file('unused-first.png'),
                    image_file('unused-second.png'),
                ],
            )
            self.assertEqual(
                set(session.files.values_list('selected_name', flat=True)),
                {'used.png', 'first.png', 'second.png'},
            )
            with self.allow_permissions():
                review = get_blog_import_review(session.id, self.actor)
            self.assertEqual(
                {warning.code for warning in review.warnings}
                & {'unused_asset_definition', 'unused_comparison_definition'},
                {'unused_asset_definition', 'unused_comparison_definition'},
            )
            self.confirm(session)

        self.assertEqual(BlogImage.objects.filter(created_by=self.actor).count(), 1)
        self.assertEqual(
            BlogImageComparison.objects.filter(created_by=self.actor).count(),
            1,
        )
        self.assertEqual(list(import_root.rglob('*')), [])

    def test_decorative_and_invalid_alt_text_references_are_rejected(self):
        cases = (
            (
                'decorative body',
                {'type': 'image', 'asset_id': 'hero'},
                None,
                'decorative_body_image',
            ),
            (
                'decorative featured',
                {'type': 'heading', 'level': 2, 'text': 'Heading'},
                'featured',
                'decorative_featured_image',
            ),
            (
                'missing alt text',
                {'type': 'image', 'asset_id': 'hero'},
                'missing-alt',
                'missing_alt_text',
            ),
        )
        with import_workspace() as (_media_root, _import_root):
            for name, block, featured, expected_code in cases:
                with self.subTest(name=name):
                    payload = import_payload(blocks=[block])
                    if featured == 'featured':
                        payload['article']['featured_image'] = 'hero'
                    alt_text = '' if featured == 'missing-alt' else ''
                    payload['assets'] = [{
                        'id': 'hero',
                        'file': 'hero.png',
                        'name': 'Hero',
                        'alt_text': alt_text,
                        'is_decorative': featured != 'missing-alt',
                    }]
                    with self.assertRaises(BlogImportValidationError) as error:
                        self.stage(payload, image_files=[image_file()])
                    self.assertIn(
                        expected_code,
                        {issue.code for issue in error.exception.issues},
                    )
                    self.assertFalse(BlogArticleImport.objects.exists())

    def test_processing_failure_after_media_writes_rolls_back_files_and_keeps_stage_retryable(self):
        payload = import_payload(blocks=[{'type': 'image', 'asset_id': 'hero'}])
        payload['assets'] = [{
            'id': 'hero',
            'file': 'hero.png',
            'name': 'Hero',
            'alt_text': 'A hero image',
        }]

        with import_workspace() as (media_root, import_root):
            session = self.stage(payload, image_files=[image_file()])
            real_process_image = import_services.process_image

            def process_then_fail(image):
                real_process_image(image)
                raise RuntimeError('forced processing failure')

            with patch.object(import_services, 'process_image', side_effect=process_then_fail):
                with self.assertRaises(RuntimeError):
                    self.confirm(session)

            self.assertFalse(BlogPost.objects.filter(title='Imported article').exists())
            self.assertFalse(BlogImage.objects.exists())
            self.assertEqual(self.media_files(media_root), [])
            self.assertTrue(BlogArticleImport.objects.filter(pk=session.pk).exists())
            self.assertEqual(session.files.count(), 1)
            self.assertTrue(list(import_root.rglob('*')))

            post = self.confirm(session)
            self.assertIsNone(post.featured_image_id)
            self.assertEqual(BlogImage.objects.count(), 1)
            self.assertEqual(len(self.media_files(media_root)), 5)

    def test_permanent_cleanup_failure_persists_paths_for_a_safe_retry(self):
        payload = import_payload(blocks=[{'type': 'image', 'asset_id': 'hero'}])
        payload['assets'] = [{
            'id': 'hero',
            'file': 'hero.png',
            'name': 'Hero',
            'alt_text': 'A hero image',
        }]

        with import_workspace() as (media_root, _import_root):
            session = self.stage(payload, image_files=[image_file()])
            real_process_image = import_services.process_image
            default_storage = storages['default']
            original_delete = default_storage.delete
            failed_path = None
            cleanup_started = False

            def process_then_fail(image):
                nonlocal cleanup_started
                real_process_image(image)
                cleanup_started = True
                raise RuntimeError('forced processing failure')

            def fail_once(name):
                nonlocal failed_path
                if cleanup_started and failed_path is None and name.startswith('blog/'):
                    failed_path = name
                    raise OSError('temporary permanent cleanup failure')
                return original_delete(name)

            with patch.object(import_services, 'process_image', side_effect=process_then_fail):
                with patch.object(default_storage, 'delete', side_effect=fail_once):
                    with self.assertRaises(RuntimeError):
                        self.confirm(session)

            session.refresh_from_db()
            self.assertEqual(session.permanent_cleanup_paths, [failed_path])
            self.assertTrue(default_storage.exists(failed_path))
            self.assertEqual(len(self.media_files(media_root)), 1)

            with self.allow_permissions():
                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    post = create_blog_post_from_import(
                        session,
                        self.reviewed_references(),
                        self.actor,
                    )

            session.refresh_from_db()
            self.assertEqual(session.permanent_cleanup_paths, [])
            self.assertEqual(post.featured_image_id, None)
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()
            self.assertFalse(default_storage.exists(failed_path))
            self.assertEqual(len(self.media_files(media_root)), 5)

        self.assertFalse(BlogArticleImport.objects.filter(pk=session.pk).exists())

    def test_failed_media_validation_does_not_delete_unrelated_file_named_like_source(self):
        payload = import_payload(blocks=[{'type': 'image', 'asset_id': 'hero'}])
        payload['assets'] = [{
            'id': 'hero',
            'file': 'hero.png',
            'name': 'Hero',
            'alt_text': 'A hero image',
        }]

        with import_workspace() as (media_root, _import_root):
            default_storage = storages['default']
            preserved_name = default_storage.save('hero.png', ContentFile(b'keep me'))
            session = self.stage(payload, image_files=[image_file()])
            real_save_validated = import_services._save_validated

            def fail_on_image(instance):
                if isinstance(instance, BlogImage):
                    raise ValidationError('forced media validation failure')
                return real_save_validated(instance)

            with patch.object(import_services, '_save_validated', side_effect=fail_on_image):
                with self.assertRaises(ValidationError):
                    self.confirm(session)

            self.assertTrue(default_storage.exists(preserved_name))
            with default_storage.open(preserved_name, 'rb') as preserved_file:
                self.assertEqual(preserved_file.read(), b'keep me')
            self.assertFalse(BlogImage.objects.exists())
            self.assertFalse(BlogPost.objects.filter(title='Imported article').exists())
            self.assertTrue(BlogArticleImport.objects.filter(pk=session.pk).exists())
            self.assertEqual(
                [path for path in media_root.rglob('*') if path.is_file()],
                [media_root / preserved_name],
            )

    def test_database_failure_after_media_writes_rolls_back_rows_and_files_and_allows_retry(self):
        payload = import_payload(blocks=[{'type': 'image', 'asset_id': 'hero'}])
        payload['assets'] = [{
            'id': 'hero',
            'file': 'hero.png',
            'name': 'Hero',
            'alt_text': 'A hero image',
        }]

        with import_workspace() as (media_root, import_root):
            session = self.stage(payload, image_files=[image_file()])

            with patch.object(
                import_services,
                '_create_import_block',
                side_effect=DatabaseError('forced database failure'),
            ):
                with self.assertRaises(DatabaseError):
                    self.confirm(session)

            self.assertFalse(BlogPost.objects.filter(title='Imported article').exists())
            self.assertFalse(BlogImage.objects.exists())
            self.assertFalse(BlogPostPublication.objects.exists())
            self.assertEqual(self.media_files(media_root), [])
            self.assertTrue(BlogArticleImport.objects.filter(pk=session.pk).exists())
            self.assertTrue(list(import_root.rglob('*')))

            post = self.confirm(session)
            self.assertEqual(post.title, 'Imported article')
            self.assertEqual(BlogImage.objects.count(), 1)
            self.assertEqual(len(self.media_files(media_root)), 5)

    def test_successful_import_retains_consumed_metadata_when_private_cleanup_fails_then_retries(self):
        payload = import_payload(blocks=[{'type': 'image', 'asset_id': 'hero'}])
        payload['assets'] = [{
            'id': 'hero',
            'file': 'hero.png',
            'name': 'Hero',
            'alt_text': 'A hero image',
        }]

        with import_workspace() as (_media_root, import_root):
            session = self.stage(payload, image_files=[image_file()])
            storage = storages['blog_imports']
            original_delete = storage.delete

            def fail_once(name):
                if not hasattr(fail_once, 'failed'):
                    fail_once.failed = True
                    raise OSError('temporary private cleanup failure')
                return original_delete(name)

            with patch.object(storage, 'delete', side_effect=fail_once):
                post = self.confirm(session)

            session.refresh_from_db()
            self.assertEqual(session.completed_post_id, post.pk)
            self.assertIsNotNone(session.consumed_at)
            self.assertTrue(session.files.exists())
            self.assertTrue(list(import_root.rglob('*')))
            with self.assertRaises(BlogImportUnavailable):
                import_services.get_pending_import(actor=self.actor, import_id=session.id)

            result = cleanup_staged_imports(batch_size=1)

        self.assertEqual(result.consumed_deleted, 1)
        self.assertEqual(result.files_deleted, 1)
        self.assertFalse(BlogArticleImport.objects.filter(pk=session.pk).exists())

    def test_repeated_confirmation_returns_same_post_before_post_commit_cleanup(self):
        payload = import_payload()

        with import_workspace() as (_media_root, import_root):
            session = self.stage(payload, image_files=[])
            with self.allow_permissions():
                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    first = create_blog_post_from_import(
                        session,
                        self.reviewed_references(),
                        self.actor,
                    )
                    second = create_blog_post_from_import(
                        session,
                        self.reviewed_references(),
                        self.actor,
                    )
                    session.refresh_from_db()
                    self.assertEqual(first.pk, second.pk)
                    self.assertEqual(BlogPost.objects.filter(title='Imported article').count(), 1)
                    self.assertEqual(session.completed_post_id, first.pk)

            self.assertEqual(len(callbacks), 1)
            callbacks[0]()
            self.assertFalse(BlogArticleImport.objects.filter(pk=session.pk).exists())
            self.assertEqual(list(import_root.rglob('*')), [])

    def test_slug_integrity_race_retries_without_overwriting_winning_post(self):
        winner = BlogPost.objects.create(
            status=BlogPost.Status.DRAFT,
            title='Winning concurrent draft',
            slug='race-slug',
            category=self.category,
        )
        payload = import_payload(title='Raced import', slug='race-slug')
        attempts = 0
        real_save_validated = import_services._save_validated

        def fail_first_post_save(instance):
            nonlocal attempts
            if isinstance(instance, BlogPost) and attempts == 0:
                attempts += 1
                raise IntegrityError('simulated slug race')
            return real_save_validated(instance)

        with import_workspace():
            session = self.stage(payload, image_files=[])
            with patch.object(
                import_services,
                '_unique_import_slug',
                side_effect=['race-slug', 'race-slug-2'],
            ), patch.object(
                import_services,
                '_save_validated',
                side_effect=fail_first_post_save,
            ):
                post = self.confirm(session)

        winner.refresh_from_db()
        self.assertEqual(post.slug, 'race-slug-2')
        self.assertEqual(winner.title, 'Winning concurrent draft')
        self.assertEqual(BlogPost.objects.filter(slug='race-slug').count(), 1)
        self.assertEqual(BlogPost.objects.filter(slug='race-slug-2').count(), 1)

    def test_unrelated_integrity_error_is_not_retried_as_slug_conflict(self):
        payload = import_payload()
        with import_workspace():
            session = self.stage(payload, image_files=[])
            with patch.object(
                import_services,
                '_save_validated',
                side_effect=IntegrityError('unrelated unique constraint'),
            ):
                with self.assertRaises(IntegrityError):
                    self.confirm(session)

        self.assertFalse(BlogPost.objects.exists())
        self.assertTrue(BlogArticleImport.objects.filter(pk=session.pk).exists())


class BlogImportConcurrencyTests(ImportMediaFixtureMixin, TransactionTestCase):
    @skipUnless(
        connection.features.has_select_for_update,
        'Concurrent row-lock behavior requires a database with SELECT FOR UPDATE.',
    )
    def test_concurrent_confirmation_creates_at_most_one_post(self):
        payload = import_payload()
        with import_workspace() as (_media_root, import_root):
            session = self.stage(payload, image_files=[])
            reviewed = self.reviewed_references()
            first_validation = Event()
            release_first = Event()
            second_lock_attempt = Event()
            results = {}
            errors = {}
            real_validate = import_services.validate_reviewed_blog_import
            real_select_for_update = BlogArticleImport.objects.select_for_update

            def block_first_validation(*args, **kwargs):
                if threading.current_thread().name == 'import-confirm-first':
                    first_validation.set()
                    if not release_first.wait(timeout=10):
                        raise RuntimeError('test lock release timed out')
                return real_validate(*args, **kwargs)

            def track_second_lock(*args, **kwargs):
                if threading.current_thread().name == 'import-confirm-second':
                    second_lock_attempt.set()
                return real_select_for_update(*args, **kwargs)

            def confirm_in_thread(name):
                close_old_connections()
                try:
                    results[name] = create_blog_post_from_import(session, reviewed, self.actor)
                except BaseException as error:  # pragma: no cover - asserted below
                    errors[name] = error
                finally:
                    close_old_connections()

            with self.allow_permissions():
                with patch.object(
                    import_services,
                    'validate_reviewed_blog_import',
                    side_effect=block_first_validation,
                ), patch.object(
                    BlogArticleImport.objects,
                    'select_for_update',
                    side_effect=track_second_lock,
                ):
                    first = Thread(
                        target=confirm_in_thread,
                        args=('first',),
                        name='import-confirm-first',
                    )
                    second = Thread(
                        target=confirm_in_thread,
                        args=('second',),
                        name='import-confirm-second',
                    )
                    first.start()
                    self.assertTrue(first_validation.wait(timeout=10))
                    second.start()
                    self.assertTrue(second_lock_attempt.wait(timeout=10))
                    release_first.set()
                    first.join(timeout=15)
                    second.join(timeout=15)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertNotIn('first', errors)
            self.assertEqual(BlogPost.objects.filter(title='Imported article').count(), 1)
            if 'second' in results:
                self.assertEqual(results['first'].pk, results['second'].pk)
            else:
                self.assertIsInstance(errors.get('second'), BlogImportUnavailable)
            self.assertEqual(list(import_root.rglob('*')), [])
