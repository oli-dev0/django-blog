"""Functions the host's ``apps.core.sites`` integration must provide.

The returned site definition objects must expose the attributes consumed by
the Blog, including ``slug``, ``name``, ``hosts``, ``template_namespace``,
``blog_url_namespace``, ``route_namespaces``, and Blog publication/SEO
configuration.
"""

# Constants used by the extracted implementation:
# PERSONAL_SITE, EASY_MEALS_SITE, and VANTA_SITE.

# Required functions:
# build_site_absolute_url(site_slug, path, *, scheme=None) -> str
# get_blog_site_definitions() -> mapping[str, SiteDefinition]
# get_blog_site_slug_choices() -> iterable[tuple[str, str]]
# get_site_definition(site_slug) -> SiteDefinition
# get_site_template_name(site, template_name) -> str
# require_site_for_host(host) -> SiteDefinition
