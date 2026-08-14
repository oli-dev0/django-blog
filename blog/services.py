from dataclasses import dataclass
from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.core.sites import get_blog_site_slug_choices

from .models import (
    BLOG_BLOCK_MODELS,
    AuthorProfile,
    BlogCalloutBlock,
    BlogCategory,
    BlogChecklistBlock,
    BlogCodeBlock,
    BlogEmbedSharingBlock,
    BlogFAQBlock,
    BlogHeadingBlock,
    BlogImageBlock,
    BlogImageComparisonBlock,
    BlogInternalLinkBlock,
    BlogPost,
    BlogPostPublication,
    BlogRichTextBlock,
    BlogSourceLinkBlock,
)
from .embed_sharing import (
    INVALID_EMBED_REFERENCE_MESSAGE,
    EmbedSharingError,
    InvalidEmbedReference,
    fingerprint_for_block,
    normalize_embed_reference,
    verify_article_embeds,
)
from .faq import normalize_faq_items

BLOG_DRAFT_TEMPLATES = {
    'blank': (),
    'guide': (
        'What you need',
        'Step-by-step',
        'Next steps',
    ),
    'release_notes': (
        'Highlights',
        'Changes',
        'Upgrade notes',
    ),
    'announcement': (
        'What is changing',
        'Why it matters',
        'What happens next',
    ),
}
BLOG_DRAFT_TEMPLATE_CHOICES = (
    ('blank', _('Blank article — start with empty content')),
    ('guide', _('Guide — preparation, step-by-step, and next steps')),
    ('release_notes', _('Release notes — highlights, changes, and upgrade notes')),
    ('announcement', _('Announcement — what is changing, why it matters, and what happens next')),
)
@dataclass
class BlogWorkflowError(Exception):
    messages: list[str]

    def __str__(self):
        return '; '.join(str(message) for message in self.messages)


def _require_permission(actor, permission):
    if actor is None or not actor.is_authenticated or not actor.has_perm(permission):
        raise BlogWorkflowError([_('You do not have permission to perform this action.')])


def _locked_post(post):
    if not post.pk:
        raise BlogWorkflowError([_('Save the article before changing its publication state.')])
    return BlogPost.objects.select_for_update().get(pk=post.pk)


def _unique_post_slug(title):
    base_slug = slugify(title)[:210] or 'article'
    slug = base_slug
    suffix = 2
    while BlogPost.objects.filter(slug=slug).exists():
        slug = f'{base_slug}-{suffix}'
        suffix += 1
    return slug


@transaction.atomic
def create_post_draft(*, title, site_slug, type, category, author, draft_template, actor):
    _require_permission(actor, 'blog.add_blogpost')
    _require_permission(actor, 'blog.change_blogpost')
    _require_permission(actor, 'blog.add_blogrichtextblock')

    headings = BLOG_DRAFT_TEMPLATES.get(draft_template)
    if headings is None:
        raise BlogWorkflowError([_('Choose a valid starting template.')])
    if headings:
        _require_permission(actor, 'blog.add_blogheadingblock')

    valid_site_slugs = {
        slug
        for slug, _label in get_blog_site_slug_choices()
    }
    if site_slug not in valid_site_slugs:
        raise BlogWorkflowError([_('Choose a configured blog site.')])

    valid_types = {value for value, _label in BlogPost.Type.choices}
    if type not in valid_types:
        raise BlogWorkflowError([_('Choose a valid type.')])
    if not isinstance(category, BlogCategory) or not category.pk:
        raise BlogWorkflowError([_('Choose a valid category.')])
    if not category.websites.filter(slug=site_slug).exists():
        raise BlogWorkflowError([_('Choose a category available on the selected website.')])
    if not isinstance(author, AuthorProfile) or not author.pk:
        raise BlogWorkflowError([_('Choose a valid author.')])

    title = title.strip()
    if not title:
        raise BlogWorkflowError([_('Add a title before creating the draft.')])

    post = BlogPost.objects.create(
        title=title,
        slug=_unique_post_slug(title),
        type=type,
        category=category,
        author=author,
        canonical_site_slug=site_slug,
        created_by=actor,
        updated_by=actor,
    )
    BlogPostPublication.objects.create(post=post, site_slug=site_slug)

    if draft_template != 'blank':
        ordering = 10
        BlogRichTextBlock.objects.create(parent=post, region='main', ordering=ordering, body='')
        for heading in headings:
            ordering += 10
            BlogHeadingBlock.objects.create(
                parent=post,
                region='main',
                ordering=ordering,
                text=heading,
                anchor=slugify(heading),
            )
            ordering += 10
            BlogRichTextBlock.objects.create(parent=post, region='main', ordering=ordering, body='')

    return post


