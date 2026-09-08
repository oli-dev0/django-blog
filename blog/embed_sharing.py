"""Safe local parsing and bounded provider verification for Blog embeds.

Only normalized references cross this module's boundary. Provider responses are
used for verification facts and are never returned, stored, or rendered.
"""

from __future__ import annotations

import json
import re
import socket
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.utils.translation import gettext_lazy as _


YOUTUBE = "youtube"
X = "x"
REDDIT = "reddit"
SUPPORTED_PLATFORMS = frozenset({YOUTUBE, X, REDDIT})
PLATFORM_CHOICES = (
    (YOUTUBE, _("YouTube")),
    (X, _("X")),
    (REDDIT, _("Reddit")),
)

INVALID_EMBED_REFERENCE_MESSAGE = _("Enter a valid URL from the selected platform.")
UNSUPPORTED_EMBED_ITEM_MESSAGE = _(
    "This type of content cannot be embedded here. "
    "Use a public YouTube video, X post, or Reddit post."
)
EMBED_VERIFICATION_UNAVAILABLE_MESSAGE = _(
    "The embedded content could not be verified right now. Try again."
)

YOUTUBE_OEMBED_ENDPOINT = "https://www.youtube.com/oembed"
X_OEMBED_ENDPOINT = "https://publish.x.com/oembed"
REDDIT_OEMBED_ENDPOINT = "https://www.reddit.com/oembed"

REQUEST_TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 128 * 1024
MAX_REDIRECTS = 2
USER_AGENT = "django-blog-showcase-embed-verifier/1.0"

_YOUTUBE_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
)
_X_HOSTS = frozenset(
    {
        "x.com",
        "www.x.com",
        "mobile.x.com",
        "twitter.com",
        "www.twitter.com",
        "mobile.twitter.com",
    }
)
_REDDIT_HOSTS = frozenset(
    {
        "reddit.com",
        "www.reddit.com",
        "old.reddit.com",
        "new.reddit.com",
        "redd.it",
        "www.redd.it",
    }
)

_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_X_USERNAME = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_X_STATUS_ID = re.compile(r"^[1-9][0-9]{0,19}$")
_REDDIT_SUBREDDIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,20}$")
_REDDIT_POST_ID = re.compile(r"^[A-Za-z0-9]{3,20}$")

_CANONICAL_HOSTS = {
    YOUTUBE: frozenset({"www.youtube.com"}),
    X: frozenset({"x.com"}),
    REDDIT: frozenset({"www.reddit.com", "redd.it"}),
}


class EmbedSharingError(Exception):
    """Base class for safe, user-facing embed boundary failures."""

    default_message = _("The embedded content could not be processed.")

    def __init__(self, message=None, *, block_id=None):
        super().__init__(message or self.default_message)
        self.block_id = block_id


class InvalidEmbedReference(EmbedSharingError):
    """The URL is malformed, unsupported locally, or from another platform."""

    default_message = INVALID_EMBED_REFERENCE_MESSAGE


class UnsupportedEmbedItem(EmbedSharingError):
    """The selected provider definitively does not support the item."""

    default_message = UNSUPPORTED_EMBED_ITEM_MESSAGE


class EmbedVerificationUnavailable(EmbedSharingError):
    """The provider could not be verified safely at this time."""

    default_message = EMBED_VERIFICATION_UNAVAILABLE_MESSAGE


