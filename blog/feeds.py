from io import StringIO
from mimetypes import guess_type
from xml.dom import minidom

from django.contrib.syndication.views import Feed
from django.http import Http404
from django.utils.feedgenerator import Rss201rev2Feed
from django.utils.translation import gettext_lazy as _

from apps.core.sites import build_site_absolute_url, require_site_for_host

from .image_services import image_sources
from .selectors import get_canonical_post_url, get_public_posts_for_feed
from .urls_helpers import reverse_blog


class PrettyRss201rev2Feed(Rss201rev2Feed):
    def write(self, outfile, encoding):
        compact_xml = StringIO()
        super().write(compact_xml, encoding)
        pretty_xml = minidom.parseString(compact_xml.getvalue()).toprettyxml(
            indent="  ",
            encoding=encoding,
        )
        outfile.write(pretty_xml)


class BlogFeed(Feed):
    feed_type = PrettyRss201rev2Feed
    title = _('Blog')
    description = _('Latest published English blog articles.')
    feed_language = 'en'

    def link(self, site_slug):
        return build_site_absolute_url(site_slug, reverse_blog('list', site_slug=site_slug))

    def feed_url(self, site_slug):
        return build_site_absolute_url(site_slug, reverse_blog('rss', site_slug=site_slug))

    def get_object(self, request, *args, **kwargs):
        if request.LANGUAGE_CODE != 'en':
            raise Http404(_('That feed is not available.'))
        return require_site_for_host(request.get_host()).slug

    def items(self, site_slug):
        return get_public_posts_for_feed(site_slug=site_slug, limit=20)

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.summary

    def item_link(self, item):
        return get_canonical_post_url(item)

    def item_guid(self, item):
        return self.item_link(item)

    def item_author_name(self, item):
        return item.author.public_author_name if item.author and item.author.public_author_name else ''

    def item_pubdate(self, item):
        return item.published_at

    def item_updateddate(self, item):
        return item.content_updated_at

    def _image_sources(self, item):
        if not hasattr(item, '_blog_feed_image_sources'):
            item._blog_feed_image_sources = image_sources(item.featured_image)
        return item._blog_feed_image_sources

    def item_enclosure_url(self, item):
        sources = self._image_sources(item)
        if not sources:
            return None
        url = sources['original']
        return build_site_absolute_url(item.canonical_site_slug, url)

    def item_enclosure_length(self, item):
        image = item.featured_image
        if not self._image_sources(item):
            return 0
        try:
            return image.original.size
        except OSError:
            return 0

    def item_enclosure_mime_type(self, item):
        image = item.featured_image
        return guess_type(image.original.name)[0] if self._image_sources(item) else 'application/octet-stream'
