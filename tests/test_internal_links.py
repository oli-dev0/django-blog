from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection
from django.forms.models import inlineformset_factory
from django.templatetags.static import static
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch

from apps.blog.forms import (
    BlogInternalLinkBlockForm,
    BlogInternalLinkInlineFormSet,
    BlogPostAdminForm,
    BlogRichTextBlockForm,
    BlogRichTextInlineFormSet,
)
from apps.blog.internal_links import (
    get_internal_link_editor_destinations,
    get_internal_link_choices,
    resolve_internal_link,
    validate_internal_link_destination,
    validate_inline_internal_links,
)
from apps.blog.models import (
    BlogCategory,
    BlogInternalLinkBlock,
    BlogPost,
    BlogPostPublication,
    BlogRichTextBlock,
)
from apps.blog.services import BlogWorkflowError, mark_post_ready
from apps.blog.rendering import build_article_context
from apps.core.sites import EASY_MEALS_SITE, PERSONAL_SITE, VANTA_SITE

from .site_settings import BLOG_ENABLED_SITE_DEFINITIONS


@override_settings(SITE_DEFINITIONS=BLOG_ENABLED_SITE_DEFINITIONS)
class InternalLinkRegistryTests(TestCase):
    def test_editor_metadata_contains_registry_routes(self):
        destinations = {item['key']: item for item in get_internal_link_editor_destinations()}

        self.assertEqual(destinations['personal-projects']['url'], '/projects/')
        self.assertIn(PERSONAL_SITE, destinations['personal-projects']['allowed_site_slugs'])

    def test_inline_links_validate_registry_keys(self):
        validate_inline_internal_links(
            '<p>Read <a href="https://example.test" data-blog-internal-key="personal-projects">this</a>.</p>',
            {PERSONAL_SITE},
        )
        with self.assertRaisesMessage(ValidationError, 'every selected publication website'):
            validate_inline_internal_links(
                '<p><a data-blog-internal-key="vanta-features">wrong site</a></p>',
                {PERSONAL_SITE},
            )

    def test_inline_link_validation_parses_html_attribute_whitespace(self):
        with self.assertRaisesMessage(ValidationError, 'every selected publication website'):
            validate_inline_internal_links(
                '<p><a data-blog-internal-key = "vanta-features">wrong site</a></p>',
                {PERSONAL_SITE},
            )

    def test_registry_only_offers_destinations_available_on_every_selected_site(self):
        personal_choices = dict(get_internal_link_choices({PERSONAL_SITE}))

        self.assertIn('personal-projects', personal_choices)
        self.assertNotIn('vanta-features', personal_choices)
        self.assertEqual(get_internal_link_choices({PERSONAL_SITE, VANTA_SITE}), [])

    def test_registry_rejects_unknown_and_cross_site_destinations(self):
        for destination in ('https://example.com', 'javascript:alert(1)'):
            with (
                self.subTest(destination=destination),
                self.assertRaisesMessage(ValidationError, 'approved internal destination'),
            ):
                validate_internal_link_destination(destination, {PERSONAL_SITE})
        with self.assertRaisesMessage(ValidationError, 'every selected publication website'):
            validate_internal_link_destination('vanta-features', {PERSONAL_SITE})

    def test_registry_resolves_a_stable_local_route(self):
        self.assertEqual(resolve_internal_link('personal-projects', {PERSONAL_SITE}), '/projects/')
        self.assertEqual(resolve_internal_link('easy-meals-home', {EASY_MEALS_SITE}), '/')


