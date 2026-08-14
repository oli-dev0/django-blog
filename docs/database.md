# Blog Database

## Models

- `AuthorProfile` maps one Django user to an editorial public name, globally
  unique stable slug, and optional 96×96 WebP profile picture. New profiles
  generate a collision-safe slug when none is supplied; later public-name edits
  do not regenerate it. The picture is optional; public article output uses a
  default avatar when it is absent. Deleting a profile removes its stored
  picture; Admin bulk deletion uses the same per-profile delete path.
- `BlogPost` stores English metadata, lifecycle status, globally unique stable
  slug, summary, non-editable denormalized `search_body_text`, required author
  and category, SEO fields, featured image,
  tags, related links, canonical site, audit users, and timestamps.
  `(status, published_at)` supports visibility reads.
- `BlogPostPublication` is the explicit post-to-site row with unique
  `(post, site_slug)`. A public post needs an assignment and its canonical site
  must be assigned.
- `BlogSite` stores the code-owned Blog website slugs used by taxonomy
  assignments. `BlogCategory` and `BlogTag` store globally unique names and
  normalized slugs plus explicit `websites` relations through
  `BlogCategorySite` and `BlogTagSite`. Taxonomy assignment rows are unique per
  term and website and cascade when either side is deleted. Category and tag
  Admin forms require at least one configured Blog website; an existing slug is
  not regenerated. Categories are required on articles and use `PROTECT` on
  deletion.
- `BlogPostRelated` is a directional, ordered through model. It has unique
  `(post, related_post)`, `(position, pk)` ordering, self-link validation, and
  `CASCADE` foreign keys.
- `BlogImage` stores the uploaded original plus 480px, 800px, 1200px, and
  1600px WebP renditions,
  dimensions, alt/decorative state, caption, processing state, diagnostics,
  creator, and timestamps. `is_feature=True` images are available as article
  featured images, while regular body image blocks use only
  `is_feature=False` images. Featured images use `SET_NULL`; body image blocks
  use `PROTECT`. Deleting an image removes its original and stored renditions;
  Admin bulk deletion uses the same per-image delete path. Replacing an
  original keeps the existing ready state until the replacement is processed:
  success removes the previous files, while failure restores every previous
  file reference, dimension, and processing field and removes files created by
  the failed attempt.
- `BlogImageComparison` stores a reusable first/second image pair. Each side
  has its original, 480/800/1200/1600 renditions, dimensions, alt text,
  processing state, and error; the pair owns shared caption fields and audit
  timestamps.
  `BlogImageComparisonBlock` references it with `PROTECT` and participates in
  the ordered `main` content region. Deleting a pair removes both originals
  and all stored renditions; Admin bulk deletion uses the same per-pair delete
  path. Replacing either side follows the same success-cleanup and
  failure-restore contract without changing the other side.
- `BlogArticleImport` is an unregistered, transient staging session keyed by a
  UUID. It belongs to one user through `created_by` (`CASCADE`), stores the
  normalized JSON `payload`, structured `warnings`, the bounded source
  filename, indexed `expires_at`, optional `completed_post`, `consumed_at`, a
  bounded `permanent_cleanup_paths` retry list, and `created_at`. The completed
  post relation is one-to-one and uses `SET_NULL`; `consumed_at` remains the
  lifecycle guard if the completed post is deleted. The retry list is
  application-managed metadata for permanent media paths that could not be
  removed after a failed import transaction; it is not a media ownership
  relation or a user-editable field.
- `BlogArticleImportFile` belongs to a staging session with `CASCADE`, stores a
  bounded `selected_name`, a private-storage `file`, and `created_at`. The
  `(import_session, selected_name)` constraint prevents two selected uploads
  with the same basename in one session. Stored paths are generated below the
  session UUID and do not use the user-provided name as a path.

## Content blocks

`django-content-editor` stores ordered `main` blocks: heading, rich text,
checklist, code, verified embed sharing, callout, source link, link group,
internal link, image, comparison image, and FAQ. See [Article content
blocks](./content-blocks.md) for the complete editorial and rendering
contract.
`BlogFAQBlock` stores its ordered questions and sanitized rich answers as one
validated JSON list. Items have no independent identity or lifecycle. The
parent cascade deletes the block; application validation protects the exact
shape because the database cannot constrain nested JSON content.

`BlogInternalLinkBlock` stores a bounded destination registry key, required
descriptive label, and optional plain-text note; resolved paths and origins are
not persisted. Prose is sanitized by the configured `django-prose-editor`
extension sets. Quick start creates a blank article; no template identity is
persisted.

## Compatibility invariant

For every related row, the source publication-site set must be non-empty and
must be a subset of the target publication-site set. Model validation, Admin
forms/formsets, and selectors enforce or fail closed around this invariant.
It is intentionally not denormalized or represented as per-site relation
rows. Changing site assignments rejects invalid retained incoming/outgoing
relationships and never deletes them automatically.

## Query and migration notes

Public selectors join publication rows, use `distinct()`, select related
metadata, and prefetch tags where needed. Lists, tag, category, and author
archives paginate at 12; RSS is capped at 20. No extra cache or speculative
index is used.

The migration chain adds author slugs in `0008_authorprofile_slug`, creates
the internal-link block table in `0009_bloginternallinkblock`, and adds
comparison pairs/blocks in `0010_blogimagecomparison_blogimagecomparisonblock`.
It adds the private import staging tables in
`0017_blogarticleimport_blogarticleimportfile`. Migration `0017` depends on
`0016_blogfaqblock`, creates only the two transient tables and their
relationships/index/constraint, and performs no data backfill. The author-slug
data migration processes existing profiles in primary-key order, uses a
normalized public name or `author-<pk>` fallback, and appends the profile
primary key when a normalized value collides before making the field required
and unique. The internal-link, comparison, and import-staging migrations
require no data backfill. Migration `0018_blogimage_rendition_1600_and_more`
adds the optional 1600px rendition fields. Migration
`0019_blogarticleimport_permanent_cleanup_paths` adds the bounded retry
metadata field to `BlogArticleImport`; it is additive and also requires no data
backfill. Migration `0022_blogembedsharingblock` adds the ordered embed block
table and requires no data backfill. Migration
`0023_blogsite_blogcategorysite_blogcategory_websites_and_more` adds the
Blog-site taxonomy tables and backfills existing categories and tags to Vanta
plus every site already used by their article publication rows. Its reverse
operation is a no-op.

The article filter reads the existing post, publication, category, author, and
tag relations and applies site-scoped queries; taxonomy choices and public
archives also require the term's website assignment. Search uses the denormalized
`BlogPost.search_body_text` field for reader-facing structured content and
keeps the field current through block signals. Migration
`0021_add_blog_search_body` backfills that field from existing searchable
blocks; code blocks are excluded. Search results use relevance ordering before
the normal `-published_at, -pk` tie-breakers.
