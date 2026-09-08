from django.core.exceptions import ValidationError
from django.db.models.signals import post_delete, post_save, pre_delete
from django.utils.translation import gettext_lazy as _

from .content_text import rebuild_post_search_body
from .models import (
    BlogCalloutBlock,
    BlogCategorySite,
    BlogChecklistBlock,
    BlogEmbedSharingBlock,
    BlogFAQBlock,
    BlogHeadingBlock,
    BlogInternalLinkBlock,
    BlogLinkGroupBlock,
    BlogRichTextBlock,
    BlogSourceLinkBlock,
    BlogTagSite,
)

SEARCHABLE_BLOCK_MODELS = (
    BlogHeadingBlock,
    BlogRichTextBlock,
    BlogFAQBlock,
    BlogChecklistBlock,
    BlogEmbedSharingBlock,
    BlogCalloutBlock,
    BlogSourceLinkBlock,
    BlogLinkGroupBlock,
    BlogInternalLinkBlock,
)


def refresh_search_body(sender, instance, **kwargs):
    rebuild_post_search_body(instance.parent_id)


for block_model in SEARCHABLE_BLOCK_MODELS:
    post_save.connect(
        refresh_search_body,
        sender=block_model,
        dispatch_uid=f'blog.refresh_search_body.save.{block_model.__name__}',
    )
    post_delete.connect(
        refresh_search_body,
        sender=block_model,
        dispatch_uid=f'blog.refresh_search_body.delete.{block_model.__name__}',
    )


def protect_used_taxonomy_site_assignment(sender, instance, using, **kwargs):
    if instance.taxonomy.posts.using(using).filter(
        publications__site_slug=instance.site_id
    ).exists():
        raise ValidationError(
            _('This website cannot be removed because an article on it uses this term.')
        )


for assignment_model in (BlogCategorySite, BlogTagSite):
    pre_delete.connect(
        protect_used_taxonomy_site_assignment,
        sender=assignment_model,
        dispatch_uid=f'blog.protect_taxonomy_site.delete.{assignment_model.__name__}',
    )
