import logging
import re
from html import unescape

from django.contrib import admin, messages
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import strip_tags
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _, override
from content_editor.admin import ContentEditor, ContentEditorInline

from apps.core.sites import get_site_definition

from .forms import (
    BlogCalloutBlockForm,
    BlogCategoryAdminForm,
    BlogChecklistBlockForm,
    BlogEmbedSharingBlockForm,
    BlogFAQBlockForm,
    BlogFAQInlineFormSet,
    AuthorProfileAdminForm,
    BlogLinkGroupBlockForm,
    BlogHeadingBlockForm,
    BlogImageAdminForm,
    BlogImageBlockForm,
    BlogImageComparisonAdminForm,
    BlogImageComparisonBlockForm,
    BlogInternalLinkBlockForm,
    BlogInternalLinkInlineFormSet,
    BlogPostAdminForm,
    BlogPostQuickStartForm,
    BlogPostRelatedForm,
    BlogTagAdminForm,
    BlogRelatedInlineFormSet,
    PreviewWebsiteForm,
    BlogRichTextBlockForm,
    BlogRichTextInlineFormSet,
    ConfirmActionForm,
    MarkReviewedForm,
    SchedulePostForm,
)
from .internal_links import get_internal_link_editor_destinations
from .image_services import (
    comparison_image_state,
    image_state,
    process_author_profile_picture,
    process_comparison_image,
    process_image,
)
from .import_forms import BlogArticleImportReviewForm, BlogArticleImportUploadForm
from .import_services import (
    BlogImportError,
    BlogImportPermissionError,
    BlogImportUnavailable,
    BlogImportValidationError,
    create_blog_post_from_import,
    discard_staged_import,
    get_blog_import_review,
    validate_and_stage_blog_import,
    validate_reviewed_blog_import,
)
from .models import (
    BlogCalloutBlock,
    BlogCategory,
    BlogChecklistBlock,
    BlogCodeBlock,
    BlogEmbedSharingBlock,
    BlogFAQBlock,
    BlogHeadingBlock,
    BlogImage,
    BlogImageBlock,
    BlogImageComparison,
    BlogImageComparisonBlock,
    BlogInternalLinkBlock,
    BlogLinkGroupBlock,
    BlogPost,
    BlogPostPublication,
    BlogPostRelated,
    BlogRichTextBlock,
    BlogSourceLinkBlock,
    BlogTag,
    AuthorProfile,
)
from .services import (
    BlogWorkflowError,
    create_post_draft,
    mark_post_ready,
    mark_post_reviewed,
    publish_post_now,
    schedule_post,
    unpublish_post,
)
from .selectors import get_publication_site_slugs, get_related_public_posts
from .views import get_preview_blog_template, resolve_preview_site_slug


logger = logging.getLogger(__name__)

BLOG_IMAGE_FILE_SUFFIXES = (
    'original',
    'rendition_480',
    'rendition_800',
    'rendition_1200',
    'rendition_1600',
)
BLOG_IMAGE_ACTION_STATE_ATTR = '_blog_image_action_state'

IMPORT_CORE_PERMISSIONS = (
    'blog.add_blogpost',
    'blog.change_blogpost',
    'blog.organize_blogpost',
)
_IMPORT_URL_PATTERN = re.compile(r'\b(?:https?://|www\.)\S+', re.IGNORECASE)
_IMPORT_ISSUE_GROUPS = (
    ('Article metadata and references', 'metadata'),
    ('Taxonomy and publication', 'taxonomy'),
    ('Content blocks', 'content'),
    ('Images and files', 'images'),
    ('Import package', 'package'),
)
_IMPORT_ISSUE_MESSAGE_OVERRIDES = {
    'invalid_image_file': _(
        'This image cannot be imported. Choose a supported local image file.'
    ),
}
_IMPORT_WARNING_ACKNOWLEDGEMENT_SALT = 'blog.import.warning-acknowledgement'


def _blog_image_side_is_consistent(obj, side=None):
    prefix = f'{side}_' if side else ''
    if (
        getattr(obj, f'{prefix}processing_status') != obj.ProcessingStatus.READY
        or not getattr(obj, f'{prefix}width')
        or not getattr(obj, f'{prefix}height')
    ):
        return False

    for suffix in BLOG_IMAGE_FILE_SUFFIXES:
        stored_file = getattr(obj, f'{prefix}{suffix}')
        if not stored_file or not stored_file.name:
            return False
        try:
            if not stored_file.storage.exists(stored_file.name):
                return False
        except OSError:
            return False
    return True


def _plain_import_text(value, *, limit=280):
    """Return a short, escaped-by-template, URL-free text preview."""

    text = unescape(strip_tags(str(value or '')))
    text = _IMPORT_URL_PATTERN.sub('[URL omitted]', text)
    text = ' '.join(text.split())
    if len(text) > limit:
        return f'{text[:limit - 1].rstrip()}…'
    return text


def _import_issue_group(location):
    location = str(location or '').lower()
    if location.startswith(('article.blocks', 'content')):
        return 'content'
    if location.startswith(('article.tags', 'article.publication_sites', 'article.canonical_site', 'tags')):
        return 'taxonomy'
    if location.startswith(('assets', 'comparisons', 'image_files', '$.image')):
        return 'images'
    if location.startswith(('article.', 'author', 'category')):
        return 'metadata'
    return 'package'


def _group_import_issues(issues):
    grouped = {key: [] for _, key in _IMPORT_ISSUE_GROUPS}
    for issue in issues or ():
        code = getattr(issue, 'code', '')
        grouped[_import_issue_group(getattr(issue, 'location', ''))].append(
            {
                'location': _plain_import_text(getattr(issue, 'location', ''), limit=140),
                'message': _plain_import_text(
                    _IMPORT_ISSUE_MESSAGE_OVERRIDES.get(
                        code,
                        getattr(issue, 'message', ''),
                    ),
                    limit=280,
                ),
            }
        )
    return tuple(
        {'label': label, 'issues': tuple(grouped[key])}
        for label, key in _IMPORT_ISSUE_GROUPS
        if grouped[key]
    )


def _import_warning_key(warning):
    if isinstance(warning, dict):
        return tuple(str(warning.get(field, '')) for field in ('code', 'location', 'message'))
    return tuple(str(getattr(warning, field, '')) for field in ('code', 'location', 'message'))


def _import_warning_acknowledgement(import_id, warnings):
    warning_keys = [list(_import_warning_key(warning)) for warning in warnings]
    return signing.dumps(
        {
            'import_id': str(import_id),
            'warning_keys': warning_keys,
        },
        salt=_IMPORT_WARNING_ACKNOWLEDGEMENT_SALT,
        compress=True,
    )


