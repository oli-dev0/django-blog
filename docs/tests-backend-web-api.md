# Blog Backend And Web Tests

Reference frontend coverage is kept in the extracted showcase test package:

- `tests/test_reference_frontend.py`: opt-in template selection, list/detail
  rendering, metadata and JSON-LD preservation, filtered-list noindex behavior,
  and the missing-template system check.
- `tests/test_reference_frontend_assets.py`: app-owned static/template
  references, font paths, neutral branding, progressive-enhancement assets,
  and safe theme initialization.

The request/response tests require a compatible host project and its Django
settings. Run `collectstatic --noinput` first when the host uses
manifest-backed static storage.

Focused coverage is under `tests/blog/`:

- `test_models.py`: author profiles and stable unique slugs, required categories,
  publication/canonical constraints, site-scoped Blog taxonomy assignments and
  uniqueness, related compatibility, slugs, anchors, images, FAQ normalization
  and registration, comparison protection, and sanitization.
- `test_filters.py`: normalized filter parsing and serialization, one article
  type, AND-tag/date/year behavior, site-scoped options and visibility,
  archive initialization, removal links, canonical/robots headers,
  pagination/404 behavior, tag-only dropdown restoration, shared asset cache
  keys, and filtered-return markup.
- `test_search.py`: normalized multi-term matching across metadata, tags, and
  reader-facing block content; relevance and site/publication boundaries;
  search markup, clear links, pagination, canonical/robots behavior, archive
  redirects, empty states, and all three site shells.
- `test_selectors.py`: site-scoped visibility, schedules, compatible targets,
  and fail-closed public related links.
- `test_embed_sharing_styling.py`: synchronized shared/Vanta stylesheet
  contracts for responsive provider targets, captions, source links, focus,
  forced colors, and print behavior.
- `test_services.py`: permissions, ready/publish/schedule/unpublish/review,
  publication timestamps, incomplete content, and image readiness.
- `test_admin.py`: quick start metadata requirements, site-required category/tag
  Admin forms, projected taxonomy filtering, protected/read-only
  fields, author slug Admin exposure, author picture processing, preview
  default and explicit site selection, strict unavailable previews, private
  headers, analytics and RSS-autodiscovery suppression, related filtering,
  manual invalid values, projected site edits, incoming conflicts, and
  preservation of bound values, plus successful replacement cleanup and
  failed-replacement restoration for regular images and comparison sides, and
  bulk-delete cleanup for images, comparison pairs, and author profile
  pictures. Saved-preview coverage also verifies that the selected personal
  shell loads its site-owned stylesheet and emits only unprefixed `/blog/`
  navigation rather than inheriting `/en/blog/` from the Admin request.
- `test_admin_import.py`: both `admin` and `dev_admin` namespaces, the complete
  core-permission gate and conditional import links, private headers, grouped
  safe upload errors, owner-bound staging and PRG, read-only review, expiry and
  ownership failures, safe ordered review output, stale reviewed-choice
  validation, duplicate-warning second confirmation, idempotent creation,
  success messages and redirects, change-files/cancel cleanup, and unsupported
  methods. It also verifies the import stylesheet and JavaScript are rendered
  under both Admin namespaces, one page heading, native file-control markup,
  linked/focusable error-summary hooks, safe filename DOM handling, scoped
  theme selectors, wrapping, narrow layouts, and non-color status markup.
- `test_views.py`: public shells, personal unprefixed Blog routes and Vanta's
  localized route preservation, metadata, content and provider embed rendering
  (including order, captions, conditional scripts, and fail-closed invalid
  stored references), English-only behavior,
  pagination, slug-based tag/category/author archives, stable author links and
  canonicals, default avatars, breadcrumbs, reading time, 404s, RSS, and
  three-site list/detail RSS autodiscovery. The autodiscovery coverage parses
  head links and verifies one trusted, active-site English feed URL with no
  cross-site URL, including empty lists, archive pages, saved previews, and a
  representative non-Blog page. It
  parses rendered JSON-LD scripts and covers trusted article/author/publisher
  URLs, publisher types and assets for all three sites, language, category,
  tags, real author images, omission of unavailable optional facts, private
  account-field exclusion, visible-title headlines, content modification
  metadata, and script-breaking editor text.
  It also covers FAQ disclosure markup and fail-closed malformed content, plus
  comparison figures, responsive sources, shared captions, dialog triggers,
  and the public share menu, platform order and icons, copy-link, read-mode,
  and print-control markup contracts across all three site shells.
