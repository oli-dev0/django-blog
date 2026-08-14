# Blog Article Sharing, Read Mode, and Printing

Sharing, read mode, and printing are progressively enhanced client-side
controls on public English Blog article detail pages. They keep the existing
article URL and server-rendered content. Sharing hands the current title and URL
to the visitor's browser or a selected service, read mode narrows the article
for focused reading, and printing prepares a clean paper/PDF layout through the
browser's native print preview.

## Public behavior

- Public articles on the personal, Easy Meals, and Vanta Blog shells render an
  icon-only `Read mode` action in the article metadata row, including articles
  without tags. Its English-only accessible label and popup are `Read mode`.
- Activating the action keeps the same URL and article DOM, hides the known
  surrounding navigation/related/back content, applies the narrow reading
  layout, and shows `Exit read mode` plus a native reading-progress element.
- Entry and exit preserve normalized article-relative position when the
  browser supports the required layout APIs. Exit returns focus to the entry
  action when visible, otherwise to the article root.
- The article retains its title, metadata, featured image, table of contents,
  every existing content block, links, and image dialog.
- Reloading starts in normal presentation. No read-mode state or progress is
  persisted in cookies, storage, sessions, accounts, or the database.
- Saved Admin previews do not render read-mode controls.

## Printing

- Public articles on all three Blog shells render a printer icon with the
  literal accessible label and tooltip `Print` when `window.print` is available.
- Activating it calls the browser's native `window.print()` API. No route,
  query parameter, fragment, server request, account state, or database state
  is created.
- The Print-button path temporarily switches lazy article images to eager
  loading and waits for them to finish loading before opening print preview;
  this ensures lower article and comparison images are included. The original
  loading attributes are restored afterward.
- Print media rules hide site chrome, navigation, tags, related articles,
  dialogs, focus/print controls, and progress UI. The article, title, summary,
  table of contents, metadata, links, figures, callouts, tables, code, and FAQ
  content remain printable with light colors and print-oriented page margins.
- Print styling is paired in `apps/blog/static/blog/css/article.css` and
  `apps/vanta_site/static/vanta_site/css/blog.css`. The button remains hidden
  when the browser does not expose `window.print`; saved Admin previews never
  render it.

## Sharing the article

- Public articles on all three Blog shells render an icon-only `Share` action
  before Print. The action is progressively revealed after its menu and copy
  behavior initialize.
- On a secure, coarse-pointer device with Web Share support, activating Share
  opens the browser's native share sheet with the visible article title and
  current URL. Cancelling leaves the page unchanged. If native sharing throws
  or rejects for another reason, the platform menu opens instead.
- Other browsers use the fallback menu. It provides URL-encoded links, in this
  order, for X, Facebook, LinkedIn, Reddit, WhatsApp, and email, followed by
  `Copy link`. Platform links open in a new tab or external handler with
  `noopener noreferrer`.
- The menu toggles from the Share action and closes after choosing a platform,
  clicking outside it, or pressing Escape. Platform selection and Escape return
  focus to Share.
- `Copy link` uses the Clipboard API in a secure context and the existing
  textarea fallback elsewhere. It changes to `Article link copied` for 1.8
  seconds after success or `Copy failed` after rejection, then restores its
  default label.
- Sharing and copying are transient. They do not add a route, query parameter,
  fragment, server request to this application, account state, or database
  state. The entire Share control is hidden from print output, read mode, and
  saved Admin previews.

These controls do not add a route, query parameter, fragment, API, feed item,
sitemap entry, canonical, structured-data identity, or social metadata.

## Ownership and implementation

The shared article partial owns the markup and literal English labels:
`apps/blog/templates/blog/article_content.html`. The existing deferred
`apps/blog/static/blog/js/article.js` owns sharing, copy-link, print, and read
mode initialization, hidden-state swaps, focus, position restoration, and
requestAnimationFrame-throttled progress. Share URLs are built from the visible
article heading and `window.location.href`; no application endpoint is called.

The personal and Easy Meals shells use
`apps/blog/static/blog/css/article.css`; Vanta uses
`apps/vanta_site/static/vanta_site/css/blog.css`. Both stylesheets contain the
paired read-mode layout, responsive reflow, theme-token, forced-colors,
reduced-motion, and print rules. Locally hosted variable Literata normal and
italic WOFF2 files live under `apps/blog/static/blog/fonts/` and are loaded
only by the active reading typography rules. `OFL.txt` remains alongside the
font files.

There are no model, migration, view, selector, service, permission,
configuration, or dependency changes. Existing publication visibility and
site assignment remain server-side authorities.

## Known shell limitation

Easy Meals is currently a placeholder page. Its header is nested inside its
`main` element and is not included in the current shared shell-hide selectors;
this was intentionally left unchanged because the placeholder shell is out
of scope for this feature finalization.

## Verification and tests

Server-rendered coverage is in:

- `tests/blog/test_views.py` for all three public shells, tagged/no-tag rows,
  Share/menu markup, platform order and icons, labels, assets, and preserved
  article semantics.
- `tests/blog/test_admin.py` for preview omission.
- `tests/blog/test_fonts.py` for local WOFF2 validity, variable axes, license,
  and paired stylesheet declarations.
- `tests/blog/test_read_mode.py` for the Python-only JavaScript and CSS source
  contracts (native sharing and fallback, platform URL construction,
  copy-link clipboard/fallback and feedback behavior, menu dismissal and focus,
  state swaps, position safeguards, progress bounds, responsive rules, forced
  colors, reduced motion, print controls, print exclusions, and wide-content
  constraints).

Run the focused feature checks with:

```bash
set -a; source .env; set +a
UV_CACHE_DIR=/tmp/django-blog-uv-cache DJANGO_SETTINGS_MODULE=config.settings.local uv run python manage.py test tests.blog.test_read_mode tests.blog.test_fonts tests.blog.test_views tests.blog.test_admin
UV_CACHE_DIR=/tmp/django-blog-uv-cache uv run ruff check tests/blog/test_read_mode.py tests/blog/test_fonts.py
node --check apps/blog/static/blog/js/article.js
```

No Playwright test exists for sharing, read mode, or printing. The Python tests
do not prove an actual native share sheet, external-handler behavior, browser
clicks, clipboard access, focus movement, scrolling, theme switching, native
print-preview output, pagination, or rendered layout; those remain
manual/browser verification surfaces.