def _block_queryset(post):
    blocks = []
    for block_model in BLOG_BLOCK_MODELS:
        queryset = block_model.objects.filter(parent=post, region='main')
        if block_model is BlogImageBlock:
            queryset = queryset.select_related('image')
        elif block_model is BlogImageComparisonBlock:
            queryset = queryset.select_related('comparison')
        blocks.extend(queryset)
    return sorted(blocks, key=lambda block: (block.ordering, block.pk))


def _embed_blocks(post, *, for_update=False):
    queryset = BlogEmbedSharingBlock.objects.filter(parent=post, region='main')
    if for_update:
        queryset = queryset.select_for_update()
    return list(queryset.order_by('ordering', 'pk'))


def _embed_position(blocks, block_id):
    for position, block in enumerate(blocks, start=1):
        if block.pk == block_id:
            return position
    return None


def _embed_workflow_error(error, blocks):
    position = _embed_position(blocks, error.block_id)
    if position is None:
        return BlogWorkflowError([str(error)])
    return BlogWorkflowError([
        _('Embed block %(position)s: %(message)s') % {
            'position': position,
            'message': str(error),
        },
    ])


def _verify_post_embeds(post):
    blocks = _embed_blocks(post)
    if not blocks:
        return ()
    try:
        return verify_article_embeds(blocks)
    except EmbedSharingError as error:
        raise _embed_workflow_error(error, blocks) from error


def _verified_embeds_match(post, verified_embeds):
    """Lock and compare the ordered provider identities; captions are not identities."""

    try:
        current = tuple(
            fingerprint_for_block(block)
            for block in _embed_blocks(post, for_update=True)
        )
    except (EmbedSharingError, TypeError, ValueError):
        return False
    expected = tuple(verified.fingerprint for verified in verified_embeds)
    return current == expected


def _require_verified_embeds(post, verified_embeds):
    if not _verified_embeds_match(post, verified_embeds):
        raise BlogWorkflowError([
            _('Embedded content changed while it was being verified. Review the embeds and try again.'),
        ])


def _image_ready(image, *, field_name, messages):
    if image is None:
        return
    if image.processing_status != image.ProcessingStatus.READY:
        messages.append(_('%(field)s is not ready for publication.') % {'field': field_name})
    elif not image.has_publication_files():
        messages.append(_('%(field)s is missing one or more stored image files.') % {'field': field_name})
    if image.is_decorative:
        messages.append(_('%(field)s cannot be decorative when attached to a published article.') % {'field': field_name})
    elif not image.alt_text.strip():
        messages.append(_('%(field)s must have meaningful alternative text.') % {'field': field_name})


def _comparison_ready(comparison, messages):
    if comparison is None:
        return
    for side, label in (
        ('first', _('First comparison image')),
        ('second', _('Second comparison image')),
    ):
        status = getattr(comparison, f'{side}_processing_status')
        if status != comparison.ProcessingStatus.READY:
            messages.append(_('%(field)s is not ready for publication.') % {'field': label})
        elif not comparison.has_publication_files(side):
            messages.append(_('%(field)s is missing one or more stored image files.') % {'field': label})
        if not getattr(comparison, f'{side}_alt_text').strip():
            messages.append(_('%(field)s must have meaningful alternative text.') % {'field': label})


def _block_has_content(block):
    if isinstance(block, BlogHeadingBlock):
        return bool(block.text.strip() and block.anchor.strip())
    if isinstance(block, BlogRichTextBlock):
        return bool(strip_tags(block.body or '').strip())
    if isinstance(block, BlogFAQBlock):
        try:
            return bool(normalize_faq_items(block.items))
        except ValidationError:
            return False
    if isinstance(block, BlogChecklistBlock):
        return bool([item for item in block.items if str(item).strip()])
    if isinstance(block, BlogCodeBlock):
        return bool(block.code.strip())
    if isinstance(block, BlogCalloutBlock):
        return bool(strip_tags(block.body or '').strip())
    if isinstance(block, BlogSourceLinkBlock):
        return bool(block.label.strip() and block.url.strip())
    if isinstance(block, BlogInternalLinkBlock):
        return bool(block.destination_key.strip() and block.label.strip())
    if isinstance(block, BlogEmbedSharingBlock):
        try:
            normalize_embed_reference(block.platform, block.url)
        except (InvalidEmbedReference, TypeError, ValueError):
            return False
        return True
    return True


