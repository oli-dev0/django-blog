from contextlib import contextmanager
from io import BytesIO
import json
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from apps.blog import embed_sharing
from apps.blog.import_services import (
    BlogImportPermissionError,
    BlogImportUnavailable,
    BlogImportValidationError,
    ReviewedImportReferences,
    create_blog_post_from_import,
    validate_and_stage_blog_import,
)
from apps.blog.models import (
    AuthorProfile,
    BlogArticleImport,
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
    BlogPostRelated,
    BlogRichTextBlock,
    BlogSite,
    BlogSourceLinkBlock,
    BlogTag,
)


@contextmanager
def import_workspace():
    with TemporaryDirectory() as media_root, TemporaryDirectory() as import_root:
        with override_settings(MEDIA_ROOT=media_root, BLOG_IMPORT_ROOT=import_root):
            yield


def source_file(payload):
    return SimpleUploadedFile(
        'article.json',
        json.dumps(payload).encode('utf-8'),
        content_type='application/json',
    )


def image_file(name='hero.png'):
    output = BytesIO()
    Image.new('RGB', (2, 2), 'white').save(output, format='PNG')
    return SimpleUploadedFile(name, output.getvalue(), content_type='image/png')


def import_payload(
    *,
    title='Imported article',
    slug='imported-article',
    blocks=None,
    related_articles=None,
    tags=None,
    include_seo=True,
):
    article = {
        'title': title,
        'summary': 'A useful imported draft.',
        'author': {'slug': 'oli'},
        'category': {'slug': 'development'},
        'tags': [{'slug': value} for value in (tags or [])],
        'publication_sites': ['vanta_admin'],
        'canonical_site': 'vanta_admin',
        'related_articles': [
            {'slug': value} for value in (related_articles or [])
        ],
        'blocks': blocks or [{'type': 'heading', 'level': 2, 'text': 'A section'}],
    }
    if slug is not None:
        article['slug'] = slug
    if include_seo:
        article['type'] = 'guide'
        article['seo'] = {
            'title': 'Imported search title',
            'description': 'Imported search description.',
        }
    return {
        'format': 'blog-article-import',
        'version': 1,
        'article': article,
        'assets': [],
        'comparisons': [],
    }


class BlogImportCreationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.actor = user_model.objects.create_user(username='import-owner')
        self.other_actor = user_model.objects.create_user(username='other-owner')
        self.author = AuthorProfile.objects.create(
            user=user_model.objects.create_user(username='article-author'),
            public_author_name='Oli',
            slug='oli',
        )
        self.category = BlogCategory.objects.create(name='Development', slug='development')
        self.tags = [
            BlogTag.objects.create(name='Django', slug='django'),
            BlogTag.objects.create(name='Python', slug='python'),
        ]
        site, _created = BlogSite.objects.get_or_create(slug='vanta_admin')
        self.category.websites.add(site)
        for tag in self.tags:
            tag.websites.add(site)

    def allow_permissions(self, actor=None):
        return patch.object(actor or self.actor, 'has_perm', return_value=True)

    def reviewed_references(self, *, tags=None, actor=None):
        return ReviewedImportReferences(
            author=self.author,
            category=self.category,
            tags=tuple(tags if tags is not None else self.tags),
            publication_sites=('vanta_admin',),
            canonical_site='vanta_admin',
        )

    def stage(self, payload, *, actor=None, image_files=()):
        actor = actor or self.actor
        with self.allow_permissions(actor):
            return validate_and_stage_blog_import(
                source_file(payload),
                list(image_files),
                actor,
            )

    def create(self, session, *, reviewed=None, actor=None):
        actor = actor or self.actor
        reviewed = reviewed or self.reviewed_references()
        with self.allow_permissions(actor):
            with self.captureOnCommitCallbacks(execute=True):
                return create_blog_post_from_import(session, reviewed, actor)

    def create_related_post(self, slug):
        post = BlogPost.objects.create(
            status=BlogPost.Status.DRAFT,
            title=f'Related {slug}',
            slug=slug,
            category=self.category,
        )
        BlogPostPublication.objects.create(post=post, site_slug='vanta_admin')
        return post

    def test_v2_creates_confirmed_taxonomy_and_assignments_atomically(self):
        payload = import_payload(title='New taxonomy')
        payload['version'] = 2
        payload['article']['category'] = {'name': 'Tools', 'slug': 'tools'}
        payload['article']['tags'] = [
            {'name': 'JavaScript', 'slug': 'javascript'},
            {'name': 'Optional tag', 'slug': 'optional-tag'},
        ]
        reviewed = ReviewedImportReferences(
            author=self.author,
            category=None,
            tags=(),
            publication_sites=('vanta_admin',),
            canonical_site='vanta_admin',
            create_category=True,
            create_tags=('javascript',),
        )

        with import_workspace():
            session = self.stage(payload)
            post = self.create(session, reviewed=reviewed)

        self.assertEqual(post.category.slug, 'tools')
        self.assertEqual(list(post.tags.values_list('slug', flat=True)), ['javascript'])
        self.assertEqual(
            set(post.category.websites.values_list('slug', flat=True)),
            {'vanta_admin'},
        )
        self.assertFalse(BlogTag.objects.filter(slug='optional-tag').exists())

    def test_image_free_payload_creates_draft_metadata_relationships_and_all_blocks_in_order(self):
        related_posts = [
            self.create_related_post('related-first'),
            self.create_related_post('related-second'),
        ]
        blocks = [
            {'type': 'heading', 'level': 2, 'text': 'Same heading'},
            {
                'type': 'rich_text',
                'body': (
                    '<p>Keep <strong>bold</strong>. <script>bad()</script>'
                    '<a href="https://example.com" onclick="bad()">Source</a> '
                    '<a data-blog-internal-key="vanta-home">Vanta</a></p>'
                ),
            },
            {
                'type': 'faq',
                'items': [
                    {
                        'question': '  Is this useful?  ',
                        'answer': '<p>Yes. <script>bad()</script></p>',
                    }
                ],
            },
            {'type': 'checklist', 'marker': 'square', 'items': [' First step ', 'Second step']},
            {
                'type': 'code',
                'language': 'python',
                'code': 'print("hello")\n',
                'caption': 'Run it',
            },
            {
                'type': 'callout',
                'callout_type': 'tip',
                'title': 'A tip',
                'body': '<p><em>Keep it simple.</em><script>bad()</script></p>',
            },
            {
                'type': 'source_link',
                'label': 'Documentation',
                'url': 'https://example.com/docs',
                'note': 'The official docs.',
            },
            {
                'type': 'link_group',
                'label': 'Further reading',
                'links': [{'label': 'Django', 'url': 'https://www.djangoproject.com/'}],
            },
            {
                'type': 'internal_link',
                'destination_key': 'vanta-home',
                'label': 'Vanta Admin home',
                'note': 'Visit the product site.',
            },
            {'type': 'heading', 'level': 3, 'text': 'Same heading'},
        ]
        payload = import_payload(
            title='Imported guide',
            slug='imported-guide',
            blocks=blocks,
            related_articles=[post.slug for post in related_posts],
            tags=['django', 'python'],
        )

        with import_workspace():
            session = self.stage(payload)
            post = self.create(session)

        post.refresh_from_db()
        self.assertEqual(BlogPost.objects.filter(pk=post.pk).count(), 1)
        self.assertEqual(post.status, BlogPost.Status.DRAFT)
        self.assertEqual(post.type, BlogPost.Type.GUIDE)
        self.assertEqual(post.title, 'Imported guide')
        self.assertEqual(post.slug, 'imported-guide')
        self.assertEqual(post.summary, 'A useful imported draft.')
        self.assertEqual(post.seo_title, 'Imported search title')
        self.assertEqual(post.seo_description, 'Imported search description.')
        self.assertEqual(post.author_id, self.author.pk)
        self.assertEqual(post.category_id, self.category.pk)
        self.assertEqual(post.canonical_site_slug, 'vanta_admin')
        self.assertEqual(post.created_by_id, self.actor.pk)
        self.assertEqual(post.updated_by_id, self.actor.pk)
        self.assertIsNone(post.published_at)
        self.assertIsNone(post.last_reviewed_on)
        self.assertIsNone(post.content_updated_at)
        self.assertEqual(
            set(post.tags.values_list('slug', flat=True)),
            {'django', 'python'},
        )
        self.assertEqual(
            list(post.publications.values_list('site_slug', flat=True)),
            ['vanta_admin'],
        )
        self.assertEqual(
            list(
                post.related_links.order_by('position').values_list(
                    'related_post_id', 'position'
                )
            ),
            [(related_posts[0].pk, 0), (related_posts[1].pk, 1)],
        )

        content_models = (
            BlogHeadingBlock,
            BlogRichTextBlock,
            BlogFAQBlock,
            BlogChecklistBlock,
            BlogCodeBlock,
            BlogCalloutBlock,
            BlogSourceLinkBlock,
            BlogLinkGroupBlock,
            BlogInternalLinkBlock,
        )
        blocks_by_order = []
        for model in content_models:
            blocks_by_order.extend(model.objects.filter(parent=post))
        blocks_by_order.sort(key=lambda block: block.ordering)
        self.assertEqual(
            [block.ordering for block in blocks_by_order],
            list(range(10, 110, 10)),
        )
        self.assertEqual(
            [type(block) for block in blocks_by_order],
            [
                BlogHeadingBlock,
                BlogRichTextBlock,
                BlogFAQBlock,
                BlogChecklistBlock,
                BlogCodeBlock,
                BlogCalloutBlock,
                BlogSourceLinkBlock,
                BlogLinkGroupBlock,
                BlogInternalLinkBlock,
                BlogHeadingBlock,
            ],
        )

        headings = list(BlogHeadingBlock.objects.filter(parent=post).order_by('ordering'))
        self.assertEqual([heading.anchor for heading in headings], ['same-heading', 'same-heading-2'])
        rich_text = BlogRichTextBlock.objects.get(parent=post)
        self.assertIn('<strong>bold</strong>', rich_text.body)
        self.assertIn('data-blog-internal-key="vanta-home"', rich_text.body)
        self.assertNotIn('<script', rich_text.body.lower())
        self.assertNotIn('onclick', rich_text.body.lower())
        self.assertIn('rel="noopener noreferrer"', rich_text.body)
        faq = BlogFAQBlock.objects.get(parent=post)
        self.assertEqual(faq.items[0]['question'], 'Is this useful?')
        self.assertNotIn('<script', faq.items[0]['answer'].lower())
        checklist = BlogChecklistBlock.objects.get(parent=post)
        self.assertEqual(checklist.items, ['First step', 'Second step'])
        self.assertEqual(BlogCodeBlock.objects.get(parent=post).language, 'python')
        callout = BlogCalloutBlock.objects.get(parent=post)
        self.assertNotIn('<script', callout.body.lower())
        self.assertEqual(BlogSourceLinkBlock.objects.get(parent=post).url, 'https://example.com/docs')
        self.assertEqual(
            BlogLinkGroupBlock.objects.get(parent=post).links[0]['url'],
            'https://www.djangoproject.com/',
        )
        self.assertEqual(
            BlogInternalLinkBlock.objects.get(parent=post).destination_key,
            'vanta-home',
        )
        self.assertFalse(BlogImage.objects.exists())
        self.assertFalse(BlogArticleImport.objects.filter(pk=session.pk).exists())

    def test_embed_sharing_block_is_created_in_source_order_without_provider_request(self):
        payload = import_payload(
            title='Imported embed article',
            slug='imported-embed-article',
            blocks=[
                {'type': 'heading', 'level': 2, 'text': 'Before the embed'},
                {
                    'type': 'embed_sharing',
                    'platform': 'youtube',
                    'url': 'https://youtu.be/dQw4w9WgXcQ?si=tracking',
                    'caption': 'A useful video',
                },
                {'type': 'rich_text', 'body': '<p>After the embed.</p>'},
            ],
        )

        with patch.object(embed_sharing, 'build_opener') as build_opener:
            with import_workspace():
                session = self.stage(payload)
                post = self.create(session)

        ordered_blocks = []
        for model in (BlogHeadingBlock, BlogEmbedSharingBlock, BlogRichTextBlock):
            ordered_blocks.extend(model.objects.filter(parent=post))
        ordered_blocks.sort(key=lambda block: block.ordering)

        self.assertEqual(
            [type(block) for block in ordered_blocks],
            [BlogHeadingBlock, BlogEmbedSharingBlock, BlogRichTextBlock],
        )
        self.assertEqual([block.ordering for block in ordered_blocks], [10, 20, 30])
        embed = ordered_blocks[1]
        self.assertEqual(embed.platform, 'youtube')
        self.assertEqual(embed.url, 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        self.assertEqual(embed.caption, 'A useful video')
        build_opener.assert_not_called()

    def test_post_slug_collisions_use_deterministic_suffixes_and_empty_fallback(self):
        BlogPost.objects.create(
            status=BlogPost.Status.DRAFT,
            title='Existing requested slug',
            slug='requested-slug',
            category=self.category,
        )
        BlogPost.objects.create(
            status=BlogPost.Status.DRAFT,
            title='Existing generated slug',
            slug='generated-title',
            category=self.category,
        )
        BlogPost.objects.create(
            status=BlogPost.Status.DRAFT,
            title='Existing fallback slug',
            slug='article',
            category=self.category,
        )

        cases = (
            ('Requested collision', 'requested-slug', 'requested-slug-2'),
            ('Generated Title', None, 'generated-title-2'),
            ('!!!', None, 'article-2'),
        )
        with import_workspace():
            for title, slug, expected_slug in cases:
                with self.subTest(title=title):
                    payload = import_payload(
                        title=title,
                        slug=slug,
                        blocks=[{'type': 'heading', 'level': 2, 'text': title}],
                    )
                    session = self.stage(payload)
                    post = self.create(session, reviewed=self.reviewed_references(tags=()))
                    self.assertEqual(post.slug, expected_slug)

        self.assertEqual(BlogPost.objects.get(slug='requested-slug').title, 'Existing requested slug')
        self.assertEqual(BlogPost.objects.get(slug='generated-title').title, 'Existing generated slug')
        self.assertEqual(BlogPost.objects.get(slug='article').title, 'Existing fallback slug')

    def test_empty_heading_slugs_use_section_fallback_and_numeric_suffix(self):
        payload = import_payload(
            blocks=[
                {'type': 'heading', 'level': 2, 'text': '!!!'},
                {'type': 'heading', 'level': 3, 'text': '!!!'},
            ]
        )

        with import_workspace():
            session = self.stage(payload)
            post = self.create(session, reviewed=self.reviewed_references(tags=()))

        self.assertEqual(
            list(
                BlogHeadingBlock.objects.filter(parent=post)
                .order_by('ordering')
                .values_list('anchor', flat=True)
            ),
            ['section', 'section-2'],
        )

    def test_unknown_block_and_invalid_boundaries_are_rejected_before_persistence(self):
        cases = (
            (
                'unknown block',
                [{'type': 'unknown', 'value': 'not supported'}],
            ),
            (
                'invalid source URL',
                [{'type': 'source_link', 'url': 'javascript:alert(1)'}],
            ),
            (
                'HTML checklist item',
                [{'type': 'checklist', 'items': ['<strong>unsafe</strong>']}],
            ),
            (
                'unapproved internal destination',
                [
                    {
                        'type': 'internal_link',
                        'destination_key': 'not-approved',
                        'label': 'Descriptive destination',
                    }
                ],
            ),
        )

        with import_workspace():
            for name, blocks in cases:
                with self.subTest(name=name):
                    with self.assertRaises(BlogImportValidationError):
                        self.stage(import_payload(blocks=blocks))
                    self.assertFalse(BlogPost.objects.filter(title='Imported article').exists())
                    self.assertFalse(BlogArticleImport.objects.exists())

    def test_missing_permission_is_rejected_before_any_persistence(self):
        payload = import_payload()
        with import_workspace():
            with patch.object(
                self.actor,
                'has_perm',
                side_effect=lambda permission: permission != 'blog.add_blogpost',
            ):
                with self.assertRaises(BlogImportPermissionError) as error:
                    validate_and_stage_blog_import(source_file(payload), [], self.actor)

        self.assertEqual(error.exception.missing_permissions, ('blog.add_blogpost',))
        self.assertFalse(BlogPost.objects.exists())
        self.assertFalse(BlogArticleImport.objects.exists())

    def test_media_blocks_are_created_with_the_draft(self):
        payload = import_payload(
            blocks=[{'type': 'image', 'asset_id': 'hero'}],
        )
        payload['assets'] = [
            {
                'id': 'hero',
                'file': 'images/hero.png',
                'name': 'Hero',
                'alt_text': 'A hero image',
            }
        ]

        with import_workspace():
            session = self.stage(payload, image_files=[image_file()])
            post = self.create(session, reviewed=self.reviewed_references(tags=()))

        image = BlogImage.objects.get(name='Hero')
        self.assertEqual(BlogImageBlock.objects.get(parent=post).image_id, image.pk)
        self.assertIsNone(post.featured_image_id)
        self.assertFalse(BlogArticleImport.objects.filter(pk=session.pk).exists())

    def test_featured_image_is_assigned_to_the_created_draft(self):
        payload = import_payload()
        payload['article']['featured_image'] = 'hero'
        payload['assets'] = [
            {
                'id': 'hero',
                'file': 'images/hero.png',
                'name': 'Hero',
                'alt_text': 'A hero image',
            }
        ]

        with import_workspace():
            session = self.stage(payload, image_files=[image_file()])
            post = self.create(session, reviewed=self.reviewed_references(tags=()))

        self.assertEqual(post.featured_image.name, 'Hero')
        self.assertEqual(BlogImage.objects.count(), 1)
        self.assertFalse(BlogArticleImport.objects.filter(pk=session.pk).exists())

    def test_owner_and_expiry_are_rechecked_at_creation(self):
        payload = import_payload()
        with import_workspace():
            session = self.stage(payload)
            with self.assertRaises(PermissionDenied):
                create_blog_post_from_import(
                    session,
                    self.reviewed_references(tags=()),
                    self.other_actor,
                )
            session.expires_at = timezone.now() - timezone.timedelta(minutes=1)
            session.save(update_fields=['expires_at'])
            with self.assertRaises(BlogImportUnavailable):
                self.create(session, reviewed=self.reviewed_references(tags=()))

        self.assertFalse(BlogPost.objects.exists())

    def test_deleted_reviewed_reference_is_rechecked_before_creation(self):
        payload = import_payload()
        with import_workspace():
            session = self.stage(payload)
            self.author.delete()
            with self.assertRaises(BlogImportValidationError) as error:
                self.create(session, reviewed=self.reviewed_references(tags=()))

        self.assertIn('invalid_review_author', {issue.code for issue in error.exception.issues})
        self.assertFalse(BlogPost.objects.exists())
        self.assertTrue(BlogArticleImport.objects.filter(pk=session.pk).exists())

    def test_deleted_related_article_is_rechecked_before_creation(self):
        related = self.create_related_post('related-first')
        payload = import_payload(related_articles=[related.slug])

        with import_workspace():
            session = self.stage(payload)
            related.delete()
            with self.assertRaises(BlogImportValidationError) as error:
                self.create(session, reviewed=self.reviewed_references(tags=()))

        self.assertIn('missing_related_article', {issue.code for issue in error.exception.issues})
        self.assertFalse(BlogPost.objects.filter(title='Imported article').exists())
        self.assertTrue(BlogArticleImport.objects.filter(pk=session.pk).exists())

    def test_validation_failure_after_relationship_writes_rolls_back_post_and_content(self):
        related = self.create_related_post('related-first')
        payload = import_payload(
            related_articles=[related.slug],
            blocks=[
                {'type': 'heading', 'level': 2, 'text': 'Persisted first'},
                {'type': 'rich_text', 'body': '<p>Fails after the heading.</p>'},
            ],
        )

        with import_workspace():
            session = self.stage(payload)
            from apps.blog import import_services

            real_save_validated = import_services._save_validated

            def fail_on_rich_text(instance):
                if isinstance(instance, BlogRichTextBlock):
                    raise ValidationError('Forced content validation failure.')
                return real_save_validated(instance)

            with patch.object(import_services, '_save_validated', side_effect=fail_on_rich_text):
                with self.assertRaises(ValidationError):
                    self.create(session)

        self.assertFalse(BlogPost.objects.filter(title='Imported article').exists())
        self.assertFalse(BlogPostPublication.objects.filter(post__title='Imported article').exists())
        self.assertFalse(BlogPostRelated.objects.filter(post__title='Imported article').exists())
        self.assertFalse(BlogHeadingBlock.objects.filter(parent__title='Imported article').exists())
        self.assertFalse(BlogRichTextBlock.objects.filter(parent__title='Imported article').exists())
        self.assertTrue(BlogArticleImport.objects.filter(pk=session.pk).exists())
