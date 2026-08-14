# Django Blog Showcase

This is a working example of a full-featured blog built with Django.

It goes beyond a basic title-and-body blog. Articles can use reusable content blocks, images, FAQs, related links, social embeds, search, filters, RSS feeds, and SEO metadata. There is also a structured import flow for preparing complete article drafts.

The project is shared as a showcase for anyone who wants to explore how these features can fit together in a real Django application.

## See it in action

You can see the Blog running on two live websites:

- [Personal Blog](https://oli-dev0.me/blog/)
- [Vanta Admin Blog](https://vanta-admin.org/en/blog/)

You can also [read more about the project](https://oli-dev0.me/projects/django-blog).

For a closer look at every feature working together, read the full showcase article:

- [Full Blog feature showcase](https://oli-dev0.me/blog/django-blog-publishing-system/)

## What you can explore

- A clear writing and publishing workflow in Django Admin
- Flexible article layouts made from reusable content blocks
- Search, categories, tags, authors, and article filters
- Image uploads, responsive image sizes, and comparison images
- FAQ sections and controlled links between articles and pages
- YouTube, X, and Reddit embeds with privacy and safety checks
- RSS feeds, sitemaps, sharing metadata, and other SEO basics
- JSON article imports with careful validation and cleanup
- Tests for the main publishing, security, and display behaviour

If you want the full technical detail, the [`docs/`](docs/) folder explains how each part works.

## A good place to start

You don't need to understand the whole project at once.

- Start with [`blog/models.py`](blog/models.py) to see how articles and content blocks are stored.
- Look at [`blog/admin.py`](blog/admin.py) for the writing and publishing experience.
- Browse [`blog/templates/blog/`](blog/templates/blog/) to see how articles are displayed.
- Read the [feature overview](docs/features.md) for a guided tour of the main parts.
- Check the [`tests/`](tests/) folder for practical examples of the expected behaviour.

## Using this code in another project

This repository contains the Blog part of a larger Django project. It isn't a complete website that you can run by itself.

The original project provides shared settings, permissions, image checks, page templates, URLs, and support for publishing the same Blog on more than one website. If you want to reuse the code, copy `blog/` into your project as `apps/blog/` and adapt those connections to match your own setup.

You'll need Django, Pillow, Bleach, and jsonschema. You will also need to connect the app to your own settings, URLs, database, templates, and media storage before running migrations.

For the exact integration details, see:

- [Data model](docs/database.md)
- [Content blocks](docs/content-blocks.md)
- [Import workflow](docs/import.md)
- [Services and selectors](docs/services-selectors.md)
- [SEO and feeds](docs/SEO.md)
- [Test coverage](docs/tests-backend-web-api.md)

## A note about testing

The included tests came from the original Django project, so they need a compatible host project and settings to run. The code can still be read, studied, and adapted without that project.

Once it is connected to a compatible Django project, the Blog test suite can be run with:

```bash
DJANGO_SETTINGS_MODULE=config.settings.local python manage.py test tests.blog
```

## Security and privacy

The Blog includes practical safety measures for rich text, uploads, imported files, internal links, publishing permissions, and third-party embeds. These are useful examples, but every project still needs its own secure Django settings, storage rules, trusted domains, dependency updates, and deployment checks.

No production data, private settings, credentials, or deployment configuration are included in this showcase.

## License

This project is available under the [MIT License](LICENSE).
