"""Safe Blog settings to adapt in the host project's settings module."""

from pathlib import Path


INSTALLED_APPS += [  # noqa: F821
    'apps.blog.apps.BlogConfig',
]

BLOG_IMPORT_ROOT = Path(MEDIA_ROOT) / '_staged_blog_imports'  # noqa: F821
BLOG_IMPORT_RETENTION_HOURS = 24
BLOG_IMPORT_CLEANUP_BATCH_SIZE = 100

# No publisher identity, social image, or site-specific stylesheet is assumed.
# Keys are your configured site slugs. Only reference assets that actually exist.
BLOG_SITE_PUBLISHERS = {}
BLOG_SITE_SOCIAL_IMAGES = {}
BLOG_STYLESHEET_PATHS = {}

# For example, after adding your real publisher name and logo:
# BLOG_SITE_PUBLISHERS = {
#     'product_site': {
#         'type': 'Organization',
#         'name': 'Your organization',
#         'home_url_name': 'product_site:home',
#         'logo_path': 'product_site/img/logo.png',
#     },
# }
# BLOG_SITE_SOCIAL_IMAGES = {
#     'product_site': ('product_site/img/social-preview.png', 'Your image description'),
# }
# BLOG_STYLESHEET_PATHS = {'product_site': 'product_site/css/blog.css'}
