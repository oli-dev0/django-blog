# Blog Services And Selectors

## Selectors

- `get_public_posts()` and `get_public_post_by_slug()` enforce effective
  publication and active-site assignment; the list selector also accepts tag,
  category, and exact profile public-name filters.
- `get_publication_site_slugs()` returns an article's assigned site set.
- `get_compatible_related_posts()` returns editorial targets containing every
  required source site, excluding the source and ordering by case-insensitive
  title and primary key.
- `get_incompatible_incoming_related_links()` finds incoming rows invalidated
  by projected target-site changes.
- `get_related_public_posts()` additionally requires effective publication and
  active-site assignment, and fails closed for stale incompatible rows.
- Feed, indexable-tag, indexable-category, indexable-author, and canonical-URL
  selectors reuse the same public visibility/site boundaries. Taxonomy and
  author archives enter a sitemap only when they have at least two visible
  articles on the active site.
- `get_blog_site_definitions()` and `get_blog_site_slug_choices()` expose only
  code-configured Blog websites. Taxonomy forms, import review, publication
  validation, and public archives use those choices rather than treating every
  configured site or route namespace as Blog-enabled.

## Services

`apps.blog.services` owns `create_post_draft`, `mark_post_ready`,
`publish_post_now`, `schedule_post`, `unpublish_post`, and
`mark_post_reviewed`. Lifecycle services use atomic transactions, lock the
post, recheck permissions/status, validate the public contract, and update
only their owned fields.

Publication validation resolves every internal-link destination against the
article's assigned sites. An unknown or cross-site key, or a registry route
that no longer reverses, prevents readiness, immediate publication, and
scheduling. Public rendering independently fails closed if stale data bypasses
the editorial and service boundaries.

`create_post_draft()` validates the configured site, type, category,
author, and permissions; generates a stable unique slug; assigns the initial
publication/canonical site; and creates the draft in one transaction. The
current Admin entry point uses a blank draft.

Taxonomy Admin forms own website assignment changes. The post form and import
review service validate that selected categories and tags are available on all
projected publication sites; import v2 can create proposed terms or expand
existing assignments only after the relevant add/change permissions are
revalidated.

## Article import services

The complete staging, package-validation, review, draft-creation,
storage-compensation, cleanup, and Admin-adapter boundaries are documented in
[import.md](./import.md). `apps.blog.import_services` owns state changes;
`BlogPostAdmin` is the private web adapter. The workflow has no public selector
or API.

## Permissions

Existing Django model permissions protect Admin access. Custom post
permissions are `organize_blogpost`, `publish_blogpost`, and
`unpublish_blogpost`. Preview uses existing view/change access. Organizing
publication sites and related rows requires the existing organization
permission. Import taxonomy creation uses the normal
`add_blogcategory`/`add_blogtag` permissions; expanding existing assignments
uses `change_blogcategory`/`change_blogtag`. No role or permission is added for
site preview or compatibility.

## Article filters, FAQ blocks, and comparison images

The full current-state boundaries are documented in
[article-filter.md](./article-filter.md), [faq.md](./faq.md), and
[comparison-images.md](./comparison-images.md).

Those guides are the authoritative service and selector references for these
three features.
