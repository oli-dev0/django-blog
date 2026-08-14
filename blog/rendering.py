import json
import math
import re
from dataclasses import dataclass

from django.utils.html import escape, mark_safe, strip_tags
from django.templatetags.static import static
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.urls import NoReverseMatch
from django.utils.translation import gettext_lazy as _, override
from content_editor.contents import contents_for_item
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import ClassNotFound, get_lexer_by_name

from apps.core.sites import (
    EASY_MEALS_SITE,
    PERSONAL_SITE,
    VANTA_SITE,
    build_site_absolute_url,
    get_site_definition,
    get_site_origin,
)

from .image_services import comparison_sources, image_sources
from .faq import normalize_faq_items
from .content_text import reader_facing_block_text
from .embed_sharing import InvalidEmbedReference, normalize_embed_reference
from .models import (
    BLOG_BLOCK_MODELS,
    BlogCalloutBlock,
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
    BlogRichTextBlock,
    BlogSourceLinkBlock,
)
from .selectors import get_canonical_post_url
from .urls_helpers import get_blog_url_namespace, reverse_blog
from .services import validate_post_for_publication

SITE_SOCIAL_IMAGES = {
    PERSONAL_SITE: ('my_website/img/avatar.png', _('Portrait of Oli')),
    EASY_MEALS_SITE: ('easy_meals/img/logo.png', _('Easy Meals logo')),
    VANTA_SITE: ('vanta_site/img/social-preview.png', _('Vanta Admin interface preview')),
}

SITE_PUBLISHERS = {
    PERSONAL_SITE: {
        'type': 'Person',
        'name': 'Oli',
        'home_url_name': 'site-root',
        'image_path': 'my_website/img/avatar.png',
    },
    EASY_MEALS_SITE: {
        'type': 'Organization',
        'name': 'Easy Meals',
        'home_url_name': 'site-root',
        'logo_path': 'easy_meals/img/logo.png',
    },
    VANTA_SITE: {
        'type': 'Organization',
        'name': 'Vanta Admin',
        'home_url_name': 'vanta_site:home',
        'logo_path': 'vanta_site/img/logo.png',
    },
}

BLOG_STYLESHEET_PATHS = {
    PERSONAL_SITE: 'my_website/css/blog.css',
    VANTA_SITE: 'vanta_site/css/blog.css',
}
BLOG_FALLBACK_STYLESHEET = 'blog/css/article.css'
READING_WORDS_PER_MINUTE = 180


def get_blog_stylesheet(site_slug):
    return BLOG_STYLESHEET_PATHS.get(site_slug, BLOG_FALLBACK_STYLESHEET)


@dataclass
class RenderedBlock:
    template_name: str
    item: object
    image: dict | None = None
    loading: str | None = 'lazy'
    url: str | None = None
    comparison: dict | None = None
    embed: 'RenderedEmbed | None' = None


@dataclass(frozen=True, slots=True)
class RenderedEmbed:
    platform: str
    canonical_url: str
    item_id: str


def _safe_prose(value):
    return _open_article_links_in_new_tab(value)


_INLINE_INTERNAL_LINK_TAG_RE = re.compile(r'<a\b(?P<attrs>[^>]*)>', re.IGNORECASE)
_INLINE_INTERNAL_KEY_RE = re.compile(
    r'\bdata-blog-internal-key\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_ARTICLE_LINK_TARGET_RE = re.compile(
    r'\btarget\s*=\s*(["\'])[^"\']*\1',
    re.IGNORECASE,
)
_ARTICLE_LINK_REL_RE = re.compile(
    r'\brel\s*=\s*(["\'])([^"\']*)\1',
    re.IGNORECASE,
)


def _open_article_links_in_new_tab(value):
    def replace_anchor(match):
        attrs = match.group('attrs')
        if _ARTICLE_LINK_TARGET_RE.search(attrs):
            attrs = _ARTICLE_LINK_TARGET_RE.sub('target="_blank"', attrs, count=1)
        else:
            attrs = f'{attrs} target="_blank"'

        rel_match = _ARTICLE_LINK_REL_RE.search(attrs)
        if rel_match:
            rel_values = set(rel_match.group(2).split())
            rel_values.update(('noopener', 'noreferrer'))
            attrs = _ARTICLE_LINK_REL_RE.sub(
                f'rel="{" ".join(sorted(rel_values))}"',
                attrs,
                count=1,
            )
        else:
            attrs = f'{attrs} rel="noopener noreferrer"'
        return f'<a{attrs}>'

    return mark_safe(_INLINE_INTERNAL_LINK_TAG_RE.sub(replace_anchor, value or ''))