@dataclass(frozen=True, slots=True)
class NormalizedEmbedReference:
    """The only provider data retained by the application."""

    platform: str
    url: str
    item_id: str

    def __post_init__(self):
        if self.platform not in SUPPORTED_PLATFORMS:
            raise ValueError("Unknown embed platform.")
        if not isinstance(self.url, str) or not isinstance(self.item_id, str):
            raise ValueError("Embed reference values must be strings.")
        if any(character.isspace() for character in self.url) or "#" in self.url:
            raise ValueError("Embed reference URL must be canonical.")

        parsed = urlsplit(self.url)
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("Embed reference URL has an invalid port.") from error
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.hostname not in _CANONICAL_HOSTS[self.platform]
        ):
            raise ValueError("Embed reference URL must be canonical.")

        item_pattern = {
            YOUTUBE: _YOUTUBE_ID,
            X: _X_STATUS_ID,
            REDDIT: _REDDIT_POST_ID,
        }[self.platform]
        if not item_pattern.fullmatch(self.item_id):
            raise ValueError("Embed reference item ID is not bounded.")
        if not _reference_values_are_canonical(self):
            raise ValueError("Embed reference URL does not match its item ID.")

    @property
    def canonical_url(self):
        """A descriptive alias for callers that do not use the model field name."""

        return self.url

    @property
    def fingerprint(self):
        return (self.platform, self.url, self.item_id)


def _reference_values_are_canonical(reference):
    """Confirm a directly constructed reference matches parser output exactly."""

    parsed = urlsplit(reference.url)
    segments = [segment for segment in parsed.path.split("/") if segment]

    if reference.platform == YOUTUBE:
        return reference.url == f"https://www.youtube.com/watch?v={reference.item_id}"
    if reference.platform == X:
        return (
            len(segments) == 3
            and segments[1] == "status"
            and (segments[0] == "i" or _X_USERNAME.fullmatch(segments[0]) is not None)
            and segments[2] == reference.item_id
            and reference.url
            == f"https://x.com/{segments[0]}/status/{reference.item_id}"
        )
    if parsed.hostname == "redd.it":
        return reference.url == f"https://redd.it/{reference.item_id}"
    return (
        reference.url == f"https://www.reddit.com/comments/{reference.item_id}/"
        or (
            len(segments) == 4
            and segments[0] == "r"
            and _REDDIT_SUBREDDIT.fullmatch(segments[1]) is not None
            and segments[1] == segments[1].lower()
            and segments[2:] == ["comments", reference.item_id]
            and reference.url
            == f"https://www.reddit.com/r/{segments[1]}/comments/{reference.item_id}/"
        )
    )


@dataclass(frozen=True, slots=True)
class EmbedBlockFingerprint:
    """A persisted block identity plus the normalized reference it contained."""

    block_id: object
    platform: str
    url: str
    item_id: str

    @classmethod
    def from_reference(cls, block_id, reference):
        return cls(block_id, reference.platform, reference.url, reference.item_id)


@dataclass(frozen=True, slots=True)
class VerifiedEmbed:
    """One verified block, retained in the article's original order."""

    block_id: object
    reference: NormalizedEmbedReference

    @property
    def fingerprint(self):
        return EmbedBlockFingerprint.from_reference(self.block_id, self.reference)


def _invalid_reference():
    raise InvalidEmbedReference()


def _public_url_parts(value):
    if not isinstance(value, str):
        _invalid_reference()
    value = value.strip()
    if not value or any(character.isspace() for character in value):
        _invalid_reference()

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        _invalid_reference()

    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        _invalid_reference()
    if (
        parsed.username is not None
        or parsed.password is not None
        or not parsed.hostname
    ):
        _invalid_reference()

    expected_port = 80 if parsed.scheme.lower() == "http" else 443
    if port not in {None, expected_port}:
        _invalid_reference()

    return parsed.hostname.lower(), parsed.path, parsed.query


def _path_segments(path):
    if not path.startswith("/"):
        _invalid_reference()

    raw_segments = [segment for segment in path.split("/") if segment]
    segments = []
    for raw_segment in raw_segments:
        segment = unquote(raw_segment)
        if not segment or "/" in segment or "\\" in segment or segment in {".", ".."}:
            _invalid_reference()
        segments.append(segment)
    return segments