def _matches_import_warning_acknowledgement(value, import_id, warnings):
    if not value:
        return False
    try:
        acknowledged_state = signing.loads(
            value,
            salt=_IMPORT_WARNING_ACKNOWLEDGEMENT_SALT,
        )
    except signing.BadSignature:
        return False
    current_keys = [list(_import_warning_key(warning)) for warning in warnings]
    return acknowledged_state == {
        'import_id': str(import_id),
        'warning_keys': current_keys,
    }


def _import_block_summaries(parsed):
    assets = {asset.id: asset for asset in parsed.assets}
    comparisons = {comparison.id: comparison for comparison in parsed.comparisons}
    type_labels = {
        'heading': 'Heading',
        'rich_text': 'Rich text',
        'faq': 'FAQ',
        'checklist': 'Checklist',
        'code': 'Code',
        'embed_sharing': 'Embed sharing',
        'callout': 'Callout',
        'source_link': 'Source link',
        'link_group': 'Link group',
        'internal_link': 'Internal link',
        'image': 'Image',
        'image_comparison': 'Image comparison',
    }
    summaries = []
    for position, block in enumerate(parsed.article.blocks, start=1):
        block_type = getattr(block, 'type', 'block')
        if block_type == 'heading':
            summary = _plain_import_text(block.text)
        elif block_type == 'rich_text':
            summary = _plain_import_text(block.body)
        elif block_type == 'faq':
            summary = '; '.join(
                f'{_plain_import_text(item.question, limit=100)}: '
                f'{_plain_import_text(item.answer, limit=140)}'
                for item in block.items
            )
        elif block_type == 'checklist':
            summary = '; '.join(_plain_import_text(item, limit=140) for item in block.items)
        elif block_type == 'code':
            summary = f'{_plain_import_text(block.language, limit=40)}: {_plain_import_text(block.code)}'
        elif block_type == 'embed_sharing':
            platform_labels = {
                'youtube': 'YouTube',
                'x': 'X',
                'reddit': 'Reddit',
            }
            summary = f'{platform_labels.get(block.platform, "Embed")} content'
            if block.caption:
                summary += f' — {_plain_import_text(block.caption, limit=200)}'
        elif block_type == 'callout':
            title = _plain_import_text(block.title, limit=100)
            body = _plain_import_text(block.body)
            summary = f'{title}: {body}' if title else body
        elif block_type == 'source_link':
            summary = _plain_import_text(block.label)
            if block.note:
                summary = f'{summary} — {_plain_import_text(block.note, limit=140)}'
        elif block_type == 'link_group':
            summary = f'{_plain_import_text(block.label)} ({len(block.links)} links)'
        elif block_type == 'internal_link':
            summary = f'{_plain_import_text(block.label)} — destination reviewed'
            if block.note:
                summary += f' — {_plain_import_text(block.note, limit=140)}'
        elif block_type == 'image':
            asset = assets.get(block.asset_id)
            if asset:
                summary = _plain_import_text(asset.name or asset.id)
                if asset.alt_text:
                    summary += f' — {_plain_import_text(asset.alt_text, limit=140)}'
            else:
                summary = 'Referenced image'
        elif block_type == 'image_comparison':
            comparison = comparisons.get(block.comparison_id)
            if comparison:
                summary = _plain_import_text(comparison.name or comparison.id)
            else:
                summary = 'Referenced image comparison'
        else:
            summary = 'Imported content block'
        summaries.append(
            {
                'position': position,
                'type': type_labels.get(block_type, 'Content block'),
                'summary': summary or 'No preview text',
            }
        )
    return tuple(summaries)


def _import_image_rows(review):
    parsed = review.parsed
    candidates = {}
    for asset in parsed.assets:
        basename = str(asset.file).replace('\\', '/').rsplit('/', 1)[-1]
        candidates.setdefault(basename, []).append(
            {
                'import_id': asset.id,
                'name': asset.name,
                'alt_text': asset.alt_text,
            }
        )
    for comparison in parsed.comparisons:
        for side in ('first', 'second'):
            comparison_side = getattr(comparison, side)
            basename = str(comparison_side.file).replace('\\', '/').rsplit('/', 1)[-1]
            candidates.setdefault(basename, []).append(
                {
                    'import_id': f'{comparison.id} ({side})',
                    'name': comparison.name,
                    'alt_text': comparison_side.alt_text,
                }
            )

    rows = []
    for status in review.image_statuses:
        matched = candidates.get(status.selected_name, ())
        identity = matched[0] if matched else {'import_id': 'Referenced image', 'name': '', 'alt_text': ''}
        rows.append(
            {
                'import_id': _plain_import_text(identity['import_id'], limit=120),
                'selected_name': _plain_import_text(status.selected_name, limit=160),
                'name': _plain_import_text(identity['name'], limit=160),
                'alt_text': _plain_import_text(identity['alt_text'], limit=180),
                'valid': status.valid,
                'message': _plain_import_text(status.message, limit=180),
                'locations': tuple(_plain_import_text(location, limit=100) for location in status.source_locations),
            }
        )
    return tuple(rows)


class OrganizationInlineMixin:
    def has_add_permission(self, request, obj=None):
        return super().has_add_permission(request, obj) and request.user.has_perm('blog.organize_blogpost')

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and request.user.has_perm('blog.organize_blogpost')

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and request.user.has_perm('blog.organize_blogpost')


class ContentBlockInlineMixin:
    def _has_block_permission(self, request, action):
        opts = self.model._meta
        return request.user.has_perm(f'{opts.app_label}.{action}_{opts.model_name}')

    def has_add_permission(self, request, obj=None):
        return (
            super().has_add_permission(request, obj)
            and request.user.has_perm('blog.change_blogpost')
            and self._has_block_permission(request, 'add')
        )

    def has_change_permission(self, request, obj=None):
        return (
            super().has_change_permission(request, obj)
            and request.user.has_perm('blog.change_blogpost')
            and self._has_block_permission(request, 'change')
        )

    def has_delete_permission(self, request, obj=None):
        return (
            super().has_delete_permission(request, obj)
            and request.user.has_perm('blog.change_blogpost')
            and self._has_block_permission(request, 'delete')
        )


class BlogRelatedInline(OrganizationInlineMixin, admin.TabularInline):
    model = BlogPostRelated
    form = BlogPostRelatedForm
    formset = BlogRelatedInlineFormSet
    fk_name = 'post'
    extra = 0


