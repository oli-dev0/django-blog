# Blog API

No public or DRF blog API exists. The supported interface is server-rendered
Django HTML/RSS:

| Method | Path | Result |
| --- | --- | --- |
| GET | `/blog/` or `/en/blog/` | Site-scoped, paginated public list |
| GET | `/blog/tag/<slug>/` or `/en/blog/tag/<slug>/` | Site-scoped public tag archive |
| GET | `/blog/category/<slug>/` or `/en/blog/category/<slug>/` | Site-scoped public category archive |
| GET | `/blog/author/<author_slug>/` or `/en/blog/author/<author_slug>/` | Site-scoped public author archive |
| GET | `/blog/<slug>/` or `/en/blog/<slug>/` | One public article |
| GET | `/blog/rss/` or `/en/blog/rss/` | Up to 20 public RSS items |

The unprefixed forms are the personal-site routes. The `/en/` forms are the
Vanta routes; the personal site does not expose `/en/blog/` aliases. Easy
Meals has no Blog URL namespace in the default site registry.

List, tag, category, and author archive routes accept `page`; invalid, empty, or
out-of-range values 404. All public reads reuse `apps.blog.selectors` and
expose only effectively published content assigned to the active site. Author
archive routes resolve the exact stable profile slug, then filter by that
profile. Unknown slugs and authors without public articles 404. Empty or
unknown categories likewise 404.
Admin workflow and preview routes are private Django views, not client API
contracts.

There are no JSON shapes, bearer authentication rules, API pagination/filter
syntax, versioning headers, or public write methods. Internal statuses, audit
users, permissions, preview controls, unpublished content, and site
compatibility choices are intentionally not exposed through an API.