def validate_post_for_publication(post, *, now=None, scheduled=False, require_publication_time=False):
    now = now or timezone.now()
    messages = []
    if not post.title.strip():
        messages.append(_('Add a title before publishing.'))
    if not post.slug.strip():
        messages.append(_('Add a slug before publishing.'))
    if not post.summary.strip():
        messages.append(_('Add a summary before publishing.'))
    if not post.type:
        messages.append(_('Choose a type before publishing.'))
    if require_publication_time and not post.published_at:
        messages.append(_('Add a publication date before publishing.'))

    site_slugs = set(post.publications.values_list('site_slug', flat=True))
    if not site_slugs:
        messages.append(_('Assign the article to at least one site.'))
    if not post.canonical_site_slug:
        messages.append(_('Choose a canonical site before publishing.'))
    elif post.canonical_site_slug not in site_slugs:
        messages.append(_('The canonical site must be one of the assigned sites.'))
    if post.category_id:
        missing_category_sites = site_slugs - set(
            post.category.websites.values_list('slug', flat=True)
        )
        if missing_category_sites:
            messages.append(_('The category must be available on every assigned site.'))
    unavailable_tags = [
        tag.name
        for tag in post.tags.all()
        if site_slugs - set(tag.websites.values_list('slug', flat=True))
    ]
    if unavailable_tags:
        messages.append(
            _('Every tag must be available on every assigned site: %(tags)s.')
            % {'tags': ', '.join(unavailable_tags)}
        )

    blocks = _block_queryset(post)
    if not blocks or not any(_block_has_content(block) for block in blocks):
        messages.append(_('Add meaningful article content before publishing.'))

    heading_anchors = [block.anchor for block in blocks if isinstance(block, BlogHeadingBlock)]
    if len(heading_anchors) != len(set(heading_anchors)):
        messages.append(_('Heading anchors must be unique.'))

    _image_ready(post.featured_image, field_name=_('Featured image'), messages=messages)
    for block in blocks:
        if isinstance(block, BlogImageBlock):
            _image_ready(block.image, field_name=_('Body image'), messages=messages)
        elif isinstance(block, BlogImageComparisonBlock):
            _comparison_ready(block.comparison, messages)
        elif isinstance(block, BlogInternalLinkBlock):
            from django.urls import NoReverseMatch

            from .internal_links import resolve_internal_link

            try:
                resolve_internal_link(block.destination_key, site_slugs)
            except ValidationError as error:
                messages.extend(error.messages)
            except NoReverseMatch:
                messages.append(_('An internal link destination is not configured correctly.'))
        elif isinstance(block, BlogRichTextBlock):
            from .internal_links import validate_inline_internal_links

            try:
                validate_inline_internal_links(block.body, site_slugs)
            except ValidationError as error:
                messages.extend(error.messages)
        elif isinstance(block, BlogFAQBlock):
            from .internal_links import validate_inline_internal_links

            try:
                items = normalize_faq_items(block.items)
                for item in items:
                    validate_inline_internal_links(item['answer'], site_slugs)
            except ValidationError as error:
                messages.extend(error.messages)
        elif isinstance(block, BlogEmbedSharingBlock):
            try:
                normalize_embed_reference(block.platform, block.url)
            except (InvalidEmbedReference, TypeError, ValueError):
                position = _embed_position(
                    [item for item in blocks if isinstance(item, BlogEmbedSharingBlock)],
                    block.pk,
                )
                if position is not None:
                    messages.append(_(
                        'Embed block %(position)s: %(message)s'
                    ) % {
                        'position': position,
                        'message': str(INVALID_EMBED_REFERENCE_MESSAGE),
                    })
    if scheduled and (post.published_at is None or post.published_at <= now):
        messages.append(_('Choose a future publication time.'))

    try:
        post.full_clean(exclude=['status', 'published_at'])
    except ValidationError as error:
        messages.extend(error.messages)

    return list(dict.fromkeys(str(message) for message in messages))