@admin.register(AuthorProfile)
class AuthorProfileAdmin(admin.ModelAdmin):
    form = AuthorProfileAdminForm
    list_display = ('public_author_name', 'slug', 'user', 'profile_picture_preview')
    search_fields = ('public_author_name', 'slug', 'user__username', 'user__email', 'user__first_name', 'user__last_name')
    prepopulated_fields = {'slug': ('public_author_name',)}
    list_select_related = ('user',)

    class Media:
        css = {'all': ('blog/css/admin.css',)}

    @admin.display(description=_('Profile picture'))
    def profile_picture_preview(self, obj):
        if not obj.profile_picture:
            return '—'
        try:
            image_url = obj.profile_picture.url
        except (OSError, ValueError):
            return '—'
        alt_text = _('Profile picture for %(name)s') % {'name': obj.public_author_name}
        return format_html(
            '<img class="blog-author-profile-picture-preview" src="{}" alt="{}">',
            image_url,
            alt_text,
        )

    def save_model(self, request, obj, form, change):
        old_picture = None
        if change and obj.pk:
            old_picture = AuthorProfile.objects.only('profile_picture').get(pk=obj.pk).profile_picture
        uploaded_picture = form.cleaned_data.get('profile_picture')
        if uploaded_picture and 'profile_picture' in form.changed_data:
            obj.profile_picture = uploaded_picture
            process_author_profile_picture(obj)
        super().save_model(request, obj, form, change)
        if (
            old_picture
            and 'profile_picture' in form.changed_data
            and old_picture.name != obj.profile_picture.name
        ):
            old_picture.delete(save=False)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            self.delete_model(request, obj)


def _content_inline(model, *, form=None, formset=None, icon=None):
    kwargs = {'model': model, 'icon': icon, 'regions': {'main'}}
    if form is not None:
        kwargs['form'] = form
    if formset is not None:
        kwargs['formset'] = formset
    inline_class = ContentEditorInline.create(**kwargs)

    class PermissionedContentInline(ContentBlockInlineMixin, inline_class):
        pass

    return PermissionedContentInline


