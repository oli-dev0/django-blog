from django.conf import settings

from apps.core.sites import PERSONAL_SITE, SECONDARY_SITE, PRODUCT_SITE
from django.utils.translation import gettext_lazy as _


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


# Optional host-owned assets and identities used only by integration tests.
SITE_SOCIAL_IMAGES = {
    PERSONAL_SITE: ('personal_site/img/avatar.png', _('Author portrait')),
    SECONDARY_SITE: ('secondary_site/img/logo.png', _('Secondary Site logo')),
    PRODUCT_SITE: ('product_site/img/social-preview.png', _('Product Site interface preview')),
}

SITE_PUBLISHERS = {
    PERSONAL_SITE: {
        'type': 'Person',
        'name': 'Example Author',
        'home_url_name': 'site-root',
        'image_path': 'personal_site/img/avatar.png',
    },
    SECONDARY_SITE: {
        'type': 'Organization',
        'name': 'Secondary Site',
        'home_url_name': 'site-root',
        'logo_path': 'secondary_site/img/logo.png',
    },
    PRODUCT_SITE: {
        'type': 'Organization',
        'name': 'Product Site',
        'home_url_name': 'product_site:home',
        'logo_path': 'product_site/img/logo.png',
    },
}

BLOG_STYLESHEET_PATHS = {
    PERSONAL_SITE: 'personal_site/css/blog.css',
    PRODUCT_SITE: 'product_site/css/blog.css',
}

BLOG_PRESENTATION_SETTINGS = {
    "BLOG_SITE_SOCIAL_IMAGES": SITE_SOCIAL_IMAGES,
    "BLOG_SITE_PUBLISHERS": SITE_PUBLISHERS,
    "BLOG_STYLESHEET_PATHS": BLOG_STYLESHEET_PATHS,
}