def _validate_or_raise(post, *, scheduled=False, require_publication_time=False):
    messages = validate_post_for_publication(
        post,
        scheduled=scheduled,
        require_publication_time=require_publication_time,
    )
    if messages:
        raise BlogWorkflowError(messages)


def mark_post_ready(post, *, actor):
    _require_permission(actor, 'blog.change_blogpost')
    verified_embeds = _verify_post_embeds(post)
    with transaction.atomic():
        locked_post = _locked_post(post)
        if locked_post.status not in {BlogPost.Status.DRAFT, BlogPost.Status.UNPUBLISHED}:
            raise BlogWorkflowError([_('Only draft or unpublished articles can be marked ready.')])
        _require_verified_embeds(locked_post, verified_embeds)
        _validate_or_raise(locked_post)
        locked_post.status = BlogPost.Status.READY
        locked_post.updated_by_id = actor.pk
        locked_post.save(update_fields=['status', 'updated_by'])
        return locked_post


def publish_post_now(post, *, actor, confirm_slug_change=False):
    _require_permission(actor, 'blog.publish_blogpost')
    verified_embeds = _verify_post_embeds(post)
    with transaction.atomic():
        locked_post = _locked_post(post)
        if locked_post.status not in {
            BlogPost.Status.DRAFT,
            BlogPost.Status.READY,
            BlogPost.Status.UNPUBLISHED,
            BlogPost.Status.SCHEDULED,
            BlogPost.Status.PUBLISHED,
        }:
            raise BlogWorkflowError([_('This article cannot be published from its current state.')])
        _require_verified_embeds(locked_post, verified_embeds)
        now = timezone.now()
        if locked_post.published_at is None or (
            locked_post.status == BlogPost.Status.SCHEDULED and locked_post.published_at > now
        ):
            locked_post.published_at = now
        _validate_or_raise(locked_post, require_publication_time=True)
        locked_post.status = BlogPost.Status.PUBLISHED
        locked_post.updated_by_id = actor.pk
        locked_post.save(update_fields=['status', 'published_at', 'updated_by'])
        return locked_post


def schedule_post(post, *, publish_at: datetime, actor, confirm_slug_change=False):
    _require_permission(actor, 'blog.publish_blogpost')
    verified_embeds = _verify_post_embeds(post)
    with transaction.atomic():
        locked_post = _locked_post(post)
        if locked_post.status not in {BlogPost.Status.DRAFT, BlogPost.Status.READY, BlogPost.Status.UNPUBLISHED}:
            raise BlogWorkflowError([_('Only draft, ready, or unpublished articles can be scheduled.')])
        _require_verified_embeds(locked_post, verified_embeds)
        locked_post.published_at = publish_at
        _validate_or_raise(locked_post, scheduled=True, require_publication_time=True)
        locked_post.status = BlogPost.Status.SCHEDULED
        locked_post.updated_by_id = actor.pk
        locked_post.save(update_fields=['status', 'published_at', 'updated_by'])
        return locked_post


@transaction.atomic
def unpublish_post(post, *, actor):
    _require_permission(actor, 'blog.unpublish_blogpost')
    locked_post = _locked_post(post)
    if locked_post.status not in {BlogPost.Status.PUBLISHED, BlogPost.Status.SCHEDULED}:
        raise BlogWorkflowError([_('Only published or scheduled articles can be unpublished.')])
    locked_post.status = BlogPost.Status.UNPUBLISHED
    locked_post.updated_by_id = actor.pk
    locked_post.save(update_fields=['status', 'updated_by'])
    return locked_post


@transaction.atomic
def mark_post_reviewed(post, *, reviewed_on: date, actor):
    _require_permission(actor, 'blog.publish_blogpost')
    if reviewed_on > timezone.localdate():
        raise BlogWorkflowError([_('The review date cannot be in the future.')])
    locked_post = _locked_post(post)
    if locked_post.status not in {BlogPost.Status.PUBLISHED, BlogPost.Status.SCHEDULED}:
        raise BlogWorkflowError([_('Only published articles can be marked reviewed.')])
    BlogPost.objects.filter(pk=locked_post.pk).update(
        last_reviewed_on=reviewed_on,
        updated_by_id=actor.pk,
    )
    return BlogPost.objects.get(pk=locked_post.pk)
