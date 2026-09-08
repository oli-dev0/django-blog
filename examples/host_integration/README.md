# Host Integration Reference

These snippets are starting points for integrating the extracted Blog into an
existing Django project. They are not a standalone settings package.

1. Copy `blog/` to `apps/blog/` so the existing `apps.blog` imports and
   migration references remain valid.
2. Add `apps.blog.apps.BlogConfig` to `INSTALLED_APPS`.
3. Add the values from `settings_example.py` to the host settings.
4. Include `apps.blog.urls` as shown in `urls.py`.
5. Implement the `apps.core.sites` functions listed in `site_contract.py`, or
   adapt those imports to the host project's site registry.
6. Configure local-path-capable media storage, run migrations, and collect
   static files before exercising image or rendering flows.

The bundled `blog/templates/blog/base.html` and app-owned static assets provide
a neutral reference frontend. A host can override the shell without changing
the Blog's content templates.

## Neutral site examples

The internal-link registry and host integration tests use `PERSONAL_SITE`,
`SECONDARY_SITE`, and `PRODUCT_SITE` as illustrative constants, with slugs
`personal_site`, `secondary_site`, and `product_site`. Implement them in the
host adapter or adapt the registry to your own sites and named routes.
These examples do not imply that three websites are required. Custom-shell
integration tests expect matching host templates/assets; those shells are not
bundled. Use the reference-frontend tests for the included presentation.

Publisher metadata, social images, and stylesheet overrides are optional host
settings documented in `settings_example.py`. The default uses the shared
article stylesheet and omits unconfigured publisher and social-image metadata.
The import JSON example references an example author, site, and internal-link
key; create or replace those references before importing it.