def _render_rich_text(value, site_slugs):
    from .internal_links import resolve_internal_link

    def replace_anchor(match):
        attrs = match.group('attrs')
        key_match = _INLINE_INTERNAL_KEY_RE.search(attrs)
        if not key_match:
            return match.group(0)
        try:
            url = resolve_internal_link(key_match.group(1), site_slugs)
        except (ValidationError, NoReverseMatch):
            return '<a>'
        return f'<a href="{url}">'

    return _open_article_links_in_new_tab(
        _INLINE_INTERNAL_LINK_TAG_RE.sub(replace_anchor, value or '')
    )


def _highlight_code(value, language):
    try:
        lexer = get_lexer_by_name(language)
    except ClassNotFound:
        return mark_safe(escape(value or ''))
    return mark_safe(highlight(value or '', lexer, HtmlFormatter(nowrap=True)))


def _rendered_embed(item):
    try:
        reference = normalize_embed_reference(item.platform, item.url)
    except InvalidEmbedReference:
        return None
    return RenderedEmbed(
        platform=reference.platform,
        canonical_url=reference.canonical_url,
        item_id=reference.item_id,
    )


def _rendered_blocks(post, *, primary_image_seen=False):
    contents = contents_for_item(post, BLOG_BLOCK_MODELS)
    rendered = []
    publication_site_slugs = None
    items = list(contents.main)
    image_ids = {
        item.image_id
        for item in items
        if isinstance(item, BlogImageBlock) and item.image_id
    }
    images = BlogImage.objects.in_bulk(image_ids)
    comparison_ids = {
        item.comparison_id
        for item in items
        if isinstance(item, BlogImageComparisonBlock) and item.comparison_id
    }
    comparisons = BlogImageComparison.objects.in_bulk(comparison_ids)
    for item in items:
        if isinstance(item, BlogHeadingBlock):
            rendered.append(RenderedBlock('blog/blocks/heading.html', item))
        elif isinstance(item, BlogRichTextBlock):
            if publication_site_slugs is None:
                publication_site_slugs = set(post.publications.values_list('site_slug', flat=True))
            item.body = _render_rich_text(item.body, publication_site_slugs)
            rendered.append(RenderedBlock('blog/blocks/rich_text.html', item))
        elif isinstance(item, BlogFAQBlock):
            # Stored JSON is validated again because out-of-band writes must never reach mark_safe.
            try:
                faq_items = normalize_faq_items(item.items)
            except ValidationError:
                continue
            if not faq_items:
                continue
            if publication_site_slugs is None:
                publication_site_slugs = set(post.publications.values_list('site_slug', flat=True))
            try:
                for faq_item in faq_items:
                    faq_item['answer'] = _render_rich_text(
                        faq_item['answer'],
                        publication_site_slugs,
                    )
            except (ValidationError, NoReverseMatch):
                continue
            item.items = faq_items
            rendered.append(RenderedBlock('blog/blocks/faq.html', item))
        elif isinstance(item, BlogChecklistBlock):
            rendered.append(RenderedBlock('blog/blocks/checklist.html', item))
        elif isinstance(item, BlogCodeBlock):
            item.code = _highlight_code(item.code, item.language)
            rendered.append(RenderedBlock('blog/blocks/code.html', item))
        elif isinstance(item, BlogEmbedSharingBlock):
            if isinstance(item.caption, str):
                item.caption = item.caption.strip()
            rendered.append(
                RenderedBlock(
                    'blog/blocks/embed_sharing.html',
                    item,
                    embed=_rendered_embed(item),
                )
            )
        elif isinstance(item, BlogCalloutBlock):
            item.body = _safe_prose(item.body)
            rendered.append(RenderedBlock('blog/blocks/callout.html', item))
        elif isinstance(item, BlogSourceLinkBlock):
            rendered.append(RenderedBlock('blog/blocks/source_link.html', item))
        elif isinstance(item, BlogLinkGroupBlock):
            rendered.append(RenderedBlock('blog/blocks/link_group.html', item))
        elif isinstance(item, BlogInternalLinkBlock):
            from .internal_links import resolve_internal_link

            if publication_site_slugs is None:
                publication_site_slugs = set(
                    post.publications.values_list('site_slug', flat=True)
                )
            try:
                url = resolve_internal_link(
                    item.destination_key,
                    publication_site_slugs,
                )
            except (ValidationError, NoReverseMatch):
                continue
            rendered.append(RenderedBlock('blog/blocks/internal_link.html', item, url=url))
        elif isinstance(item, BlogImageBlock):
            rendered.append(
                RenderedBlock(
                    'blog/blocks/image.html',
                    item,
                    image_sources(images.get(item.image_id)),
                    None if not primary_image_seen else 'lazy',
                )
            )
            primary_image_seen = True
        elif isinstance(item, BlogImageComparisonBlock):
            rendered.append(
                RenderedBlock(
                    'blog/blocks/image_comparison.html',
                    item,
                    comparison=comparison_sources(comparisons.get(item.comparison_id)),
                    loading=None if not primary_image_seen else 'lazy',
                )
            )
            primary_image_seen = True
    return rendered


