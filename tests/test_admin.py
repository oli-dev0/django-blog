from io import BytesIO
import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch

from django.contrib import admin
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.contrib.messages import get_messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.forms.models import inlineformset_factory
from django.conf import settings
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice
from PIL import Image

from apps.blog.forms import (
    BlogCalloutBlockForm,
    BlogChecklistBlockForm,
    BlogEmbedSharingBlockForm,
    BlogFAQBlockForm,
    BlogLinkGroupBlockForm,
    BlogHeadingBlockForm,
    BlogImageAdminForm,
    BlogImageBlockForm,
    BlogImageComparisonAdminForm,
    BlogImageComparisonBlockForm,
    BlogPostAdminForm,
    BlogPostQuickStartForm,
    BlogPostPublicationForm,
    BlogPostRelatedForm,
    BlogRelatedInlineFormSet,
    BlogRichTextBlockForm,
)
from apps.blog.models import (
    BLOG_BLOCK_MODELS,
    AuthorProfile,
    BlogCategory,
    BlogEmbedSharingBlock,
    BlogHeadingBlock,
    BlogFAQBlock,
    BlogImage,
    BlogImageComparison,
    BlogPost,
    BlogPostPublication,
    BlogPostRelated,
    BlogRichTextBlock,
    BlogSite,
    BlogTag,
)
from apps.core.sites import (
    DEFAULT_SITE_DEFINITIONS,
    EASY_MEALS_SITE,
    PERSONAL_SITE,
    VANTA_SITE,
)
from apps.blog.views import resolve_preview_site_slug
from apps.blog.embed_sharing import (
    EmbedVerificationUnavailable,
    NormalizedEmbedReference,
    UnsupportedEmbedItem,
)

from .site_settings import BLOG_ENABLED_SITE_DEFINITIONS


