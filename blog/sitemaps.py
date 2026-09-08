from django.utils.translation import override

from apps.core.sites import build_site_absolute_url, get_blog_site_definitions

from .selectors import (
    get_indexable_public_authors,
    get_indexable_public_categories,
    get_indexable_public_tags,
    get_public_posts,
)
from .urls_helpers import reverse_blog


def get_sitemap_entries(*, request, site, languages):
    if site.slug not in get_blog_site_definitions():
        return []

    with override('en'):
        entries = [
            _entry(site.slug, reverse_blog('list', site_slug=site.slug), changefreq='weekly', priority='0.8'),
        ]

        for tag in get_indexable_public_tags(site_slug=site.slug):
            entries.append(
                _entry(
                    site.slug,
                    reverse_blog('tag', site_slug=site.slug, kwargs={'slug': tag.slug}),
                    changefreq='weekly',
                    priority='0.5',
                )
            )

        for category in get_indexable_public_categories(site_slug=site.slug):
            entries.append(
                _entry(
                    site.slug,
                    reverse_blog('category', site_slug=site.slug, kwargs={'slug': category.slug}),
                    changefreq='weekly',
                    priority='0.5',
                )
            )

        for author in get_indexable_public_authors(site_slug=site.slug):
            entries.append(
                _entry(
                    site.slug,
                    reverse_blog('author', site_slug=site.slug, kwargs={'author_slug': author.slug}),
                    changefreq='weekly',
                    priority='0.5',
                )
            )

        for post in get_public_posts(site_slug=site.slug):
            if post.canonical_site_slug != site.slug:
                continue
            lastmod = post.content_updated_at or (post.published_at.date() if post.published_at else None)
            entries.append(
                _entry(
                    site.slug,
                    reverse_blog('detail', site_slug=site.slug, kwargs={'slug': post.slug}),
                    lastmod=lastmod.isoformat() if lastmod else None,
                    changefreq='monthly',
                    priority='0.7',
                )
            )
    return entries


def _entry(site_slug, path, *, lastmod=None, changefreq=None, priority=None):
    return {
        'loc': build_site_absolute_url(site_slug, path),
        'lastmod': lastmod,
        'changefreq': changefreq,
        'priority': priority,
    }
