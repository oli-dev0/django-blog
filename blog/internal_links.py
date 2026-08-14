from dataclasses import dataclass
from html.parser import HTMLParser

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.translation import gettext_lazy as _, override

from apps.core.sites import EASY_MEALS_SITE, PERSONAL_SITE, VANTA_SITE


@dataclass(frozen=True)
class InternalLinkDestination:
    key: str
    label: object
    route_name: str
    allowed_site_slugs: frozenset[str]


DESTINATIONS = (
    InternalLinkDestination('personal-home', _('Personal site home'), 'my_website:home', frozenset({PERSONAL_SITE})),
    InternalLinkDestination('personal-about', _('About the author'), 'my_website:about', frozenset({PERSONAL_SITE})),
    InternalLinkDestination('personal-projects', _('Projects'), 'personal:projects', frozenset({PERSONAL_SITE})),
    InternalLinkDestination('easy-meals-home', _('Easy Meals home'), 'site-root', frozenset({EASY_MEALS_SITE})),
    InternalLinkDestination('vanta-home', _('Vanta Admin home'), 'vanta_site:home', frozenset({VANTA_SITE})),
    InternalLinkDestination('vanta-get-started', _('Vanta Admin getting started'), 'vanta_site:get_started', frozenset({VANTA_SITE})),
    InternalLinkDestination('vanta-features', _('Vanta Admin features'), 'vanta_site:features', frozenset({VANTA_SITE})),
    InternalLinkDestination('vanta-releases', _('Vanta Admin releases'), 'vanta_site:releases', frozenset({VANTA_SITE})),
)
DESTINATIONS_BY_KEY = {destination.key: destination for destination in DESTINATIONS}


class _InlineInternalLinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.keys = []

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        attributes = dict(attrs)
        if key := attributes.get('data-blog-internal-key'):
            self.keys.append(key)

    handle_startendtag = handle_starttag


def get_internal_link_editor_destinations():
    """Return registry metadata needed by the Admin rich-text picker."""
    return [
        {
            'key': destination.key,
            'label': str(destination.label),
            'allowed_site_slugs': sorted(destination.allowed_site_slugs),
            'url': resolve_internal_link(destination.key, destination.allowed_site_slugs),
        }
        for destination in DESTINATIONS
    ]


def get_internal_link_choices(site_slugs):
    required_sites = set(site_slugs)
    if not required_sites:
        return []
    destinations = [
        destination
        for destination in DESTINATIONS
        if required_sites <= destination.allowed_site_slugs
    ]
    return [(destination.key, destination.label) for destination in sorted(destinations, key=lambda item: str(item.label))]


def validate_internal_link_destination(key, site_slugs):
    destination = DESTINATIONS_BY_KEY.get(key)
    if destination is None:
        raise ValidationError(_('Choose an approved internal destination.'))
    required_sites = set(site_slugs)
    if not required_sites:
        raise ValidationError(_('Assign this article to a publication website first.'))
    if not required_sites <= destination.allowed_site_slugs:
        raise ValidationError(_('Choose a destination available on every selected publication website.'))
    return destination


def resolve_internal_link(key, site_slugs):
    destination = validate_internal_link_destination(key, site_slugs)
    with override('en'):
        return reverse(destination.route_name)


def iter_inline_internal_link_keys(value):
    parser = _InlineInternalLinkParser()
    parser.feed(value or '')
    parser.close()
    return parser.keys


def validate_inline_internal_links(value, site_slugs):
    for key in iter_inline_internal_link_keys(value):
        validate_internal_link_destination(key, site_slugs)
