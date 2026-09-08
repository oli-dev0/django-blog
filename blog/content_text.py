from html.parser import HTMLParser

from django.core.exceptions import ValidationError
from content_editor.contents import contents_for_item

from .faq import normalize_faq_items
from .models import (
    BLOG_BLOCK_MODELS,
    BlogCalloutBlock,
    BlogChecklistBlock,
    BlogEmbedSharingBlock,
    BlogFAQBlock,
    BlogHeadingBlock,
    BlogInternalLinkBlock,
    BlogLinkGroupBlock,
    BlogPost,
    BlogRichTextBlock,
    BlogSourceLinkBlock,
)


class _ReaderTextParser(HTMLParser):
    BOUNDARY_TAGS = frozenset({
        'address', 'article', 'aside', 'blockquote', 'br', 'dd', 'div', 'dl',
        'dt', 'figcaption', 'figure', 'footer', 'h1', 'h2', 'h3', 'h4', 'h5',
        'h6', 'header', 'hr', 'li', 'main', 'nav', 'ol', 'p', 'pre', 'section',
        'table', 'tbody', 'td', 'tfoot', 'th', 'thead', 'tr', 'ul',
    })

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BOUNDARY_TAGS:
            self.parts.append(' ')

    def handle_startendtag(self, tag, attrs):
        if tag in self.BOUNDARY_TAGS:
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in self.BOUNDARY_TAGS:
            self.parts.append(' ')

    def handle_data(self, data):
        self.parts.append(data)


def normalize_reader_text(*values):
    parser = _ReaderTextParser()
    parser.feed(' '.join(str(value or '') for value in values))
    parser.close()
    text = ''.join(parser.parts).replace('\xa0', ' ')
    return ' '.join(text.split())


def reader_facing_block_text(block):
    item = getattr(block, 'item', block)
    if isinstance(item, BlogHeadingBlock):
        return normalize_reader_text(item.text)
    if isinstance(item, BlogRichTextBlock):
        return normalize_reader_text(item.body)
    if isinstance(item, BlogFAQBlock):
        try:
            items = normalize_faq_items(item.items)
        except ValidationError:
            return ''
        return normalize_reader_text(
            *(value for faq_item in items for value in (faq_item['question'], faq_item['answer']))
        )
    if isinstance(item, BlogChecklistBlock):
        return normalize_reader_text(*item.items)
    if isinstance(item, BlogCalloutBlock):
        return normalize_reader_text(item.title, item.body)
    if isinstance(item, BlogSourceLinkBlock):
        return normalize_reader_text(item.label, item.note)
    if isinstance(item, BlogLinkGroupBlock):
        labels = [
            link.get('label', '')
            for link in item.links
            if isinstance(link, dict)
        ]
        return normalize_reader_text(item.label, *labels)
    if isinstance(item, BlogInternalLinkBlock):
        return normalize_reader_text(item.label, item.note)
    if isinstance(item, BlogEmbedSharingBlock):
        return normalize_reader_text(item.caption)
    return ''


def build_search_body_text(post):
    contents = contents_for_item(post, BLOG_BLOCK_MODELS)
    return normalize_reader_text(
        *(reader_facing_block_text(block) for block in contents.main)
    )


def rebuild_post_search_body(post_id):
    post = BlogPost.objects.filter(pk=post_id).first()
    if post is None:
        return
    BlogPost.objects.filter(pk=post_id).update(
        search_body_text=build_search_body_text(post),
    )
