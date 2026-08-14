# Blog Features

## Publishing and editorial workflow

- **Draft creation:** Admin quick start creates a blank draft with a title, site, article type, category, and author.
- **Publication lifecycle:** Editors can mark articles ready, publish immediately, schedule publication, unpublish, and mark articles reviewed.
- **Multi-site publication:** One article can be assigned to multiple Blog sites, with a separate canonical site for SEO identity.
- **Scheduled visibility:** Public selectors calculate whether an article is effectively published from its status, publication time, and site assignment.
- **Live published edits:** Changes to an already published article become public immediately; there is no revision-staging layer.
- **Author profiles:** Articles can use profile-backed authors with stable public slugs, names, archive pages, and optional profile pictures.
- **Categories and tags:** Articles support required categories and reusable tags with public archive pages and Admin taxonomy management.
- **Site-scoped taxonomy:** Categories and tags retain globally stable names and slugs, but each term is explicitly enabled for one or more Blog websites. Article forms and publication validation require taxonomy compatibility across every selected site.
- **Structured content blocks:** Articles are assembled from headings, rich text, FAQs, checklists, code, verified provider embeds, callouts, source links, link groups, internal links, images, and image comparisons. See [Article content blocks](./content-blocks.md).
- **Reading-time metadata:** Reading time is computed from normalized article content and shown in public article metadata.
- **Related articles:** Editors can select related posts, while compatibility and public visibility rules keep invalid or unavailable relationships hidden.
- **Controlled internal links:** Editors can link to approved first-party destinations through registry keys instead of arbitrary URLs.

## Media and presentation

- **Blog image library:** Admin manages reusable featured and in-article images with separate content roles.
- **Validated image processing:** Uploads are checked for format, size, pixels, animation, and metadata, then stored with responsive WebP renditions.
- **Author image processing:** Author pictures are validated and cropped to a standard square rendition, with a shared default avatar when absent.
- **Comparison images:** Two independently processed images can be published as one paired block with separate alt text and a shared caption.
- **Responsive image delivery:** Public templates use available renditions, `srcset`, dimensions, and loading priorities for article media.
- **Image viewer:** Public articles provide a progressive-enhancement lightbox with loading/error states, captions, previous/next navigation, keyboard support, and touch swiping.
- **FAQ disclosures:** FAQ blocks render as ordered native `<details>` sections and remain part of the normal article content flow.

## Public reading and discovery

- **Blog homepage:** Each site provides a paginated list of effectively published articles.
- **Taxonomy and author archives:** Visitors can browse site-scoped category, tag, and author archives.
- **Server-rendered filtering:** The homepage and clean archives support combined article-type, category, author, date, year, and tag filters, with JavaScript enhancement only.
- **Personal-site type choices:** The personal website currently exposes only All, Articles, and Guides in its public type row; Comparisons, Top lists, and Showcases remain available to other Blog sites.
- **Article search:** The homepage search matches normalized query terms against visible article metadata and reader-facing content, preserves other filters and pagination, and ranks stronger title/category/tag matches first.
- **Article detail pages:** Details include breadcrumbs, title, summary, metadata, tags, optional table of contents, structured content, related articles, and a return link.
- **Responsive tag rows:** Tags that do not fit collapse behind a `+N` disclosure whose popup is repositioned to remain inside the viewport.
- **Article sharing:** Readers can use native sharing on supported touch-first browsers or a fallback menu for X, Facebook, LinkedIn, Reddit, WhatsApp, email, and copying the article link.
- **Read mode:** Readers can enter an in-place focused layout with progress tracking and an accessible exit action; no state is persisted.
- **Printing:** Print prepares lazy images and opens the browser’s native print preview with article-focused print styling.
- **RSS feed:** Each site exposes an English feed containing the latest effectively published articles.
- **RSS autodiscovery:** Public Blog lists, archives, and details advertise the active site’s feed through standard head metadata.

## SEO and public metadata

- **Canonical identity:** Canonical URLs, metadata, feeds, sitemaps, and robots references use each site’s trusted primary origin.
- **Open Graph and social metadata:** Article pages emit site-specific titles, descriptions, URLs, images, and article timestamps for social previews.
- **Structured data:** Pages emit escaped `BlogPosting` and `BreadcrumbList` JSON-LD based on visible article facts.
- **Sitemaps:** Public article and sufficiently populated taxonomy/author archive URLs are included with meaningful last-modified dates.
- **Indexability rules:** Drafts, wrong-site content, invalid archives, and filtered combinations receive the appropriate 404, noindex, or canonical behavior.

## Admin, import, and safety

- **Structured Admin editor:** Django Admin provides ordered block editing, taxonomy controls, publication-site management, related articles, media selection, and workflow actions.
- **Saved website previews:** Authenticated editors can preview an article in a selected Blog site shell, including unavailable-site warnings when needed.
- **Private article import:** Editors can upload a versioned JSON package and local images, review resolved references, then create a separate draft.
- **Versioned import contracts:** Import v1 remains supported; import v2 can propose named missing categories/tags and explicitly review term creation or website-assignment expansion before draft creation.
- **Import staging and cleanup:** Import files use private storage, owner-bound staging, expiry, transaction compensation, and a cleanup management command.
- **Permission checks:** Blog model permissions and custom organize, publish, and unpublish permissions protect Admin operations.
- **Fail-closed validation:** Publication, rendering, related articles, internal links, and media independently reject or hide invalid stored data.

## Sites, language, and boundaries

- **Shared Blog backend:** `apps/blog` supplies common data, selectors, rendering, feeds, sitemaps, and content behavior while each site owns its shell and branding.
- **Site-owned shells:** The shared Blog can render through the personal-site, Easy Meals, and Vanta presentation shells in the configured/test matrix. Public Blog routing is currently enabled for the personal site and Vanta; Easy Meals retains its shell for portability and presentation coverage but has no public Blog namespace in the default site registry.
- **English-only contract:** Blog content, slugs, metadata, public routes, and responses use English; non-English Blog paths return 404.
- **Server-rendered interface:** The public Blog is Django HTML and RSS with progressive enhancement; there is no public Blog API or mobile client.