def _table_of_contents(blocks):
    headings = [
        block.item
        for block in blocks
        if isinstance(block.item, BlogHeadingBlock) and block.item.level == BlogHeadingBlock.Level.H2
    ]
    if len(headings) < 2:
        return []
    return headings


def _reading_time_minutes(blocks):
    text = ' '.join(reader_facing_block_text(block) for block in blocks)
    words = re.findall(r"\b[\w]+(?:['’-][\w]+)*\b", text, flags=re.UNICODE)
    return max(1, math.ceil(len(words) / READING_WORDS_PER_MINUTE))


def get_reading_time_minutes(post):
    contents = contents_for_item(post, BLOG_BLOCK_MODELS)
    return _reading_time_minutes(contents.main)


def _absolute_media_url(site_slug, source):
    if not source:
        return None
    return build_site_absolute_url(site_slug, source)


def get_site_social_image(site_slug):
    image = SITE_SOCIAL_IMAGES.get(site_slug)
    if not image:
        return {'url': '', 'alt': ''}
    path, alt = image
    return {'url': build_site_absolute_url(site_slug, static(path)), 'alt': str(alt)}


def get_rss_feed_metadata(site_slug, *, current_app=None):
    site = get_site_definition(site_slug)
    with override('en'):
        feed_path = reverse_blog('rss', site_slug=site_slug, current_app=current_app)
    return {
        'rss_feed_url': build_site_absolute_url(site_slug, feed_path),
        'rss_feed_title': _('%(site_name)s Blog RSS') % {'site_name': site.name},
    }


