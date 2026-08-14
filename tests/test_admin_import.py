from contextlib import contextmanager
from datetime import timedelta
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice
from PIL import Image

from apps.blog.import_services import validate_and_stage_blog_import
from apps.blog.models import (
    AuthorProfile,
    BlogArticleImport,
    BlogCategory,
    BlogPost,
    BlogPostPublication,
    BlogSite,
    BlogTag,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
BLOG_ADMIN_CSS = REPO_ROOT / 'apps/blog/static/blog/css/admin.css'
BLOG_ADMIN_JS = REPO_ROOT / 'apps/blog/static/blog/js/admin.js'


@contextmanager
def import_workspace():
    with TemporaryDirectory() as media_root, TemporaryDirectory() as import_root:
        with override_settings(MEDIA_ROOT=media_root, BLOG_IMPORT_ROOT=Path(import_root)):
            yield Path(media_root), Path(import_root)


def source_upload(payload, name='article.json'):
    return SimpleUploadedFile(
        name,
        json.dumps(payload).encode('utf-8'),
        content_type='application/json',
    )


def image_upload(name='hero.png', content=None):
    if content is None:
        output = BytesIO()
        Image.new('RGB', (2, 2), 'white').save(output, format='PNG')
        content = output.getvalue()
    return SimpleUploadedFile(name, content, content_type='image/png')


def import_payload(*, title='Imported article', with_image=False, unsafe_html=False):
    body = '<p>Keep <strong>bold</strong>. <script>secret()</script> Source</p>'
    if unsafe_html:
        body = '<p>Keep <strong>bold</strong>. <script>secret()</script> <a href="https://example.com" onclick="bad()">Source</a></p>'
    article = {
        'title': title,
        'slug': 'imported-article',
        'type': 'guide',
        'summary': 'A useful imported draft.',
        'author': {'slug': 'oli'},
        'category': {'slug': 'development'},
        'tags': [{'slug': 'django'}],
        'publication_sites': ['vanta_admin'],
        'canonical_site': 'vanta_admin',
        'seo': {
            'title': 'Imported search title',
            'description': 'Imported search description.',
        },
        'blocks': [
            {'type': 'heading', 'level': 2, 'text': 'First section'},
            {'type': 'rich_text', 'body': body},
        ],
    }
    payload = {
        'format': 'blog-article-import',
        'version': 1,
        'article': article,
        'assets': [],
        'comparisons': [],
    }
    if with_image:
        article['featured_image'] = 'hero'
        article['blocks'].append({'type': 'image', 'asset_id': 'hero'})
        payload['assets'] = [
            {
                'id': 'hero',
                'file': 'images/hero.png',
                'name': 'Hero image',
                'alt_text': 'A hero image',
            },
            {
                'id': 'unused',
                'file': 'images/unused.png',
                'name': 'Unused image',
                'alt_text': 'An unused image',
            },
        ]
    return payload


class BlogAdminImportTests(TestCase):
    admin_hosts = {
        'admin': 'admin.localhost',
        'dev_admin': 'dev-admin.localhost',
    }

    def setUp(self):
        if not settings.ENABLE_DEV_ADMIN:
            self.admin_hosts = {'admin': 'admin.localhost'}
        user_model = get_user_model()
        self.author = AuthorProfile.objects.create(
            user=user_model.objects.create_user(username='article-author'),
            public_author_name='Oli',
            slug='oli',
        )
        self.category = BlogCategory.objects.create(name='Development', slug='development')
        self.tag = BlogTag.objects.create(name='Django', slug='django')
        site, _created = BlogSite.objects.get_or_create(slug='vanta_admin')
        self.category.websites.add(site)
        self.tag.websites.add(site)
        self.editor = self.make_user(
            'complete-editor',
            'add_blogpost',
            'change_blogpost',
            'organize_blogpost',
            'add_blogheadingblock',
            'add_blogrichtextblock',
            'add_blogembedsharingblock',
            'add_blogimageblock',
            'add_blogimage',
        )

    def make_user(self, username, *codenames):
        user = get_user_model().objects.create_user(username=username)
        user.is_staff = True
        user.save(update_fields=['is_staff'])
        permissions = Permission.objects.filter(
            content_type__app_label='blog',
            codename__in=codenames,
        )
        user.user_permissions.add(*permissions)
        return user

    def verify_admin_session(self, user):
        device = TOTPDevice.objects.create(user=user, name=f'{user.username}-device', confirmed=True)
        self.client.force_login(user)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()

    def admin_url(self, namespace, name, *, import_id=None):
        if import_id is None:
            return reverse(f'{namespace}:{name}')
        return reverse(f'{namespace}:{name}', kwargs={'import_id': import_id})

    def admin_request(self, namespace, method, path, **kwargs):
        return getattr(self.client, method)(path, HTTP_HOST=self.admin_hosts[namespace], **kwargs)

    def assert_private_headers(self, response):
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow, noarchive')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'], 'same-origin')
        self.assertEqual(response['Content-Language'], 'en')

    def stage(self, actor=None, payload=None, image_files=()):
        actor = actor or self.editor
        with patch.object(actor, 'has_perm', return_value=True):
            return validate_and_stage_blog_import(
                source_upload(payload or import_payload()),
                list(image_files),
                actor,
            )

    def review_url(self, namespace, import_session):
        return self.admin_url(namespace, 'blogpost_import_review', import_id=import_session.pk)

    def review_data(self, *, author=None, category=None, tag=None, sites=None, canonical=None):
        return {
            'action': 'create',
            'author': (author or self.author).pk,
            'category': (category or self.category).pk,
            'tags': [(tag or self.tag).pk],
            'publication_sites': sites or ['vanta_admin'],
            'canonical_site': canonical or 'vanta_admin',
            'draft_confirmation': 'on',
        }

    def test_import_routes_render_with_private_headers_under_both_admin_namespaces(self):
        self.verify_admin_session(self.editor)

        for namespace in self.admin_hosts:
            with self.subTest(namespace=namespace):
                response = self.admin_request(
                    namespace,
                    'get',
                    self.admin_url(namespace, 'blogpost_import'),
                )

                self.assertEqual(response.status_code, 200)
                self.assert_private_headers(response)
                self.assertContains(response, 'Import blog article')
                self.assertContains(response, 'Article JSON file')
                self.assertContains(response, 'Local images')
                self.assertContains(response, 'Validate import')
                self.assertContains(response, 'Importing creates a separate new draft.')
                html = response.content.decode()
                self.assertEqual(html.count('<h1'), 1)
                self.assertIn('data-blog-import-page', html)
                self.assertIn('data-blog-import-form', html)
                self.assertIn('for="id_source_file"', html)
                self.assertIn('for="id_image_files"', html)
                self.assertIn('id="id_source_file_helptext"', html)
                self.assertIn('id="id_image_files_helptext"', html)
                self.assertIn('accept=".json,application/json"', html)
                self.assertIn('multiple', html)
                self.assertEqual(html.count('data-blog-import-file-field'), 2)
                self.assertEqual(html.count('data-blog-import-selected-files'), 2)
                self.assertIn('/static/blog/css/admin.', html)
                self.assertIn('/static/blog/js/admin.', html)
                self.assertIn('enctype="multipart/form-data"', html)
                self.assertIn('name="csrfmiddlewaretoken"', html)
                if namespace == 'dev_admin':
                    self.assertNotIn('href="/admin/blog/blogpost/import/"', html)
                    self.assertIn('href="/dev-admin/blog/blogpost/"', html)

    def test_import_requires_all_core_permissions_and_is_hidden_from_partial_and_view_only_users(self):
        partial = self.make_user('partial-editor', 'add_blogpost', 'change_blogpost')
        view_only = self.make_user('view-only-editor', 'view_blogpost')

        for user in (partial, view_only):
            with self.subTest(user=user.username):
                self.verify_admin_session(user)
                response = self.admin_request(
                    'admin',
                    'get',
                    self.admin_url('admin', 'blogpost_import'),
                )
                self.assertEqual(response.status_code, 403)

                changelist = self.admin_request(
                    'admin',
                    'get',
                    reverse('admin:blog_blogpost_changelist'),
                )
                self.assertEqual(changelist.status_code, 200)
                self.assertNotContains(changelist, 'Import article')

                if user is partial:
                    quick_start = self.admin_request(
                        'admin',
                        'get',
                        reverse('admin:blog_blogpost_add'),
                    )
                    self.assertEqual(quick_start.status_code, 200)
                    self.assertNotContains(quick_start, 'Import article')

        self.verify_admin_session(self.editor)
        changelist = self.admin_request(
            'admin',
            'get',
            reverse('admin:blog_blogpost_changelist'),
        )
        self.assertContains(changelist, reverse('admin:blogpost_import'))

    @skipUnless(settings.ENABLE_DEV_ADMIN, 'development admin is disabled')
    def test_import_link_is_only_on_changelist_and_uses_the_active_admin_namespace(self):
        self.verify_admin_session(self.editor)

        quick_start = self.admin_request(
            'dev_admin',
            'get',
            reverse('dev_admin:blog_blogpost_add'),
        )
        changelist = self.admin_request(
            'dev_admin',
            'get',
            reverse('dev_admin:blog_blogpost_changelist'),
        )

        self.assertEqual(quick_start.status_code, 200)
        self.assertEqual(changelist.status_code, 200)
        self.assertNotContains(quick_start, 'Import article')
        self.assertContains(changelist, reverse('dev_admin:blogpost_import'))
        self.assertNotContains(quick_start, 'href="/admin/blog/blogpost/import/"')
        self.assertNotContains(changelist, 'href="/admin/blog/blogpost/import/"')

    def test_anonymous_user_keeps_existing_admin_authentication_boundary(self):
        response = self.admin_request(
            'admin',
            'get',
            self.admin_url('admin', 'blogpost_import'),
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_invalid_uploads_stay_on_upload_page_with_grouped_safe_errors_and_no_stage(self):
        self.verify_admin_session(self.editor)
        upload_url = self.admin_url('admin', 'blogpost_import')

        cases = (
            (
                'missing source',
                {'csrfmiddlewaretoken': 'test-token'},
                ('Article JSON file', 'Choose an article file to import.'),
            ),
            (
                'invalid source',
                {'source_file': SimpleUploadedFile('article.json', b'{not-json', content_type='application/json')},
                ('Import package', 'The article file is not valid JSON.'),
            ),
            (
                'missing image',
                {'source_file': source_upload(import_payload(with_image=True))},
                ('Images and files', 'The article references a local image that was not selected.'),
            ),
            (
                'unsupported image',
                {
                    'source_file': source_upload(import_payload(with_image=True)),
                    'image_files': [image_upload('hero.png', b'not an image')],
                },
                ('Images and files', 'This image cannot be imported. Choose a supported local image file.'),
            ),
        )

        for label, files, expected in cases:
            with self.subTest(label=label), import_workspace() as (media_root, import_root):
                response = self.admin_request('admin', 'post', upload_url, data=files)

                self.assertEqual(response.status_code, 200)
                self.assert_private_headers(response)
                self.assertContains(response, expected[0])
                self.assertContains(response, expected[1])
                self.assertNotContains(response, 'Traceback')
                self.assertNotContains(response, 'not-json')
                self.assertEqual(BlogArticleImport.objects.count(), 0)
                self.assertEqual(BlogPost.objects.count(), 0)
                self.assertEqual(list(import_root.rglob('*')), [])

    def test_invalid_upload_error_summary_is_linked_focusable_and_safe(self):
        self.verify_admin_session(self.editor)

        response = self.admin_request(
            'admin',
            'post',
            self.admin_url('admin', 'blogpost_import'),
            data={'csrfmiddlewaretoken': 'test-token'},
        )

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('id="import-error-summary"', html)
        self.assertIn('data-blog-import-error-summary', html)
        self.assertIn('role="alert"', html)
        self.assertIn('tabindex="-1"', html)
        self.assertIn('aria-labelledby="import-error-summary-title"', html)
        self.assertIn('href="#id_source_file"', html)
        self.assertIn('Article JSON file', html)
        self.assertIn('Choose an article file to import.', html)
        self.assertNotIn('Traceback', html)

    def test_import_admin_assets_keep_progressive_enhancements_scoped_and_theme_aware(self):
        css = BLOG_ADMIN_CSS.read_text(encoding='utf-8')
        js = BLOG_ADMIN_JS.read_text(encoding='utf-8')
        import_css_start = css.index('body.blog-import,')
        import_css = css[import_css_start:]

        for token in (
            '--page-bg',
            '--body-bg',
            '--surface',
            '--darkened-bg',
            '--text',
            '--body-fg',
            '--accent',
            '--link-fg',
            '--focus',
        ):
            with self.subTest(token=token):
                self.assertIn(token, import_css)

        for selector in (
            'html[data-theme="light"] body.blog-import',
            'html[data-theme="dark"] body.blog-import',
            'body.theme-light.blog-import',
            'body.theme-dark.blog-import',
            '@media (prefers-color-scheme: dark)',
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, import_css)

        for hook in (
            'overflow-wrap: anywhere;',
            'word-break: break-word;',
            ':focus-visible',
            'outline: 3px solid var(--blog-import-focus);',
            'border-inline-start: 4px solid var(--blog-import-warning);',
            '@media (max-width: 640px)',
            'grid-template-columns: minmax(0, 1fr);',
            'flex-direction: column;',
            'display: block;',
            'content: attr(data-label);',
            'body.blog-import-review #content-main .blog-import__choice-row',
            'body.blog-import-review #content-main .blog-import__choice-row > label',
            'body.blog-import-review #content-main .blog-import__category-row',
            'body.blog-import-review #content-main .blog-import__section',
            'body.blog-import-review #content-main .blog-import__section > h2',
            'body.blog-import-review #content-main .blog-import__source-value',
            'body.blog-import-review #content-main .blog-import__muted-separator',
            'color: var(--blog-import-muted);',
            'border-radius: 10px;',
            'font-size: .94rem;',
            'font-weight: 860;',
            'font-size: 1.05rem;',
            'font-weight: 700;',
        ):
            with self.subTest(hook=hook):
                self.assertIn(hook, import_css)

        self.assertNotIn('.blog-import__', css[:import_css_start])
        self.assertIn('#fff7e6', import_css)
        self.assertIn('#302817', import_css)
        self.assertIn('#111213', import_css)
        self.assertIn('#62b96f', import_css)

        for hook in (
            'function initializeImportFileNames(root)',
            "root.querySelectorAll('[data-blog-import-file-field]')",
            'const files = [...(input.files || [])];',
            'item.textContent = file.name;',
            'output.hidden = files.length === 0;',
            'function initializeImportPage(root)',
            'errorSummary.focus();',
            "document.querySelectorAll('[data-blog-import-page]').forEach(initializeImportPage);",
        ):
            with self.subTest(hook=hook):
                self.assertIn(hook, js)

        import_js_start = js.index('function initializeImportFileNames')
        self.assertNotIn(
            "document.querySelectorAll('[data-blog-import-file-field]')",
            js[:import_js_start],
        )
        self.assertIn("root.querySelectorAll('[data-blog-comparison-select]')", js)
        self.assertIn("document.querySelectorAll('[data-blog-tag-picker]')", js)
        self.assertIn("document.addEventListener('prose-editor:ready'", js)

    def test_storage_failure_is_safe_and_does_not_leave_a_stage(self):
        self.verify_admin_session(self.editor)
        upload_url = self.admin_url('admin', 'blogpost_import')

        with import_workspace() as (_media_root, import_root):
            with patch(
                'apps.blog.admin.validate_and_stage_blog_import',
                side_effect=OSError('private storage secret path'),
            ):
                response = self.admin_request(
                    'admin',
                    'post',
                    upload_url,
                    data={'source_file': source_upload(import_payload())},
                )

            self.assertEqual(response.status_code, 200)
            self.assert_private_headers(response)
            self.assertContains(response, 'The import package could not be prepared for review.')
            self.assertNotContains(response, 'private storage secret path')
            self.assertEqual(BlogArticleImport.objects.count(), 0)
            self.assertEqual(list(import_root.rglob('*')), [])

    def test_valid_upload_uses_prg_and_creates_an_owner_bound_review(self):
        self.verify_admin_session(self.editor)

        for namespace in self.admin_hosts:
            with self.subTest(namespace=namespace), import_workspace() as (_media_root, import_root):
                response = self.admin_request(
                    namespace,
                    'post',
                    self.admin_url(namespace, 'blogpost_import'),
                    data={'source_file': source_upload(import_payload())},
                )

                self.assertEqual(response.status_code, 302, response.content.decode())
                self.assert_private_headers(response)
                import_session = BlogArticleImport.objects.get()
                self.assertEqual(response.url, self.review_url(namespace, import_session))
                self.assertEqual(import_session.created_by, self.editor)
                self.assertNotEqual(response.url, '/admin/blog/blogpost/import/')
                self.assertEqual(BlogPost.objects.count(), 0)
                self.assertTrue(list(import_root.rglob('*')) == [])
                BlogArticleImport.objects.all().delete()

    def test_review_refresh_is_read_only_and_displays_safe_ordered_content_images_and_warnings(self):
        self.verify_admin_session(self.editor)
        existing = BlogPost.objects.create(
            title='Imported article',
            slug='imported-article',
            category=self.category,
        )
        BlogPostPublication.objects.create(post=existing, site_slug='vanta_admin')

        with import_workspace() as (_media_root, import_root):
            payload = import_payload(with_image=True, unsafe_html=True)
            payload['article']['blocks'].append(
                {
                    'type': 'embed_sharing',
                    'platform': 'youtube',
                    'url': 'https://youtu.be/dQw4w9WgXcQ?si=tracking',
                    'caption': 'A useful embedded video',
                }
            )
            payload['article']['tags'].append({'slug': 'unresolved-tag'})
            session = self.stage(
                payload=payload,
                image_files=[image_upload('hero.png'), image_upload('extra.png')],
            )
            before_stage = BlogArticleImport.objects.values('payload', 'warnings', 'expires_at').get(pk=session.pk)
            before_posts = BlogPost.objects.count()

            response = self.admin_request('admin', 'get', self.review_url('admin', session))
            refreshed = self.admin_request('admin', 'get', self.review_url('admin', session))

            for page in (response, refreshed):
                self.assertEqual(page.status_code, 200)
                self.assert_private_headers(page)
                self.assertEqual(page.content.decode().count('<h1'), 1)
                self.assertContains(page, 'Review imported article')
                self.assertContains(page, 'Imported article')
                self.assertContains(page, 'oli')
                self.assertContains(page, '<dt>Author</dt>')
                self.assertContains(page, 'Oli')
                self.assertContains(page, '<span class="blog-import__source-value">(oli)</span>')
                self.assertNotContains(page, 'Author — source')
                self.assertNotContains(page, 'Author — resolved')
                self.assertContains(page, '<dt>Tags</dt>')
                self.assertNotContains(page, '<dd>Django <span class="blog-import__source-value">(django)</span></dd>')
                self.assertContains(page, 'Development')
                self.assertContains(page, 'Django')
                self.assertContains(page, 'data-blog-tag-picker')
                self.assertContains(page, 'data-blog-tag-action="add-all"')
                self.assertContains(page, 'data-blog-tag-action="remove-all"')
                self.assertContains(page, 'type="checkbox" name="tags" value="%s"' % self.tag.pk)
                self.assertContains(page, 'class="form-row blog-import__choice-row"')
                self.assertContains(page, 'class="form-row blog-import__category-row"')
                self.assertContains(page, 'Vanta Admin')
                self.assertContains(page, 'Imported search title')
                self.assertContains(page, 'Hero image')
                self.assertContains(page, 'A hero image')
                self.assertContains(page, 'hero.png')
                self.assertContains(page, 'Referenced images and validation results')
                self.assertContains(page, 'Valid')
                self.assertContains(page, 'Warnings')
                self.assertContains(page, 'An existing article may match')
                self.assertContains(page, 'not referenced by the article')
                self.assertContains(page, 'not used by the article')
                self.assertContains(page, 'aria-labelledby="import-warning-title"')
                self.assertContains(page, 'class="messagelist__message blog-import__warning"')
                self.assertContains(page, 'class="blog-import__status blog-import__status--valid"')
                self.assertContains(page, 'data-label="Selected file"')
                self.assertContains(page, 'No existing tag matches')
                self.assertContains(page, 'Heading')
                self.assertContains(page, 'Rich text')
                self.assertContains(page, 'Keep bold.')
                self.assertContains(page, 'Embed sharing')
                self.assertContains(page, 'A useful embedded video')
                self.assertNotContains(page, '<strong>bold</strong>')
                self.assertNotContains(page, '<script>secret()</script>')
                self.assertNotContains(page, '<iframe')
                self.assertNotContains(page, 'onclick="bad()"')
                self.assertNotContains(page, str(import_root))
                self.assertNotContains(page, 'private_blog_imports')
                self.assertNotContains(page, '/media/blog/')
                html = page.content.decode()
                self.assertLess(html.index('Block 1'), html.index('Block 2'))
                self.assertLess(html.index('Block 2'), html.index('Block 3'))
                form_hook = html.index('data-blog-import-form')
                form_start = html.rfind('<form', 0, form_hook)
                form_end = html.index('</form>', form_hook)
                for field_name in (
                    'author',
                    'category',
                    'tags',
                    'publication_sites',
                    'canonical_site',
                    'draft_confirmation',
                ):
                    with self.subTest(field_name=field_name):
                        field_position = html.index(f'name="{field_name}"')
                        self.assertGreater(field_position, form_start)
                        self.assertLess(field_position, form_end)

            after_stage = BlogArticleImport.objects.values('payload', 'warnings', 'expires_at').get(pk=session.pk)
            self.assertEqual(before_stage, after_stage)
            self.assertEqual(BlogPost.objects.count(), before_posts)

    def test_foreign_owner_cannot_view_or_submit_a_review(self):
        other = self.make_user(
            'other-editor',
            'add_blogpost',
            'change_blogpost',
            'organize_blogpost',
            'add_blogheadingblock',
            'add_blogrichtextblock',
        )
        self.verify_admin_session(self.editor)

        with import_workspace():
            session = self.stage()
            review_url = self.review_url('admin', session)
            self.verify_admin_session(other)

            response = self.admin_request('admin', 'get', review_url)
            submit = self.admin_request('admin', 'post', review_url, data=self.review_data())

            self.assertEqual(response.status_code, 403)
            self.assertEqual(submit.status_code, 403)
            self.assertTrue(BlogArticleImport.objects.filter(pk=session.pk).exists())
            self.assertEqual(BlogPost.objects.count(), 0)

    def test_expired_or_missing_review_redirects_without_leaking_content(self):
        self.verify_admin_session(self.editor)

        with import_workspace():
            session = self.stage()
            session.expires_at = timezone.now() - timedelta(minutes=1)
            session.save(update_fields=['expires_at'])
            expired = self.admin_request('admin', 'get', self.review_url('admin', session))
            missing = self.admin_request(
                'admin',
                'get',
                self.admin_url('admin', 'blogpost_import_review', import_id='11111111-1111-4111-8111-111111111111'),
            )

            import_url = self.admin_url('admin', 'blogpost_import')
            for response in (expired, missing):
                self.assertEqual(response.status_code, 302)
                self.assert_private_headers(response)
                self.assertEqual(response.url, import_url)
                self.assertNotIn('Imported article', response.content.decode())
        self.assertEqual(BlogPost.objects.count(), 0)

    def test_new_duplicate_warning_requires_updated_confirmation_before_create(self):
        self.verify_admin_session(self.editor)

        with import_workspace():
            session = self.stage()
            review_url = self.review_url('admin', session)
            initial_review = self.admin_request('admin', 'get', review_url)
            self.assertEqual(initial_review.status_code, 200)

            BlogPost.objects.create(
                title='Imported article',
                slug='new-existing-article',
                category=self.category,
            )
            first_submit = self.admin_request(
                'admin',
                'post',
                review_url,
                data=self.review_data(),
            )

            self.assertEqual(first_submit.status_code, 200)
            self.assertContains(first_submit, 'An existing article may match')
            self.assertContains(first_submit, 'Review the updated warnings before creating the draft.')
            self.assertContains(first_submit, 'name="warnings_acknowledged"')
            self.assertEqual(BlogPost.objects.filter(title='Imported article').count(), 1)
            acknowledgement = first_submit.context['warning_acknowledgement']
            self.assertTrue(acknowledgement)

            second_submit = self.admin_request(
                'admin',
                'post',
                review_url,
                data={**self.review_data(), 'warnings_acknowledged': acknowledgement},
            )

            self.assertEqual(second_submit.status_code, 302)
            self.assertEqual(BlogPost.objects.filter(title='Imported article').count(), 2)

    def test_warning_acknowledgement_is_bound_to_the_exact_displayed_warnings(self):
        self.verify_admin_session(self.editor)

        with import_workspace():
            session = self.stage()
            review_url = self.review_url('admin', session)
            BlogPost.objects.create(
                title='Imported article',
                slug='first-existing-article',
                category=self.category,
            )
            first_submit = self.admin_request(
                'admin',
                'post',
                review_url,
                data=self.review_data(),
            )
            stale_acknowledgement = first_submit.context['warning_acknowledgement']

            BlogPost.objects.create(
                title='Imported article',
                slug='second-existing-article',
                category=self.category,
            )
            stale_submit = self.admin_request(
                'admin',
                'post',
                review_url,
                data={
                    **self.review_data(),
                    'warnings_acknowledged': stale_acknowledgement,
                },
            )

            self.assertEqual(stale_submit.status_code, 200)
            self.assertContains(
                stale_submit,
                'Review the updated warnings before creating the draft.',
            )
            self.assertEqual(BlogPost.objects.filter(title='Imported article').count(), 2)
            current_acknowledgement = stale_submit.context['warning_acknowledgement']
            self.assertNotEqual(current_acknowledgement, stale_acknowledgement)

            confirmed_submit = self.admin_request(
                'admin',
                'post',
                review_url,
                data={
                    **self.review_data(),
                    'warnings_acknowledged': current_acknowledgement,
                },
            )

            self.assertEqual(confirmed_submit.status_code, 302)
            self.assertEqual(BlogPost.objects.filter(title='Imported article').count(), 3)

    def test_invalid_reviewed_choices_return_field_errors_without_creating_a_draft(self):
        self.verify_admin_session(self.editor)

        with import_workspace():
            session = self.stage()
            review_url = self.review_url('admin', session)
            data = self.review_data()
            data['author'] = 999999
            data['canonical_site'] = 'my_website'
            response = self.admin_request('admin', 'post', review_url, data=data)

            self.assertEqual(response.status_code, 200)
            self.assert_private_headers(response)
            self.assertContains(response, 'Select a valid choice')
            self.assertContains(response, 'Canonical site')
            self.assertTrue(BlogArticleImport.objects.filter(pk=session.pk).exists())
            self.assertEqual(BlogPost.objects.count(), 0)

    def test_create_redirects_with_message_and_a_second_submission_cannot_create_another_draft(self):
        self.verify_admin_session(self.editor)

        with import_workspace():
            session = self.stage()
            review_url = self.review_url('admin', session)
            response = self.admin_request(
                'admin',
                'post',
                review_url,
                data=self.review_data(),
            )

            self.assertEqual(response.status_code, 302)
            post = BlogPost.objects.get(slug='imported-article')
            self.assertEqual(response.url, reverse('admin:blog_blogpost_change', args=[post.pk]))
            messages = [str(message) for message in get_messages(response.wsgi_request)]
            self.assertIn('Draft imported successfully. Review the article before publishing.', messages)
            self.assertEqual(BlogPost.objects.filter(title='Imported article').count(), 1)
            self.assertEqual(post.status, BlogPost.Status.DRAFT)

            repeat = self.admin_request(
                'admin',
                'post',
                review_url,
                data=self.review_data(),
            )

            self.assertEqual(repeat.status_code, 302)
            self.assertEqual(repeat.url, self.admin_url('admin', 'blogpost_import'))
            self.assertEqual(BlogPost.objects.filter(title='Imported article').count(), 1)

    def test_change_files_discards_staging_without_creating_permanent_data(self):
        self.verify_admin_session(self.editor)

        with import_workspace() as (_media_root, import_root):
            session = self.stage(
                payload=import_payload(with_image=True),
                image_files=[image_upload('hero.png')],
            )
            response = self.admin_request(
                'admin',
                'post',
                self.review_url('admin', session),
                data={'action': 'change_files'},
            )

            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, self.admin_url('admin', 'blogpost_import'))
            messages = [str(message) for message in get_messages(response.wsgi_request)]
            self.assertIn('Staged import discarded. Choose the corrected files to validate again.', messages)
            self.assertFalse(BlogArticleImport.objects.filter(pk=session.pk).exists())
            self.assertEqual(BlogPost.objects.count(), 0)
            self.assertEqual(list(import_root.rglob('*')), [])

    @skipUnless(settings.ENABLE_DEV_ADMIN, 'development admin is disabled')
    def test_cancel_discards_staging_and_returns_to_the_active_changelist(self):
        self.verify_admin_session(self.editor)

        with import_workspace() as (_media_root, import_root):
            session = self.stage(
                payload=import_payload(with_image=True),
                image_files=[image_upload('hero.png')],
            )
            response = self.admin_request(
                'dev_admin',
                'post',
                self.review_url('dev_admin', session),
                data={'action': 'cancel'},
            )

            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, reverse('dev_admin:blog_blogpost_changelist'))
            messages = [str(message) for message in get_messages(response.wsgi_request)]
            self.assertIn('Import cancelled. No draft was created.', messages)
            self.assertFalse(BlogArticleImport.objects.filter(pk=session.pk).exists())
            self.assertEqual(BlogPost.objects.count(), 0)
            self.assertEqual(list(import_root.rglob('*')), [])

    def test_unsupported_methods_are_not_allowed_and_do_not_mutate_state(self):
        self.verify_admin_session(self.editor)

        with import_workspace():
            session = self.stage()
            import_url = self.admin_url('admin', 'blogpost_import')
            review_url = self.review_url('admin', session)
            before = BlogArticleImport.objects.values('payload', 'warnings').get(pk=session.pk)

            import_response = self.admin_request('admin', 'put', import_url)
            review_response = self.admin_request('admin', 'put', review_url)

            for response in (import_response, review_response):
                self.assertEqual(response.status_code, 405)
                self.assert_private_headers(response)
                self.assertEqual(response['Allow'], 'GET, POST')
            self.assertEqual(before, BlogArticleImport.objects.values('payload', 'warnings').get(pk=session.pk))
            self.assertEqual(BlogPost.objects.count(), 0)
