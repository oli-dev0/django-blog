# Blog SEO

The shared Blog emits deterministic, server-rendered SEO metadata for the
configured Blog-enabled sites. The default registry enables `my_website` and
`vanta_site`; `easy_meals` retains a presentation shell for portability and
tests but does not expose public Blog routes by default. `apps.blog` owns the
common rules; each site app owns its public shell, branding, and social image.

The Blog is English-only. The personal site uses unprefixed `/blog/` routes;
Vanta uses `/en/blog/`. No translated article variants or Blog hreflang cluster
are emitted.

## Trusted origins and canonical identity

The first host in `SITE_DEFINITIONS[site_slug]["hosts"]` is the site's trusted
SEO host. `SEO_CANONICAL_SCHEME` is `https` in base settings and `http` in
local settings. `apps.core.sites.get_site_origin()` and
`build_site_absolute_url()` build absolute public URLs without trusting the
incoming request host or forwarded scheme. The absolute-URL helper accepts
root-relative paths only and rejects unknown sites, unsupported schemes, and
absolute or protocol-relative input paths.

`BlogPostPublication` controls the sites where an article is visible.
`BlogPost.canonical_site_slug` independently selects its canonical article
identity and must reference an assigned site. An article may render through a
secondary assigned site's shell, but its canonical URL, `mainEntityOfPage`,
and sitemap eligibility continue to use its canonical site.

The trusted-origin path is used for:

- article and archive canonicals;
- Open Graph and Twitter metadata;
- article, author, publisher, image, and breadcrumb JSON-LD URLs;
- RSS feed and item URLs;
- every public sitemap provider; and
- the sitemap reference in `robots.txt`.

Production rollout still requires an external check that the proxy and DNS
redirect alternate hosts to the intended HTTPS primary host. Django's emitted
URLs do not prove that infrastructure behavior.

## Public routes and indexability

The public SEO surfaces are the following route families:

- `/blog/` or `/en/blog/`;
- `/blog/tag/<slug>/` or `/en/blog/tag/<slug>/`;
- `/blog/category/<slug>/` or `/en/blog/category/<slug>/`;
- `/blog/author/<author_slug>/` or `/en/blog/author/<author_slug>/`;
- `/blog/<slug>/` or `/en/blog/<slug>/`; and
- `/blog/rss/` or `/en/blog/rss/`.

Only effectively published articles assigned to the active site are public.
Only the canonical site's article detail enters a sitemap. Tag, category, and
author archives are active-site scoped. An archive with at least two visible
articles is indexable and sitemap-eligible; a one-article archive remains
navigable with `noindex,follow`; an empty or unknown archive returns 404.

Author archive URLs use `AuthorProfile.slug`, not the editable display name.
The slug is lowercase, URL-safe, globally unique, collision-safe when
generated, and stable when the public name changes. Migration
`0008_authorprofile_slug` deterministically backfills existing profiles,
including normalized-name collisions, empty normalized names, and the
120-character field limit. No redirect from the former name-based shape is
provided because those URLs were not publicly established.

## Article metadata and structured data

Article pages emit canonical, Open Graph, and Twitter metadata in the initial
HTML. Social metadata uses the active site's established social image. That
image is metadata-only: it is not rendered as article content and is not
claimed as the article's structured-data image. A `BlogPosting.image` is
included only when the editor selected a real public featured image.

Each article emits safely serialized `BlogPosting` and `BreadcrumbList`
JSON-LD. Script-sensitive `<`, `>`, and `&` characters are escaped after
`json.dumps()`. The article entity includes:

- canonical `mainEntityOfPage` and publication date;
- the visible article title as `headline`;
- `dateModified` when `BlogPost.content_updated_at` records a meaningful
  article edit;
- `inLanguage: "en"`;
- the visible category as `articleSection`;
- non-empty visible tag names as ordered `keywords`;
- the stable author archive URL and public display name when an author exists;
- the author's stored public profile image when present; and
- an explicitly configured publisher for the active site.

The personal site publisher is a `Person`. Easy Meals and Vanta Admin are
`Organization` publishers using their real site-owned logos. Publisher assets
are configured separately from social images. Missing optional facts are
omitted rather than represented by empty values or invented fallbacks. Private
account fields and unsupported ratings, reviews, offers, prices, FAQ, Product,
or SoftwareApplication claims are not exposed.

`seo_title` remains the browser, Open Graph, and Twitter/X title, but it is not
used as the JSON-LD `headline` when it differs from the visible article title.
`last_reviewed_on` is an editorial review label only and does not populate
`dateModified`. Existing articles without a known content edit date omit
`dateModified` and `article:modified_time`; this is intentional.

