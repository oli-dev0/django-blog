from django.conf import settings

from apps.core.sites import PERSONAL_SITE


BLOG_ENABLED_SITE_DEFINITIONS = {
    site_slug: {
        **definition,
        'route_namespaces': tuple(
            dict.fromkeys(
                (
                    *definition['route_namespaces'],
                    'blog',
                    *(('personal_blog',) if site_slug == PERSONAL_SITE else ()),
                )
            )
        ),
        'blog_url_namespace': 'personal_blog' if site_slug == PERSONAL_SITE else 'blog',
    }
    for site_slug, definition in settings.SITE_DEFINITIONS.items()
}

REFERENCE_BLOG_SITE_DEFINITIONS = {
    site_slug: {
        **definition,
        'template_namespace': 'blog',
        'route_namespaces': tuple(dict.fromkeys((*definition['route_namespaces'], 'blog'))),
        'blog_url_namespace': 'blog',
    }
    for site_slug, definition in settings.SITE_DEFINITIONS.items()
}