@admin.register(BlogPost)
class BlogPostAdmin(ContentEditor):
    form = BlogPostAdminForm
    change_form_template = 'admin/blog/change_form.html'
    change_list_template = 'admin/blog/change_list.html'

    class Media:
        css = {'all': ('blog/css/admin.css',)}
        js = ('blog/js/admin.js',)

    inlines = [
        # Publication sites are edited in the main form above the article title.
        _content_inline(BlogHeadingBlock, form=BlogHeadingBlockForm, icon='title'),
        _content_inline(
            BlogRichTextBlock,
            form=BlogRichTextBlockForm,
            formset=BlogRichTextInlineFormSet,
            icon='article',
        ),
        _content_inline(
            BlogFAQBlock,
            form=BlogFAQBlockForm,
            formset=BlogFAQInlineFormSet,
            icon='help',
        ),
        _content_inline(BlogChecklistBlock, form=BlogChecklistBlockForm, icon='checklist'),
        _content_inline(BlogCodeBlock, icon='code'),
        _content_inline(BlogEmbedSharingBlock, form=BlogEmbedSharingBlockForm, icon='share'),
        _content_inline(BlogCalloutBlock, form=BlogCalloutBlockForm, icon='info'),
        _content_inline(BlogSourceLinkBlock, icon='link'),
        _content_inline(BlogLinkGroupBlock, form=BlogLinkGroupBlockForm, icon='link'),
        _content_inline(
            BlogInternalLinkBlock,
            form=BlogInternalLinkBlockForm,
            formset=BlogInternalLinkInlineFormSet,
            icon='link',
        ),
        _content_inline(BlogImageBlock, form=BlogImageBlockForm, icon='image'),
        _content_inline(BlogImageComparisonBlock, form=BlogImageComparisonBlockForm, icon='compare'),
        BlogRelatedInline,
    ]
    list_display = ('title', 'type', 'effective_status_display', 'published_at', 'updated_at')
    list_filter = ('status', 'type', 'category', 'tags', 'publications__site_slug')
    search_fields = ('title', 'slug', 'summary', 'author__public_author_name', 'category__name', 'tags__name')
    ordering = ('-published_at', '-pk')
    fieldsets = (
        (_('Article content'), {
            'fields': ('publication_sites', 'title', 'type', 'summary'),
        }),
        (_('Author and search presentation'), {
            'fields': (
                'author',
                'seo_title',
                'seo_description',
            ),
        }),
        (_('Organization'), {
            'fields': ('category', 'tags', 'featured_image', 'canonical_site_slug'),
        }),
        (_('Publication status'), {
            'fields': (
                'status',
                'published_at',
                'last_reviewed_on',
                'content_updated_at',
                'effective_status_display',
                'created_at',
                'updated_at',
                'created_by',
                'updated_by',
            ),
        }),
    )
    readonly_fields = (
        'status',
        'published_at',
        'last_reviewed_on',
        'content_updated_at',
        'effective_status_display',
        'created_at',
        'updated_at',
        'created_by',
        'updated_by',
    )
    organization_fields = ('publication_sites', 'category', 'tags', 'featured_image', 'canonical_site_slug')
    content_update_fields = frozenset(
        {
            'title',
            'type',
            'summary',
            'author',
            'seo_title',
            'seo_description',
            'featured_image',
            'category',
            'tags',
            'canonical_site_slug',
        }
    )

    @admin.display(description=_('Effective status'))
    def effective_status_display(self, obj):
        return obj.Status(obj.effective_status).label if obj else '-'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('category', 'created_by', 'updated_by')

    def has_blog_import_permission(self, request):
        return request.user.has_perms(IMPORT_CORE_PERMISSIONS)

    def _blog_import_url(self):
        return reverse(f'{self.admin_site.name}:blogpost_import')

    def _blog_import_review_url(self, import_id):
        return reverse(
            f'{self.admin_site.name}:blogpost_import_review',
            kwargs={'import_id': import_id},
        )

    def _changelist_url(self):
        return reverse(
            f'{self.admin_site.name}:{self.opts.app_label}_{self.opts.model_name}_changelist'
        )

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        if self.has_blog_import_permission(request):
            extra_context['blog_import_url'] = self._blog_import_url()
        return super().changelist_view(request, extra_context=extra_context)

    def _import_context(self, request, *, title, form, **extra_context):
        context = {
            **self.admin_site.each_context(request),
            'title': title,
            'opts': self.model._meta,
            'form': form,
            'media': self.media + form.media,
            'admin_index_url': reverse(f'{self.admin_site.name}:index'),
            'admin_app_list_url': reverse(
                f'{self.admin_site.name}:app_list',
                kwargs={'app_label': self.opts.app_label},
            ),
            'changelist_url': reverse(
                f'{self.admin_site.name}:{self.opts.app_label}_{self.opts.model_name}_changelist'
            ),
        }
        context.update(extra_context)
        return context

    def _private_import_response(self, response):
        response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        response['Cache-Control'] = 'no-store'
        response['Referrer-Policy'] = 'same-origin'
        response['Content-Language'] = 'en'
        return response

    def _render_private_import(self, request, template_name, context):
        with override('en'):
            response = render(request, template_name, context)
        return self._private_import_response(response)

    def _import_issue_form_error(self, form):
        form.add_error(None, _('Review the blocking import issues before continuing.'))

    def _review_context(
        self,
        request,
        review,
        form,
        *,
        issues=(),
        warnings=(),
        warning_acknowledgement='',
    ):
        parsed = review.parsed
        article = parsed.article
        references = review.references
        site_labels = {
            str(value): str(label)
            for value, label in form.fields['publication_sites'].choices
        }
        source_reference_rows = (
            {
                'label': _('Author'),
                'source': _plain_import_text(article.author.slug, limit=120),
                'resolved': _plain_import_text(references.author.public_author_name, limit=160)
                if references.author
                else _('Not resolved'),
            },
            {
                'label': _('Category'),
                'source': _plain_import_text(article.category.slug, limit=120),
                'resolved': _plain_import_text(references.category.name, limit=160)
                if references.category
                else _('Not resolved'),
            },
            {
                'label': _('Tags'),
                'source': ', '.join(_plain_import_text(tag.slug, limit=100) for tag in article.tags)
                or _('None'),
                'resolved': ' | '.join(_plain_import_text(tag.name, limit=140) for tag in references.tags)
                or _('None resolved'),
                'resolved_items': tuple(_plain_import_text(tag.name, limit=140) for tag in references.tags),
                'hide_source': True,
            },
            {
                'label': _('Publication sites'),
                'source': ', '.join(_plain_import_text(site, limit=100) for site in article.publication_sites)
                or _('None'),
                'resolved': ', '.join(
                    _plain_import_text(site_labels.get(site, site), limit=140)
                    for site in references.resolved_publication_sites
                )
                or _('None resolved'),
            },
            {
                'label': _('Canonical site'),
                'source': _plain_import_text(article.canonical_site, limit=120) or _('None'),
                'resolved': _plain_import_text(
                    site_labels.get(references.resolved_canonical_site, references.resolved_canonical_site),
                    limit=140,
                )
                if references.resolved_canonical_site
                else _('Not resolved'),
            },
        )
        related_articles = tuple(
            {
                'source_slug': _plain_import_text(source.slug, limit=140),
                'title': _plain_import_text(related.title, limit=180) if related else _('Not resolved'),
                'status': _plain_import_text(related.get_status_display(), limit=100) if related else '',
            }
            for source, related in zip(article.related_articles, references.related_posts)
        )
        assets_by_id = {asset.id: asset for asset in parsed.assets}
        featured_asset = assets_by_id.get(article.featured_image)
        featured_asset_data = (
            {
                'id': _plain_import_text(featured_asset.id, limit=120),
                'name': _plain_import_text(featured_asset.name, limit=160),
                'alt_text': _plain_import_text(featured_asset.alt_text, limit=180),
            }
            if featured_asset
            else None
        )
        return self._import_context(
            request,
            title=_('Review imported article'),
            form=form,
            review=review,
            article=article,
            import_session=review.import_session,
            article_title=_plain_import_text(article.title, limit=220),
            article_summary=_plain_import_text(article.summary),
            article_type=_plain_import_text(article.type, limit=80),
            source_filename=_plain_import_text(review.import_session.source_filename, limit=160),
            source_reference_rows=source_reference_rows,
            site_labels=site_labels,
            related_articles=related_articles,
            featured_asset=featured_asset_data,
            block_summaries=_import_block_summaries(parsed),
            image_rows=_import_image_rows(review),
            issue_groups=_group_import_issues((*review.issues, *issues)),
            warning_groups=_group_import_issues((*review.warnings, *warnings)),
            warning_acknowledgement=warning_acknowledgement,
            import_url=self._blog_import_url(),
            review_url=self._blog_import_review_url(review.import_session.id),
            seo_title=_plain_import_text(article.seo.title) or _('Not supplied'),
            seo_description=_plain_import_text(article.seo.description) or _('Not supplied'),
            publication_site_labels=tuple(
                _plain_import_text(site_labels.get(site, site), limit=140)
                for site in references.resolved_publication_sites
            ),
        )

    def import_view(self, request):
        if not self.has_blog_import_permission(request):
            raise PermissionDenied
        if request.method not in {'GET', 'POST'}:
            return self._private_import_response(HttpResponseNotAllowed(['GET', 'POST']))

        form = BlogArticleImportUploadForm(
            request.POST or None,
            request.FILES or None,
        )
        form.fields['source_file'].error_messages['required'] = _(
            'Choose an article file to import.'
        )
        issue_groups = ()
        if request.method == 'POST' and form.is_valid():
            try:
                import_session = validate_and_stage_blog_import(
                    form.cleaned_data['source_file'],
                    form.cleaned_data['image_files'],
                    request.user,
                )
            except BlogImportValidationError as error:
                issue_groups = _group_import_issues(error.issues)
            except BlogImportPermissionError:
                form.add_error(None, _('You do not have permission to import every part of this package.'))
            except (BlogImportError, OSError, ValueError):
                form.add_error(None, _('The import package could not be prepared for review.'))
            except Exception as error:
                logger.error('Blog import staging failed (%s)', type(error).__name__)
                form.add_error(None, _('The import package could not be prepared for review.'))
            else:
                response = redirect(self._blog_import_review_url(import_session.id))
                return self._private_import_response(response)

        context = self._import_context(
            request,
            title=_('Import blog article'),
            form=form,
            issue_groups=issue_groups,
            import_url=self._blog_import_url(),
        )
        return self._render_private_import(request, 'admin/blog/import_form.html', context)

    def blog_import_review_view(self, request, import_id):
        if not self.has_blog_import_permission(request):
            raise PermissionDenied
        if request.method not in {'GET', 'POST'}:
            return self._private_import_response(HttpResponseNotAllowed(['GET', 'POST']))

        try:
            review = get_blog_import_review(
                import_id,
                request.user,
                admin_site_name=self.admin_site.name,
            )
        except PermissionDenied:
            raise
        except BlogImportUnavailable:
            self.message_user(request, _('This import is no longer available.'), messages.ERROR)
            return self._private_import_response(redirect(self._blog_import_url()))
        except BlogImportValidationError:
            self.message_user(request, _('This import could not be reviewed safely.'), messages.ERROR)
            return self._private_import_response(redirect(self._blog_import_url()))
        except Exception as error:
            logger.error('Blog import review failed for %s (%s)', import_id, type(error).__name__)
            self.message_user(request, _('This import could not be reviewed safely.'), messages.ERROR)
            return self._private_import_response(redirect(self._blog_import_url()))

        if request.method == 'GET':
            form = BlogArticleImportReviewForm(review=review)
            context = self._review_context(request, review, form)
            return self._render_private_import(request, 'admin/blog/import_review.html', context)

        action = request.POST.get('action')
        if action in {'change_files', 'cancel'}:
            try:
                discard_staged_import(actor=request.user, import_id=review.import_session.id)
            except PermissionDenied:
                raise
            except BlogImportUnavailable:
                self.message_user(request, _('This import is no longer available.'), messages.ERROR)
                return self._private_import_response(redirect(self._blog_import_url()))
            except (OSError, ValueError):
                self.message_user(request, _('The staged files could not be discarded safely.'), messages.ERROR)
                return self._private_import_response(redirect(self._blog_import_review_url(import_id)))
            else:
                if action == 'change_files':
                    self.message_user(
                        request,
                        _('Staged import discarded. Choose the corrected files to validate again.'),
                        messages.SUCCESS,
                    )
                    return self._private_import_response(redirect(self._blog_import_url()))
                self.message_user(request, _('Import cancelled. No draft was created.'), messages.SUCCESS)
                return self._private_import_response(redirect(self._changelist_url()))

        form = BlogArticleImportReviewForm(
            request.POST,
            review=review,
            action='create',
        )
        if action != 'create':
            form.add_error(None, _('Choose a named import action before continuing.'))
            context = self._review_context(request, review, form)
            return self._render_private_import(request, 'admin/blog/import_review.html', context)

        issues = ()
        warnings = ()
        warning_acknowledgement = ''
        if form.is_valid():
            try:
                reviewed_references = form.reviewed_references()
                validation = validate_reviewed_blog_import(
                    review.import_session,
                    reviewed_references,
                    request.user,
                )
                issues = validation.issues
                warnings = validation.warnings
                if validation.valid:
                    stored_warning_keys = {
                        _import_warning_key(warning)
                        for warning in review.import_session.warnings
                        if isinstance(warning, dict)
                    }
                    new_warnings = tuple(
                        warning
                        for warning in warnings
                        if _import_warning_key(warning) not in stored_warning_keys
                    )
                    if new_warnings and not _matches_import_warning_acknowledgement(
                        request.POST.get('warnings_acknowledged'),
                        review.import_session.id,
                        new_warnings,
                    ):
                        form.add_error(
                            None,
                            _('Review the updated warnings before creating the draft.'),
                        )
                        warning_acknowledgement = _import_warning_acknowledgement(
                            review.import_session.id,
                            new_warnings,
                        )
                        post = None
                    else:
                        post = create_blog_post_from_import(
                            review.import_session,
                            validation.references,
                            request.user,
                        )
                else:
                    post = None
            except BlogImportValidationError as error:
                issues = error.issues
                post = None
            except BlogImportPermissionError:
                raise PermissionDenied
            except BlogImportUnavailable:
                self.message_user(request, _('This import is no longer available.'), messages.ERROR)
                return self._private_import_response(redirect(self._blog_import_url()))
            except (BlogImportError, OSError, ValueError):
                form.add_error(None, _('The draft could not be created. The staged import remains available to retry.'))
                post = None
            except Exception as error:
                logger.error('Blog import creation failed for %s (%s)', import_id, type(error).__name__)
                form.add_error(None, _('The draft could not be created. The staged import remains available to retry.'))
                post = None
            else:
                if post is not None:
                    self.message_user(
                        request,
                        _('Draft imported successfully. Review the article before publishing.'),
                        messages.SUCCESS,
                    )
                    return self._private_import_response(redirect(self._action_url('change', post.pk)))

        if issues:
            self._import_issue_form_error(form)
        context = self._review_context(
            request,
            review,
            form,
            issues=issues,
            warnings=warnings,
            warning_acknowledgement=warning_acknowledgement,
        )
        return self._render_private_import(request, 'admin/blog/import_review.html', context)

    def add_view(self, request, form_url='', extra_context=None):
        if not self.has_add_permission(request):
            raise PermissionDenied

        form = BlogPostQuickStartForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            try:
                post = create_post_draft(
                    title=form.cleaned_data['title'],
                    site_slug=form.cleaned_data['site_slug'],
                    type=form.cleaned_data['type'],
                    category=form.cleaned_data['category'],
                    author=form.cleaned_data['author'],
                    draft_template='blank',
                    actor=request.user,
                )
            except BlogWorkflowError as error:
                form.add_error(None, error.messages)
            else:
                self.message_user(
                    request,
                    _('Draft created. Start writing below.'),
                    messages.SUCCESS,
                )
                return redirect(self._action_url('change', post.pk))

        context = {
            **self.admin_site.each_context(request),
            'title': _('Start a new article'),
            'opts': self.model._meta,
            'form': form,
            'media': self.media + form.media,
            'admin_index_url': reverse(f'{self.admin_site.name}:index'),
            'admin_app_list_url': reverse(
                f'{self.admin_site.name}:app_list',
                kwargs={'app_label': self.opts.app_label},
            ),
            'changelist_url': reverse(
                f'{self.admin_site.name}:{self.opts.app_label}_{self.opts.model_name}_changelist'
            ),
        }
        context.update(extra_context or {})
        return render(request, 'admin/blog/quick_start.html', context)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) or request.user.has_perm('blog.organize_blogpost')

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if not request.user.has_perm('blog.organize_blogpost'):
            fields.extend(self.organization_fields)
        if not request.user.has_perm('blog.change_blogpost'):
            fields.extend(
                field_name
                for field_name in self.form.base_fields
                if field_name not in self.organization_fields
            )
        return tuple(dict.fromkeys(fields))

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        if not request.user.has_perm('blog.organize_blogpost'):
            return
        selected_sites = set(form.cleaned_data.get('publication_sites', ()))
        BlogPostPublication.objects.filter(post=obj).exclude(site_slug__in=selected_sites).delete()
        existing_sites = set(
            BlogPostPublication.objects.filter(post=obj).values_list('site_slug', flat=True)
        )
        BlogPostPublication.objects.bulk_create(
            [
                BlogPostPublication(post=obj, site_slug=site_slug)
                for site_slug in selected_sites - existing_sites
            ]
        )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if not change:
            return
        main_form_changed = bool(set(form.changed_data) & self.content_update_fields)
        inline_changed = any(formset.has_changed() for formset in formsets)
        if main_form_changed or inline_changed:
            BlogPost.objects.filter(pk=form.instance.pk).update(content_updated_at=timezone.now())

    def get_form(self, request, obj=None, **kwargs):
        form_class = super().get_form(request, obj, **kwargs)
        if not request.user.has_perm('blog.organize_blogpost'):
            form_class.base_fields['publication_sites'].disabled = True
        return form_class

    def get_formset_kwargs(self, request, obj, inline, prefix):
        formset_kwargs = super().get_formset_kwargs(request, obj, inline, prefix)
        if isinstance(inline, BlogRelatedInline):
            formset_kwargs['publication_sites_editable'] = request.user.has_perm(
                'blog.organize_blogpost'
            )
        elif inline.model in {BlogFAQBlock, BlogInternalLinkBlock, BlogRichTextBlock}:
            formset_kwargs['publication_sites_editable'] = request.user.has_perm('blog.organize_blogpost')
        return formset_kwargs

    def render_change_form(self, request, context, add=False, change=False, form_url='', obj=None):
        context['blog_action_urls'] = self._action_urls(obj) if obj else {}
        context['blog_internal_link_destinations'] = get_internal_link_editor_destinations()
        return super().render_change_form(
            request,
            context,
            add=add,
            change=change,
            form_url=form_url,
            obj=obj,
        )

    def _action_url(self, action, object_id):
        url_name = (
            f'{self.opts.app_label}_{self.opts.model_name}_{action}'
            if action == 'change'
            else f'{self.opts.model_name}_{action}'
        )
        return reverse(
            f'{self.admin_site.name}:{url_name}',
            args=[object_id],
        )

    def _action_urls(self, obj):
        return {
            action: self._action_url(action, obj.pk)
            for action in ('preview', 'mark_ready', 'publish', 'schedule', 'unpublish', 'mark_reviewed')
        }

    def get_urls(self):
        custom_urls = [
            path('import/', self.admin_site.admin_view(self.import_view), name='blogpost_import'),
            path(
                'import/<uuid:import_id>/',
                self.admin_site.admin_view(self.blog_import_review_view),
                name='blogpost_import_review',
            ),
            path('<path:object_id>/preview/', self.admin_site.admin_view(self.preview_view), name='blogpost_preview'),
            path('<path:object_id>/mark-ready/', self.admin_site.admin_view(self.mark_ready_view), name='blogpost_mark_ready'),
            path('<path:object_id>/publish/', self.admin_site.admin_view(self.publish_view), name='blogpost_publish'),
            path('<path:object_id>/schedule/', self.admin_site.admin_view(self.schedule_view), name='blogpost_schedule'),
            path('<path:object_id>/unpublish/', self.admin_site.admin_view(self.unpublish_view), name='blogpost_unpublish'),
            path('<path:object_id>/mark-reviewed/', self.admin_site.admin_view(self.mark_reviewed_view), name='blogpost_mark_reviewed'),
        ]
        return custom_urls + super().get_urls()

    def _get_post(self, request, object_id):
        return get_object_or_404(self.get_queryset(request), pk=object_id)

    def preview_view(self, request, object_id):
        post = self._get_post(request, object_id)
        if not self.has_view_or_change_permission(request, post):
            return self._forbidden(request)
        if request.method != 'GET':
            return self._private_preview_response(HttpResponseNotAllowed(['GET']))
        from .rendering import BLOG_FALLBACK_STYLESHEET, build_article_context
        from .urls_helpers import get_blog_url_namespace

        with override('en'):
            preview_form = PreviewWebsiteForm(request.GET if 'site' in request.GET else None)
            change_url = request.build_absolute_uri(self._action_url('change', post.pk))
            preview_context = {
                'post': post,
                'change_url': change_url,
                'preview_website_form': preview_form,
                'preview_has_site_choices': bool(preview_form.fields['site'].choices),
                'blog_stylesheet': BLOG_FALLBACK_STYLESHEET,
            }

            if 'site' not in request.GET:
                default_site_slug = resolve_preview_site_slug(
                    post,
                    choices=preview_form.fields['site'].choices,
                )
                if default_site_slug:
                    response = redirect(f'{request.path}?site={default_site_slug}')
                    return self._private_preview_response(response)
                return self._render_unavailable_preview(request, preview_context)

            if not preview_form.is_valid():
                return self._render_unavailable_preview(request, preview_context)

            site_slug = preview_form.cleaned_data['site']
            site = get_site_definition(site_slug)
            template_name = get_preview_blog_template(site) if site else None
            if not template_name:
                return self._render_unavailable_preview(request, preview_context)

            publication_sites = get_publication_site_slugs(post)
            request.current_app = get_blog_url_namespace(site_slug)
            related_posts = get_related_public_posts(
                post=post,
                site_slug=site_slug,
                source_site_slugs=publication_sites,
            )
            context = build_article_context(
                post,
                request=request,
                site_slug=site_slug,
                related_posts=related_posts,
                preview=True,
            )
            context.update(
                {
                    'change_url': change_url,
                    'preview_website_form': preview_form,
                    'preview_has_site_choices': bool(preview_form.fields['site'].choices),
                    'preview_site_unassigned': site_slug not in publication_sites,
                }
            )
            response = render(request, template_name, context)
            return self._private_preview_response(response)

    def _render_unavailable_preview(self, request, context):
        response = render(request, 'admin/blog/preview.html', context)
        return self._private_preview_response(response)

    def _private_preview_response(self, response):
        return self._private_import_response(response)

    def _forbidden(self, request):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied

    def _post_action(self, request, post, form, service, *, success_message):
        if request.method != 'POST':
            return HttpResponseNotAllowed(['POST'])
        form = form(request.POST)
        if form.is_valid():
            try:
                service(post, actor=request.user)
            except BlogWorkflowError as error:
                form.add_error(None, error.messages)
            else:
                self.message_user(request, success_message, messages.SUCCESS)
                return redirect(self._action_url('change', post.pk))
        return render(
            request,
            'admin/blog/action_form.html',
            {'form': form, 'post': post, 'change_url': self._action_url('change', post.pk)},
        )

    def mark_ready_view(self, request, object_id):
        post = self._get_post(request, object_id)
        if not request.user.has_perm('blog.change_blogpost'):
            return self._forbidden(request)
        try:
            if request.method != 'POST':
                return HttpResponseNotAllowed(['POST'])
            mark_post_ready(post, actor=request.user)
        except BlogWorkflowError as error:
            self.message_user(request, str(error), messages.ERROR)
        else:
            self.message_user(request, _('Article marked ready.'), messages.SUCCESS)
        return redirect(self._action_url('change', post.pk))

    def publish_view(self, request, object_id):
        post = self._get_post(request, object_id)
        if not request.user.has_perm('blog.publish_blogpost'):
            return self._forbidden(request)
        form = ConfirmActionForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            try:
                publish_post_now(post, actor=request.user)
            except BlogWorkflowError as error:
                form.add_error(None, error.messages)
            else:
                self.message_user(request, _('Article published.'), messages.SUCCESS)
                return redirect(self._action_url('change', post.pk))
        return render(
            request,
            'admin/blog/action_form.html',
            {
                'form': form,
                'post': post,
                'action_title': _('Publish article'),
                'change_url': self._action_url('change', post.pk),
            },
        )

    def schedule_view(self, request, object_id):
        post = self._get_post(request, object_id)
        if not request.user.has_perm('blog.publish_blogpost'):
            return self._forbidden(request)
        form = SchedulePostForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            try:
                schedule_post(post, publish_at=form.cleaned_data['publish_at'], actor=request.user)
            except BlogWorkflowError as error:
                form.add_error(None, error.messages)
            else:
                self.message_user(request, _('Article scheduled.'), messages.SUCCESS)
                return redirect(self._action_url('change', post.pk))
        return render(
            request,
            'admin/blog/action_form.html',
            {
                'form': form,
                'post': post,
                'action_title': _('Schedule article'),
                'change_url': self._action_url('change', post.pk),
            },
        )

    def unpublish_view(self, request, object_id):
        post = self._get_post(request, object_id)
        if not request.user.has_perm('blog.unpublish_blogpost'):
            return self._forbidden(request)
        form = ConfirmActionForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            try:
                unpublish_post(post, actor=request.user)
            except BlogWorkflowError as error:
                form.add_error(None, error.messages)
            else:
                self.message_user(request, _('Article unpublished.'), messages.SUCCESS)
                return redirect(self._action_url('change', post.pk))
        return render(
            request,
            'admin/blog/action_form.html',
            {
                'form': form,
                'post': post,
                'action_title': _('Unpublish article'),
                'change_url': self._action_url('change', post.pk),
            },
        )

    def mark_reviewed_view(self, request, object_id):
        post = self._get_post(request, object_id)
        if not request.user.has_perm('blog.publish_blogpost'):
            return self._forbidden(request)
        form = MarkReviewedForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            try:
                mark_post_reviewed(post, reviewed_on=form.cleaned_data['reviewed_on'], actor=request.user)
            except BlogWorkflowError as error:
                form.add_error(None, error.messages)
            else:
                self.message_user(request, _('Article marked reviewed.'), messages.SUCCESS)
                return redirect(self._action_url('change', post.pk))
        return render(
            request,
            'admin/blog/action_form.html',
            {
                'form': form,
                'post': post,
                'action_title': _('Mark article reviewed'),
                'change_url': self._action_url('change', post.pk),
            },
        )


