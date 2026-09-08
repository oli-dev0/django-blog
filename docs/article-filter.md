# Blog Article Filtering

The Blog list and clean taxonomy archives share one server-rendered GET filter.
The feature narrows public articles without creating a second search index,
API, or client-side result store.

## Public behavior

Visitors can combine one article type, one category, one author, a date preset
or year, and multiple tags. Tags use AND semantics. A date preset takes
precedence over a year. The available values are derived from effectively
public posts assigned to the active site.

The homepage also provides a `q` search field. Query whitespace is normalized,
only the first non-empty query is used, and the value is limited to 10 terms and
200 characters. Every distinct term must match the title, summary, category,
reader-facing content, or a tag. Results are ranked by exact title, category or
tag phrase, summary, then reader-facing content before the normal publication
date and primary-key ordering. Search can be combined with every other filter;
clean category, tag, and author archives redirect a search to the combined Blog
homepage URL while preserving the archive dimension.

The filter is available on the Blog homepage and initializes from clean
category, tag, and author archives. Combined archive selections submit to the
normal Blog list URL. Invalid or duplicate values redirect to one normalized
URL; pagination preserves the normalized state and out-of-range pages return
404.

Filtered combinations are `noindex, follow` and canonicalize to the clean Blog
homepage. Clean archives keep their own canonical and indexability rules.

## Progressive enhancement

The HTML form and result list work without JavaScript. `list.js` enhances the
custom accessible dropdowns, immediate desktop submission, mobile apply/cancel
behavior, tag-menu restoration after a tag change, and article-type overflow
navigation.

Opening an article can store one short-lived same-tab return target.
`article.js` validates that handoff before using it; arbitrary origins and
unrelated paths are not accepted. Category, author, and date changes close the
active filter panel after submission.

## Ownership

- `apps/blog/filters.py` parses and serializes immutable `FilterState` values.
- `apps/blog/selectors.py` discovers site-scoped choices and applies the query.
- `apps/blog/content_text.py` extracts normalized reader-facing block text for
  the searchable article-content field, and `apps/blog/signals.py` refreshes it
  when searchable content blocks change.
- `apps/blog/views.py` owns redirects, pagination, canonical/robots headers,
  archive initialization, search redirects, and empty states.
- `apps/blog/templates/blog/list_filters.html` owns shared form markup.
- `apps/blog/static/blog/js/list.js` and `article.js` own enhancement only.
- Each site app owns its Blog list shell and styling presentation.

The filter dimensions read existing posts, publications, taxonomy, authors, and
publication dates. Search adds the non-editable `BlogPost.search_body_text`
field and migration `0021_add_blog_search_body`; the migration backfills
reader-facing heading, rich-text, FAQ, checklist, callout, source-link,
link-group, and internal-link text while excluding code blocks. Signals keep
that field current after searchable block saves and deletes. There is no
separate search service, API, or external index.

## Tests

`tests/blog/test_filters.py` covers parsing, normalized serialization, option
visibility, combined dimensions, archive initialization, pagination, headers,
empty states, and filter markup. `tests/blog/test_search.py` covers term
matching and relevance, content extraction, site/publication boundaries,
normalization, canonical/robots output, archive redirects, pagination, clear
links, empty states, and all three site shells. `tests/blog/test_migrations.py`
covers the search-body backfill and code-block exclusion. `tests/blog/test_views.py`
covers the clean archive and public-list boundary.

Real-browser coverage remains useful for custom dropdown focus, mobile
apply/cancel, overflow controls, responsive wrapping, and one-shot return state.
Real-browser layout, keyboard, and progressive-enhancement checks remain manual verification responsibilities for the host project.

Historical requirements remain under `docs/blog/features/article-filter/`.
