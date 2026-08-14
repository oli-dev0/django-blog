# Blog Decisions

## Shared Content And Site Assignment

Blog content lives in one shared app. `BlogPostPublication` is the explicit visibility boundary for each consuming site, while `canonical_site_slug` selects the preferred SEO host. This allows reuse without duplicating article content.

Ordered related articles use the same boundary: a target must be assigned to every site assigned to its source. The rule is enforced at Admin and model boundaries, while public reads fail closed for stale out-of-band rows instead of deleting editorial data.

The site registry explicitly marks Blog-enabled sites with their Blog URL
namespace. `BlogSite` mirrors those code-owned choices only as a database
handle for taxonomy availability; it is not an independent site registry.
Categories and tags therefore keep globally stable names/slugs while gaining
per-website assignments. Existing terms are backfilled to Vanta and to sites
already used by their article publication rows, preserving current visibility while
making future site compatibility explicit.

## English-Only Editorial Content

Article fields are stored directly on `BlogPost`. The empty legacy translation table was removed instead of retaining a half-active translation workflow. Non-English blog URLs return 404, no fallback is used, and no blog hreflang cluster is emitted.

## Structured Blocks, Not Page HTML

`django-content-editor` provides ordered typed blocks and `django-prose-editor` provides narrow sanitized prose fields. Controlled provider embeds are included as a typed block: only normalized, verified YouTube, X, and Reddit references cross the public rendering boundary. Arbitrary HTML/CSS, provider-supplied embed markup, custom article widgets, and page-builder behavior remain excluded.

## Controlled First-Party Destinations

Internal-link blocks store a stable code-owned destination key instead of a
free-form URL or route name. Each registry entry declares the named route and
the publication sites where it is valid. This keeps editor input narrow,
prevents accidental cross-site shell links, and lets route paths change without
rewriting content while the route name and key remain stable. Other Blog
articles continue to use the related-article relation rather than a second
article picker.

## Quick Start Uses Ordinary Blocks

New articles begin with a compact Admin setup step and a selected code-owned template. The quick-start service creates the draft, initial site assignment, and normal heading/rich-text rows in one transaction, then redirects to the existing structured editor.

Templates are not a separate database model or page-builder layer. Once created, their blocks have no special status and can be edited, reordered, or removed. This keeps article rendering and publication validation on the existing content contract.

## Generated Stable Slugs

Quick start generates a collision-safe article slug from the initial title. The normal editor displays it as read-only information and title edits do not regenerate it. This removes repetitive input and avoids accidental URL changes. A future deliberate URL-change workflow must include redirect policy rather than restoring casual slug editing.

Tags follow the same low-input principle: editors enter a name and the model generates the initial normalized slug. The slug remains stable for public taxonomy URLs.

Author profiles also have a globally unique stable slug. Admin suggests it
from the public name, while model creation supplies a collision-safe value when
needed. Changing the visible author name does not rewrite the archive URL.

## Public Taxonomy And Author Archives

Categories and tags appear in public article content and link to site-scoped
archives. Public author attribution follows the same pattern: it links by the
profile's stable slug to an internal, site-scoped archive, rather than an
external profile. All three archive types become indexable and enter
the active site's sitemap when at least two effectively published articles
match. One-article archives remain useful navigation but use noindex; empty or
unknown archives return 404. This supports internal discovery without creating
thin indexable pages or exposing private account data.

## Profile-Backed Authors

Public authors are selectable `AuthorProfile` records rather than free text on
each article. The profile owns the public name and optional picture while the
linked Django user remains an administrative identity. Profile pictures use
the bounded Blog image validation and are normalized to 96×96 WebP; a shared
default avatar covers profiles without a picture.

## Native Admin Taxonomy Controls

Tags use Django checkbox inputs plus small project-owned JavaScript for select-all, clear-all, and related-popup insertion. This keeps the relation as a normal `ManyToManyField`, avoids a frontend framework, and preserves server-side form validation.

Category and tag Admin forms require explicit website availability. Post forms
filter choices by projected publication sites instead of allowing a term that
would be invisible on one selected site. Import v2 can create proposed named
terms or expand existing assignments only through explicit review choices and
normal model add/change permissions.

## Site-Owned Shells

Views choose the consuming site's list/detail shell. `apps.blog` owns selection, workflow, blocks, shared article presentation, metadata, RSS, and sitemap rules; the personal website, Easy Meals, and Vanta Admin own their headers, footers, and branding. Existing navigation is unchanged.

Saved Admin previews resolve an explicit configured site and render its mapped detail shell. Missing or unmapped presentations use an Admin-owned unavailable state rather than the public fallback. Preview controls are shared article UI, and preview responses suppress site analytics while retaining private noindex/no-store headers.

## Immediate Published Edits

Saving an already-public article remains live. Revision staging/history is excluded. Publisher actions control lifecycle state and `last_reviewed_on` without creating a second article version.

