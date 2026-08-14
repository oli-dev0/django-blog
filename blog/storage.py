from django.conf import settings
from django.core.files.storage import FileSystemStorage, storages


class PrivateBlogImportStorage(FileSystemStorage):
    """Filesystem storage for staged imports that deliberately has no URL."""

    @property
    def base_location(self):
        return str(settings.BLOG_IMPORT_ROOT)

    @property
    def base_url(self):
        return None

    def _clear_cached_properties(self, setting, **kwargs):
        super()._clear_cached_properties(setting, **kwargs)
        if setting == 'BLOG_IMPORT_ROOT':
            self.__dict__.pop('location', None)


def get_private_blog_import_storage():
    return storages['blog_imports']