class BlogImageConsistencyAdminMixin:
    upload_fields = ()
    consistency_sides = (None,)

    def _record_processed_side(self, request, obj, side=None):
        state = getattr(request, BLOG_IMAGE_ACTION_STATE_ATTR, None)
        if state is None:
            state = {'object_id': obj.pk, 'sides': set()}
            setattr(request, BLOG_IMAGE_ACTION_STATE_ATTR, state)
        state['sides'].add(side)

    def _retry_url(self, object_id=None):
        action = 'change' if object_id else 'add'
        name = (
            f'{self.admin_site.name}:'
            f'{self.opts.app_label}_{self.opts.model_name}_{action}'
        )
        args = (object_id,) if object_id else None
        return reverse(name, args=args)

    def _retry_response(self, request, object_id=None):
        # Avoid showing Django's success message alongside the retry warning.
        list(messages.get_messages(request))
        self.message_user(
            request,
            _('The image change could not be completed. Open the image and try again.'),
            messages.ERROR,
        )
        return redirect(self._retry_url(object_id))

    def _delete_retry_response(self, request, object_id=None):
        list(messages.get_messages(request))
        self.message_user(
            request,
            _('The image deletion could not be completed. Check the image list and try again.'),
            messages.ERROR,
        )
        if object_id:
            return redirect(self._retry_url(object_id))
        changelist_name = (
            f'{self.admin_site.name}:'
            f'{self.opts.app_label}_{self.opts.model_name}_changelist'
        )
        return redirect(reverse(changelist_name))

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        upload_requested = request.method == 'POST' and any(
            field_name in request.FILES for field_name in self.upload_fields
        )
        try:
            response = super().changeform_view(
                request,
                object_id,
                form_url,
                extra_context,
            )
        except (DatabaseError, OSError, ValidationError):
            if not upload_requested:
                raise
            logger.exception(
                'Blog image admin action failed for %s %s.',
                self.opts.model_name,
                object_id or 'new',
            )
            return self._retry_response(request, object_id)

        state = getattr(request, BLOG_IMAGE_ACTION_STATE_ATTR, None)
        if not state:
            return response
        try:
            obj = self.model.objects.get(pk=state['object_id'])
        except self.model.DoesNotExist:
            logger.error(
                'Blog image admin action committed without %s %s.',
                self.opts.model_name,
                state['object_id'],
            )
            return self._retry_response(request)
        if any(
            not _blog_image_side_is_consistent(obj, side)
            for side in state['sides']
        ):
            logger.error(
                'Blog image admin action left incomplete files for %s %s.',
                self.opts.model_name,
                obj.pk,
            )
            return self._retry_response(request, obj.pk)
        return response

    def _stored_file_references(self, obj):
        references = []
        for side in self.consistency_sides:
            prefix = f'{side}_' if side else ''
            for suffix in BLOG_IMAGE_FILE_SUFFIXES:
                stored_file = getattr(obj, f'{prefix}{suffix}')
                if stored_file and stored_file.name:
                    references.append((stored_file.storage, stored_file.name))
        return references

    @staticmethod
    def _remaining_files(references):
        remaining = []
        for storage, name in references:
            try:
                if storage.exists(name):
                    remaining.append((storage, name))
            except OSError:
                remaining.append((storage, name))
        return remaining

    def delete_view(self, request, object_id, extra_context=None):
        if request.method != 'POST':
            return super().delete_view(request, object_id, extra_context)
        obj = self.get_object(request, object_id)
        references = self._stored_file_references(obj) if obj else []
        try:
            response = super().delete_view(request, object_id, extra_context)
        except (DatabaseError, OSError):
            logger.exception(
                'Blog image admin delete failed for %s %s.',
                self.opts.model_name,
                object_id,
            )
            return self._delete_retry_response(request, object_id)

        row_exists = self.model.objects.filter(pk=object_id).exists()
        remaining = self._remaining_files(references)
        if not row_exists and remaining:
            for storage, name in remaining:
                try:
                    storage.delete(name)
                except OSError:
                    pass
            remaining = self._remaining_files(references)
        if row_exists or remaining:
            logger.error(
                'Blog image admin delete left inconsistent state for %s %s.',
                self.opts.model_name,
                object_id,
            )
            retry_object_id = object_id if row_exists else None
            return self._delete_retry_response(request, retry_object_id)
        return response


