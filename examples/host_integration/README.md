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