@override_settings(SITE_DEFINITIONS=BLOG_ENABLED_SITE_DEFINITIONS)
class InternalLinkEditingTests(TestCase):
    def setUp(self):
        self.category = BlogCategory.objects.create(name='General', slug='general')
        self.post = BlogPost.objects.create(
            title='Internal links',
            slug='internal-links',
            summary='Internal-link coverage.',
            category=self.category,
            canonical_site_slug=PERSONAL_SITE,
        )
        BlogPostPublication.objects.create(post=self.post, site_slug=PERSONAL_SITE)

    def test_block_form_accepts_descriptive_same_site_link(self):
        form = BlogInternalLinkBlockForm(
            data={
                'destination_key': 'personal-projects',
                'label': 'Explore my Django projects',
                'note': 'See practical examples.',
                'region': 'main',
                'ordering': 10,
            },
            site_slugs={PERSONAL_SITE},
        )

        self.assertTrue(form.is_valid(), form.errors)

    def test_rich_text_form_rejects_inline_destination_unavailable_on_post_site(self):
        form = BlogRichTextBlockForm(
            data={'body': '<p><a data-blog-internal-key="vanta-features">Vanta</a></p>'},
            instance=BlogRichTextBlock(parent=self.post),
        )

        self.assertFalse(form.is_valid())
        self.assertIn('every selected publication website', str(form.errors))

    @override_settings(
        STORAGES={
            'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
        }
    )
    def test_rich_text_editor_uses_resolved_custom_extension_url(self):
        config = BlogRichTextBlockForm().fields['body'].widget.get_config()

        self.assertEqual(
            config['js_modules'],
            [static('blog/js/internal-link-editor.js')],
        )

    def test_rich_text_formset_uses_projected_publication_sites(self):
        formset_class = inlineformset_factory(
            BlogPost,
            BlogRichTextBlock,
            form=BlogRichTextBlockForm,
            formset=BlogRichTextInlineFormSet,
            extra=0,
        )
        prefix = formset_class.get_default_prefix()
        formset = formset_class(
            data={
                'publication_sites': [VANTA_SITE],
                f'{prefix}-TOTAL_FORMS': '1',
                f'{prefix}-INITIAL_FORMS': '0',
                f'{prefix}-MIN_NUM_FORMS': '0',
                f'{prefix}-MAX_NUM_FORMS': '1000',
                f'{prefix}-0-body': '<p><a data-blog-internal-key="vanta-features">Vanta</a></p>',
                f'{prefix}-0-region': 'main',
                f'{prefix}-0-ordering': '10',
            },
            instance=self.post,
        )

        self.assertEqual(formset.site_slugs, {VANTA_SITE})
        self.assertTrue(formset.is_valid(), formset.errors)

    def test_rich_text_formset_rejects_link_for_previous_publication_site(self):
        formset_class = inlineformset_factory(
            BlogPost,
            BlogRichTextBlock,
            form=BlogRichTextBlockForm,
            formset=BlogRichTextInlineFormSet,
            extra=0,
        )
        prefix = formset_class.get_default_prefix()
        formset = formset_class(
            data={
                'publication_sites': VANTA_SITE,
                f'{prefix}-TOTAL_FORMS': '1',
                f'{prefix}-INITIAL_FORMS': '0',
                f'{prefix}-MIN_NUM_FORMS': '0',
                f'{prefix}-MAX_NUM_FORMS': '1000',
                f'{prefix}-0-body': '<p><a data-blog-internal-key="personal-projects">Projects</a></p>',
                f'{prefix}-0-region': 'main',
                f'{prefix}-0-ordering': '10',
            },
            instance=self.post,
        )

        self.assertEqual(formset.site_slugs, {VANTA_SITE})
        self.assertFalse(formset.is_valid())
        self.assertIn('every selected publication website', str(formset.errors))

    def test_post_form_validates_submitted_rich_text_against_projected_sites(self):
        rich_prefix = BlogRichTextBlock._meta.get_field('parent').remote_field.get_accessor_name()
        form = BlogPostAdminForm(
            data={
                'publication_sites': [VANTA_SITE],
                'title': self.post.title,
                'type': self.post.type,
                'summary': self.post.summary,
                'category': self.category.pk,
                'canonical_site_slug': VANTA_SITE,
                f'{rich_prefix}-TOTAL_FORMS': '1',
                f'{rich_prefix}-INITIAL_FORMS': '0',
                f'{rich_prefix}-0-body': (
                    '<p><a data-blog-internal-key="personal-projects">Projects</a></p>'
                ),
            },
            instance=self.post,
        )

        self.assertFalse(form.is_valid())
        self.assertIn(
            'Some rich-text internal links are not available',
            str(form.errors['publication_sites']),
        )

    def test_block_model_rejects_generic_anchor_text(self):
        block = BlogInternalLinkBlock(
            parent=self.post,
            destination_key='personal-projects',
            label=' Click   Here ',
            region='main',
        )

        with self.assertRaisesMessage(ValidationError, 'descriptive anchor text'):
            block.full_clean()

    def test_non_organizer_formset_uses_persisted_sites_not_missing_post_data(self):
        formset_class = inlineformset_factory(
            BlogPost,
            BlogInternalLinkBlock,
            form=BlogInternalLinkBlockForm,
            formset=BlogInternalLinkInlineFormSet,
            extra=0,
        )
        formset = formset_class(
            data={
                'internal_link_blocks-TOTAL_FORMS': '1',
                'internal_link_blocks-INITIAL_FORMS': '0',
                'internal_link_blocks-MIN_NUM_FORMS': '0',
                'internal_link_blocks-MAX_NUM_FORMS': '1000',
                'internal_link_blocks-0-destination_key': 'personal-projects',
                'internal_link_blocks-0-label': 'Explore my projects',
                'internal_link_blocks-0-region': 'main',
                'internal_link_blocks-0-ordering': '10',
            },
            instance=self.post,
            prefix='internal_link_blocks',
            publication_sites_editable=False,
        )

        self.assertTrue(formset.is_valid(), formset.errors)

    def test_non_organizer_validation_ignores_forged_publication_sites(self):
        post_form = BlogPostAdminForm(
            data={'publication_sites': VANTA_SITE},
            instance=self.post,
        )
        post_form.fields['publication_sites'].disabled = True

        self.assertEqual(post_form._projected_publication_sites(), {PERSONAL_SITE})

        formset_class = inlineformset_factory(
            BlogPost,
            BlogInternalLinkBlock,
            form=BlogInternalLinkBlockForm,
            formset=BlogInternalLinkInlineFormSet,
            extra=0,
        )
        prefix = formset_class.get_default_prefix()
        formset = formset_class(
            data={
                'publication_sites': VANTA_SITE,
                f'{prefix}-TOTAL_FORMS': '1',
                f'{prefix}-INITIAL_FORMS': '0',
                f'{prefix}-MIN_NUM_FORMS': '0',
                f'{prefix}-MAX_NUM_FORMS': '1000',
                f'{prefix}-0-destination_key': 'vanta-features',
                f'{prefix}-0-label': 'Explore Vanta features',
                f'{prefix}-0-region': 'main',
                f'{prefix}-0-ordering': '10',
            },
            instance=self.post,
            publication_sites_editable=False,
        )

        self.assertEqual(formset.site_slugs, {PERSONAL_SITE})
        self.assertFalse(formset.is_valid())
        self.assertIn('Select a valid choice', str(formset.errors))

    def test_publication_readiness_rejects_stale_destination(self):
        BlogRichTextBlock.objects.create(parent=self.post, region='main', body='<p>Body.</p>')
        BlogInternalLinkBlock.objects.create(
            parent=self.post,
            destination_key='removed-destination',
            label='Former destination',
            region='main',
        )

        with self.assertRaisesMessage(BlogWorkflowError, 'approved internal destination'):
            mark_post_ready(self.post, actor=self._publisher())

    def test_publication_readiness_rejects_destination_with_stale_route(self):
        BlogRichTextBlock.objects.create(parent=self.post, region='main', body='<p>Body.</p>')
        BlogInternalLinkBlock.objects.create(
            parent=self.post,
            destination_key='personal-projects',
            label='Explore my projects',
            region='main',
        )

        with (
            patch('apps.blog.internal_links.reverse', side_effect=NoReverseMatch),
            self.assertRaisesMessage(BlogWorkflowError, 'not configured correctly'),
        ):
            mark_post_ready(self.post, actor=self._publisher())

    @staticmethod
    def _publisher():
        from django.contrib.auth import get_user_model

        return get_user_model().objects.create_superuser(
            username='internal-link-publisher',
            email='publisher@example.com',
            password='test-password',
        )