@admin.register(BlogImage)
class BlogImageAdmin(BlogImageConsistencyAdminMixin, admin.ModelAdmin):
    upload_fields = ('original',)
    form = BlogImageAdminForm
    list_display = ('__str__', 'width', 'height', 'processing_status', 'created_at')
    list_filter = ('processing_status', 'is_decorative', 'is_feature')
    search_fields = ('name', 'alt_text', 'caption_title', 'caption_text', 'processing_error')
    readonly_fields = (
        'rendition_480',
        'rendition_800',
        'rendition_1200',
        'rendition_1600',
        'width',
        'height',
        'processing_status',
        'processing_error',
        'created_by',
        'created_at',
        'updated_at',
    )

    def save_model(self, request, obj, form, change):
        previous_original = None
        previous_state = None
        replacing_original = change and obj.pk and 'original' in form.changed_data
        if replacing_original:
            previous = BlogImage.objects.get(pk=obj.pk)
            previous_original = previous.original
            previous_state = image_state(previous)
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        if form.cleaned_data.get('original') and (not change or 'original' in form.changed_data):
            try:
                process_image(obj, previous_state=previous_state)
            except ValidationError as error:
                self.message_user(request, '; '.join(error.messages), messages.ERROR)
            else:
                self._record_processed_side(request, obj)
                if previous_original and previous_original.name != obj.original.name:
                    previous_original.delete(save=False)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            self.delete_model(request, obj)

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and request.user.has_perm('blog.organize_blogpost')