@override_settings(SITE_DEFINITIONS=BLOG_ENABLED_SITE_DEFINITIONS)
class BlogAdminTests(TestCase):
    def setUp(self):
        self.admin_instance = admin.site._registry[BlogPost]
        self.factory = RequestFactory()
        BlogSite.objects.bulk_create(
            [BlogSite(slug=slug) for slug in (PERSONAL_SITE, EASY_MEALS_SITE, VANTA_SITE)],
            ignore_conflicts=True,
        )
        self.category = BlogCategory.objects.create(name='General', slug='general')
        self.category.websites.add(PERSONAL_SITE, EASY_MEALS_SITE, VANTA_SITE)

    def create_post(self, **kwargs):
        kwargs.setdefault('category', self.category)
        return BlogPost.objects.create(**kwargs)

    def grant(self, user, *codenames):
        permissions = Permission.objects.filter(
            content_type__app_label='blog',
            codename__in=codenames,
        )
        user.user_permissions.add(*permissions)

    def request_for(self, user):
        request = self.factory.get('/admin/blog/blogpost/')
        request.user = user
        return request

    def verify_admin_session(self, user):
        device = TOTPDevice.objects.create(user=user, name='default', confirmed=True)
        self.client.force_login(user)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()

    def profile_picture_upload(self, *, color, name):
        output = BytesIO()
        Image.new('RGB', (400, 200), color).save(output, format='PNG')
        return SimpleUploadedFile(name, output.getvalue(), content_type='image/png')

    def comparison_upload(self, *, color, name):
        output = BytesIO()
        Image.new('RGB', (800, 450), color).save(output, format='PNG')
        return SimpleUploadedFile(name, output.getvalue(), content_type='image/png')

    def test_author_admin_resizes_replaces_and_clears_profile_picture(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            admin_user = get_user_model().objects.create_superuser(
                username='author-picture-admin',
                email='author-picture-admin@example.com',
                password='test-password',
            )
            author_user = get_user_model().objects.create_user(username='pictured-author')
            self.verify_admin_session(admin_user)

            add_response = self.client.post(
                '/admin/blog/authorprofile/add/',
                {
                    'user': author_user.pk,
                    'public_author_name': 'Pictured author',
                    'slug': 'pictured-author',
                    'profile_picture': self.profile_picture_upload(color='red', name='first.png'),
                },
                HTTP_HOST='admin.localhost',
            )

            self.assertEqual(add_response.status_code, 302)
            author = AuthorProfile.objects.get(user=author_user)
            first_name = author.profile_picture.name
            self.assertTrue(first_name.endswith('.webp'))
            with Image.open(author.profile_picture.path) as picture:
                self.assertEqual(picture.format, 'WEBP')
                self.assertEqual(picture.size, (96, 96))

            change_url = f'/admin/blog/authorprofile/{author.pk}/change/'
            replace_response = self.client.post(
                change_url,
                {
                    'user': author_user.pk,
                    'public_author_name': 'Pictured author',
                    'slug': 'pictured-author',
                    'profile_picture': self.profile_picture_upload(color='blue', name='second.png'),
                },
                HTTP_HOST='admin.localhost',
            )

            self.assertEqual(replace_response.status_code, 302)
            author.refresh_from_db()
            second_name = author.profile_picture.name
            self.assertNotEqual(first_name, second_name)
            self.assertFalse(author.profile_picture.storage.exists(first_name))
            self.assertTrue(author.profile_picture.storage.exists(second_name))

            clear_response = self.client.post(
                change_url,
                {
                    'user': author_user.pk,
                    'public_author_name': 'Pictured author',
                    'slug': 'pictured-author',
                    'profile_picture-clear': 'on',
                },
                HTTP_HOST='admin.localhost',
            )

            self.assertEqual(clear_response.status_code, 302)
            author.refresh_from_db()
            self.assertFalse(author.profile_picture)
            self.assertFalse(author.profile_picture.storage.exists(second_name))

    def test_author_admin_profile_picture_column_renders_an_image(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            author_user = get_user_model().objects.create_user(username='pictured-author')
            author = AuthorProfile.objects.create(
                user=author_user,
                public_author_name='Pictured author',
                profile_picture=self.profile_picture_upload(color='red', name='author.png'),
            )

            author_admin = admin.site._registry[AuthorProfile]
            preview = author_admin.profile_picture_preview(author)

            self.assertIn('<img ', preview)
            self.assertIn(f'src="{author.profile_picture.url}"', preview)
            self.assertIn('alt="Profile picture for Pictured author"', preview)
            self.assertIn('blog-author-profile-picture-preview', preview)

    def test_effective_status_display_shows_due_schedule_as_published(self):
        post = self.create_post(
            status=BlogPost.Status.SCHEDULED,
            title='Due article',
            slug='due-article',
            published_at=timezone.now() - timezone.timedelta(minutes=1),
        )

        self.assertEqual(self.admin_instance.effective_status_display(post), 'Published')

    def test_blog_admin_model_labels_are_concise_and_correctly_pluralized(self):
        self.assertEqual(BlogPost._meta.verbose_name_plural, 'articles')
        self.assertEqual(BlogTag._meta.verbose_name_plural, 'tags')

    def test_article_author_field_uses_author_profiles_and_concise_labels(self):
        form = BlogPostAdminForm()

        self.assertIn('author', form.fields)
        self.assertNotIn('public_author_name', form.fields)
        self.assertEqual(form.fields['author'].label, 'Author')
        self.assertEqual(AuthorProfile._meta.verbose_name_plural, 'authors')
        self.assertTrue(form.fields['author'].required)
        self.assertIsNone(form.fields['author'].empty_label)
        self.assertTrue(form.fields['category'].required)
        self.assertIsNone(form.fields['category'].empty_label)

    def test_featured_image_field_only_offers_feature_images(self):
        feature_image = BlogImage.objects.create(
            name='Feature image',
            original='blog/originals/feature.jpg',
            is_feature=True,
        )
        regular_image = BlogImage.objects.create(
            name='Regular image',
            original='blog/originals/regular.jpg',
        )

        form = BlogPostAdminForm()

        self.assertQuerySetEqual(
            form.fields['featured_image'].queryset,
            [feature_image],
        )
        self.assertNotIn(regular_image, form.fields['featured_image'].queryset)

    def test_regular_image_block_field_only_offers_non_feature_images(self):
        feature_image = BlogImage.objects.create(
            name='Feature image',
            original='blog/originals/feature.jpg',
            is_feature=True,
        )
        regular_image = BlogImage.objects.create(
            name='Regular image',
            original='blog/originals/regular.jpg',
        )

        form = BlogImageBlockForm()

        self.assertQuerySetEqual(
            form.fields['image'].queryset,
            [regular_image],
        )
        self.assertNotIn(feature_image, form.fields['image'].queryset)

        user = get_user_model().objects.create_superuser(
            username='admin-widget-user',
            email='admin-widget@example.com',
            password='test-password',
        )
        admin_form = self.admin_instance.get_form(self.request_for(user), self.create_post())
        self.assertIsInstance(admin_form.base_fields['author'].widget, RelatedFieldWidgetWrapper)
        self.assertIsInstance(admin_form.base_fields['category'].widget, RelatedFieldWidgetWrapper)

        quick_start_form = BlogPostQuickStartForm()
        self.assertIsNone(quick_start_form.fields['author'].empty_label)
        self.assertIsNone(quick_start_form.fields['category'].empty_label)

    def test_author_admin_exposes_slug_for_stable_public_urls(self):
        author_admin = admin.site._registry[AuthorProfile]

        self.assertIn('slug', author_admin.list_display)
        self.assertIn('slug', author_admin.search_fields)
        self.assertEqual(author_admin.prepopulated_fields, {'slug': ('public_author_name',)})
        self.assertIn('slug', author_admin.form().fields)

    def test_image_admin_starts_with_manual_name(self):
        form = BlogImageAdminForm()

        self.assertEqual(next(iter(form.fields)), 'name')
        self.assertEqual(
            list(form.fields),
            ['name', 'original', 'alt_text', 'is_decorative', 'is_feature', 'caption_title', 'caption_text'],
        )
        self.assertEqual(form.fields['caption_title'].label, 'Caption title (bold)')
        self.assertEqual(form.fields['caption_text'].label, 'Caption text')
        self.assertEqual(admin.site._registry[BlogImage].list_display[0], '__str__')
        self.assertIn('is_feature', admin.site._registry[BlogImage].list_filter)
        self.assertIn('rendition_1600', admin.site._registry[BlogImage].readonly_fields)

    def test_image_admin_replacement_removes_previous_original_and_renditions(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            user = get_user_model().objects.create_superuser(
                username='image-replacement-admin',
                email='image-replacement-admin@example.com',
                password='test-password',
            )
            request = self.request_for(user)
            image_admin = admin.site._registry[BlogImage]
            form_data = {
                'name': 'Replace image',
                'alt_text': 'An image being replaced',
                'caption_title': '',
                'caption_text': '',
            }
            form = BlogImageAdminForm(
                data=form_data,
                files={'original': self.comparison_upload(color='red', name='first.png')},
            )
            self.assertTrue(form.is_valid(), form.errors)
            image = form.save(commit=False)
            image_admin.save_model(request, image, form, False)
            image.refresh_from_db()
            previous_files = [
                (stored_file.storage, stored_file.name)
                for stored_file in (
                    image.original,
                    image.rendition_480,
                    image.rendition_800,
                    image.rendition_1200,
                    image.rendition_1600,
                )
            ]

            replacement_form = BlogImageAdminForm(
                data=form_data,
                files={'original': self.comparison_upload(color='blue', name='replacement.png')},
                instance=image,
            )
            self.assertTrue(replacement_form.is_valid(), replacement_form.errors)
            replacement = replacement_form.save(commit=False)
            image_admin.save_model(request, replacement, replacement_form, True)
            replacement.refresh_from_db()

            self.assertTrue(replacement.original.storage.exists(replacement.original.name))
            self.assertTrue(all(not storage.exists(name) for storage, name in previous_files))

    def test_image_admin_prompts_for_retry_when_committed_files_are_incomplete(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            user = get_user_model().objects.create_superuser(
                username='image-consistency-admin',
                email='image-consistency-admin@example.com',
                password='test-password',
            )
            self.verify_admin_session(user)

            with patch('apps.blog.admin._blog_image_side_is_consistent', return_value=False):
                response = self.client.post(
                    '/admin/blog/blogimage/add/',
                    {
                        'name': 'Check image',
                        'original': self.comparison_upload(color='red', name='check.png'),
                        'alt_text': 'An image to check',
                        'caption_title': '',
                        'caption_text': '',
                    },
                    HTTP_HOST='admin.localhost',
                )

            image = BlogImage.objects.get(name='Check image')
            self.assertRedirects(
                response,
                f'/admin/blog/blogimage/{image.pk}/change/',
                fetch_redirect_response=False,
            )
            self.assertIn(
                'try again',
                ' '.join(str(message) for message in get_messages(response.wsgi_request)),
            )

    def test_image_admin_restores_ready_image_when_replacement_processing_fails(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            user = get_user_model().objects.create_superuser(
                username='image-replacement-failure-admin',
                email='image-replacement-failure-admin@example.com',
                password='test-password',
            )
            request = self.request_for(user)
            image_admin = admin.site._registry[BlogImage]
            form_data = {
                'name': 'Keep existing image',
                'alt_text': 'An existing image',
                'caption_title': '',
                'caption_text': '',
            }
            form = BlogImageAdminForm(
                data=form_data,
                files={'original': self.comparison_upload(color='red', name='first.png')},
            )
            self.assertTrue(form.is_valid(), form.errors)
            image = form.save(commit=False)
            image_admin.save_model(request, image, form, False)
            image.refresh_from_db()
            previous_state = tuple(
                getattr(image, field_name).name
                if field_name.startswith(('original', 'rendition'))
                else getattr(image, field_name)
                for field_name in (
                    'original',
                    'rendition_480',
                    'rendition_800',
                    'rendition_1200',
                    'rendition_1600',
                    'width',
                    'height',
                    'processing_status',
                    'processing_error',
                )
            )
            files_before = {
                path.relative_to(media_root)
                for path in Path(media_root).rglob('*')
                if path.is_file()
            }

            replacement_form = BlogImageAdminForm(
                data=form_data,
                files={'original': self.comparison_upload(color='blue', name='replacement.png')},
                instance=image,
            )
            self.assertTrue(replacement_form.is_valid(), replacement_form.errors)
            replacement = replacement_form.save(commit=False)

            with (
                patch('apps.blog.image_services._save_image_bytes', side_effect=OSError('processing failed')),
                patch.object(image_admin, 'message_user') as message_user,
                self.assertLogs('apps.blog.image_services', level='WARNING'),
            ):
                image_admin.save_model(request, replacement, replacement_form, True)

            replacement.refresh_from_db()
            restored_state = tuple(
                getattr(replacement, field_name).name
                if field_name.startswith(('original', 'rendition'))
                else getattr(replacement, field_name)
                for field_name in (
                    'original',
                    'rendition_480',
                    'rendition_800',
                    'rendition_1200',
                    'rendition_1600',
                    'width',
                    'height',
                    'processing_status',
                    'processing_error',
                )
            )
            files_after = {
                path.relative_to(media_root)
                for path in Path(media_root).rglob('*')
                if path.is_file()
            }

            self.assertEqual(restored_state, previous_state)
            self.assertEqual(files_after, files_before)
            message_user.assert_called_once()

    def test_image_admin_bulk_delete_removes_original_and_renditions(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            image = BlogImage.objects.create(
                name='Delete me',
                original='blog/originals/delete-me.png',
                rendition_480='blog/renditions/delete-me-480.webp',
                rendition_800='blog/renditions/delete-me-800.webp',
                rendition_1200='blog/renditions/delete-me-1200.webp',
                rendition_1600='blog/renditions/delete-me-1600.webp',
            )
            stored_files = [
                image.original,
                image.rendition_480,
                image.rendition_800,
                image.rendition_1200,
                image.rendition_1600,
            ]
            for stored_file in stored_files:
                path = Path(stored_file.path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            image_admin = admin.site._registry[BlogImage]
            image_admin.delete_queryset(
                self.request_for(get_user_model().objects.create_user(username='image-delete-admin')),
                BlogImage.objects.filter(pk=image.pk),
            )

            self.assertFalse(BlogImage.objects.filter(pk=image.pk).exists())
            for stored_file in stored_files:
                self.assertFalse(Path(stored_file.path).exists())

    def test_comparison_admin_bulk_delete_removes_both_sides(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            comparison = BlogImageComparison.objects.create(
                name='Delete comparison',
                first_original='blog/comparisons/originals/first.png',
                first_rendition_480='blog/comparisons/renditions/first-480.webp',
                first_rendition_800='blog/comparisons/renditions/first-800.webp',
                first_rendition_1200='blog/comparisons/renditions/first-1200.webp',
                first_rendition_1600='blog/comparisons/renditions/first-1600.webp',
                first_alt_text='First view',
                second_original='blog/comparisons/originals/second.png',
                second_rendition_480='blog/comparisons/renditions/second-480.webp',
                second_rendition_800='blog/comparisons/renditions/second-800.webp',
                second_rendition_1200='blog/comparisons/renditions/second-1200.webp',
                second_rendition_1600='blog/comparisons/renditions/second-1600.webp',
                second_alt_text='Second view',
            )
            stored_files = [
                getattr(comparison, f'{side}_{suffix}')
                for side in ('first', 'second')
                for suffix in (
                    'original',
                    'rendition_480',
                    'rendition_800',
                    'rendition_1200',
                    'rendition_1600',
                )
            ]
            for stored_file in stored_files:
                path = Path(stored_file.path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            comparison_admin = admin.site._registry[BlogImageComparison]
            comparison_admin.delete_queryset(
                self.request_for(get_user_model().objects.create_user(username='comparison-delete-admin')),
                BlogImageComparison.objects.filter(pk=comparison.pk),
            )

            self.assertFalse(BlogImageComparison.objects.filter(pk=comparison.pk).exists())
            for stored_file in stored_files:
                self.assertFalse(Path(stored_file.path).exists())

    def test_comparison_admin_prompts_for_retry_when_committed_files_are_incomplete(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            user = get_user_model().objects.create_superuser(
                username='comparison-consistency-admin',
                email='comparison-consistency-admin@example.com',
                password='test-password',
            )
            self.verify_admin_session(user)

            with patch('apps.blog.admin._blog_image_side_is_consistent', return_value=False):
                response = self.client.post(
                    '/admin/blog/blogimagecomparison/add/',
                    {
                        'name': 'Check comparison',
                        'first_original': self.comparison_upload(color='red', name='first.png'),
                        'first_alt_text': 'First view',
                        'second_original': self.comparison_upload(color='blue', name='second.png'),
                        'second_alt_text': 'Second view',
                        'caption_title': '',
                        'caption_text': '',
                    },
                    HTTP_HOST='admin.localhost',
                )

            comparison = BlogImageComparison.objects.get(name='Check comparison')
            self.assertRedirects(
                response,
                f'/admin/blog/blogimagecomparison/{comparison.pk}/change/',
                fetch_redirect_response=False,
            )
            self.assertIn(
                'try again',
                ' '.join(str(message) for message in get_messages(response.wsgi_request)),
            )

    def test_image_admin_prompts_for_retry_when_delete_does_not_remove_row(self):
        user = get_user_model().objects.create_superuser(
            username='image-delete-consistency-admin',
            email='image-delete-consistency-admin@example.com',
            password='test-password',
        )
        self.verify_admin_session(user)
        image = BlogImage.objects.create(name='Keep after failed delete')
        image_admin = admin.site._registry[BlogImage]

        with patch.object(image_admin, 'delete_model'):
            response = self.client.post(
                f'/admin/blog/blogimage/{image.pk}/delete/',
                {'post': 'yes'},
                HTTP_HOST='admin.localhost',
            )

        self.assertTrue(BlogImage.objects.filter(pk=image.pk).exists())
        self.assertRedirects(
            response,
            f'/admin/blog/blogimage/{image.pk}/change/',
            fetch_redirect_response=False,
        )
        self.assertIn(
            'try again',
            ' '.join(str(message) for message in get_messages(response.wsgi_request)),
        )

    def test_author_admin_bulk_delete_removes_profile_picture(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            user = get_user_model().objects.create_user(username='author-delete-admin')
            author = AuthorProfile.objects.create(
                user=user,
                public_author_name='Delete author',
                profile_picture=self.profile_picture_upload(color='red', name='author.png'),
            )
            stored_picture = Path(author.profile_picture.path)
            self.assertTrue(stored_picture.exists())

            author_admin = admin.site._registry[AuthorProfile]
            author_admin.delete_queryset(
                self.request_for(get_user_model().objects.create_user(username='author-bulk-delete-admin')),
                AuthorProfile.objects.filter(pk=author.pk),
            )

            self.assertFalse(AuthorProfile.objects.filter(pk=author.pk).exists())
            self.assertFalse(stored_picture.exists())

    def test_comparison_admin_form_has_two_slots_and_shared_caption_fields(self):
        form = BlogImageComparisonAdminForm()

        self.assertEqual(
            list(form.fields),
            [
                'name',
                'first_original',
                'first_alt_text',
                'second_original',
                'second_alt_text',
                'caption_title',
                'caption_text',
            ],
        )
        self.assertEqual(form.fields['caption_title'].label, 'Caption title (bold)')
        self.assertEqual(form.fields['caption_text'].label, 'Caption text')

        valid_form = BlogImageComparisonAdminForm(data={
            'name': 'Pair',
            'first_alt_text': 'First view',
            'second_alt_text': 'Second view',
            'caption_title': 'Shared title',
            'caption_text': 'Shared text',
        }, files={
            'first_original': self.comparison_upload(color='red', name='first.png'),
            'second_original': self.comparison_upload(color='blue', name='second.png'),
        })

        self.assertTrue(valid_form.is_valid(), valid_form.errors)

    def test_comparison_admin_is_separate_and_selector_only_uses_database_ready_rows(self):
        self.assertIn(BlogImageComparison, admin.site._registry)
        self.assertEqual(BlogImageComparison._meta.verbose_name_plural, 'comparison images')
        comparison = BlogImageComparison.objects.create(
            name='Ready pair',
            first_original='blog/comparisons/originals/first.png',
            first_rendition_480='blog/comparisons/renditions/first-480.webp',
            first_rendition_800='blog/comparisons/renditions/first-800.webp',
            first_rendition_1200='blog/comparisons/renditions/first-1200.webp',
            first_width=800,
            first_height=450,
            first_alt_text='First view',
            first_processing_status=BlogImageComparison.ProcessingStatus.READY,
            second_original='blog/comparisons/originals/second.png',
            second_rendition_480='blog/comparisons/renditions/second-480.webp',
            second_rendition_800='blog/comparisons/renditions/second-800.webp',
            second_rendition_1200='blog/comparisons/renditions/second-1200.webp',
            second_width=800,
            second_height=450,
            second_alt_text='Second view',
            second_processing_status=BlogImageComparison.ProcessingStatus.READY,
        )

        with patch('django.core.files.storage.FileSystemStorage.exists', side_effect=AssertionError('choice list checked storage')):
            form = BlogImageComparisonBlockForm()
            rendered = str(form['comparison'])

        self.assertIn(str(comparison.pk), rendered)
        self.assertIn('data-first-preview', rendered)
        self.assertNotIn('Blog image', rendered)

    def test_comparison_admin_uses_bounded_renditions_for_library_and_editor_previews(self):
        comparison = BlogImageComparison(
            name='Preview pair',
            first_rendition_480='blog/comparisons/renditions/first-480.webp',
            first_width=800,
            first_height=450,
            first_alt_text='First <view>',
            second_rendition_480='blog/comparisons/renditions/second-480.webp',
            second_width=600,
            second_height=800,
            second_alt_text='Second view',
        )
        comparison_admin = admin.site._registry[BlogImageComparison]

        pair_preview = str(comparison_admin.pair_preview(comparison))
        first_preview = str(comparison_admin.first_preview(comparison))

        self.assertEqual(pair_preview.count('<img'), 2)
        self.assertIn('first-480.webp', pair_preview)
        self.assertIn('second-480.webp', pair_preview)
        self.assertIn('First &lt;view&gt;', pair_preview)
        self.assertNotIn('/originals/', pair_preview)
        self.assertEqual(first_preview.count('<img'), 1)
        self.assertIn('pair_preview', comparison_admin.list_display)
        self.assertIn('first_preview', comparison_admin.readonly_fields)
        self.assertIn('second_preview', comparison_admin.readonly_fields)
        self.assertIn('first_rendition_1600', comparison_admin.readonly_fields)
        self.assertIn('second_rendition_1600', comparison_admin.readonly_fields)
        self.assertIn('blog/css/admin.css', comparison_admin.Media.css['all'])

    def test_comparison_admin_pages_render_bounded_previews_on_both_admin_hosts(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            user = get_user_model().objects.create_superuser(
                username='comparison-preview-admin',
                email='comparison-preview-admin@example.com',
                password='test-password',
            )
            request = self.request_for(user)
            comparison_admin = admin.site._registry[BlogImageComparison]
            form = BlogImageComparisonAdminForm(data={
                'name': 'Preview pair',
                'first_alt_text': 'First view',
                'second_alt_text': 'Second view',
                'caption_title': '',
                'caption_text': '',
            }, files={
                'first_original': self.comparison_upload(color='red', name='first.png'),
                'second_original': self.comparison_upload(color='blue', name='second.png'),
            })
            self.assertTrue(form.is_valid(), form.errors)
            comparison = form.save(commit=False)
            comparison_admin.save_model(request, comparison, form, False)
            comparison.refresh_from_db()
            self.verify_admin_session(user)

            responses = [
                self.client.get(
                    f'/admin/blog/blogimagecomparison/{comparison.pk}/change/',
                    HTTP_HOST='admin.localhost',
                ),
            ]
            if settings.ENABLE_DEV_ADMIN:
                responses.append(
                    self.client.get(
                        f'/dev-admin/blog/blogimagecomparison/{comparison.pk}/change/',
                        HTTP_HOST='dev-admin.localhost',
                    )
                )

            for response in responses:
                with self.subTest(path=response.request['PATH_INFO']):
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, 'blog-image-comparison-admin-preview--single')
                    self.assertContains(response, comparison.first_rendition_480.url)
                    self.assertContains(response, comparison.second_rendition_480.url)

    def test_comparison_admin_processes_both_sides_and_replaces_only_selected_side(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            user = get_user_model().objects.create_superuser(
                username='comparison-admin',
                email='comparison-admin@example.com',
                password='test-password',
            )
            request = self.request_for(user)
            comparison_admin = admin.site._registry[BlogImageComparison]
            form = BlogImageComparisonAdminForm(data={
                'name': 'Admin pair',
                'first_alt_text': 'First view',
                'second_alt_text': 'Second view',
                'caption_title': 'Title',
                'caption_text': 'Text',
            }, files={
                'first_original': self.comparison_upload(color='red', name='first.png'),
                'second_original': self.comparison_upload(color='blue', name='second.png'),
            })
            self.assertTrue(form.is_valid(), form.errors)
            comparison = form.save(commit=False)
            comparison_admin.save_model(request, comparison, form, False)
            comparison.refresh_from_db()

            self.assertEqual(comparison.first_processing_status, BlogImageComparison.ProcessingStatus.READY)
            self.assertEqual(comparison.second_processing_status, BlogImageComparison.ProcessingStatus.READY)
            old_second_files = (
                comparison.second_original.name,
                comparison.second_rendition_1200.name,
                comparison.second_rendition_1600.name,
            )
            old_first_original = comparison.first_original.name

            replacement_form = BlogImageComparisonAdminForm(data={
                'name': comparison.name,
                'first_alt_text': comparison.first_alt_text,
                'second_alt_text': comparison.second_alt_text,
                'caption_title': comparison.caption_title,
                'caption_text': comparison.caption_text,
            }, files={
                'first_original': self.comparison_upload(color='green', name='replacement.png'),
            }, instance=comparison)
            self.assertTrue(replacement_form.is_valid(), replacement_form.errors)
            replacement = replacement_form.save(commit=False)
            comparison_admin.save_model(request, replacement, replacement_form, True)
            replacement.refresh_from_db()

            self.assertEqual(
                (
                    replacement.second_original.name,
                    replacement.second_rendition_1200.name,
                    replacement.second_rendition_1600.name,
                ),
                old_second_files,
            )
            self.assertNotEqual(replacement.first_original.name, old_first_original)
            self.assertFalse(replacement.first_original.storage.exists(old_first_original))

    def test_comparison_admin_restores_the_ready_side_when_replacement_processing_fails(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            user = get_user_model().objects.create_superuser(
                username='comparison-failure-admin',
                email='comparison-failure-admin@example.com',
                password='test-password',
            )
            request = self.request_for(user)
            comparison_admin = admin.site._registry[BlogImageComparison]
            form = BlogImageComparisonAdminForm(data={
                'name': 'Admin pair',
                'first_alt_text': 'First view',
                'second_alt_text': 'Second view',
                'caption_title': 'Title',
                'caption_text': 'Text',
            }, files={
                'first_original': self.comparison_upload(color='red', name='first.png'),
                'second_original': self.comparison_upload(color='blue', name='second.png'),
            })
            self.assertTrue(form.is_valid(), form.errors)
            comparison = form.save(commit=False)
            comparison_admin.save_model(request, comparison, form, False)
            comparison.refresh_from_db()
            previous_state = tuple(
                getattr(comparison, f'first_{suffix}').name
                if suffix.startswith(('original', 'rendition'))
                else getattr(comparison, f'first_{suffix}')
                for suffix in (
                    'original',
                    'rendition_480',
                    'rendition_800',
                    'rendition_1200',
                    'rendition_1600',
                    'width',
                    'height',
                    'processing_status',
                    'processing_error',
                )
            )
            files_before = {
                path.relative_to(media_root)
                for path in Path(media_root).rglob('*')
                if path.is_file()
            }

            replacement_form = BlogImageComparisonAdminForm(data={
                'name': comparison.name,
                'first_alt_text': comparison.first_alt_text,
                'second_alt_text': comparison.second_alt_text,
                'caption_title': comparison.caption_title,
                'caption_text': comparison.caption_text,
            }, files={
                'first_original': self.comparison_upload(color='green', name='replacement.png'),
            }, instance=comparison)
            self.assertTrue(replacement_form.is_valid(), replacement_form.errors)
            replacement = replacement_form.save(commit=False)

            with (
                patch('apps.blog.image_services._save_image_bytes', side_effect=OSError('processing failed')),
                patch.object(comparison_admin, 'message_user') as message_user,
                self.assertLogs('apps.blog.image_services', level='WARNING'),
            ):
                comparison_admin.save_model(request, replacement, replacement_form, True)

            replacement.refresh_from_db()
            restored_state = tuple(
                getattr(replacement, f'first_{suffix}').name
                if suffix.startswith(('original', 'rendition'))
                else getattr(replacement, f'first_{suffix}')
                for suffix in (
                    'original',
                    'rendition_480',
                    'rendition_800',
                    'rendition_1200',
                    'rendition_1600',
                    'width',
                    'height',
                    'processing_status',
                    'processing_error',
                )
            )
            files_after = {
                path.relative_to(media_root)
                for path in Path(media_root).rglob('*')
                if path.is_file()
            }

            self.assertEqual(restored_state, previous_state)
            self.assertEqual(files_after, files_before)
            message_user.assert_called_once()

    def test_link_group_form_parses_ordered_label_url_lines(self):
        form = BlogLinkGroupBlockForm(data={
            'label': 'Evaluation links',
            'links': 'Review features | https://example.com/features\nInstall | https://example.com/install',
            'region': 'main',
            'ordering': '0',
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data['links'],
            [
                {'label': 'Review features', 'url': 'https://example.com/features'},
                {'label': 'Install', 'url': 'https://example.com/install'},
            ],
        )

    def test_link_group_form_rejects_non_http_urls(self):
        form = BlogLinkGroupBlockForm(data={
            'label': 'Evaluation links',
            'links': 'Unsafe | javascript:alert(1)',
            'region': 'main',
            'ordering': '0',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('absolute HTTP(S) URL', str(form.errors))

    def test_category_admin_requires_websites_and_generates_slug(self):
        category_admin = admin.site._registry[BlogCategory]
        self.assertEqual(category_admin.fields, ('name', 'available_websites'))
        self.assertEqual(category_admin.list_display, ('name', 'website_names'))

        category = BlogCategory.objects.create(name='Product building')
        self.assertEqual(category.slug, 'product-building')

    def test_organization_and_content_permissions_split_readonly_fields(self):
        user_model = get_user_model()
        editor = user_model.objects.create_user(username='editor')
        organizer = user_model.objects.create_user(username='organizer')
        self.grant(editor, 'view_blogpost', 'change_blogpost')
        self.grant(organizer, 'view_blogpost', 'organize_blogpost')

        editor_readonly = self.admin_instance.get_readonly_fields(self.request_for(editor))
        organizer_readonly = self.admin_instance.get_readonly_fields(self.request_for(organizer))

        self.assertIn('category', editor_readonly)
        self.assertNotIn('title', editor_readonly)
        self.assertIn('title', organizer_readonly)
        self.assertNotIn('category', organizer_readonly)

    def test_add_and_change_pages_render(self):
        user = get_user_model().objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='test-password',
        )
        post = self.create_post(title='Admin article', slug='admin-article')
        self.verify_admin_session(user)

        add_response = self.client.get('/admin/blog/blogpost/add/', HTTP_HOST='admin.localhost')
        dev_add_response = None
        if settings.ENABLE_DEV_ADMIN:
            dev_add_response = self.client.get(
                '/dev-admin/blog/blogpost/add/',
                HTTP_HOST='dev-admin.localhost',
            )
        change_response = self.client.get(
            f'/admin/blog/blogpost/{post.pk}/change/',
            HTTP_HOST='admin.localhost',
        )

        self.assertEqual(add_response.status_code, 200)
        self.assertContains(add_response, 'Start a new article')
        self.assertContains(add_response, 'Create draft and start writing')
        self.assertNotContains(add_response, 'Import article')
        self.assertNotContains(add_response, 'Starting template')
        self.assertContains(add_response, 'app-blog model-blogpost change-form')
        self.assertContains(add_response, 'id="content-main"')
        if dev_add_response is not None:
            self.assertEqual(dev_add_response.status_code, 200)
            self.assertContains(dev_add_response, '/dev-admin/blog/blogpost/')
            self.assertContains(dev_add_response, 'app-blog model-blogpost change-form')
        self.assertEqual(change_response.status_code, 200)
        change_html = change_response.content.decode()
        self.assertLess(change_html.index('Publication sites'), change_html.index('name="title"'))

    @skipUnless(settings.ENABLE_DEV_ADMIN, 'development admin is disabled')
    def test_publish_page_has_back_link_to_change_page(self):
        user = get_user_model().objects.create_superuser(
            username='publish-back-admin',
            email='publish-back-admin@example.com',
            password='test-password',
        )
        post = self.create_post(title='Publishable article', slug='publishable-article')
        self.verify_admin_session(user)

        for action in ('publish', 'schedule', 'unpublish', 'mark-reviewed'):
            with self.subTest(action=action):
                response = self.client.get(
                    f'/dev-admin/blog/blogpost/{post.pk}/{action}/',
                    HTTP_HOST='dev-admin.localhost',
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    f'href="/dev-admin/blog/blogpost/{post.pk}/change/"',
                    html=False,
                )
                response_html = response.content.decode()
                continue_position = response_html.index('value="Continue"')
                back_position = response_html.index('>Back</a>')
                self.assertLess(continue_position, back_position)
                self.assertIn('class="closelink"', response_html[back_position - 100:back_position])

    def test_quick_start_creates_blank_draft_without_content_blocks(self):
        user = get_user_model().objects.create_superuser(
            username='quick-start-admin',
            email='quick-start@example.com',
            password='test-password',
        )
        author = AuthorProfile.objects.create(user=user, public_author_name='Quick start author')
        self.verify_admin_session(user)

        response = self.client.post(
            '/admin/blog/blogpost/add/',
            {
                'title': 'A simpler writing flow',
                'site_slug': PERSONAL_SITE,
                'type': BlogPost.Type.ARTICLE,
                'category': self.category.pk,
                'author': author.pk,
            },
            HTTP_HOST='admin.localhost',
        )

        post = BlogPost.objects.get(title='A simpler writing flow')
        self.assertRedirects(
            response,
            f'/admin/blog/blogpost/{post.pk}/change/',
            fetch_redirect_response=False,
        )
        self.assertEqual(post.slug, 'a-simpler-writing-flow')
        self.assertEqual(post.status, BlogPost.Status.DRAFT)
        self.assertEqual(post.author, author)
        self.assertEqual(post.category, self.category)
        self.assertEqual(post.canonical_site_slug, PERSONAL_SITE)
        self.assertEqual(post.created_by, user)
        self.assertTrue(
            BlogPostPublication.objects.filter(post=post, site_slug=PERSONAL_SITE).exists()
        )
        self.assertFalse(BlogRichTextBlock.objects.filter(parent=post).exists())

    def test_quick_start_requires_category(self):
        user = get_user_model().objects.create_superuser(
            username='missing-category-admin',
            email='missing-category@example.com',
            password='test-password',
        )
        author = AuthorProfile.objects.create(user=user, public_author_name='Category test author')
        self.verify_admin_session(user)

        response = self.client.post(
            '/admin/blog/blogpost/add/',
            {
                'title': 'Missing category',
                'site_slug': PERSONAL_SITE,
                'type': BlogPost.Type.ARTICLE,
                'author': author.pk,
                'draft_template': 'blank',
            },
            HTTP_HOST='admin.localhost',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This field is required.')
        self.assertFalse(BlogPost.objects.filter(title='Missing category').exists())

    def test_quick_start_requires_author(self):
        user = get_user_model().objects.create_superuser(
            username='missing-author-admin',
            email='missing-author@example.com',
            password='test-password',
        )
        self.verify_admin_session(user)

        response = self.client.post(
            '/admin/blog/blogpost/add/',
            {
                'title': 'Missing author',
                'site_slug': PERSONAL_SITE,
                'type': BlogPost.Type.ARTICLE,
                'category': self.category.pk,
                'draft_template': 'blank',
            },
            HTTP_HOST='admin.localhost',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This field is required.')
        self.assertFalse(BlogPost.objects.filter(title='Missing author').exists())

    def test_quick_start_no_longer_exposes_starting_templates(self):
        user = get_user_model().objects.create_superuser(
            username='guide-admin',
            email='guide@example.com',
            password='test-password',
        )
        author = AuthorProfile.objects.create(user=user, public_author_name='Guide author')
        self.create_post(title='Existing title', slug='existing-title')
        self.verify_admin_session(user)

        response = self.client.post(
            '/admin/blog/blogpost/add/',
            {
                'title': 'Existing title',
                'site_slug': VANTA_SITE,
                'type': BlogPost.Type.GUIDE,
                'category': self.category.pk,
                'author': author.pk,
            },
            HTTP_HOST='admin.localhost',
        )

        self.assertEqual(response.status_code, 302)
        post = BlogPost.objects.get(slug='existing-title-2')
        self.assertFalse(BlogHeadingBlock.objects.filter(parent=post).exists())
        self.assertFalse(BlogRichTextBlock.objects.filter(parent=post).exists())

    def test_starter_rich_text_blocks_may_remain_empty_while_drafting(self):
        form = BlogRichTextBlockForm(
            data={
                'body': '',
                'region': 'main',
                'ordering': 10,
            }
        )

        self.assertTrue(form.is_valid())

    def test_custom_block_forms_keep_content_editor_position_fields(self):
        form_classes = (
            BlogHeadingBlockForm,
            BlogRichTextBlockForm,
            BlogFAQBlockForm,
            BlogChecklistBlockForm,
            BlogCalloutBlockForm,
            BlogEmbedSharingBlockForm,
        )

        for form_class in form_classes:
            with self.subTest(form=form_class.__name__):
                self.assertIn('region', form_class.base_fields)
                self.assertIn('ordering', form_class.base_fields)

    def test_embed_form_renders_labelled_fields_help_text_limit_and_bound_values(self):
        post = self.create_post(title='Embed article', slug='embed-article')
        block = BlogEmbedSharingBlock(
            parent=post,
            region='main',
            platform=BlogEmbedSharingBlock.Platform.YOUTUBE,
            url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            caption='A useful caption',
        )
        form = BlogEmbedSharingBlockForm(instance=block)
        html = str(form)

        self.assertEqual(
            list(form.fields),
            ['platform', 'url', 'caption', 'region', 'ordering'],
        )
        self.assertEqual(form['platform'].label, 'Platform')
        self.assertEqual(form['url'].label, 'Content URL')
        self.assertEqual(form['caption'].label, 'Caption (optional)')
        self.assertIn('Paste a public YouTube video, X post, or Reddit post URL.', html)
        self.assertIn('Briefly explain why this content is relevant to the article.', html)
        self.assertIn('maxlength="300"', html)
        self.assertIn('aria-describedby="id_caption_helptext"', html)
        self.assertIn('value="A useful caption"', html)
        self.assertIn('type="hidden"', str(form['region']))
        self.assertIn('type="hidden"', str(form['ordering']))

    def test_embed_draft_form_uses_local_validation_without_provider_verification(self):
        post = self.create_post(title='Draft embed', slug='draft-embed')
        form = BlogEmbedSharingBlockForm(
            data={
                'platform': BlogEmbedSharingBlock.Platform.YOUTUBE,
                'url': ' https://youtu.be/dQw4w9WgXcQ?si=tracking ',
                'caption': '  Draft caption  ',
                'region': 'main',
                'ordering': 10,
            },
            instance=BlogEmbedSharingBlock(parent=post),
        )

        with patch('apps.blog.forms.verify_reference') as verify:
            self.assertTrue(form.is_valid(), form.errors)

        verify.assert_not_called()
        self.assertEqual(form.cleaned_data['url'], 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        self.assertEqual(form.cleaned_data['caption'], 'Draft caption')

    def test_embed_form_returns_field_errors_without_provider_verification(self):
        post = self.create_post(title='Invalid embed', slug='invalid-embed')
        cases = (
            (
                {'platform': '', 'url': '', 'caption': ''},
                {'platform': 'Choose a platform.', 'url': 'Enter a content URL.'},
            ),
            (
                {'platform': 'youtube', 'url': 'https://x.com/example/status/123456789'},
                {'url': 'Enter a valid URL from the selected platform.'},
            ),
            (
                {'platform': 'youtube', 'url': 'https://www.youtube.com/playlist?list=PL1234567890'},
                {'url': 'Enter a valid URL from the selected platform.'},
            ),
        )

        for values, expected_errors in cases:
            with self.subTest(values=values):
                form = BlogEmbedSharingBlockForm(
                    data={**values, 'region': 'main', 'ordering': 10},
                    instance=BlogEmbedSharingBlock(parent=post),
                )
                with patch('apps.blog.forms.verify_reference') as verify:
                    self.assertFalse(form.is_valid())

                for field, message in expected_errors.items():
                    self.assertIn(message, form.errors[field])
                verify.assert_not_called()

    def test_changed_live_embed_requires_verification_and_keeps_saved_row_on_failure(self):
        post = self.create_post(
            title='Live embed',
            slug='live-embed',
            status=BlogPost.Status.PUBLISHED,
            published_at=timezone.now(),
        )
        block = BlogEmbedSharingBlock.objects.create(
            parent=post,
            region='main',
            platform=BlogEmbedSharingBlock.Platform.YOUTUBE,
            url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        )
        attempted_url = 'https://www.youtube.com/watch?v=9bZkp7q19f0'
        form = BlogEmbedSharingBlockForm(
            data={
                'platform': BlogEmbedSharingBlock.Platform.YOUTUBE,
                'url': attempted_url,
                'caption': '',
                'region': 'main',
                'ordering': 10,
            },
            instance=block,
        )

        with patch(
            'apps.blog.forms.verify_reference',
            side_effect=UnsupportedEmbedItem(),
        ) as verify:
            self.assertFalse(form.is_valid())

        verify.assert_called_once_with(
            NormalizedEmbedReference(
                BlogEmbedSharingBlock.Platform.YOUTUBE,
                attempted_url,
                '9bZkp7q19f0',
            )
        )
        self.assertIn(
            'This type of content cannot be embedded here. Use a public YouTube video, X post, or Reddit post.',
            form.errors['url'],
        )
        self.assertEqual(form.data['url'], attempted_url)
        block.refresh_from_db()
        self.assertEqual(block.url, 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')

    def test_changed_future_scheduled_embed_requires_verification(self):
        post = self.create_post(
            title='Scheduled embed',
            slug='scheduled-embed',
            status=BlogPost.Status.SCHEDULED,
            published_at=timezone.now() + timedelta(days=1),
        )
        block = BlogEmbedSharingBlock.objects.create(
            parent=post,
            region='main',
            platform=BlogEmbedSharingBlock.Platform.YOUTUBE,
            url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        )
        attempted_url = 'https://www.youtube.com/watch?v=9bZkp7q19f0'
        form = BlogEmbedSharingBlockForm(
            data={
                'platform': BlogEmbedSharingBlock.Platform.YOUTUBE,
                'url': attempted_url,
                'caption': '',
                'region': 'main',
                'ordering': 10,
            },
            instance=block,
        )

        with patch(
            'apps.blog.forms.verify_reference',
            side_effect=UnsupportedEmbedItem(),
        ) as verify:
            self.assertFalse(form.is_valid())

        verify.assert_called_once_with(
            NormalizedEmbedReference(
                BlogEmbedSharingBlock.Platform.YOUTUBE,
                attempted_url,
                '9bZkp7q19f0',
            )
        )
        block.refresh_from_db()
        self.assertEqual(block.url, 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')

    def test_new_live_embed_requires_verification_and_remains_unsaved_on_failure(self):
        post = self.create_post(
            title='Live article with new embed',
            slug='live-article-with-new-embed',
            status=BlogPost.Status.PUBLISHED,
            published_at=timezone.now(),
        )
        attempted_url = 'https://www.youtube.com/watch?v=9bZkp7q19f0'
        block = BlogEmbedSharingBlock(parent=post)
        form = BlogEmbedSharingBlockForm(
            data={
                'platform': BlogEmbedSharingBlock.Platform.YOUTUBE,
                'url': attempted_url,
                'caption': '',
                'region': 'main',
                'ordering': 10,
            },
            instance=block,
        )

        with patch(
            'apps.blog.forms.verify_reference',
            side_effect=UnsupportedEmbedItem(),
        ) as verify:
            self.assertFalse(form.is_valid())

        verify.assert_called_once_with(
            NormalizedEmbedReference(
                BlogEmbedSharingBlock.Platform.YOUTUBE,
                attempted_url,
                '9bZkp7q19f0',
            )
        )
        self.assertIn(
            'This type of content cannot be embedded here. Use a public YouTube video, X post, or Reddit post.',
            form.errors['url'],
        )
        self.assertIsNone(block.pk)
        self.assertFalse(BlogEmbedSharingBlock.objects.filter(parent=post).exists())

    def test_live_caption_and_ordering_edits_do_not_verify_provider_content(self):
        post = self.create_post(
            title='Live caption',
            slug='live-caption',
            status=BlogPost.Status.PUBLISHED,
            published_at=timezone.now(),
        )
        block = BlogEmbedSharingBlock.objects.create(
            parent=post,
            region='main',
            ordering=10,
            platform=BlogEmbedSharingBlock.Platform.YOUTUBE,
            url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        )

        for caption, ordering in (('New caption', '10'), ('', '20')):
            with self.subTest(caption=caption, ordering=ordering):
                form = BlogEmbedSharingBlockForm(
                    data={
                        'platform': BlogEmbedSharingBlock.Platform.YOUTUBE,
                        'url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                        'caption': caption,
                        'region': 'main',
                        'ordering': ordering,
                    },
                    instance=block,
                )
                with patch('apps.blog.forms.verify_reference') as verify:
                    self.assertTrue(form.is_valid(), form.errors)
                verify.assert_not_called()

    def test_changed_live_embed_transient_failure_retains_attempted_value(self):
        post = self.create_post(
            title='Unavailable embed',
            slug='unavailable-embed',
            status=BlogPost.Status.PUBLISHED,
            published_at=timezone.now(),
        )
        block = BlogEmbedSharingBlock.objects.create(
            parent=post,
            region='main',
            platform=BlogEmbedSharingBlock.Platform.YOUTUBE,
            url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        )
        attempted_url = 'https://www.youtube.com/watch?v=9bZkp7q19f0'
        form = BlogEmbedSharingBlockForm(
            data={
                'platform': BlogEmbedSharingBlock.Platform.YOUTUBE,
                'url': attempted_url,
                'caption': '',
                'region': 'main',
                'ordering': 10,
            },
            instance=block,
        )

        with patch(
            'apps.blog.forms.verify_reference',
            side_effect=EmbedVerificationUnavailable(),
        ):
            self.assertFalse(form.is_valid())

        self.assertIn(
            'The embedded content could not be verified right now. Try again.',
            form.errors['url'],
        )
        self.assertEqual(form.data['url'], attempted_url)
        block.refresh_from_db()
        self.assertEqual(block.url, 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')

    def test_embed_inline_uses_share_icon_and_existing_permission_boundary(self):
        user_model = get_user_model()
        editor = user_model.objects.create_user(username='embed-editor')
        view_only = user_model.objects.create_user(username='embed-viewer')
        article_only = user_model.objects.create_user(username='embed-article-only')
        self.grant(
            editor,
            'view_blogpost',
            'change_blogpost',
            'view_blogembedsharingblock',
            'add_blogembedsharingblock',
            'change_blogembedsharingblock',
            'delete_blogembedsharingblock',
        )
        self.grant(view_only, 'view_blogpost', 'view_blogembedsharingblock')
        self.grant(article_only, 'view_blogpost', 'change_blogpost')
        inline_class = next(
            inline
            for inline in self.admin_instance.inlines
            if inline.model is BlogEmbedSharingBlock
        )
        inline = inline_class(BlogPost, admin.site)

        self.assertEqual(inline.icon, 'share')
        editor_request = self.request_for(editor)
        self.assertTrue(inline.has_add_permission(editor_request))
        self.assertTrue(inline.has_change_permission(editor_request))
        self.assertTrue(inline.has_delete_permission(editor_request))

        view_request = self.request_for(view_only)
        self.assertFalse(inline.has_add_permission(view_request))
        self.assertFalse(inline.has_change_permission(view_request))
        self.assertFalse(inline.has_delete_permission(view_request))

        article_request = self.request_for(article_only)
        self.assertFalse(inline.has_add_permission(article_request))
        self.assertFalse(inline.has_change_permission(article_request))
        self.assertFalse(inline.has_delete_permission(article_request))

    def test_projected_body_counts_complete_embed_but_not_deleted_or_incomplete_embed(self):
        post = self.create_post(
            title='Embed-only article',
            slug='embed-only-article',
            status=BlogPost.Status.PUBLISHED,
            published_at=timezone.now(),
        )
        block = BlogEmbedSharingBlock.objects.create(
            parent=post,
            region='main',
            platform=BlogEmbedSharingBlock.Platform.YOUTUBE,
            url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        )
        data = {}
        for block_model in BLOG_BLOCK_MODELS:
            prefix = block_model._meta.get_field('parent').remote_field.get_accessor_name()
            data[f'{prefix}-TOTAL_FORMS'] = '0'
            data[f'{prefix}-INITIAL_FORMS'] = '0'
        prefix = BlogEmbedSharingBlock._meta.get_field('parent').remote_field.get_accessor_name()
        data.update({
            f'{prefix}-TOTAL_FORMS': '1',
            f'{prefix}-INITIAL_FORMS': '1',
            f'{prefix}-0-id': str(block.pk),
            f'{prefix}-0-platform': BlogEmbedSharingBlock.Platform.YOUTUBE,
            f'{prefix}-0-url': block.url,
        })

        self.assertTrue(BlogPostAdminForm(data=data, instance=post)._projected_body_exists())

        data[f'{prefix}-0-DELETE'] = 'on'
        self.assertFalse(BlogPostAdminForm(data=data, instance=post)._projected_body_exists())

        data.pop(f'{prefix}-0-DELETE')
        data[f'{prefix}-0-url'] = ''
        self.assertFalse(BlogPostAdminForm(data=data, instance=post)._projected_body_exists())

    def test_embed_removal_confirmation_is_scoped_and_capture_phase(self):
        script = Path(__file__).resolve().parents[2] / 'apps/blog/static/blog/js/admin.js'
        source = script.read_text()

        self.assertIn('blog_blogembedsharingblock_set-', source)
        self.assertIn('Remove this embedded content from the article?', source)
        self.assertIn("document.addEventListener('click', confirmEmbedRemoval, true)", source)
        self.assertIn("inline.classList.remove('collapsed', 'for-deletion')", source)

    def test_faq_form_widget_renders_items_actions_and_admin_module(self):
        form = BlogFAQBlockForm(
            initial={
                'items': [
                    {'question': 'How?', 'answer': '<p>Like this.</p>'},
                ],
            }
        )

        html = str(form['items'])

        self.assertEqual(form['items'].label, 'FAQ')
        self.assertIn('FAQ', html)
        self.assertNotIn('Frequently asked questions', html)
        self.assertIn('Question 1', html)
        self.assertIn('Answer 1', html)
        self.assertIn('Move up', html)
        self.assertIn('Move down', html)
        self.assertIn('Delete question', html)
        self.assertIn('Add question', html)
        self.assertEqual(html.count('class="button" data-faq-action'), 6)
        self.assertEqual(html.count('class="button" data-faq-add'), 1)
        self.assertIn('faq-admin.', str(form.media))
        self.assertIn('type="module"', str(form.media))

    def test_faq_form_normalizes_order_and_preserves_invalid_submission(self):
        items = [
            {'question': ' Second? ', 'answer': '<p>Second.</p>'},
            {'question': 'First?', 'answer': '<p>First.</p>'},
        ]
        form = BlogFAQBlockForm(
            data={'items': json.dumps(items), 'region': 'main', 'ordering': 10},
            site_slugs={PERSONAL_SITE},
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            [item['question'] for item in form.cleaned_data['items']],
            ['Second?', 'First?'],
        )

        invalid = BlogFAQBlockForm(
            data={
                'items': json.dumps([{'question': 'Incomplete?', 'answer': ''}]),
                'region': 'main',
                'ordering': 10,
            },
            site_slugs={PERSONAL_SITE},
        )
        self.assertFalse(invalid.is_valid())
        self.assertIn('Question 1: Enter an answer.', invalid.errors['items'])
        self.assertIn('Incomplete?', str(invalid['items']))

        malformed = BlogFAQBlockForm(
            data={'items': 'not-json', 'region': 'main', 'ordering': 10},
            site_slugs={PERSONAL_SITE},
        )
        self.assertFalse(malformed.is_valid())
        self.assertIn('The FAQ content could not be read.', malformed.errors['items'][0])
        self.assertIn('value="not-json"', str(malformed['items']))

    def test_faq_form_validates_internal_links_for_projected_sites(self):
        items = [{
            'question': 'Where?',
            'answer': '<p><a data-blog-internal-key="personal-projects">Projects</a></p>',
        }]
        form = BlogFAQBlockForm(
            data={'items': json.dumps(items), 'region': 'main', 'ordering': 10},
            site_slugs={VANTA_SITE},
        )

        self.assertFalse(form.is_valid())
        self.assertIn('Choose a destination available on every selected publication website.', form.errors['items'])

    def test_faq_inline_requires_article_and_generated_block_permissions(self):
        user_model = get_user_model()
        editor = user_model.objects.create_user(username='faq-editor')
        view_only = user_model.objects.create_user(username='faq-viewer')
        article_only = user_model.objects.create_user(username='faq-article-only')
        self.grant(
            editor,
            'view_blogpost',
            'change_blogpost',
            'view_blogfaqblock',
            'add_blogfaqblock',
            'change_blogfaqblock',
            'delete_blogfaqblock',
        )
        self.grant(view_only, 'view_blogpost', 'view_blogfaqblock')
        self.grant(article_only, 'view_blogpost', 'change_blogpost')
        inline_class = next(
            inline
            for inline in self.admin_instance.inlines
            if inline.model is BlogFAQBlock
        )
        inline = inline_class(BlogPost, admin.site)

        editor_request = self.request_for(editor)
        self.assertTrue(inline.has_add_permission(editor_request))
        self.assertTrue(inline.has_change_permission(editor_request))
        self.assertTrue(inline.has_delete_permission(editor_request))

        view_request = self.request_for(view_only)
        self.assertTrue(inline.has_view_permission(view_request))
        self.assertFalse(inline.has_add_permission(view_request))
        self.assertFalse(inline.has_change_permission(view_request))
        self.assertFalse(inline.has_delete_permission(view_request))

        article_request = self.request_for(article_only)
        self.assertFalse(inline.has_add_permission(article_request))
        self.assertFalse(inline.has_change_permission(article_request))
        self.assertFalse(inline.has_delete_permission(article_request))

    def test_empty_faq_does_not_count_as_projected_public_body(self):
        post = self.create_post(
            status=BlogPost.Status.PUBLISHED,
            title='FAQ article',
            slug='faq-article',
            summary='Summary',
            published_at=timezone.now(),
            canonical_site_slug=PERSONAL_SITE,
        )
        faq = BlogFAQBlock.objects.create(parent=post, region='main', items=[])
        data = {}
        for block_model in BLOG_BLOCK_MODELS:
            prefix = block_model._meta.get_field('parent').remote_field.get_accessor_name()
            data[f'{prefix}-TOTAL_FORMS'] = '0'
            data[f'{prefix}-INITIAL_FORMS'] = '0'
        prefix = BlogFAQBlock._meta.get_field('parent').remote_field.get_accessor_name()
        data.update({
            f'{prefix}-TOTAL_FORMS': '1',
            f'{prefix}-INITIAL_FORMS': '1',
            f'{prefix}-0-id': str(faq.pk),
            f'{prefix}-0-items': '[]',
        })

        self.assertFalse(BlogPostAdminForm(data=data, instance=post)._projected_body_exists())

    def test_checklist_form_allows_supported_markers(self):
        form = BlogChecklistBlockForm()

        self.assertEqual(
            list(form.fields['marker'].choices),
            [
                ('checkmark', 'Checkmark'),
                ('square', 'Square checkbox'),
                ('arrow', 'Arrow'),
            ],
        )
        self.assertEqual(form.fields['marker'].initial, None)

    def test_heading_anchor_is_generated_and_not_editable(self):
        form = BlogHeadingBlockForm(
            data={
                'level': 2,
                'text': 'Getting Started',
                'anchor': 'manual-anchor',
                'region': 'main',
                'ordering': 10,
            }
        )

        self.assertTrue(form.fields['anchor'].disabled)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['anchor'], 'getting-started')

    def test_preview_redirects_to_default_and_uses_selected_site_shell(self):
        user = get_user_model().objects.create_superuser(
            username='preview-admin',
            email='preview@example.com',
            password='test-password',
        )
        post = self.create_post(
            title='Preview article',
            slug='preview-article',
            summary='Preview summary',
            canonical_site_slug=PERSONAL_SITE,
        )
        BlogRichTextBlock.objects.create(parent=post, region='main', body='<p>Preview body</p>')
        self.verify_admin_session(user)

        redirect_response = self.client.get(
            f'/admin/blog/blogpost/{post.pk}/preview/',
            HTTP_HOST='admin.localhost',
        )
        self.assertRedirects(
            redirect_response,
            f'/admin/blog/blogpost/{post.pk}/preview/?site={PERSONAL_SITE}',
            fetch_redirect_response=False,
        )

        response = self.client.get(
            f'/admin/blog/blogpost/{post.pk}/preview/?site={PERSONAL_SITE}',
            HTTP_HOST='admin.localhost',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'my_website/blog/detail.html')
        self.assertContains(response, '>Preview<', html=False)
        self.assertContains(response, 'Private preview. Only signed-in staff can view this page.')
        self.assertContains(response, 'Preview body')
        self.assertContains(response, '>Back<', html=False)
        self.assertRegex(
            response.content.decode(),
            r'/static/my_website/css/blog\.[0-9a-f]+\.css',
        )
        self.assertContains(response, 'href="/blog/"', html=False)
        self.assertContains(response, 'href="/blog/category/general/"', html=False)
        self.assertNotContains(response, '/en/blog/', html=False)
        self.assertNotContains(response, 'plausible.personal.example.com')
        self.assertNotContains(response, 'type="application/rss+xml"', html=False)
        self.assertContains(response, 'id="blog-image-dialog"', html=False)
        for hook in (
            'data-blog-share',
            'data-blog-copy-link',
            'data-blog-print',
            'data-blog-read-mode-toolbar',
            'data-blog-read-mode-entry',
            'data-blog-read-mode-exit',
            'data-blog-read-mode-progress',
        ):
            self.assertNotContains(response, hook, html=False)
        self.assertEqual(response.headers['X-Robots-Tag'], 'noindex, nofollow, noarchive')
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(response.headers['Content-Language'], 'en')

        easy_response = self.client.get(
            f'/admin/blog/blogpost/{post.pk}/preview/?site={EASY_MEALS_SITE}',
            HTTP_HOST='admin.localhost',
        )

        self.assertEqual(easy_response.status_code, 200)
        self.assertTemplateUsed(easy_response, 'easy_meals/blog/detail.html')
        self.assertContains(easy_response, 'This article is not available on the selected website.')
        self.assertRegex(easy_response.content.decode(), r'/static/blog/css/article\.[0-9a-f]+\.css')
        self.assertNotContains(easy_response, 'plausible.personal.example.com')
        self.assertNotContains(easy_response, 'type="application/rss+xml"', html=False)
        self.assertContains(
            easy_response,
            f'http://admin.localhost/admin/blog/blogpost/{post.pk}/change/',
            html=False,
        )

        vanta_response = self.client.get(
            f'/admin/blog/blogpost/{post.pk}/preview/?site={VANTA_SITE}',
            HTTP_HOST='admin.localhost',
        )

        self.assertEqual(vanta_response.status_code, 200)
        self.assertTemplateUsed(vanta_response, 'vanta_site/blog/detail.html')
        self.assertRegex(vanta_response.content.decode(), r'/static/vanta_site/css/blog\.[0-9a-f]+\.css')
        self.assertNotRegex(vanta_response.content.decode(), r'/static/blog/css/article\.[0-9a-f]+\.css')
        self.assertNotContains(vanta_response, 'plausible.personal.example.com')
        self.assertNotContains(vanta_response, 'type="application/rss+xml"', html=False)
        self.assertNotContains(vanta_response, 'data-blog-read-mode-toolbar', html=False)
        for preview_response in (response, easy_response, vanta_response):
            with self.subTest(template=preview_response.templates[0].name):
                self.assertNotContains(preview_response, 'data-blog-share', html=False)
                self.assertNotContains(preview_response, 'data-blog-copy-link', html=False)

    def test_preview_default_order_is_canonical_then_assigned_then_configured(self):
        post = self.create_post(
            title='Default preview article',
            slug='default-preview-article',
            canonical_site_slug=VANTA_SITE,
        )
        BlogPostPublication.objects.create(post=post, site_slug=PERSONAL_SITE)
        self.assertEqual(resolve_preview_site_slug(post), VANTA_SITE)

        post.canonical_site_slug = 'not-configured'
        self.assertEqual(resolve_preview_site_slug(post), PERSONAL_SITE)

        post.publications.all().delete()
        self.assertEqual(resolve_preview_site_slug(post), VANTA_SITE)

    def test_preview_rejects_invalid_site_without_rendering_a_shell(self):
        user = get_user_model().objects.create_superuser(
            username='invalid-preview-admin',
            email='invalid-preview@example.com',
            password='test-password',
        )
        post = self.create_post(title='Preview article', slug='invalid-preview-article')
        self.verify_admin_session(user)

        response = self.client.get(
            f'/admin/blog/blogpost/{post.pk}/preview/?site=not-configured',
            HTTP_HOST='admin.localhost',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'admin/blog/preview.html')
        self.assertContains(response, 'The selected website preview is not available.')
        self.assertContains(response, 'Select a valid choice')
        self.assertContains(response, 'class="blog-preview-unavailable-page"', html=False)
        self.assertRegex(response.content.decode(), r'/static/blog/css/article\.[0-9a-f]+\.css')
        self.assertNotContains(response, 'class="blog-article"', html=False)

    def test_preview_does_not_fall_back_for_a_configured_site_without_presentation(self):
        settings_sites = {
            slug: {
                'name': site.name,
                'template_namespace': site.template_namespace,
                'hosts': site.hosts,
                'admin_hosts': site.admin_hosts,
                'status_hosts': site.status_hosts,
                'route_namespaces': site.route_namespaces,
            }
            for slug, site in DEFAULT_SITE_DEFINITIONS.items()
        }
        settings_sites['missing_preview'] = {
            'name': 'Missing Preview',
            'template_namespace': 'missing/blog',
            'hosts': ('missing-preview.localhost',),
            'admin_hosts': (),
            'status_hosts': (),
            'route_namespaces': ('blog',),
        }
        user = get_user_model().objects.create_superuser(
            username='missing-preview-admin',
            email='missing-preview@example.com',
            password='test-password',
        )
        post = self.create_post(title='Preview article', slug='missing-preview-article')
        self.verify_admin_session(user)

        with override_settings(SITE_DEFINITIONS=settings_sites):
            response = self.client.get(
                f'/admin/blog/blogpost/{post.pk}/preview/?site=missing_preview',
                HTTP_HOST='admin.localhost',
            )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'admin/blog/preview.html')
        self.assertContains(response, 'The selected website preview is not available.')
        self.assertNotContains(response, 'class="blog-article"', html=False)

    def test_related_form_filters_targets_by_all_projected_sites(self):
        source = self.create_post(title='Source', slug='related-source')
        BlogPostPublication.objects.create(post=source, site_slug=PERSONAL_SITE)
        BlogPostPublication.objects.create(post=source, site_slug=VANTA_SITE)
        personal_only = self.create_post(title='Personal only', slug='personal-only')
        BlogPostPublication.objects.create(post=personal_only, site_slug=PERSONAL_SITE)
        both_sites = self.create_post(title='Both sites', slug='both-sites')
        BlogPostPublication.objects.create(post=both_sites, site_slug=PERSONAL_SITE)
        BlogPostPublication.objects.create(post=both_sites, site_slug=VANTA_SITE)

        form = BlogPostRelatedForm(source_post=source)

        self.assertEqual(list(form.fields['related_post'].queryset), [both_sites])
        self.assertIn('Vanta Admin', form.fields['related_post'].help_text)

    def test_related_form_rejects_manually_submitted_incompatible_target(self):
        source = self.create_post(title='Source', slug='manual-source')
        BlogPostPublication.objects.create(post=source, site_slug=PERSONAL_SITE)
        target = self.create_post(title='Easy only', slug='easy-only')
        BlogPostPublication.objects.create(post=target, site_slug=EASY_MEALS_SITE)

        form = BlogPostRelatedForm(
            data={'related_post': target.pk, 'position': 1},
            source_post=source,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('Choose an article available on the same website.', form.errors['related_post'])

    def test_related_formset_uses_projected_publication_sites(self):
        source = self.create_post(title='Source', slug='projected-source')
        BlogPostPublication.objects.create(post=source, site_slug=PERSONAL_SITE)
        target = self.create_post(title='Personal only', slug='projected-target')
        BlogPostPublication.objects.create(post=target, site_slug=PERSONAL_SITE)
        formset_class = inlineformset_factory(
            BlogPost,
            BlogPostRelated,
            form=BlogPostRelatedForm,
            formset=BlogRelatedInlineFormSet,
            fk_name='post',
            extra=0,
        )

        formset = formset_class(
            data={
                'publication_sites': [PERSONAL_SITE, VANTA_SITE],
                'related_links-TOTAL_FORMS': '1',
                'related_links-INITIAL_FORMS': '0',
                'related_links-MIN_NUM_FORMS': '0',
                'related_links-MAX_NUM_FORMS': '1000',
                'related_links-0-related_post': target.pk,
                'related_links-0-position': '1',
            },
            instance=source,
            prefix='related_links',
        )

        self.assertEqual(list(formset.empty_form.fields['related_post'].queryset), [])
        self.assertFalse(formset.is_valid())
        self.assertIn(
            'Choose an article available on the same website.',
            formset.errors[0]['related_post'],
        )

    def test_related_formset_accepts_target_compatible_with_projected_site_removal(self):
        source = self.create_post(title='Source', slug='projected-removal-source')
        BlogPostPublication.objects.create(post=source, site_slug=PERSONAL_SITE)
        BlogPostPublication.objects.create(post=source, site_slug=VANTA_SITE)
        target = self.create_post(title='Personal target', slug='projected-removal-target')
        BlogPostPublication.objects.create(post=target, site_slug=PERSONAL_SITE)
        formset_class = inlineformset_factory(
            BlogPost,
            BlogPostRelated,
            form=BlogPostRelatedForm,
            formset=BlogRelatedInlineFormSet,
            fk_name='post',
            extra=0,
        )

        formset = formset_class(
            data={
                'publication_sites': [PERSONAL_SITE],
                'related_links-TOTAL_FORMS': '1',
                'related_links-INITIAL_FORMS': '0',
                'related_links-MIN_NUM_FORMS': '0',
                'related_links-MAX_NUM_FORMS': '1000',
                'related_links-0-related_post': target.pk,
                'related_links-0-position': '1',
            },
            instance=source,
            prefix='related_links',
        )

        self.assertTrue(formset.is_valid(), formset.errors)

    def test_non_organizer_related_formset_ignores_forged_publication_sites(self):
        source = self.create_post(title='Source', slug='restricted-source')
        BlogPostPublication.objects.create(post=source, site_slug=PERSONAL_SITE)
        target = self.create_post(title='Vanta target', slug='restricted-target')
        BlogPostPublication.objects.create(post=target, site_slug=VANTA_SITE)
        formset_class = inlineformset_factory(
            BlogPost,
            BlogPostRelated,
            form=BlogPostRelatedForm,
            formset=BlogRelatedInlineFormSet,
            fk_name='post',
            extra=0,
        )

        formset = formset_class(
            data={
                'publication_sites': VANTA_SITE,
                'related_links-TOTAL_FORMS': '1',
                'related_links-INITIAL_FORMS': '0',
                'related_links-MIN_NUM_FORMS': '0',
                'related_links-MAX_NUM_FORMS': '1000',
                'related_links-0-related_post': target.pk,
                'related_links-0-position': '1',
            },
            instance=source,
            prefix='related_links',
            publication_sites_editable=False,
        )

        self.assertEqual(formset.source_site_slugs, {PERSONAL_SITE})
        self.assertFalse(formset.is_valid())
        self.assertIn(
            'Choose an article available on the same website.',
            formset.errors[0]['related_post'],
        )

    def test_related_formset_choice_query_count_does_not_grow_per_inline_row(self):
        one_link_source = self.create_post(title='One link', slug='one-link-source')
        many_links_source = self.create_post(title='Many links', slug='many-links-source')
        for source in (one_link_source, many_links_source):
            BlogPostPublication.objects.create(post=source, site_slug=PERSONAL_SITE)

        targets = []
        for index in range(4):
            target = self.create_post(
                title=f'Query target {index}',
                slug=f'query-target-{index}',
            )
            BlogPostPublication.objects.create(post=target, site_slug=PERSONAL_SITE)
            targets.append(target)

        BlogPostRelated.objects.create(post=one_link_source, related_post=targets[0])
        for target in targets:
            BlogPostRelated.objects.create(post=many_links_source, related_post=target)

        formset_class = inlineformset_factory(
            BlogPost,
            BlogPostRelated,
            form=BlogPostRelatedForm,
            formset=BlogRelatedInlineFormSet,
            fk_name='post',
            extra=0,
        )

        def rendered_query_count(source):
            with CaptureQueriesContext(connection) as queries:
                formset = formset_class(instance=source, prefix=f'related-links-{source.pk}')
                str(formset)
            return len(queries)

        self.assertEqual(
            rendered_query_count(one_link_source),
            rendered_query_count(many_links_source),
        )

    def test_target_site_changes_reject_incoming_relationship_conflicts(self):
        source = self.create_post(title='Source depends on both', slug='incoming-source')
        BlogPostPublication.objects.create(post=source, site_slug=PERSONAL_SITE)
        BlogPostPublication.objects.create(post=source, site_slug=VANTA_SITE)
        target = self.create_post(title='Target', slug='incoming-target')
        BlogPostPublication.objects.create(post=target, site_slug=PERSONAL_SITE)
        BlogPostPublication.objects.create(post=target, site_slug=VANTA_SITE)
        BlogPostRelated.objects.create(post=source, related_post=target)

        form = BlogPostAdminForm(
            data={
                'publication_sites': [PERSONAL_SITE],
                'title': target.title,
                'type': target.type,
                'summary': target.summary,
                'category': self.category.pk,
                'canonical_site_slug': PERSONAL_SITE,
            },
            instance=target,
        )

        self.assertFalse(form.is_valid())
        self.assertTrue(
            any(
                'Cannot remove a publication website' in error
                for error in form.errors['publication_sites']
            )
        )

    def test_site_fields_use_blog_enabled_select_choices(self):
        post_form = BlogPostAdminForm()
        publication_form = BlogPostPublicationForm()

        self.assertEqual(post_form.fields['canonical_site_slug'].widget.input_type, 'select')
        self.assertEqual(publication_form.fields['site_slug'].widget.input_type, 'select')
        self.assertIn(VANTA_SITE, dict(post_form.fields['canonical_site_slug'].choices))
        self.assertIn(VANTA_SITE, dict(publication_form.fields['site_slug'].choices))
        self.assertIn(VANTA_SITE, dict(post_form.fields['publication_sites'].choices))
        self.assertEqual(
            post_form.fields['publication_sites'].widget.attrs['class'],
            'blog-publication-picker__choices',
        )

    def test_publication_sites_are_first_and_publications_admin_is_not_registered(self):
        post_form = BlogPostAdminForm()

        self.assertEqual(next(iter(post_form.fields)), 'publication_sites')
        self.assertNotIn(BlogPostPublication, admin.site._registry)
        self.assertIn('publications__site_slug', self.admin_instance.list_filter)

    @skipUnless(settings.ENABLE_DEV_ADMIN, 'development admin is disabled')
    def test_changing_publication_sites_syncs_assignment_rows(self):
        user = get_user_model().objects.create_superuser(
            username='publication-admin',
            email='publication-admin@example.com',
            password='test-password',
        )
        author = AuthorProfile.objects.create(user=user, public_author_name='Publication author')
        post = self.create_post(title='Assigned article', slug='assigned-article', author=author)
        BlogPostPublication.objects.create(post=post, site_slug=PERSONAL_SITE)
        self.verify_admin_session(user)

        change_data = {
            'publication_sites': [VANTA_SITE],
            'title': post.title,
            'type': post.type,
            'summary': post.summary,
            'author': author.pk,
            'category': self.category.pk,
            'canonical_site_slug': '',
            '_save': 'Save',
            **{
                f'{model._meta.get_field("parent").remote_field.get_accessor_name()}-TOTAL_FORMS': '0'
                for model in BLOG_BLOCK_MODELS
            },
            **{
                f'{model._meta.get_field("parent").remote_field.get_accessor_name()}-INITIAL_FORMS': '0'
                for model in BLOG_BLOCK_MODELS
            },
            'related_links-TOTAL_FORMS': '0',
            'related_links-INITIAL_FORMS': '0',
        }
        response = self.client.post(
            f'/dev-admin/blog/blogpost/{post.pk}/change/',
            change_data,
            HTTP_HOST='dev-admin.localhost',
        )

        self.assertEqual(response.status_code, 302)
        post.refresh_from_db()
        self.assertIsNone(post.content_updated_at)
        self.assertEqual(
            list(post.publications.values_list('site_slug', flat=True)),
            [VANTA_SITE],
        )
        change_data['summary'] = 'Updated article summary'
        response = self.client.post(
            f'/dev-admin/blog/blogpost/{post.pk}/change/',
            change_data,
            HTTP_HOST='dev-admin.localhost',
        )

        self.assertEqual(response.status_code, 302)
        post.refresh_from_db()
        self.assertIsNotNone(post.content_updated_at)
        change_page = self.client.get(
            f'/dev-admin/blog/blogpost/{post.pk}/change/',
            HTTP_HOST='dev-admin.localhost',
        )
        self.assertContains(
            change_page,
            'id="id_publication_sites_0" checked',
            html=False,
        )

    def test_post_admin_hides_slug(self):
        user = get_user_model().objects.create_superuser(
            username='slug-admin',
            email='slug-admin@example.com',
            password='test-password',
        )
        post = self.create_post(title='Stable title', slug='stable-title')
        request = self.request_for(user)

        form_class = self.admin_instance.get_form(request, post)
        readonly_fields = self.admin_instance.get_readonly_fields(request, post)
        field_names = [
            field_name
            for _, section in self.admin_instance.fieldsets
            for field_name in section['fields']
        ]

        self.assertNotIn('slug', form_class.base_fields)
        self.assertNotIn('slug', readonly_fields)
        self.assertNotIn('slug', field_names)

    def test_change_page_renders_tag_checkboxes_and_bulk_controls(self):
        user = get_user_model().objects.create_superuser(
            username='tag-admin',
            email='tag-admin@example.com',
            password='test-password',
        )
        post = self.create_post(title='Tagged article', slug='tagged-article')
        tag = BlogTag.objects.create(name='Django', slug='django')
        post.tags.add(tag)
        self.verify_admin_session(user)

        response = self.client.get(
            f'/admin/blog/blogpost/{post.pk}/change/',
            HTTP_HOST='admin.localhost',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-blog-tag-picker')
        self.assertContains(response, 'data-blog-tag-action="add-all"')
        self.assertContains(response, 'data-blog-tag-action="remove-all"')
        self.assertContains(response, 'type="checkbox" name="tags" value="%s"' % tag.pk)
        self.assertContains(response, 'id="add_id_tags"')
        self.assertContains(response, '/static/blog/js/admin.')

    @skipUnless(settings.ENABLE_DEV_ADMIN, 'development admin is disabled')
    def test_tag_admin_requires_websites_and_generates_slug(self):
        user = get_user_model().objects.create_superuser(
            username='tag-manager',
            email='tag-manager@example.com',
            password='test-password',
        )
        self.verify_admin_session(user)

        add_response = self.client.get(
            '/dev-admin/blog/blogtag/add/',
            HTTP_HOST='dev-admin.localhost',
        )
        save_response = self.client.post(
            '/dev-admin/blog/blogtag/add/',
            {'name': 'Release notes', 'available_websites': [VANTA_SITE], '_save': 'Save'},
            HTTP_HOST='dev-admin.localhost',
        )

        self.assertEqual(add_response.status_code, 200)
        self.assertEqual(
            tuple(add_response.context['adminform'].form.fields),
            ('name', 'available_websites'),
        )
        self.assertEqual(save_response.status_code, 302)
        tag = BlogTag.objects.get(name='Release notes')
        self.assertEqual(tag.slug, 'release-notes')
        self.assertEqual(
            set(tag.websites.values_list('slug', flat=True)),
            {VANTA_SITE},
        )

    @skipUnless(settings.ENABLE_DEV_ADMIN, 'development admin is disabled')
    def test_tag_changelist_uses_add_tag_button_label(self):
        user = get_user_model().objects.create_superuser(
            username='tag-list-manager',
            email='tag-list-manager@example.com',
            password='test-password',
        )
        self.verify_admin_session(user)

        response = self.client.get(
            '/dev-admin/blog/blogtag/',
            HTTP_HOST='dev-admin.localhost',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Add tag')
        self.assertNotContains(response, 'New article')

    def test_public_article_cannot_remove_all_publications_and_body_blocks(self):
        post = self.create_post(
            status=BlogPost.Status.PUBLISHED,
            title='Public article',
            slug='public-article',
            summary='Summary',
            published_at=timezone.now(),
            canonical_site_slug=PERSONAL_SITE,
        )
        BlogPostPublication.objects.create(post=post, site_slug=PERSONAL_SITE)
        body = BlogRichTextBlock.objects.create(parent=post, region='main', body='<p>Body</p>')
        data = {
            'title': post.title,
            'slug': post.slug,
            'type': post.type,
            'summary': post.summary,
            'category': self.category.pk,
            'canonical_site_slug': PERSONAL_SITE,
        }
        for block_model in BLOG_BLOCK_MODELS:
            prefix = block_model._meta.get_field('parent').remote_field.get_accessor_name()
            data[f'{prefix}-TOTAL_FORMS'] = '0'
            data[f'{prefix}-INITIAL_FORMS'] = '0'
        rich_prefix = BlogRichTextBlock._meta.get_field('parent').remote_field.get_accessor_name()
        data.update({
            f'{rich_prefix}-TOTAL_FORMS': '1',
            f'{rich_prefix}-INITIAL_FORMS': '1',
            f'{rich_prefix}-0-id': str(body.pk),
            f'{rich_prefix}-0-body': body.body,
            f'{rich_prefix}-0-DELETE': 'on',
        })

        form = BlogPostAdminForm(data=data, instance=post)

        self.assertFalse(form.is_valid())
        self.assertIn('This field is required.', form.errors['author'])
        self.assertIn('A public article must remain assigned to at least one site.', form.non_field_errors())
        self.assertIn('A public article must retain at least one content block.', form.non_field_errors())