Meaningful article edits use the separate nullable `BlogPost.content_updated_at`
timestamp for public modification metadata. It is intentionally distinct from
the internal `updated_at` audit timestamp and the publisher-only review date.
Existing articles are not backfilled because their true content-edit date is
unknown; they omit `dateModified`, `article:modified_time`, and use publication
date as the sitemap `lastmod` fallback until a known edit occurs.

## Media Safety Gate

Staff uploads are still treated as untrusted. Pillow validates decoded content, rejects SVG/animation/oversized images, strips metadata, and creates bounded 480px, 800px, 1200px, and 1600px-wide WebP renditions at quality 100. Production image publishing waits for durable, HTTPS-served, backed-up media storage.

Publication checks require the original and the baseline 480px, 800px, and 1200px rendition objects to exist in configured storage. The 1600px field is optional for compatibility with existing images and is advertised only when present. Public pages degrade to the visible image-unavailable fallback if a required file disappears later.

## Managed Media Deletion

Blog media owns its stored files. Deleting a `BlogImage` removes its original
and renditions; deleting a `BlogImageComparison` removes both originals and all
pair renditions; deleting an `AuthorProfile` removes its optional profile
picture. Admin bulk deletion iterates through the normal per-object delete
path for these models so the file cleanup runs consistently.

Image replacement is also storage-aware. The Admin snapshots the current
regular image or comparison side before saving a new upload. A successful
replacement deletes the previous original and renditions only after the new
files are ready. If processing fails, the previous ready database state and
file references are restored and files created by the failed attempt are
removed. This keeps published articles on their last valid image instead of
turning a processing failure into unavailable content.

## Import Transaction And Storage Cleanup

The final Blog import is atomic for database rows, but database transactions do
not roll back storage writes. The importer therefore tracks every new original
and rendition path written for the current attempt and deletes those paths when
processing or later database creation fails. It never treats a source filename
alone as ownership evidence and never deletes pre-existing Blog media.

Cleanup failure is recoverable state, not a reason to claim the import was
cleanly rolled back. Safe, bounded, deduplicated paths are retained in
`BlogArticleImport.permanent_cleanup_paths` (migration `0019`) and retried by a
later confirmation or the bounded cleanup operation. Successful retries clear
the metadata; failed paths remain. The session stays retryable after a failed
creation, while an earlier unresolved permanent cleanup blocks a new attempt.

Successful confirmation marks the staging row consumed before scheduling
private-file deletion with `transaction.on_commit()`. If that deletion fails,
the consumed metadata remains for the cleanup command and the session cannot be
reused. The `completed_post` relation plus the final staging-row lock make
repeated confirmation return the same draft and prevent duplicate creation on
databases that support `SELECT FOR UPDATE`; SQLite is not sufficient to verify
that concurrency guarantee.

## Reusable Comparison Image Pairs

Comparison images use a dedicated `BlogImageComparison` record and
`BlogImageComparisonBlock` because the two sides are one editorial unit: they
share a caption, must remain adjacent, and need pair-local dialog navigation.
Each side still reuses the general Blog media validation and responsive
rendition contract. A comparison does not replace the ordinary single-image
block.

## Server-rendered Article Filtering

Keep filter state in normalized GET parameters and render results on the
server. Article type is one text-link choice; tags use AND semantics. Query
combinations remain `noindex, follow`, while clean taxonomy archives keep their
own canonical behavior. JavaScript adds only transient interaction and
same-tab return state.

## Ordered FAQ Aggregates With Native Disclosure

Store the items for one FAQ block as a validated ordered JSON list because
they have no independent lifecycle. Reuse the Blog rich-text sanitizer for
answers, keep questions plain and bounded, validate again after storage, and
render native `<details>` elements. FAQ content remains ordinary article
content rather than `FAQPage` schema or table-of-contents headings.

## Social Images

Article Open Graph and Twitter/X metadata uses the consuming website's established main social image. That fallback is metadata-only and is never rendered as article content or used as the article's structured-data image. A featured image appears in the article and `BlogPosting` data only when an editor explicitly selects one; it does not replace site-wide social-sharing metadata.

## Truthful Article Structured Data

Article JSON-LD is built from public server-side facts and safely serialized
into the initial HTML. It uses the visible English category and tags, stable
author archive, optional real article and author images, and an explicitly
configured publisher for the active site. Missing optional facts are omitted;
the default author avatar is presentation fallback only and does not become a
claimed author image.

Publisher configuration is separate from social-sharing image configuration.
The personal site is a `Person` publisher, while Easy Meals and Vanta Admin are
`Organization` publishers with their actual logos. This avoids inventing one
generic organization or treating Vanta's interface preview as a logo.

## Migration Recovery

The direct empty-table transition remains reversible only while the new blog tables contain no content. Its reverse guard refuses to destroy authored posts, media records, taxonomy, relationships, or blocks; content-bearing recovery must roll forward or restore from backup.
