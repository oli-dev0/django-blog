"""Safe Blog settings to adapt in the host project's settings module."""

from pathlib import Path


INSTALLED_APPS += [  # noqa: F821
    'apps.blog.apps.BlogConfig',
]

BLOG_IMPORT_ROOT = Path(MEDIA_ROOT) / '_staged_blog_imports'  # noqa: F821
BLOG_IMPORT_RETENTION_HOURS = 24
BLOG_IMPORT_CLEANUP_BATCH_SIZE = 100