@admin.register(BlogImageComparison)
class BlogImageComparisonAdmin(BlogImageConsistencyAdminMixin, admin.ModelAdmin):
    upload_fields = ('first_original', 'second_original')
    consistency_sides = ('first', 'second')
    form = BlogImageComparisonAdminForm

    class Media:
        css = {'all': ('blog/css/admin.css',)}

    list_display = (
        '__str__',
        'pair_preview',
        'first_processing_status',
        'second_processing_status',
        'first_width',
        'second_width',
        'created_at',
    )
    list_filter = ('first_processing_status', 'second_processing_status')
    search_fields = (
        'name',
        'first_alt_text',
        'second_alt_text',
        'caption_title',
        'caption_text',
        'first_processing_error',
        'second_processing_error',
    )
    fieldsets = (
        (None, {'fields': ('name',)}),
        (_('First image'), {
            'fields': (
                'first_original',
                'first_preview',
                'first_alt_text',
                'first_rendition_480',
                'first_rendition_800',
                'first_rendition_1200',
                'first_rendition_1600',
                'first_width',
                'first_height',
                'first_processing_status',
                'first_processing_error',
            ),
        }),
        (_('Second image'), {
            'fields': (
                'second_original',
                'second_preview',
                'second_alt_text',
                'second_rendition_480',
                'second_rendition_800',
                'second_rendition_1200',
                'second_rendition_1600',
                'second_width',
                'second_height',
                'second_processing_status',
                'second_processing_error',
            ),
        }),
        (_('Shared caption'), {'fields': ('caption_title', 'caption_text')}),
        (_('Audit'), {'fields': ('created_by', 'created_at', 'updated_at')}),
    )
    readonly_fields = (
        'first_rendition_480',
        'first_rendition_800',
        'first_rendition_1200',
        'first_rendition_1600',
        'first_width',
        'first_height',
        'first_processing_status',
        'first_processing_error',
        'second_rendition_480',
        'second_rendition_800',
        'second_rendition_1200',
        'second_rendition_1600',
        'second_width',
        'second_height',
        'second_processing_status',
        'second_processing_error',
        'created_by',
        'created_at',
        'updated_at',
        'first_preview',
        'second_preview',
    )

    def _side_preview(self, obj, side):
        if not obj:
            return ''
        rendition = getattr(obj, f'{side}_rendition_480')
        width = getattr(obj, f'{side}_width')
        height = getattr(obj, f'{side}_height')
        if not rendition or not width or not height:
            return ''
        preview_width = min(width, 240)
        preview_height = max(1, round(height * preview_width / width))
        return format_html(
            '<img src="{}" alt="{}" width="{}" height="{}" loading="lazy">',
            rendition.url,
            getattr(obj, f'{side}_alt_text'),
            preview_width,
            preview_height,
        )

    @admin.display(description=_('Preview'))
    def pair_preview(self, obj):
        first = self._side_preview(obj, 'first')
        second = self._side_preview(obj, 'second')
        if not first and not second:
            return '-'
        return format_html(
            '<span class="blog-image-comparison-admin-preview">{}{}</span>',
            first,
            second,
        )

    @admin.display(description=_('Preview'))
    def first_preview(self, obj):
        preview = self._side_preview(obj, 'first')
        if not preview:
            return '-'
        return format_html(
            '<span class="blog-image-comparison-admin-preview blog-image-comparison-admin-preview--single">{}</span>',
            preview,
        )

    @admin.display(description=_('Preview'))
    def second_preview(self, obj):
        preview = self._side_preview(obj, 'second')
        if not preview:
            return '-'
        return format_html(
            '<span class="blog-image-comparison-admin-preview blog-image-comparison-admin-preview--single">{}</span>',
            preview,
        )

    def save_model(self, request, obj, form, change):
        previous_files = {}
        previous_states = {}
        if change and obj.pk:
            previous = BlogImageComparison.objects.get(pk=obj.pk)
            for side in ('first', 'second'):
                previous_states[side] = comparison_image_state(previous, side)
                previous_files[side] = [
                    getattr(previous, f'{side}_{suffix}')
                    for suffix in (
                        'original',
                        'rendition_480',
                        'rendition_800',
                        'rendition_1200',
                        'rendition_1600',
                    )
                ]
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

        for side in ('first', 'second'):
            if change and f'{side}_original' not in form.changed_data:
                continue
            try:
                process_comparison_image(
                    obj,
                    side,
                    previous_state=previous_states.get(side),
                )
            except ValidationError as error:
                self.message_user(request, '; '.join(error.messages), messages.ERROR)
                continue
            self._record_processed_side(request, obj, side)
            for stored_file in previous_files.get(side, []):
                if stored_file and stored_file.name != getattr(obj, f'{side}_original').name:
                    stored_file.delete(save=False)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            self.delete_model(request, obj)

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and request.user.has_perm('blog.organize_blogpost')