def _query_values(query):
    try:
        return parse_qs(query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        _invalid_reference()


def _youtube_reference(host, path, query):
    segments = _path_segments(path)
    item_id = None

    if host == "youtu.be":
        if len(segments) != 1:
            _invalid_reference()
        item_id = segments[0]
    elif segments == ["watch"]:
        values = _query_values(query).get("v", [])
        if len(values) != 1:
            _invalid_reference()
        item_id = values[0]
    elif len(segments) == 2 and segments[0] in {"shorts", "embed"}:
        if "v" in _query_values(query):
            _invalid_reference()
        item_id = segments[1]
    else:
        _invalid_reference()

    if not _YOUTUBE_ID.fullmatch(item_id):
        _invalid_reference()
    return NormalizedEmbedReference(
        YOUTUBE,
        f"https://www.youtube.com/watch?v={item_id}",
        item_id,
    )


def _x_reference(path):
    segments = _path_segments(path)
    if len(segments) != 3 or segments[1].lower() != "status":
        _invalid_reference()
    if segments[0].lower() != "i" and not _X_USERNAME.fullmatch(segments[0]):
        _invalid_reference()
    if not _X_STATUS_ID.fullmatch(segments[2]):
        _invalid_reference()

    author = "i" if segments[0].lower() == "i" else segments[0]
    return NormalizedEmbedReference(
        X,
        f"https://x.com/{author}/status/{segments[2]}",
        segments[2],
    )


def _reddit_reference(host, path):
    segments = _path_segments(path)
    item_id = None
    canonical_path = None

    if host in {"redd.it", "www.redd.it"}:
        if len(segments) != 1:
            _invalid_reference()
        item_id = segments[0]
    elif len(segments) in {2, 3} and segments[0].lower() == "comments":
        item_id = segments[1]
        canonical_path = f"/comments/{item_id}/"
    elif (
        len(segments) in {4, 5}
        and segments[0].lower() == "r"
        and segments[2].lower() == "comments"
    ):
        subreddit = segments[1]
        if not _REDDIT_SUBREDDIT.fullmatch(subreddit):
            _invalid_reference()
        item_id = segments[3]
        canonical_path = f"/r/{subreddit.lower()}/comments/{item_id}/"
    else:
        _invalid_reference()

    if not _REDDIT_POST_ID.fullmatch(item_id):
        _invalid_reference()
    if host in {"redd.it", "www.redd.it"}:
        canonical_url = f"https://redd.it/{item_id}"
    else:
        canonical_url = f"https://www.reddit.com{canonical_path}"
    return NormalizedEmbedReference(REDDIT, canonical_url, item_id)


def parse_embed_url(
    url: str, platform: str | None = None, *, expected_platform: str | None = None
):
    """Parse and canonicalize one supported public item URL without network I/O."""

    if expected_platform is not None:
        if platform is not None and platform != expected_platform:
            raise ValueError("Specify only one expected platform.")
        platform = expected_platform
    if platform is not None and platform not in SUPPORTED_PLATFORMS:
        raise InvalidEmbedReference()

    host, path, query = _public_url_parts(url)
    if host in _YOUTUBE_HOSTS:
        reference = _youtube_reference(host, path, query)
    elif host in _X_HOSTS:
        reference = _x_reference(path)
    elif host in _REDDIT_HOSTS:
        reference = _reddit_reference(host, path)
    else:
        _invalid_reference()

    if platform is not None and reference.platform != platform:
        raise InvalidEmbedReference()
    return reference


def normalize_embed_url(url: str, platform: str | None = None):
    """Compatibility-friendly name for the local normalization boundary."""

    return parse_embed_url(url, platform=platform)


def normalize_embed_reference(platform: str, url: str):
    """Normalize a model-like platform and URL pair."""

    return parse_embed_url(url, platform=platform)


class _ProviderRedirectHandler(HTTPRedirectHandler):
    def __init__(self, expected_host):
        super().__init__()
        self.expected_host = expected_host
        self.redirect_count = 0

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if self.redirect_count >= MAX_REDIRECTS:
            raise EmbedVerificationUnavailable()
        redirected_url = urljoin(request.full_url, newurl)
        try:
            parsed = urlsplit(redirected_url)
            port = parsed.port
        except ValueError as error:
            raise EmbedVerificationUnavailable() from error
        if (
            parsed.scheme != "https"
            or parsed.hostname != self.expected_host
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
        ):
            raise EmbedVerificationUnavailable()
        self.redirect_count += 1
        return super().redirect_request(request, fp, code, msg, headers, redirected_url)


@dataclass(frozen=True, slots=True)
class ProviderAdapter:
    platform: str
    endpoint: str
    expected_host: str
    expected_provider_names: frozenset[str]
    expected_types: frozenset[str]
    fixed_options: tuple[tuple[str, str], ...] = ()

    def verify(self, reference: NormalizedEmbedReference):
        if reference.platform != self.platform:
            raise InvalidEmbedReference()

        query = urlencode((("url", reference.url), *self.fixed_options))
        request = Request(
            f"{self.endpoint}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        response = _open_provider_request(request, self.expected_host)
        try:
            payload = _read_provider_json(response)
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()
        _validate_provider_payload(payload, self)
        return reference


PROVIDER_ADAPTERS = MappingProxyType(
    {
        YOUTUBE: ProviderAdapter(
            platform=YOUTUBE,
            endpoint=YOUTUBE_OEMBED_ENDPOINT,
            expected_host="www.youtube.com",
            expected_provider_names=frozenset({"youtube"}),
            expected_types=frozenset({"video"}),
            fixed_options=(("format", "json"),),
        ),
        X: ProviderAdapter(
            platform=X,
            endpoint=X_OEMBED_ENDPOINT,
            expected_host="publish.x.com",
            expected_provider_names=frozenset({"twitter", "x"}),
            expected_types=frozenset({"rich"}),
            fixed_options=(("omit_script", "true"), ("dnt", "true")),
        ),
        REDDIT: ProviderAdapter(
            platform=REDDIT,
            endpoint=REDDIT_OEMBED_ENDPOINT,
            expected_host="www.reddit.com",
            expected_provider_names=frozenset({"reddit"}),
            expected_types=frozenset({"rich"}),
            fixed_options=(("format", "json"),),
        ),
    }
)


def _open_provider_request(request, expected_host):
    opener = build_opener(_ProviderRedirectHandler(expected_host))
    try:
        return opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS)
    except HTTPError as error:
        if 400 <= error.code < 500 and error.code != 429:
            raise UnsupportedEmbedItem() from None
        raise EmbedVerificationUnavailable() from None
    except (
        EmbedVerificationUnavailable,
        TimeoutError,
        socket.timeout,
        OSError,
        URLError,
    ):
        raise EmbedVerificationUnavailable() from None


def _header_value(headers, name):
    if headers is None:
        return None
    get = getattr(headers, "get", None)
    if get is not None:
        value = get(name)
        if value is None:
            value = get(name.lower())
        if value is not None:
            return value
    getheader = getattr(headers, "getheader", None)
    if getheader is not None:
        return getheader(name)
    return None


def _read_provider_json(response):
    status = (
        response.getcode()
        if callable(getattr(response, "getcode", None))
        else getattr(response, "status", None)
    )
    if status is None:
        raise EmbedVerificationUnavailable()
    if 400 <= status < 500 and status != 429:
        raise UnsupportedEmbedItem()
    if not 200 <= status < 300:
        raise EmbedVerificationUnavailable()

    headers = getattr(response, "headers", None)
    content_type = _header_value(headers, "Content-Type")
    if (
        not isinstance(content_type, str)
        or content_type.split(";", 1)[0].strip().lower() != "application/json"
    ):
        raise EmbedVerificationUnavailable()

    content_length = _header_value(headers, "Content-Length")
    if content_length is not None:
        try:
            declared_length = int(str(content_length).strip())
            if declared_length < 0 or declared_length > MAX_RESPONSE_BYTES:
                raise EmbedVerificationUnavailable()
        except (TypeError, ValueError):
            raise EmbedVerificationUnavailable() from None

    try:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    except (TimeoutError, socket.timeout, OSError):
        raise EmbedVerificationUnavailable() from None
    if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES:
        raise EmbedVerificationUnavailable()
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        raise EmbedVerificationUnavailable() from None


def _validate_provider_payload(payload, adapter):
    if not isinstance(payload, dict):
        raise EmbedVerificationUnavailable()
    provider_name = payload.get("provider_name")
    embed_type = payload.get("type")
    html = payload.get("html")
    if (
        not isinstance(provider_name, str)
        or provider_name.casefold() not in adapter.expected_provider_names
        or not isinstance(embed_type, str)
        or embed_type.casefold() not in adapter.expected_types
        or not isinstance(html, str)
        or not html.strip()
    ):
        raise EmbedVerificationUnavailable()


def verify_reference(reference: NormalizedEmbedReference):
    """Verify one already-normalized reference and return it without provider data."""

    if not isinstance(reference, NormalizedEmbedReference):
        raise InvalidEmbedReference()
    try:
        adapter = PROVIDER_ADAPTERS[reference.platform]
    except KeyError:
        raise InvalidEmbedReference() from None
    adapter.verify(reference)
    return reference


def verify_embed_url(url: str, platform: str | None = None):
    """Normalize one URL locally, then verify it through its fixed adapter."""

    return verify_reference(parse_embed_url(url, platform=platform))


def _block_value(block, name):
    if isinstance(block, dict):
        return block.get(name)
    return getattr(block, name, None)


def _block_id(block):
    block_id = _block_value(block, "pk")
    if block_id is None:
        block_id = _block_value(block, "id")
    return block_id


def fingerprint_for_block(block):
    """Return the current normalized identity for a persisted article block."""

    reference = normalize_embed_reference(
        _block_value(block, "platform"), _block_value(block, "url")
    )
    return EmbedBlockFingerprint.from_reference(_block_id(block), reference)


def verify_article_embeds(blocks: Iterable[object]):
    """Verify article blocks in order after normalizing the complete collection.

    Local normalization of every block happens before the first provider call.
    This keeps malformed or wrong-platform input a purely local failure and
    gives later publication transactions an ordered, immutable fingerprint set.
    """

    normalized = []
    for block in blocks:
        block_id = _block_id(block)
        try:
            reference = normalize_embed_reference(
                _block_value(block, "platform"),
                _block_value(block, "url"),
            )
        except EmbedSharingError as error:
            if error.block_id is None:
                raise type(error)(block_id=block_id) from error
            raise
        normalized.append((block_id, reference))

    verified = []
    for block_id, reference in normalized:
        try:
            verify_reference(reference)
        except EmbedSharingError as error:
            if error.block_id is None:
                raise type(error)(block_id=block_id) from error
            raise
        verified.append(VerifiedEmbed(block_id, reference))
    return tuple(verified)


__all__ = [
    "EMBED_VERIFICATION_UNAVAILABLE_MESSAGE",
    "EmbedBlockFingerprint",
    "EmbedSharingError",
    "EmbedVerificationUnavailable",
    "InvalidEmbedReference",
    "NormalizedEmbedReference",
    "PLATFORM_CHOICES",
    "PROVIDER_ADAPTERS",
    "REDDIT",
    "SUPPORTED_PLATFORMS",
    "UNSUPPORTED_EMBED_ITEM_MESSAGE",
    "X",
    "YOUTUBE",
    "UnsupportedEmbedItem",
    "VerifiedEmbed",
    "fingerprint_for_block",
    "normalize_embed_reference",
    "normalize_embed_url",
    "parse_embed_url",
    "verify_article_embeds",
    "verify_embed_url",
    "verify_reference",
]