- `test_fonts.py`: local Literata WOFF2 validity, variable axes, license, and
  paired stylesheet declarations for read mode.
- `test_read_mode.py`: Python-only source contracts for native share and
  fallback-menu behavior, platform URL construction, copy-link clipboard and
  feedback behavior, menu dismissal/focus handling, read-mode JavaScript
  state/focus/progress safeguards, print-button API behavior, paired responsive
  CSS, print exclusions, paper-layout rules, and lazy-image/FAQ preparation.
  These tests do not execute browser JavaScript or measure rendered layout.
- `test_sitemaps.py`: canonical detail eligibility, personal unprefixed Blog
  locations, content-edit `lastmod`, and tag/category/slug-based author
  archives, including trusted HTTPS primary-host locations.
- `test_images.py`: article image renditions, feed metadata, image-source
  behavior, comparison pair processing, replacement isolation, storage
  degradation, cleanup, direct author-profile deletion cleanup, and source-file
  closure after successful, validation-failing, and processing-failing article
  and author-profile operations. Author profile Admin and bulk-delete cleanup
  are covered in `test_admin.py`.
- `test_migrations.py`: deterministic author-slug backfill, including
  collisions, empty normalization, and maximum-length values, plus the
  reader-facing search-body migration backfill and code-block exclusion, and
  migration `0023` taxonomy-site backfill and uniqueness.
- `test_internal_links.py`: site-filtered registry choices, external and unsafe
  input rejection, cross-site rejection, stable route resolution, descriptive
  labels, protected Admin formset site handling, projected-site validation for
  inline rich-text links, normalized HTML attribute parsing, resolved custom
  editor-module URLs, readiness failures for stale keys and broken named
  routes, initial-HTML link rendering, fail-closed stale output, and one
  publication-site query per rendered article.
- `test_import_contract.py`: the checked-in example, strict Draft 2020-12 v1/v2
  schemas, named taxonomy validation and normalization, all eleven block variants, immutable normalization and documented
  defaults, deterministic safe issues, duplicate keys, unknown fields,
  local-reference and internal-link validation, meaningful content, and safe
  image-path handling without ORM or permanent-media work.
- `test_import_staging.py`: private-root separation from `MEDIA_ROOT`, no-URL
  storage, UUID-owned randomized paths, basename normalization and uniqueness,
  retention, authenticated ownership, expiry/consumed access gates,
  all-or-clean staging failure handling, discard, post-commit cleanup,
  retryable cleanup failures, bounded cleanup batches, aggregate-only command
  output, and the migration-related uniqueness/lifecycle constraints.
- `test_import_forms.py`: multiple-file upload behavior, v2 create-category
  review validation, product labels and
  help text, the 1 MiB JSON limit, 50-file and 150 MiB aggregate image limits,
  exact reference preselection, unresolved-value display, existing-record
  choices, canonical-site membership, and non-create actions.
- `test_import_validation.py`: package-wide core and payload-specific
  permissions, exact reference resolution, basename matching, all affected
  missing/ambiguous image locations, one byte-validation call per distinct
  referenced file, invalid image and package limits, all-or-nothing staging,
  warning-only extras and unused definitions, bounded safe duplicate matches,
  missing related articles, internal-link and related-site compatibility, and
  revalidation after reviewed choices or site assignments become stale, v1
  taxonomy expansion confirmation, and v2 term-creation permissions.
- `test_import_creation.py`: atomic draft creation with metadata, publication,
  taxonomy, audit-user, related-article, media-block, and featured-image
  persistence, including atomic v2 taxonomy creation and assignments; source ordering; sanitization; collision-safe post slugs and
  heading anchors; permission, owner, expiry, stale-reference,
  unsupported-block, and database rollback boundaries.
- `test_import_media.py`: generated-Pillow coverage for normal and comparison
  media readiness, orientation normalization, 480/800/1200/1600 renditions,
  source-order block mapping, featured assignment, decorative/alt-text rules,
  unused-definition filtering, rollback of rows and permanent files after
  processing or database failure, safe protection of unrelated files,
  bounded/idempotent `permanent_cleanup_paths` retries, post-commit private
  staging cleanup, repeated confirmation, bounded slug-race handling, and the
  supported-database concurrent-confirmation lock boundary. The lock-specific
  test is skipped on SQLite because it requires `SELECT FOR UPDATE`; PostgreSQL
  concurrency remains an environment-level verification gap.