@override_settings(SITE_DEFINITIONS=BLOG_ENABLED_SITE_DEFINITIONS)
class InternalLinkRenderingTests(TestCase):
    def create_post(self, *, destination_key='personal-projects'):
        category = BlogCategory.objects.create(name='General', slug='general')
        post = BlogPost.objects.create(
            status=BlogPost.Status.PUBLISHED,
            title='Internal link article',
            slug='internal-link-article',
            summary='Internal-link rendering coverage.',
            category=category,
            canonical_site_slug=PERSONAL_SITE,
        )
        BlogPostPublication.objects.create(post=post, site_slug=PERSONAL_SITE)
        BlogInternalLinkBlock.objects.create(
            parent=post,
            destination_key=destination_key,
            label='Explore my Django projects',
            note='See practical examples.',
            region='main',
        )
        return post

    def test_detail_server_renders_crawlable_same_origin_link(self):
        self.create_post()

        response = self.client.get('/en/blog/internal-link-article/')

        self.assertContains(response, 'href="/projects/"', html=False)
        self.assertContains(response, '>Explore my Django projects</a>', html=False)
        self.assertContains(response, 'See practical examples.')
        self.assertContains(
            response,
            '<a href="/projects/" target="_blank" rel="noopener noreferrer">',
            html=False,
        )
        self.assertNotContains(response, '<a class="blog-rss__link"', html=False)

    def test_detail_resolves_inline_internal_link_and_keeps_text(self):
        post = self.create_post()
        BlogRichTextBlock.objects.create(
            parent=post,
            region='main',
            body='<p>Read <a href="https://stale.example/" target="_blank" '
                 'data-blog-internal-key="personal-projects">my projects</a>.</p>',
        )

        response = self.client.get('/en/blog/internal-link-article/')

        self.assertContains(response, 'href="/projects/"', html=False)
        self.assertContains(response, '>my projects</a>', html=False)
        self.assertNotContains(response, 'stale.example', html=False)

    def test_detail_downgrades_invalid_inline_link_to_plain_anchor_text(self):
        post = self.create_post()
        BlogRichTextBlock.objects.create(
            parent=post,
            region='main',
            body='<p>Read <a href="https://stale.example/" '
                 'data-blog-internal-key="removed-destination">my projects</a>.</p>',
        )

        response = self.client.get('/en/blog/internal-link-article/')

        self.assertContains(
            response,
            '<a target="_blank" rel="noopener noreferrer">my projects</a>',
            html=False,
        )
        self.assertNotContains(response, 'stale.example', html=False)

    def test_detail_omits_stale_destination_instead_of_rendering_an_unsafe_link(self):
        self.create_post(destination_key='removed-destination')

        response = self.client.get('/en/blog/internal-link-article/')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Explore my Django projects')

    def test_rendering_loads_publication_sites_once_for_multiple_internal_links(self):
        post = self.create_post()
        for ordering in (10, 20):
            BlogInternalLinkBlock.objects.create(
                parent=post,
                destination_key='personal-about',
                label=f'About this site {ordering}',
                region='main',
                ordering=ordering,
            )

        with CaptureQueriesContext(connection) as queries:
            build_article_context(post, site_slug=PERSONAL_SITE)

        publication_queries = [
            query
            for query in queries.captured_queries
            if 'blog_blogpostpublication' in query['sql']
        ]
        self.assertEqual(len(publication_queries), 1)
