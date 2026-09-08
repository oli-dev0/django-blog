from contextlib import contextmanager
from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core import management
from django.core.exceptions import PermissionDenied
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.blog.import_services import (
    BlogImportUnavailable,
    cleanup_staged_imports,
    discard_staged_import,
    get_pending_import,
    is_safe_import_path,
    mark_import_consumed,
    stage_import,
)
from apps.blog.models import BlogArticleImport, BlogArticleImportFile, BlogCategory, BlogPost


@contextmanager
def private_roots():
    with TemporaryDirectory() as media_root, TemporaryDirectory() as import_root:
        with override_settings(MEDIA_ROOT=media_root, BLOG_IMPORT_ROOT=import_root):
            yield Path(media_root), Path(import_root)


class BlogImportStagingTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username='import-owner')
        self.other_actor = get_user_model().objects.create_user(username='other-owner')

    def upload(self, name='images/hero.png', content=b'private image bytes'):
        return SimpleUploadedFile(name, content, content_type='image/png')

    def stage(self, *, now=None, files=None, source_filename='incoming/article.json'):
        return stage_import(
            actor=self.actor,
            source_filename=source_filename,
            payload={'article': {'title': 'Staged article'}, 'private': 'payload'},
            warnings=[{'code': 'extra_file', 'message': 'Safe warning'}],
            files=files if files is not None else [self.upload()],
            now=now,
        )

    def create_post(self, slug='completed-import'):
        category = BlogCategory.objects.create(name=f'Category {slug}', slug=f'category-{slug}')
        return BlogPost.objects.create(
            status=BlogPost.Status.DRAFT,
            title='Completed import',
            slug=slug,
            category=category,
        )

    def assert_private_root_empty(self, import_root):
        self.assertEqual(list(import_root.rglob('*')), [])

    def test_staged_files_use_owner_uuid_random_names_and_private_storage(self):
        with private_roots() as (media_root, import_root):
            payload = {'article': {'title': 'Original'}, 'nested': {'value': 1}}
            warnings = [{'code': 'extra_file', 'message': 'Original warning'}]
            session = stage_import(
                actor=self.actor,
                source_filename='uploads/source.json',
                payload=payload,
                warnings=warnings,
                files=[self.upload('nested/hero.PNG'), self.upload(r'photos\\second.webp')],
            )
            payload['article']['title'] = 'Mutated after staging'
            warnings[0]['message'] = 'Mutated after staging'

            session.refresh_from_db()
            storage = storages['blog_imports']
            staged_files = list(session.files.order_by('selected_name'))

            self.assertIsInstance(session.id, UUID)
            self.assertEqual(session.created_by_id, self.actor.pk)
            self.assertEqual(session.source_filename, 'source.json')
            self.assertEqual(session.payload['article']['title'], 'Original')
            self.assertEqual(session.warnings[0]['message'], 'Original warning')
            self.assertEqual([staged.selected_name for staged in staged_files], ['hero.PNG', 'second.webp'])
            self.assertIsNone(storage.base_url)

            for staged_file in staged_files:
                stored_path = Path(staged_file.file.name)
                absolute_path = Path(storage.path(staged_file.file.name)).resolve()
                self.assertEqual(stored_path.parts[0], str(session.id))
                self.assertNotEqual(stored_path.name, staged_file.selected_name)
                self.assertEqual(UUID(stored_path.stem).version, 4)
                self.assertTrue(absolute_path.is_relative_to(import_root.resolve()))
                self.assertFalse(absolute_path.is_relative_to(media_root.resolve()))
                with self.assertRaises(ValueError):
                    storage.url(staged_file.file.name)

    def test_staging_uses_configured_retention_and_rejects_duplicate_basenames(self):
        now = timezone.now().replace(microsecond=0)
        with private_roots() as (_media_root, import_root), override_settings(BLOG_IMPORT_RETENTION_HOURS=2):
            session = self.stage(now=now, files=[])
            self.assertEqual(session.expires_at, now + timedelta(hours=2))

            with self.assertRaises(ValueError):
                self.stage(
                    now=now,
                    files=[self.upload('first/duplicate.png'), self.upload('second/duplicate.png')],
                )

            self.assertEqual(BlogArticleImport.objects.count(), 1)
            self.assertEqual(BlogArticleImportFile.objects.count(), 0)
            self.assert_private_root_empty(import_root)

    def test_staging_requires_an_authenticated_actor(self):
        with private_roots():
            with self.assertRaises(PermissionDenied):
                stage_import(
                    actor=None,
                    source_filename='article.json',
                    payload={'article': {}},
                    files=[],
                )

    def test_partial_storage_failure_removes_rows_and_every_created_file(self):
        with private_roots() as (_media_root, import_root):
            storage = storages['blog_imports']
            original_save = storage.save
            calls = 0

            def save_first_file_then_fail(name, content, max_length=None):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError('storage unavailable')
                return original_save(name, content, max_length=max_length)

            with patch('apps.blog.import_services._private_storage', return_value=storage):
                with patch.object(storage, 'save', side_effect=save_first_file_then_fail):
                    with self.assertRaises(OSError):
                        self.stage(files=[self.upload('first.png'), self.upload('second.png')])

            self.assertEqual(BlogArticleImport.objects.count(), 0)
            self.assertEqual(BlogArticleImportFile.objects.count(), 0)
            self.assert_private_root_empty(import_root)

    def test_file_row_failure_removes_the_file_saved_before_the_row(self):
        with private_roots() as (_media_root, import_root):
            with patch.object(BlogArticleImportFile, 'save', side_effect=OSError('database unavailable')):
                with self.assertRaises(OSError):
                    self.stage()

            self.assertEqual(BlogArticleImport.objects.count(), 0)
            self.assertEqual(BlogArticleImportFile.objects.count(), 0)
            self.assert_private_root_empty(import_root)

    def test_pending_import_is_owner_bound_and_expires(self):
        with private_roots():
            now = timezone.now()
            session = self.stage(now=now)

            self.assertEqual(get_pending_import(actor=self.actor, import_id=session.id, now=now).pk, session.pk)
            with self.assertRaises(PermissionDenied):
                get_pending_import(actor=self.other_actor, import_id=session.id, now=now)
            with self.assertRaises(PermissionDenied):
                get_pending_import(actor=None, import_id=session.id, now=now)
            with self.assertRaises(BlogImportUnavailable):
                get_pending_import(actor=self.actor, import_id=session.id, now=session.expires_at)

    def test_safe_import_paths_are_uuid_direct_children_only(self):
        import_id = UUID('11111111-1111-4111-8111-111111111111')
        safe_name = f'{import_id}/22222222222222222222222222222222.png'

        self.assertTrue(is_safe_import_path(import_id, safe_name))
        for unsafe_name in (
            f'{import_id}/nested/file.png',
            f'{import_id}/../outside.png',
            f'{import_id}\\outside.png',
            f'other/{Path(safe_name).name}',
            'not-a-uuid/file.png',
        ):
            with self.subTest(unsafe_name=unsafe_name):
                self.assertFalse(is_safe_import_path(import_id, unsafe_name))

    def test_discard_removes_staged_rows_and_files_and_cancels_pending_access(self):
        with private_roots() as (_media_root, import_root):
            session = self.stage()
            result = discard_staged_import(actor=self.actor, import_id=session.id)

            self.assertEqual(result.rows_deleted, 1)
            self.assertEqual(result.files_deleted, 1)
            self.assertFalse(BlogArticleImport.objects.filter(pk=session.pk).exists())
            self.assertFalse(BlogArticleImportFile.objects.filter(import_session_id=session.pk).exists())
            self.assert_private_root_empty(import_root)
            with self.assertRaises(BlogImportUnavailable):
                get_pending_import(actor=self.actor, import_id=session.id)

    def test_discard_requires_an_authenticated_owner(self):
        with private_roots() as (_media_root, import_root):
            session = self.stage()

            with self.assertRaises(PermissionDenied):
                discard_staged_import(actor=self.other_actor, import_id=session.id)
            with self.assertRaises(PermissionDenied):
                discard_staged_import(actor=None, import_id=session.id)

            self.assertTrue(BlogArticleImport.objects.filter(pk=session.pk).exists())
            self.assertGreater(len(list(import_root.rglob('*'))), 0)

    def test_consumed_import_cannot_be_reused_and_success_cleanup_removes_files(self):
        with private_roots() as (_media_root, import_root):
            session = self.stage()
            completed_post = self.create_post()

            with self.captureOnCommitCallbacks(execute=True):
                mark_import_consumed(
                    actor=self.actor,
                    import_id=session.id,
                    completed_post=completed_post,
                )

            self.assertFalse(BlogArticleImport.objects.filter(pk=session.pk).exists())
            self.assertFalse(BlogArticleImportFile.objects.filter(import_session_id=session.pk).exists())
            self.assert_private_root_empty(import_root)
            with self.assertRaises(BlogImportUnavailable):
                get_pending_import(actor=self.actor, import_id=session.id)

    def test_consumed_import_stays_unavailable_after_completed_post_deletion(self):
        with private_roots() as (_media_root, import_root):
            session = self.stage()
            completed_post = self.create_post(slug='deleted-completed-import')
            storage = storages['blog_imports']

            with patch.object(storage, 'delete', side_effect=OSError('temporary cleanup failure')):
                with self.captureOnCommitCallbacks(execute=True):
                    mark_import_consumed(
                        actor=self.actor,
                        import_id=session.id,
                        completed_post=completed_post,
                    )

            completed_post.delete()
            session.refresh_from_db()
            self.assertIsNone(session.completed_post_id)
            self.assertIsNotNone(session.consumed_at)
            with self.assertRaises(BlogImportUnavailable):
                get_pending_import(actor=self.actor, import_id=session.id)

            result = cleanup_staged_imports(batch_size=1, now=timezone.now())

            self.assertEqual(result.consumed_deleted, 1)
            self.assertEqual(result.files_deleted, 1)
            self.assertFalse(BlogArticleImport.objects.filter(pk=session.pk).exists())
            self.assert_private_root_empty(import_root)

    def test_file_cleanup_failure_retains_consumed_metadata_for_retry(self):
        with private_roots() as (_media_root, import_root):
            session = self.stage()
            completed_post = self.create_post(slug='retryable-import')
            storage = storages['blog_imports']
            original_delete = storage.delete
            failed = False

            def fail_once(name):
                nonlocal failed
                if not failed:
                    failed = True
                    raise OSError('temporary cleanup failure')
                return original_delete(name)

            with patch.object(storage, 'delete', side_effect=fail_once):
                with self.captureOnCommitCallbacks(execute=True):
                    mark_import_consumed(
                        actor=self.actor,
                        import_id=session.id,
                        completed_post=completed_post,
                    )

            session.refresh_from_db()
            self.assertEqual(session.completed_post_id, completed_post.pk)
            self.assertTrue(session.files.exists())
            self.assertEqual(len(list(import_root.rglob('*'))), 2)

            result = cleanup_staged_imports(batch_size=1, now=timezone.now())

            self.assertEqual(result.consumed_deleted, 1)
            self.assertEqual(result.files_deleted, 1)
            self.assertEqual(result.file_failures, 0)
            self.assertFalse(BlogArticleImport.objects.filter(pk=session.pk).exists())
            self.assert_private_root_empty(import_root)

    def test_owner_deletion_retains_staging_and_cleanup_metadata_until_expiry(self):
        with private_roots() as (_media_root, import_root):
            session = self.stage()
            session.permanent_cleanup_paths = [
                'blog/renditions/2026/08/retry-cleanup.webp',
            ]
            session.save(update_fields=['permanent_cleanup_paths'])

            self.actor.delete()

            session.refresh_from_db()
            self.assertIsNone(session.created_by_id)
            self.assertEqual(
                session.permanent_cleanup_paths,
                ['blog/renditions/2026/08/retry-cleanup.webp'],
            )
            self.assertTrue(session.files.exists())
            with self.assertRaises(PermissionDenied):
                get_pending_import(actor=self.other_actor, import_id=session.id)

            result = cleanup_staged_imports(
                batch_size=1,
                now=session.expires_at,
            )

            self.assertEqual(result.expired_deleted, 1)
            self.assertEqual(result.files_deleted, 1)
            self.assertEqual(result.file_failures, 0)
            self.assertFalse(BlogArticleImport.objects.filter(pk=session.pk).exists())
            self.assert_private_root_empty(import_root)

    def test_cleanup_deletes_expired_rows_in_bounded_batches(self):
        with private_roots() as (_media_root, import_root):
            now = timezone.now()
            sessions = [self.stage(now=now - timedelta(days=2, minutes=index)) for index in range(3)]
            fresh = self.stage(now=now)

            first = cleanup_staged_imports(batch_size=2, now=now)
            self.assertEqual(first.expired_deleted, 2)
            self.assertEqual(first.consumed_deleted, 0)
            self.assertEqual(first.files_deleted, 2)
            self.assertTrue(BlogArticleImport.objects.filter(pk=sessions[0].pk).exists())
            self.assertTrue(BlogArticleImport.objects.filter(pk=fresh.pk).exists())

            second = cleanup_staged_imports(batch_size=2, now=now)
            self.assertEqual(second.expired_deleted, 1)
            self.assertFalse(BlogArticleImport.objects.filter(pk=sessions[0].pk).exists())
            self.assertTrue(BlogArticleImport.objects.filter(pk=fresh.pk).exists())
            self.assertEqual(len(list(import_root.rglob('*'))), 2)

    def test_cleanup_command_reports_counts_without_private_content(self):
        with private_roots() as (_media_root, import_root):
            session = self.stage(
                now=timezone.now() - timedelta(days=2),
                source_filename='private-secret-payload.json',
                files=[self.upload('private-secret-image.png')],
            )
            output = StringIO()

            management.call_command('cleanup_blog_imports', '--batch-size', '1', stdout=output)

            self.assertIn('Deleted 1 expired and 0 consumed import rows', output.getvalue())
            self.assertNotIn('private-secret-payload.json', output.getvalue())
            self.assertNotIn('private-secret-image.png', output.getvalue())
            self.assertNotIn('payload', output.getvalue())
            self.assertFalse(BlogArticleImport.objects.filter(pk=session.pk).exists())
            self.assert_private_root_empty(import_root)

    def test_staging_file_selected_name_and_completed_post_are_unique_per_contract(self):
        with private_roots():
            first = self.stage(files=[])
            second = self.stage(files=[])
            BlogArticleImportFile.objects.create(
                import_session=first,
                selected_name='same.png',
                file=f'{first.id}/first.png',
            )
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    BlogArticleImportFile.objects.create(
                        import_session=first,
                        selected_name='same.png',
                        file=f'{first.id}/second.png',
                    )

            completed_post = self.create_post(slug='unique-completed-post')
            first.completed_post = completed_post
            first.save(update_fields=['completed_post'])
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    second.completed_post = completed_post
                    second.save(update_fields=['completed_post'])
