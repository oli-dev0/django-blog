# Blog Article Import

The Blog import workflow creates a new draft article from one versioned JSON
document and optional local image files. It is a private Django Admin workflow;
there is no public import route or API.

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

`apps/blog/import_contract.py` parses at most 1 MiB of UTF-8 JSON against the
matching checked-in v1 or v2 schema under `apps/blog/schemas/`. v1 remains
supported for existing packages; v2 adds required taxonomy names so the review
can safely propose term creation. Unknown fields,
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

- Contract and schema: `apps/blog/import_contract.py`, `apps/blog/schemas/`
- Forms and package review: `apps/blog/import_forms.py`
- Staging, validation, creation, and cleanup: `apps/blog/import_services.py`
- Admin adapter: `apps/blog/admin.py`
- Templates: `apps/blog/templates/admin/blog/import_*.html`
- Cleanup command: `apps/blog/management/commands/cleanup_blog_imports.py`
- Historical planning material: `docs/blog/features/import/`

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