@admin.register(BlogCategory)
class BlogCategoryAdmin(admin.ModelAdmin):
    form = BlogCategoryAdminForm
    fields = ('name', 'available_websites')
    list_display = ('name', 'website_names')
    list_filter = ('websites',)
    search_fields = ('name', 'slug')

    @admin.display(description=_('Websites'))
    def website_names(self, obj):
        return ', '.join(str(site) for site in obj.websites.all())

    def has_add_permission(self, request):
        return super().has_add_permission(request) and request.user.has_perm('blog.organize_blogpost')

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and request.user.has_perm('blog.organize_blogpost')

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and request.user.has_perm('blog.organize_blogpost')


@admin.register(BlogTag)
class BlogTagAdmin(admin.ModelAdmin):
    form = BlogTagAdminForm
    fields = ('name', 'available_websites')
    list_display = ('name', 'website_names')
    list_filter = ('websites',)
    search_fields = ('name', 'slug')

    @admin.display(description=_('Websites'))
    def website_names(self, obj):
        return ', '.join(str(site) for site in obj.websites.all())

    def has_add_permission(self, request):
        return super().has_add_permission(request) and request.user.has_perm('blog.organize_blogpost')

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and request.user.has_perm('blog.organize_blogpost')

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and request.user.has_perm('blog.organize_blogpost')


@admin.register(BlogPostRelated)
class BlogPostRelatedAdmin(admin.ModelAdmin):
    list_display = ('post', 'related_post', 'position')
    list_filter = ('post',)

    def has_add_permission(self, request):
        return super().has_add_permission(request) and request.user.has_perm('blog.organize_blogpost')

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and request.user.has_perm('blog.organize_blogpost')

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and request.user.has_perm('blog.organize_blogpost')
