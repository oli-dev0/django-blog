# Blog Article Content Blocks

Blog articles use one ordered `main` region managed by
`django-content-editor`. The available blocks are code-owned Admin inlines;
they are rendered by shared Blog templates and can be reordered, edited, or
removed without a separate page-builder or template identity.

## Available blocks

- **Heading**: an H2-H4 section heading with a generated anchor.
- **Rich text**: sanitized prose with configured links, lists, tables,
  blockquotes, and controlled internal-link attributes.
- **FAQ**: ordered questions and sanitized answers rendered as native
  `<details>` elements. It remains normal article content and does not emit
  `FAQPage` structured data.
- **Checklist**: plain-text items with a checkmark, square, or arrow marker.
- **Code**: a code sample with a supported language and optional caption.
- **Embed sharing**: one verified public YouTube video, X post, or Reddit post,
  with an optional plain-text caption and an `Open` link to the canonical item.
- **Callout**: a sanitized rich-text note, tip, or warning with an optional
  title.
- **Source link**: a labeled external HTTP(S) source with an optional note.
- **Link group**: a labeled collection of validated external links.
- **Internal link**: a stable key from the code-owned destination registry,
  descriptive anchor text, and an optional note.
- **Image**: a reusable non-featured Blog image with optional expansion.
- **Image comparison**: a reusable comparison pair rendered through the shared
  comparison viewer.

## Embed sharing

The Admin accepts supported public provider URLs and normalizes them to a
canonical HTTPS URL before saving. Local parsing validates the provider and
item identifier; bounded provider verification confirms the item without
storing or rendering provider-supplied HTML. Only YouTube, X, and Reddit are
supported. Invalid or unavailable stored references fail closed to the visible
“This content is currently unavailable” fallback.

Public rendering is progressive enhancement: YouTube uses a privacy-enhanced
iframe, while X and Reddit use provider targets populated by
`blog/js/embed-sharing.js`. The shared template keeps the fallback and the
canonical source link available. Captions are escaped plain text. Embed
scripts are included only on detail pages that contain an embed block.

Embed presentation is owned by both
`apps/blog/static/blog/css/article.css` and
`apps/vanta_site/static/vanta_site/css/blog.css`; keep the responsive embed
rules synchronized. The contract includes bounded provider widths, rounded
clipping for the X target, a flexible caption/source footer, visible keyboard
focus, forced-colors handling, and print behavior that retains the source link
and caption while hiding provider content.

## Import and persistence

The private article-import contract represents this block as
`{"type": "embed_sharing", "platform": ..., "url": ..., "caption": ...}`.
The model is `BlogEmbedSharingBlock`, stored as an ordered child of
`BlogPost` in the `main` region. Migration `0022_blogembedsharingblock` adds
the table without changing existing article content.

The shared block registry is `BLOG_BLOCK_MODELS` in
`apps/blog/models.py`; Admin inline ordering is defined in
`BlogPostAdmin.inlines`. Public block templates live under
`apps/blog/templates/blog/blocks/`. Site apps provide the surrounding article
shells, but do not duplicate block behavior.

## Related documentation and tests

- [Blog overview](./overview.md)
- [Database](./database.md)
- [Article import](./import.md)
- [FAQ blocks](./faq.md)
- [Comparison images](./comparison-images.md)
- [Backend and web tests](./tests-backend-web-api.md)