def _json_ld(value):
    serialized = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return mark_safe(serialized.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e'))


def _publisher_schema(site_slug):
    publisher = SITE_PUBLISHERS.get(site_slug)
    if not publisher:
        return None

    with override('en'):
        homepage_path = reverse(publisher['home_url_name'])
    schema = {
        '@type': publisher['type'],
        'name': publisher['name'],
        'url': build_site_absolute_url(site_slug, homepage_path),
    }
    if image_path := publisher.get('image_path'):
        schema['image'] = build_site_absolute_url(site_slug, static(image_path))
    if logo_path := publisher.get('logo_path'):
        schema['logo'] = {
            '@type': 'ImageObject',
            'url': build_site_absolute_url(site_slug, static(logo_path)),
        }
    return schema


def _current_blog_app(request, site_slug):
    current_app = getattr(request, 'current_app', None) if request else None
    if not current_app and request and request.resolver_match:
        current_app = request.resolver_match.namespace
    if current_app not in {'blog', 'personal_blog'}:
        return get_blog_url_namespace(site_slug)
    return current_app


def _seo_data(post, *, request=None, site_slug=None, preview=False):
    canonical_url = get_canonical_post_url(post)
    featured_image = image_sources(post.featured_image)
    featured_image_url = _absolute_media_url(
        post.canonical_site_slug,
        featured_image['original'] if featured_image else None,
    )
    social_image = get_site_social_image(site_slug)
    if featured_image:
        social_image = {
            'url': _absolute_media_url(
                post.canonical_site_slug or site_slug,
                featured_image['src'],
            ),
            'alt': featured_image['alt'] or post.title,
        }
    article_schema = {
        '@context': 'https://schema.org',
        '@type': 'BlogPosting',
        'headline': post.title,
        'description': post.seo_description or post.summary,
        'mainEntityOfPage': {'@type': 'WebPage', '@id': canonical_url},
        'datePublished': post.published_at.isoformat() if post.published_at else None,
        'inLanguage': 'en',
        'articleSection': post.category.name,
    }
    if post.content_updated_at:
        article_schema['dateModified'] = post.content_updated_at.isoformat()
    if featured_image_url:
        article_schema['image'] = [featured_image_url]
    if post.author and post.author.public_author_name:
        with override('en'):
            author_path = reverse_blog(
                'author',
                site_slug=site_slug,
                current_app=_current_blog_app(request, site_slug),
                kwargs={'author_slug': post.author.slug},
            )
        article_schema['author'] = {
            '@type': 'Person',
            'name': post.author.public_author_name,
            'url': build_site_absolute_url(site_slug, author_path),
        }
        if post.author.profile_picture:
            article_schema['author']['image'] = _absolute_media_url(
                site_slug,
                post.author.profile_picture.url,
            )
    keywords = [tag.name for tag in post.tags.all() if tag.name]
    if keywords:
        article_schema['keywords'] = keywords
    if publisher_schema := _publisher_schema(site_slug):
        article_schema['publisher'] = publisher_schema

    breadcrumb_site_slug = post.canonical_site_slug or site_slug
    with override('en'):
        current_app = _current_blog_app(request, breadcrumb_site_slug)
        blog_path = reverse_blog('list', site_slug=breadcrumb_site_slug, current_app=current_app)
        category_path = reverse_blog(
            'category', site_slug=breadcrumb_site_slug, current_app=current_app, kwargs={'slug': post.category.slug}
        )
        article_path = reverse_blog(
            'detail', site_slug=breadcrumb_site_slug, current_app=current_app, kwargs={'slug': post.slug}
        )
    breadcrumb_schema = {
        '@context': 'https://schema.org',
        '@type': 'BreadcrumbList',
        'itemListElement': [
            {
                '@type': 'ListItem',
                'position': 1,
                'name': 'Blog',
                'item': build_site_absolute_url(breadcrumb_site_slug, blog_path),
            },
            {
                '@type': 'ListItem',
                'position': 2,
                'name': post.category.name,
                'item': build_site_absolute_url(breadcrumb_site_slug, category_path),
            },
            {
                '@type': 'ListItem',
                'position': 3,
                'name': post.title,
                'item': canonical_url or build_site_absolute_url(breadcrumb_site_slug, article_path),
            },
        ],
    }

    return {
        'title': post.seo_title or post.title,
        'description': post.seo_description or post.summary,
        'canonical_url': canonical_url,
        'og_image_url': social_image['url'],
        'og_image_alt': social_image['alt'],
        'article_schema_json': _json_ld(article_schema),
        'breadcrumb_schema_json': _json_ld(breadcrumb_schema),
        'is_preview': preview,
    }


def build_preview_warnings(post):
    warnings = validate_post_for_publication(post)
    if not post.summary.strip():
        warnings.append(_('Add a summary before publishing.'))
    if not post.canonical_site_slug:
        warnings.append(_('Choose a canonical site before publishing.'))
    return warnings


def _embed_origin(request, site_slug):
    if request is not None:
        return f'{request.scheme}://{request.get_host()}'
    if site_slug:
        return get_site_origin(site_slug)
    return ''


def build_article_context(post, *, request=None, site_slug=None, related_posts=(), preview=False):
    featured_image = image_sources(post.featured_image)
    blocks = _rendered_blocks(post, primary_image_seen=bool(featured_image))
    related_posts = tuple(related_posts)
    for related_post in related_posts:
        related_post.featured_image_data = image_sources(related_post.featured_image)
    site = get_site_definition(site_slug) if site_slug else None
    context = {
        'post': post,
        'featured_image': featured_image,
        'rendered_blocks': blocks,
        'has_embed_sharing': any(block.embed is not None for block in blocks),
        'embed_origin': _embed_origin(request, site_slug),
        'reading_time_minutes': _reading_time_minutes(blocks),
        'table_of_contents': _table_of_contents(blocks),
        'related_posts': related_posts,
        'type_label': post.get_type_display(),
        'summary_text': strip_tags(post.summary),
        'seo': _seo_data(post, request=request, site_slug=site_slug, preview=preview),
        'preview_warnings': build_preview_warnings(post) if preview else [],
        'site_name': site.name if site else '',
        'blog_stylesheet': get_blog_stylesheet(site_slug),
    }
    context.update(get_rss_feed_metadata(site_slug))
    return context
