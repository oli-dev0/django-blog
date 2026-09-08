# Blog Article Import

The Blog import workflow creates a new draft article from a version 2 JSON
document and optional local image files. It is a private Django Admin workflow;
there is no public import route or API.

This guide documents the v2 format only. The checked-in JSON Schema is the
machine-readable contract:

[`blog/schemas/blog-article-import-v2.schema.json`](../blog/schemas/blog-article-import-v2.schema.json)

A broad example covering the complete package structure is available at
[`docs/example-blog-article.json`](example-blog-article.json).

## Quick start

1. Copy the example JSON and edit the article metadata and blocks.
2. Put every referenced image file in the upload selection when opening the
   Admin import form. The `file` value is matched to the selected file's
   basename, so use unique filenames.
3. Upload the JSON and images together, review the resolved values, then
   explicitly confirm draft creation.

The root must always contain these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `format` | string | Always `blog-article-import`. |
| `version` | integer | Always `2`. |
| `article` | object | Article metadata and ordered content blocks. |
| `assets` | array | Definitions for regular and featured images. |
| `comparisons` | array | Definitions for two-sided comparison images. |

Unknown fields are rejected. The schema also rejects duplicate JSON keys,
unsafe image paths, invalid references, and unsupported block shapes.

## Article fields

`article` requires `title`, `summary`, `author`, `category`,
`publication_sites`, and at least one `blocks` entry.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `title` | string | yes | 1–200 characters. |
| `slug` | slug string | no | Lowercase words separated by hyphens. If omitted, it is generated from `title`. |
| `type` | string | no | `article`, `guide`, `comparison`, `top_list`, or `showcase`. Defaults to `article`. |
| `summary` | string | yes | The article summary. |
| `author` | `{ "slug": "..." }` | yes | References an existing author profile by slug. |
| `seo` | object | no | Optional `title` (maximum 70) and `description` (maximum 160). |
| `category` | `{ "name": "...", "slug": "..." }` | yes | v2 requires both the display name and slug. |
| `tags` | array of `{ "name": "...", "slug": "..." }` | no | Defaults to `[]`; names and slugs must be unique within the document. |
| `publication_sites` | array of site slugs | yes | At least one site. |
| `canonical_site` | site slug | no | Must be one of `publication_sites`; otherwise the first selected site is used. |
| `featured_image` | asset id or `null` | no | Must reference an entry in `assets`. |
| `related_articles` | array of `{ "slug": "..." }` | no | Defaults to `[]`; references existing compatible articles. |
| `blocks` | array | yes | Ordered content; each entry uses one block type below. |

Names and slugs are source values. The import review resolves them against the
destination project's authors, taxonomy, sites, and related articles. Missing
authors and missing related articles block the import. v2 can propose creating
missing named categories and tags, but the editor must confirm that choice and
have the required permissions.

## Content blocks

Every block has a `type` field. The supported shapes are:

| Type | Required fields | Optional fields |
| --- | --- | --- |
| `heading` | `level` (`2` or `3`), `text` | — |
| `rich_text` | `body` | — |
| `faq` | `items` containing `question` and `answer` | — |
| `checklist` | `items` | `marker`: `checkmark`, `square`, or `arrow` (default `checkmark`) |
| `code` | `code` | `language`: `text`, `python`, `shell`, `html`, `css`, `javascript`, `json`, `sql`, or `dart`; `caption` |
| `embed_sharing` | `platform`, `url` | `caption`; platform is `youtube`, `x`, or `reddit` |
| `callout` | `body` | `callout_type`: `note`, `tip`, or `warning` (default `note`); `title` |
| `source_link` | `url` | `label` (default `Source:`), `note` |
| `link_group` | `label`, `links` containing `label` and `url` | — |
| `internal_link` | `destination_key`, `label` | `note` |
| `image` | `asset_id` | `is_expandable` (default `true`) |
| `image_comparison` | `comparison_id` | — |

Rich text and answer/body fields contain the HTML supported by the host Blog
editor. Image blocks refer to definitions in `assets`; they do not contain
file paths directly. Internal links use a code-owned destination key, not a
free-form URL. The available keys depend on the host project and selected
publication sites.

## Image assets

Each `assets` entry has `id`, `file`, `name`, and `alt_text`:

```json
{
  "id": "hero",
  "file": "images/hero.jpg",
  "name": "Article hero image",
  "alt_text": "A short description of the hero image",
  "is_feature": true,
  "is_decorative": false,
  "caption_title": "Optional title",
  "caption_text": "Optional caption"
}
```

`id` is the document-local identifier used by `featured_image` and image
blocks. `file` is a relative POSIX path used only to match an uploaded file;
URLs, absolute paths, `..`, backslashes, and duplicate basenames are invalid.
Use `is_decorative` only when the image is decorative and does not need alt
text. `is_feature` is metadata for the featured-image choice; the article's
`featured_image` field is the explicit assignment.

Comparison entries use `id`, `name`, `first`, and `second`. Each side has a
`file` and non-empty `alt_text`:

```json
{
  "id": "before-after",
  "name": "Before and after",
  "first": { "file": "images/before.jpg", "alt_text": "Before the change" },
  "second": { "file": "images/after.jpg", "alt_text": "After the change" },
  "caption_title": "The result",
  "caption_text": "A short comparison caption."
}
```

## Product behavior

Editors open the import action from the Blog post Admin. Upload accepts one JSON
file and optional image files, validates the complete package, and redirects to
an owner-bound review page. Review shows source values beside resolved authors,
taxonomy, sites, related articles, blocks, and media. Missing authors and
existing taxonomy mappings are never guessed. Import v2 may offer explicit
creation of named missing categories/tags and may ask the editor to confirm
adding selected existing terms to the chosen websites. The editor must resolve
blocking issues and explicitly confirm draft creation.

The final action always creates a separate `DRAFT` post. It never publishes,
updates, replaces, or merges an existing article. Duplicate titles and slugs are
warnings; generated slugs receive deterministic numeric suffixes when needed.

## Contract and validation

`blog/import_contract.py` parses at most 1 MiB of UTF-8 JSON against the
checked-in v2 schema under `blog/schemas/`. v2 requires taxonomy names so the
review can safely propose term creation. Unknown fields,
duplicate keys, malformed roots, unsafe paths, invalid references, and empty or
unsafe block content are rejected with stable issue codes and `$`-based source
locations.

The contract covers the current Blog metadata, sites, taxonomy, related posts,
eleven block types, featured images, regular images, and comparison images.
Image paths must be relative POSIX paths and are matched to selected file
basenames. URLs, traversal, absolute paths, backslashes, control characters,
missing files, duplicate basenames, and ambiguous matches are blocking.

`validate_and_stage_blog_import()` resolves exact stable slugs and internal-link
keys. It checks the core post permissions plus the add permission for every
payload-owned block or relationship type. Final review revalidates taxonomy
availability for every selected publication site, requiring category/tag change
permission before expanding existing assignments and category/tag add
permission before creating v2 terms. Related articles and internal links must
be valid for every reviewed publication site.

## Private staging

`BlogArticleImport` and `BlogArticleImportFile` hold the normalized payload,
review state, warnings, and selected files. Staging uses the private
`blog_imports` storage alias rooted at `BLOG_IMPORT_ROOT`; it must remain
separate from `MEDIA_ROOT` and has no public URL.

Pending sessions are available only to their authenticated creator before
expiry and while unconsumed. The default retention is 24 hours and the default
cleanup batch is 100. These settings can be overridden with
`BLOG_IMPORT_RETENTION_HOURS` and `BLOG_IMPORT_CLEANUP_BATCH_SIZE`.

Expired, consumed, and failed-cleanup sessions are processed with:

```bash
UV_CACHE_DIR=/tmp/django-blog-uv-cache DJANGO_SETTINGS_MODULE=config.settings.local uv run python manage.py cleanup_blog_imports --batch-size 100
```

Deployment must schedule that command; defining retention does not create a
scheduler.

## Draft creation and storage safety

`create_blog_post_from_import()` locks the staging row, revalidates ownership,
expiry, permissions, current database references, files, site compatibility,
and confirmation, then creates the post and ordered content inside
`transaction.atomic()`.

Database rollback cannot remove storage writes. The service tracks newly
created Blog originals and renditions and deletes them if a later operation
fails. Failed permanent cleanup is retained as bounded retry state on the
staging record and blocks another creation attempt until cleanup succeeds.
Existing media is never a cleanup target.

After success, the session is marked consumed and private-file cleanup runs
after commit. A cleanup failure leaves the consumed session unavailable for
reuse and recoverable by the cleanup command.

## Admin and security boundary

The routes are registered through `BlogPostAdmin.get_urls()` in both the
normal and development Admin namespaces. Responses are English-only, private,
non-cacheable, noindex, and same-origin referrer-scoped. Unsupported methods
return `405` with the allowed methods.

Templates render bounded plain text and safe Admin URLs. They do not expose raw
payload HTML, storage paths, staged file URLs, exceptions, or private account
data. JavaScript only improves selected-file display and error focus; native
forms remain the complete fallback.

## Implementation map

- Contract and schema: `blog/import_contract.py`, `blog/schemas/`
- Forms and package review: `blog/import_forms.py`
- Staging, validation, creation, and cleanup: `blog/import_services.py`
- Admin adapter: `blog/admin.py`
- Templates: `blog/templates/admin/blog/import_*.html`
- Cleanup command: `blog/management/commands/cleanup_blog_imports.py`

## Tests

Focused coverage lives in:

- `tests/blog/test_import_contract.py`
- `tests/blog/test_import_forms.py`
- `tests/blog/test_import_validation.py`
- `tests/blog/test_import_staging.py`
- `tests/blog/test_import_creation.py`
- `tests/blog/test_import_media.py`
- `tests/blog/test_admin_import.py`

SQLite does not prove the row-lock concurrency path. PostgreSQL CI is the
authoritative database boundary for concurrent confirmation behavior. A real
browser test for upload/review responsive and keyboard behavior is still a
manual or future Playwright check.