Each enabled detail shell emits `article:published_time` from `published_at` and
emit `article:modified_time` only when `content_updated_at` is present. The
canonical article sitemap entry uses the same `content_updated_at` value for
`lastmod`, falling back to the publication date when no content edit date is
known.

## Controlled first-party links

`apps.blog.internal_links` owns a deliberately small registry of stable
destination keys, named routes, display labels, and allowed site slugs.
Internal-link blocks persist a registry key, descriptive anchor label, and
optional note; they never persist a submitted URL, host, scheme, route name,
query string, or fragment.

Admin choices and validation use every projected publication site. Users who
cannot edit publication sites are validated against the persisted assignments,
not submitted `publication_sites` data. Unknown keys, unsafe URL-like values,
cross-site destinations, and named routes that no longer reverse prevent
publication. Public rendering validates again and omits stale invalid rows.
Valid links are root-relative, crawlable anchors in the initial HTML without
JavaScript, `target="_blank"`, tracking parameters, or `nofollow`.

The rich-text editor can attach the same registry destinations to selected
inline text. The registry key is authoritative; the saved `href` is only a
root-relative fallback. Rich-text forms and the parent article form validate
submitted inline keys against publication sites selected in the same Admin
submission. Public rendering resolves the named route again and replaces the
fallback URL. Unknown or incompatible inline destinations keep their visible
anchor text but render without a clickable destination.

## RSS discovery

Each enabled site exposes its site-scoped English feed (`/blog/rss/` for the
personal site and `/en/blog/rss/` for Vanta), limited to the latest 20
effectively published articles. Item URLs use each article's trusted canonical
identity.

Public Blog lists, tag/category/author archives, and details emit exactly one
standard RSS autodiscovery link in `<head>`. Its URL belongs to the active
site, even when an article's canonical site differs. Empty Blog lists still
advertise the maintained feed. Saved Admin previews and non-Blog pages do not
emit Blog feed discovery metadata. There is intentionally no visible RSS
navigation control or RSS-specific styling.

## Implementation map

- `apps/core/sites.py`: trusted site origins and absolute URL construction.
- `apps/blog/selectors.py`: public visibility and canonical article URLs.
- `apps/blog/rendering.py`: page metadata, JSON-LD, publisher definitions,
  RSS discovery context, and internal-link rendering.
- `apps/blog/admin.py`: records meaningful article and content-block edits in
  `BlogPost.content_updated_at`.
- `apps/blog/models.py`: stores the nullable content-edit timestamp separately
  from internal `updated_at` audit data and `last_reviewed_on`.
- `apps/blog/internal_links.py`: approved destination registry and resolution.
- `apps/blog/feeds.py`: site-scoped English RSS output.
- `apps/blog/sitemaps.py` and site app `sitemaps.py` modules: sitemap entries.
- `config/sitemap_views.py`: sitemap and robots responses.
- Site-owned `blog/list.html` and `blog/detail.html` templates: head metadata.

## Automated verification

Relevant coverage includes:

- `tests/core/test_sites.py`: origin configuration and unsafe-path rejection;
- `tests/core/test_sitemap.py`: HTTPS sitemap and robots output;
- `tests/blog/test_views.py`: canonical/social agreement, alternate-host
  behavior, parsed JSON-LD, truthful optional fields, RSS discovery on all
  shells and archive/empty boundaries, feed scope, and non-Blog exclusion;
- `tests/blog/test_models.py` and `test_migrations.py`: stable unique author
  slugs and deterministic bounded backfill;
- `tests/blog/test_sitemaps.py`: canonical details and indexable archives;
- `tests/blog/test_internal_links.py`: registry, validation, permissions,
  projected-site rich-text formsets, robust inline HTML parsing, custom editor
  module resolution, publication readiness, server-rendered links, fail-closed
  output, and query behavior; and
- `tests/blog/test_admin.py`: preview suppression and protected projected-site
  validation.

Run the Blog suite with:

```bash
UV_CACHE_DIR=/tmp/django-blog-uv-cache DJANGO_SETTINGS_MODULE=config.settings.local uv run python manage.py test tests.blog
```

## Manual release checks

- Confirm the Admin author-slug prepopulation and saved-slug stability.
- Check the internal-link Admin workflow and a public article at narrow width,
  in light and dark themes, with keyboard focus and long wrapping text.
- Inspect representative list, archive, detail, and saved-preview source for
  the expected RSS discovery behavior.
- Validate representative production-style `BlogPosting` JSON-LD with an
  appropriate external structured-data validator.
- Verify production HTTPS and canonical-host redirects through the deployed
  proxy and DNS path.