- `test_migrations.py`: migration `0017` applies from `0016` with an empty
  staging schema and verifies the UUID primary key, expiry index, ownership
  relation, file relation, basename constraint, `SET_NULL` completed-post
  behavior, and nullable `consumed_at` field.

Run the focused suite with:

```bash
UV_CACHE_DIR=/tmp/django-blog-uv-cache DJANGO_SETTINGS_MODULE=config.settings.local uv run python manage.py test tests.blog
```

Run only the import contract tests with:

```bash
UV_CACHE_DIR=/tmp/django-blog-uv-cache DJANGO_SETTINGS_MODULE=config.settings.local uv run python manage.py test tests.blog.test_import_contract
```

Run the Ticket 03 form and package-validation tests with:

```bash
UV_CACHE_DIR=/tmp/django-blog-uv-cache DJANGO_SETTINGS_MODULE=config.settings.local uv run python manage.py test tests.blog.test_import_forms tests.blog.test_import_validation
```

Run the private staging and migration tests with:

```bash
UV_CACHE_DIR=/tmp/django-blog-uv-cache DJANGO_SETTINGS_MODULE=config.settings.local uv run python manage.py test tests.blog.test_import_staging tests.blog.test_migrations
```

Run the Ticket 05 draft/media tests with:

```bash
UV_CACHE_DIR=/tmp/django-blog-uv-cache DJANGO_SETTINGS_MODULE=config.settings.local uv run python manage.py test tests.blog.test_import_creation tests.blog.test_import_media
```

This Ticket 05-focused command passes with generated temporary media; its
SQLite run skips the `SELECT FOR UPDATE` concurrency test. Run the same tests
against PostgreSQL to verify the row-lock behavior.

Run the Ticket 06 and Ticket 07 Admin import tests with:

```bash
UV_CACHE_DIR=/tmp/django-blog-uv-cache DJANGO_SETTINGS_MODULE=config.settings.local uv run python manage.py test tests.blog.test_admin_import
```

This suite covers the Django client and static-source contracts for the private
import workflow, including delivery of the Admin CSS and JavaScript assets. It
does not execute JavaScript or prove real browser layout, focus movement,
keyboard interaction, theme rendering, zoom/reflow, or production proxy
isolation.

The implementation-focused verification commands are:

```bash
UV_CACHE_DIR=/tmp/django-blog-uv-cache DJANGO_SETTINGS_MODULE=config.settings.local uv run python manage.py check
uv run ruff check apps/blog/import_services.py tests/blog/test_import_creation.py
uv run python -m py_compile apps/blog/import_services.py tests/blog/test_import_creation.py
UV_CACHE_DIR=/tmp/django-blog-uv-cache DJANGO_SETTINGS_MODULE=config.settings.local uv run python manage.py makemigrations --check --dry-run
git diff --check
```

The import validation, Ticket 05 media, and Ticket 06 Admin import tests use
temporary `MEDIA_ROOT` and `BLOG_IMPORT_ROOT` directories; the form tests do
not write media. Ticket 05 media tests prove the application storage boundary,
permanent rollback
cleanup, safe retry metadata, and post-commit staging behavior,
not production web-server isolation, durable mounts, or scheduling of
`cleanup_blog_imports`; those remain deployment checks.

Trusted-origin helper validation is covered by `tests/core/test_sites.py`.
The shared sitemap and Admin registration integrations are covered by
`tests/core/test_sitemap.py` and `tests/core/test_dev_admin.py`; contact and
project sitemap providers have focused coverage under `tests/contact/` and
`tests/projects/`. Blog view tests also cover canonical, social, structured,
RSS, forwarded-scheme, and alternate-host output. The inline-link picker
interaction, selection preservation, keyboard behavior, and browser console
remain manual Admin checks; Django tests cover its configuration, validation,
persistence, and public HTML boundary. Browser layout and live proxy/DNS
redirect behavior are not covered by these Django tests. A representative
production-style article still needs manual checking with an external
structured-data validator.

The tag-overflow tests protect server-rendered tag markup, but no automated
browser test currently executes `tags.js` or verifies the popup's viewport
translation during open, resize, and narrow-layout interactions.
