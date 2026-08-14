from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from PIL import Image

from apps.blog.image_services import (
    comparison_sources,
    image_sources,
    process_author_profile_picture,
    process_comparison_image,
    process_image,
    validate_image_bytes,
)
from apps.blog.feeds import BlogFeed
from apps.blog.models import AuthorProfile, BlogImage, BlogImageComparison
from apps.core.sites import PERSONAL_SITE


def image_upload(*, image_format='PNG', size=(1600, 900), name='source.png'):
    output = BytesIO()
    Image.new('RGB', size, 'white').save(output, format=image_format)
    return SimpleUploadedFile(name, output.getvalue(), content_type=f'image/{image_format.lower()}')


class BlogImageServiceTests(TestCase):
    def comparison(self):
        return BlogImageComparison.objects.create(
            name='Comparison pair',
            first_original=image_upload(name='first.png'),
            first_alt_text='The first view',
            second_original=image_upload(name='second.png'),
            second_alt_text='The second view',
            caption_title='Before and after',
            caption_text='The same subject in two states.',
        )

    def test_direct_author_profile_picture_upload_preserves_its_file_format(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            user = get_user_model().objects.create_user(username='direct-author-picture-user')
            author = AuthorProfile.objects.create(
                user=user,
                public_author_name='Author',
                profile_picture=image_upload(size=(96, 96), name='author.png'),
            )

            author.refresh_from_db()
            self.assertTrue(author.profile_picture.name.endswith('.png'))
            with Image.open(author.profile_picture.path) as picture:
                self.assertEqual(picture.format, 'PNG')
                self.assertEqual(picture.size, (96, 96))

    def test_author_profile_picture_processing_closes_source_file(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            user = get_user_model().objects.create_user(username='processed-author-picture-user')
            author = AuthorProfile.objects.create(
                user=user,
                public_author_name='Processed author',
                profile_picture=image_upload(size=(160, 96), name='author.png'),
            )

            process_author_profile_picture(author)

            self.assertTrue(author.profile_picture.closed)

    def test_author_profile_picture_processing_closes_source_after_validation_failure(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            user = get_user_model().objects.create_user(username='invalid-author-picture-user')
            author = AuthorProfile.objects.create(
                user=user,
                public_author_name='Invalid author',
                profile_picture=image_upload(name='author.png'),
            )

            with patch(
                'apps.blog.image_services.validate_image_bytes',
                side_effect=ValidationError('invalid image'),
            ):
                with self.assertRaises(ValidationError):
                    process_author_profile_picture(author)

            self.assertTrue(author.profile_picture.closed)

    def test_author_profile_picture_processing_closes_source_after_processing_failure(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            user = get_user_model().objects.create_user(username='failed-author-picture-user')
            author = AuthorProfile.objects.create(
                user=user,
                public_author_name='Failed author',
                profile_picture=image_upload(name='author.png'),
            )

            with patch(
                'apps.blog.image_services._save_image_bytes',
                side_effect=OSError('processing failed'),
            ):
                with self.assertRaises(ValidationError):
                    process_author_profile_picture(author)

            self.assertTrue(author.profile_picture.closed)

    def test_deleting_author_profile_removes_profile_picture(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            user = get_user_model().objects.create_user(username='delete-author-profile-user')
            author = AuthorProfile.objects.create(
                user=user,
                public_author_name='Delete author profile',
                profile_picture=image_upload(size=(96, 96), name='author.png'),
            )
            stored_picture = author.profile_picture
            stored_name = stored_picture.name

            author.delete()

            self.assertFalse(AuthorProfile.objects.filter(pk=author.pk).exists())
            self.assertFalse(stored_picture.storage.exists(stored_name))

    def test_valid_image_generates_non_upscaled_webp_rendition(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            image = BlogImage.objects.create(
                name='Source image',
                original=image_upload(),
                alt_text='A source image',
            )
            process_image(image)

            self.assertTrue(image.original.closed)
            image.refresh_from_db()

            self.assertEqual(image.processing_status, BlogImage.ProcessingStatus.READY)
            self.assertEqual((image.width, image.height), (1600, 900))
            self.assertTrue(image.rendition_480.name)
            self.assertTrue(image.rendition_800.name)
            self.assertTrue(image.rendition_1200.name)
            self.assertTrue(image.rendition_1600.name)
            with Image.open(image.rendition_480.path) as rendition:
                self.assertEqual(rendition.format, 'WEBP')
                self.assertEqual(rendition.size, (480, 270))
            with Image.open(image.rendition_800.path) as rendition:
                self.assertEqual(rendition.format, 'WEBP')
                self.assertEqual(rendition.size, (800, 450))
            with Image.open(image.rendition_1200.path) as rendition:
                self.assertEqual(rendition.format, 'WEBP')
                self.assertEqual(rendition.size, (1200, 675))
            with Image.open(image.rendition_1600.path) as rendition:
                self.assertEqual(rendition.format, 'WEBP')
                self.assertEqual(rendition.size, (1600, 900))

    def test_image_sources_exposes_responsive_renditions(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            image = BlogImage.objects.create(
                name='Responsive image',
                original=image_upload(),
                alt_text='A responsive image',
            )
            process_image(image)
            sources = image_sources(image)

            self.assertIn(' 480w', sources['srcset'])
            self.assertIn(' 800w', sources['srcset'])
            self.assertIn(' 1200w', sources['srcset'])
            self.assertIn(' 1600w', sources['srcset'])
            self.assertEqual(sources['sizes'], '(min-width: 900px) 820px, calc(100vw - 3rem)')

    def test_image_sources_accepts_context_specific_sizes(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            image = BlogImage.objects.create(
                name='List image',
                original=image_upload(),
                alt_text='A list image',
            )
            process_image(image)

            sources = image_sources(image, sizes='(min-width: 640px) 50vw, 100vw')

            self.assertEqual(sources['sizes'], '(min-width: 640px) 50vw, 100vw')

    def test_legacy_image_without_1600_rendition_remains_available(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            image = BlogImage.objects.create(
                name='Legacy image',
                original=image_upload(),
                alt_text='A legacy image',
            )
            process_image(image)
            image.rendition_1600.delete(save=False)
            image.rendition_1600 = ''
            image.save(update_fields=['rendition_1600'])

            sources = image_sources(image)

            self.assertTrue(image.has_publication_files())
            self.assertIn(' 1200w', sources['srcset'])
            self.assertNotIn(' 1600w', sources['srcset'])
            self.assertEqual(sources['src'], image.rendition_1200.url)

    def test_image_sources_does_not_overstate_non_upscaled_rendition_widths(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            image = BlogImage.objects.create(
                name='Small image',
                original=image_upload(size=(320, 180)),
                alt_text='A small image',
            )
            process_image(image)

            self.assertEqual(image_sources(image)['srcset'], '')

    def test_image_sources_does_not_advertise_1600_for_narrower_source(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            image = BlogImage.objects.create(
                name='Narrower image',
                original=image_upload(size=(1599, 900)),
                alt_text='A narrower image',
            )
            process_image(image)

            sources = image_sources(image)

            self.assertIn(' 1200w', sources['srcset'])
            self.assertNotIn(' 1600w', sources['srcset'])
            with Image.open(image.rendition_1600.path) as rendition:
                self.assertEqual(rendition.size, (1599, 900))

    def test_unsupported_image_format_is_rejected_before_processing(self):
        upload = SimpleUploadedFile('source.svg', b'<svg></svg>', content_type='image/svg+xml')

        with self.assertRaises(ValidationError):
            validate_image_bytes(upload)

    def test_oversized_upload_is_rejected(self):
        upload = image_upload()

        with override_settings(BLOG_IMAGE_MAX_BYTES=10):
            with self.assertRaises(ValidationError):
                validate_image_bytes(upload)

    def test_image_processing_closes_source_after_validation_failure(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            image = BlogImage.objects.create(
                name='Invalid source image',
                original=image_upload(),
                alt_text='An invalid source image',
            )

            with patch(
                'apps.blog.image_services.validate_image_bytes',
                side_effect=ValidationError('invalid image'),
            ):
                with self.assertRaises(ValidationError):
                    process_image(image)

            self.assertTrue(image.original.closed)

    def test_image_processing_closes_source_after_processing_failure(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            image = BlogImage.objects.create(
                name='Failed source image',
                original=image_upload(),
                alt_text='A failed source image',
            )

            with patch(
                'apps.blog.image_services._save_image_bytes',
                side_effect=OSError('processing failed'),
            ):
                with self.assertRaises(ValidationError):
                    process_image(image)

            self.assertTrue(image.original.closed)

    def test_missing_original_uses_unavailable_rendering_contract(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            image = BlogImage.objects.create(
                name='Source image',
                original=image_upload(),
                alt_text='A source image',
            )
            process_image(image)
            image.original.storage.delete(image.original.name)

            self.assertIsNone(image_sources(image))

    def test_deleting_image_removes_original_and_all_renditions(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            image = BlogImage.objects.create(
                name='Source image',
                original=image_upload(),
                alt_text='A source image',
            )
            process_image(image)
            image.refresh_from_db()
            stored_files = [
                image.original,
                image.rendition_480,
                image.rendition_800,
                image.rendition_1200,
                image.rendition_1600,
            ]
            stored_names = [field.name for field in stored_files]

            image.delete()

            self.assertTrue(
                all(not field.storage.exists(name) for field, name in zip(stored_files, stored_names))
            )

    def test_feed_reuses_image_metadata_for_each_item(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            image = BlogImage.objects.create(
                name='Source image',
                original=image_upload(),
                alt_text='A source image',
            )
            process_image(image)
            item = type('FeedItem', (), {
                'featured_image': image,
                'canonical_site_slug': PERSONAL_SITE,
            })()
            feed = BlogFeed()

            with patch('apps.blog.feeds.image_sources', wraps=image_sources) as sources:
                self.assertTrue(feed.item_enclosure_url(item))
                self.assertGreater(feed.item_enclosure_length(item), 0)
                self.assertEqual(feed.item_enclosure_mime_type(item), 'image/png')

            sources.assert_called_once_with(image)

    def test_image_sources_exposes_split_caption_fields(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            image = BlogImage.objects.create(
                name='Captioned image',
                original=image_upload(),
                alt_text='A captioned image',
                caption_title='Main caption',
                caption_text='Supporting caption text.',
            )
            process_image(image)
            image.refresh_from_db()

            sources = image_sources(image)

            self.assertEqual(sources['caption_title'], 'Main caption')
            self.assertEqual(sources['caption_text'], 'Supporting caption text.')

    def test_comparison_processing_keeps_two_independent_ready_slots(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            comparison = self.comparison()
            process_comparison_image(comparison, 'first')
            comparison.refresh_from_db()
            process_comparison_image(comparison, 'second')
            comparison.refresh_from_db()
            second_original = comparison.second_original.name

            self.assertEqual(comparison.first_processing_status, BlogImageComparison.ProcessingStatus.READY)
            self.assertEqual(comparison.second_processing_status, BlogImageComparison.ProcessingStatus.READY)
            self.assertEqual(comparison.first_width, 1600)
            self.assertEqual(comparison.second_width, 1600)
            self.assertTrue(comparison.first_rendition_1200.name.startswith('blog/comparisons/renditions/'))
            self.assertTrue(comparison.first_rendition_1600.name.startswith('blog/comparisons/renditions/'))
            self.assertEqual(comparison.second_original.name, second_original)

    def test_processing_one_comparison_side_leaves_the_other_side_untouched(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            comparison = self.comparison()
            process_comparison_image(comparison, 'first')
            process_comparison_image(comparison, 'second')
            comparison.refresh_from_db()
            second_state = (
                comparison.second_original.name,
                comparison.second_rendition_1200.name,
                comparison.second_rendition_1600.name,
                comparison.second_processing_status,
                comparison.second_width,
            )

            comparison.first_original = image_upload(size=(800, 800), name='replacement.png')
            comparison.save(update_fields=['first_original'])
            process_comparison_image(comparison, 'first')
            comparison.refresh_from_db()

            self.assertEqual(
                (
                    comparison.second_original.name,
                    comparison.second_rendition_1200.name,
                    comparison.second_rendition_1600.name,
                    comparison.second_processing_status,
                    comparison.second_width,
                ),
                second_state,
            )

    def test_comparison_sources_are_pair_scoped_and_do_not_overstate_widths(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            comparison = self.comparison()
            process_comparison_image(comparison, 'first')
            process_comparison_image(comparison, 'second')
            sources = comparison_sources(comparison)

            self.assertEqual(sources['caption_title'], 'Before and after')
            self.assertIn('480w', sources['first']['srcset'])
            self.assertIn('800w', sources['first']['srcset'])
            self.assertIn('1200w', sources['first']['srcset'])
            self.assertIn('1600w', sources['first']['srcset'])
            self.assertEqual(
                sources['first']['sizes'],
                '(min-width: 940px) 462px, (min-width: 640px) '
                'calc((100vw - 3rem) / 2), calc(100vw - 3rem)',
            )
            self.assertEqual(sources['first']['alt'], 'The first view')

            comparison.first_rendition_1600.delete(save=False)
            comparison.first_rendition_1600 = ''
            comparison.save(update_fields=['first_rendition_1600'])
            sources = comparison_sources(comparison)
            self.assertIsNotNone(sources['first'])
            self.assertNotIn('1600w', sources['first']['srcset'])

            comparison.first_rendition_800.storage.delete(comparison.first_rendition_800.name)
            sources = comparison_sources(comparison)
            self.assertIsNone(sources['first'])
            self.assertIsNotNone(sources['second'])

    def test_deleting_comparison_removes_both_originals_and_renditions(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            comparison = self.comparison()
            process_comparison_image(comparison, 'first')
            process_comparison_image(comparison, 'second')
            comparison.refresh_from_db()
            stored_names = [
                getattr(comparison, f'{side}_{suffix}').name
                for side in ('first', 'second')
                for suffix in ('original', 'rendition_480', 'rendition_800', 'rendition_1200', 'rendition_1600')
            ]

            comparison.delete()

            self.assertFalse(BlogImageComparison.objects.filter(pk=comparison.pk).exists())
            self.assertTrue(all(not comparison.first_original.storage.exists(name) for name in stored_names))
