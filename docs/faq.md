# Blog FAQ Blocks

The FAQ block stores one ordered collection of questions and rich-text answers
inside the normal structured Blog article flow. It is article content, not a
separate public FAQ product or schema endpoint.

## Editorial behavior

Editors add, remove, and reorder FAQ items through the custom Admin widget.
Questions are required plain text up to 300 characters. Answers use the shared
Blog rich-text sanitizer and must contain visible content. Empty FAQ blocks are
allowed while drafting and omitted publicly; malformed or incomplete stored
items fail closed.

The Admin JavaScript enhances the list editor, but the server remains the
validation boundary. Stored values are normalized again during publication,
rendering, internal-link validation, and reading-time calculation.

## Persistence and rendering

`BlogFAQBlock` stores the ordered item collection as validated JSON because FAQ
items have no independent lifecycle or reuse outside their parent block. The
block participates in the normal content-editor ordering and publication
checks.

Public output uses one labelled FAQ section with independent native
`<details>` disclosures. Answers contribute to reading time, remain outside the
article table of contents, and do not emit `FAQPage` structured data. Existing
`BlogPosting` and breadcrumb JSON-LD remain unchanged.

## Ownership

- Canonical shape and normalization: `apps/blog/faq.py`
- Model and migration: `BlogFAQBlock`, migration `0016_blogfaqblock`
- Admin widget and forms: `apps/blog/forms.py` and
  `templates/admin/blog/widgets/faq_items.html`
- Admin enhancement: `apps/blog/static/blog/js/faq-admin.js`
- Public template: `apps/blog/templates/blog/blocks/faq.html`
- Historical planning material: `docs/blog/features/faq-block/`

## Locale and accessibility

Static labels use gettext. Editorial questions and answers are English because
the current Blog publication contract is English-only. Native disclosure keeps
keyboard and no-JavaScript behavior without a public FAQ script.

## Tests

`tests/blog/test_models.py`, `test_admin.py`, and `test_views.py` cover JSON
normalization, item registration, malformed-content fallback, disclosure
markup, reading-time integration, and the absence of FAQ-specific JSON-LD.
Import support is covered by the Blog import contract and creation tests.

Real-browser coverage remains useful for Admin reordering/focus and public
disclosure presentation in narrow layouts.
