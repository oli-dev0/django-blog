# Blog

`apps.blog` is the shared, English-only publishing system for the Django
backend. It owns article data, structured content, publication visibility,
editorial workflow, media metadata, SEO context, RSS, sitemap entries, and
the site-compatibility rules for related articles and controlled first-party
links. It also owns profile-backed editorial authors, taxonomy archives,
computed reading-time metadata, and shared article presentation. The consuming
site apps own their public list/detail shells and branding.

## Surfaces

- Personal-site Blog: `/blog/`, `/blog/tag/<slug>/`,
  `/blog/category/<slug>/`, `/blog/author/<author_slug>/`,
  `/blog/<slug>/`, and `/blog/rss/`.
- Vanta Blog: the same route family under `/en/blog/`.
- Django Admin: article editing, structured blocks, media, taxonomy,
  publication sites, related articles, workflow actions, saved previews, and
  private article import.
- Production Blog sites: `my_website` and `vanta_site`. Easy Meals retains a
  site-owned presentation for portability tests but does not enable public
  Blog routing.
- No mobile client or public API.

Only English Blog URLs are valid. The personal site uses unprefixed canonical
URLs and rejects `/en/blog/`; Vanta keeps its English `/en/blog/` route family.
Articles do not use translation fields or language fallback.

## Publishing model

`BlogPost` is the shared article record. `BlogPostPublication` assigns it to
one or more sites, and `canonical_site_slug` selects the preferred SEO site.
Public reads require an effectively published article assigned to the active
site. A scheduled article becomes visible when its stored publication time is
due; reads do not rewrite its status.

The site registry, rather than a generic route namespace, declares which
configured sites expose the shared Blog. `BlogSite` is the database handle for
taxonomy availability. Categories and tags can be enabled independently for
each Blog website, while their names and slugs remain globally unique. A post
whose publication sites are changed must keep its category and tags available
on every selected site.

Public SEO URLs use the first host in each configured site definition as that
site's trusted canonical origin. Production output uses HTTPS independently of
the incoming request host or forwarded scheme; local settings use HTTP for the
development server. Secondary configured hosts may serve content, but do not
change canonical, feed, sitemap, or robots URLs.

Admin article creation requires an `AuthorProfile`. Its public name and
optional processed profile picture are editorial data owned by that profile.
Attribution links to the profile's site-scoped archive through its stable
slug while displaying the current public name. Articles without an author emit
no author attribution or picture. There is no external profile URL field or
public author-account directory.

Editors create drafts through the Admin quick-start form, which requires a
title, site, type, category, and author, then edit ordinary ordered
content blocks. Taxonomy Admin forms require explicit website availability,
and article forms show only terms compatible with the projected publication
sites. Publishers use protected actions to mark ready, publish now, schedule,
unpublish, and mark reviewed. Normal edits to a published article are live
immediately; there is no revision staging layer.

Editors can also review and create a separate draft from a versioned article
JSON package and selected local images. Import v2 can propose named categories
and tags, create explicitly confirmed missing terms, or expand existing term
availability when the editor has the required permissions. The complete
private workflow and data contract are documented in [Article import](./import.md).

Rendered Blog article media uses the shared compact 8px corner radius for
featured images, regular image blocks, and comparison images. The full-size
lightbox image remains unrounded so it can display the original media without
presentation styling.

## Preview and related-article behavior

Saved Admin previews accept a configured blog-enabled site in the `site` query
parameter. The default is the canonical assigned site, then the first
assigned configured site, then the first configured blog-enabled site. A valid
selection renders that site's real article detail shell. An unassigned site
can still be selected for visual comparison and shows an unavailable warning.
Invalid or unmapped presentations use an Admin-owned unavailable state and
never borrow another site's branding. Previews are authenticated, no-store,
noindex, and do not load site analytics.

Related targets must be assigned to every publication site assigned to the
source article. The rule is applied to Admin choices, submitted form values,
model validation, projected site edits, and public reads. Stale incompatible
relationships fail closed publicly and are not deleted automatically.

Internal-link blocks store a stable key from a code-owned destination registry,
descriptive anchor text, and an optional note. Editors only see destinations
available on every selected publication site. Unknown, cross-site, or broken
destinations block publication and fail closed during public rendering; no
free-form URL, host, route name, or unsafe scheme reaches the template.

Public article pages include category breadcrumbs, an optional table of
contents, computed reading time at 180 words per minute, typed content blocks,
and a site-specific stylesheet with the shared Blog stylesheet as fallback.
Their initial HTML also contains safely serialized `BlogPosting` and matching
`BreadcrumbList` JSON-LD. Article schema uses the trusted canonical URL,
English language, visible category and tags, optional featured image, and the
active site's configured public publisher identity. When an author exists, it
links to the stable site-scoped author archive; only a real stored profile
picture is included in schema, never the visible default avatar.

## Operations

Image uploads are validated by Pillow, limited to 15 MB and 40 megapixels,
and stored as the original plus non-upscaled 480px, 800px, 1200px, and
1600px-wide WebP renditions at quality 100. Author profile pictures use the
same input validation and are cropped to 96×96 WebP. Articles without a
profile picture use the shared default author avatar.
Production media must use durable HTTPS-served storage and backups before
publishing image-bearing articles. This repository does not add a background
scheduler; effective visibility is calculated during reads. Deployment
acceptance also verifies that the production proxy and DNS redirect alternate
hosts to the intended HTTPS primary host.

## Feature guides

- [SEO](./SEO.md)
- [Article import](./import.md)
- [Article filtering](./article-filter.md)
- [Article sharing, read mode, and printing](./article-controls.md)
- [Comparison images](./comparison-images.md)
- [FAQ blocks](./faq.md)

Historical planning documents are intentionally excluded from this public
showcase. The guides above describe the retained implementation.

Implementation references: [feature behavior](./features.md), [SEO](./SEO.md),
[database](./database.md), and [tests](./tests-backend-web-api.md).
