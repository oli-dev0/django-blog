import json
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from django.test import SimpleTestCase

from apps.blog import embed_sharing


YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
X_URL = "https://x.com/example/status/123456789"
REDDIT_URL = "https://www.reddit.com/r/python/comments/abc123/example-post/"


class FakeResponse:
    def __init__(
        self, body, *, status=200, content_type="application/json", content_length=None
    ):
        self.body = body
        self.status = status
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.read_sizes = []
        self.closed = False

    def getcode(self):
        return self.status

    def read(self, size=-1):
        self.read_sizes.append(size)
        return self.body

    def close(self):
        self.closed = True


class FakeOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def provider_response(platform):
    provider_name, embed_type = {
        embed_sharing.YOUTUBE: ("YouTube", "video"),
        embed_sharing.X: ("Twitter", "rich"),
        embed_sharing.REDDIT: ("Reddit", "rich"),
    }[platform]
    return FakeResponse(
        json.dumps(
            {
                "provider_name": provider_name,
                "type": embed_type,
                "html": "<script>untrusted provider markup</script>",
                "title": "Untrusted provider title",
            }
        ).encode()
    )


class EmbedParserTests(SimpleTestCase):
    def test_supported_urls_are_canonicalized_to_bounded_references(self):
        cases = (
            (
                "http://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share#t=4",
                embed_sharing.YOUTUBE,
                YOUTUBE_URL,
                "dQw4w9WgXcQ",
            ),
            (
                "https://youtu.be/dQw4w9WgXcQ?si=tracking-value",
                embed_sharing.YOUTUBE,
                YOUTUBE_URL,
                "dQw4w9WgXcQ",
            ),
            (
                "https://www.youtube.com/shorts/dQw4w9WgXcQ?feature=share",
                embed_sharing.YOUTUBE,
                YOUTUBE_URL,
                "dQw4w9WgXcQ",
            ),
            (
                "https://youtube.com/embed/dQw4w9WgXcQ",
                embed_sharing.YOUTUBE,
                YOUTUBE_URL,
                "dQw4w9WgXcQ",
            ),
            (
                "https://twitter.com/example/status/123456789?s=20",
                embed_sharing.X,
                X_URL,
                "123456789",
            ),
            (
                "https://x.com/example/status/123456789#fragment",
                embed_sharing.X,
                X_URL,
                "123456789",
            ),
            (
                "https://x.com/i/status/123456789",
                embed_sharing.X,
                "https://x.com/i/status/123456789",
                "123456789",
            ),
            (
                REDDIT_URL,
                embed_sharing.REDDIT,
                "https://www.reddit.com/r/python/comments/abc123/",
                "abc123",
            ),
            (
                "https://reddit.com/r/Python/comments/abc123/example-post/?utm_source=share",
                embed_sharing.REDDIT,
                "https://www.reddit.com/r/python/comments/abc123/",
                "abc123",
            ),
            (
                "https://redd.it/abc123?share_id=tracking-value",
                embed_sharing.REDDIT,
                "https://redd.it/abc123",
                "abc123",
            ),
        )

        for url, platform, canonical_url, item_id in cases:
            with self.subTest(url=url):
                reference = embed_sharing.parse_embed_url(url)

                self.assertEqual(reference.platform, platform)
                self.assertEqual(reference.url, canonical_url)
                self.assertEqual(reference.canonical_url, canonical_url)
                self.assertEqual(reference.item_id, item_id)
                self.assertEqual(
                    reference.fingerprint, (platform, canonical_url, item_id)
                )

    def test_selected_platform_is_checked_locally(self):
        with self.assertRaises(embed_sharing.InvalidEmbedReference):
            embed_sharing.parse_embed_url(X_URL, platform=embed_sharing.YOUTUBE)

        with self.assertRaises(embed_sharing.InvalidEmbedReference):
            embed_sharing.normalize_embed_reference("unknown", YOUTUBE_URL)

    def test_lookalikes_credentials_ports_and_unsafe_schemes_are_rejected(self):
        invalid_urls = (
            "https://www.youtube.com.example.org/watch?v=dQw4w9WgXcQ",
            "https://user:password@www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com:8443/watch?v=dQw4w9WgXcQ",
            "//www.youtube.com/watch?v=dQw4w9WgXcQ",
            "javascript:alert(1)",
            "data:text/html,unsafe",
        )

        for url in invalid_urls:
            with (
                self.subTest(url=url),
                self.assertRaises(embed_sharing.InvalidEmbedReference),
            ):
                embed_sharing.parse_embed_url(url)

    def test_unsupported_and_ambiguous_item_paths_are_rejected_without_network(self):
        invalid_urls = (
            "https://www.youtube.com/playlist?list=PL1234567890",
            "https://www.youtube.com/channel/UC1234567890",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&v=another-id",
            "https://www.youtube.com/embed/dQw4w9WgXcQ/extra",
            "https://x.com/example",
            "https://x.com/example/likes",
            "https://x.com/intent/post?text=hello",
            "https://x.com/example/status/not-numeric",
            "https://www.reddit.com/user/example",
            "https://www.reddit.com/r/python",
            "https://www.reddit.com/search?q=python",
            "https://www.reddit.com/r/python/comments/abc123/title/comment456",
            "https://redd.it/s/abc123",
        )

        with patch.object(embed_sharing, "build_opener") as build_opener:
            for url in invalid_urls:
                with (
                    self.subTest(url=url),
                    self.assertRaises(embed_sharing.InvalidEmbedReference),
                ):
                    embed_sharing.parse_embed_url(url)
            build_opener.assert_not_called()

    def test_normalized_reference_is_immutable(self):
        reference = embed_sharing.parse_embed_url(YOUTUBE_URL)

        with self.assertRaises(AttributeError):
            reference.item_id = "another-id"

    def test_directly_constructed_reference_must_be_canonical_and_consistent(self):
        invalid_references = (
            (
                embed_sharing.YOUTUBE,
                "https://www.youtube.com/channel/not-a-video",
                "dQw4w9WgXcQ",
            ),
            (embed_sharing.X, "https://x.com/example/status/999", "123"),
            (embed_sharing.REDDIT, "https://www.reddit.com/user/example", "abc123"),
            (
                embed_sharing.REDDIT,
                "https://www.reddit.com/comments/abc123/?utm_source=share",
                "abc123",
            ),
        )

        for platform, url, item_id in invalid_references:
            with self.subTest(platform=platform, url=url), self.assertRaises(ValueError):
                embed_sharing.NormalizedEmbedReference(platform, url, item_id)


class ProviderVerificationTests(SimpleTestCase):
    def verify_with_response(self, reference, response):
        opener = FakeOpener(response)
        with patch.object(embed_sharing, "build_opener", return_value=opener):
            result = embed_sharing.verify_reference(reference)
        return result, opener

    def test_each_adapter_uses_only_its_fixed_endpoint_and_options(self):
        cases = (
            (
                embed_sharing.parse_embed_url(YOUTUBE_URL),
                embed_sharing.YOUTUBE_OEMBED_ENDPOINT,
                {"url": YOUTUBE_URL, "format": "json"},
                provider_response(embed_sharing.YOUTUBE),
            ),
            (
                embed_sharing.parse_embed_url(X_URL),
                embed_sharing.X_OEMBED_ENDPOINT,
                {"url": X_URL, "omit_script": "true", "dnt": "true"},
                provider_response(embed_sharing.X),
            ),
            (
                embed_sharing.parse_embed_url(REDDIT_URL),
                embed_sharing.REDDIT_OEMBED_ENDPOINT,
                {
                    "url": "https://www.reddit.com/r/python/comments/abc123/",
                    "format": "json",
                },
                provider_response(embed_sharing.REDDIT),
            ),
        )

        for reference, endpoint, expected_query, response in cases:
            with self.subTest(platform=reference.platform):
                result, opener = self.verify_with_response(reference, response)
                self.assertEqual(result, reference)
                self.assertEqual(len(opener.calls), 1)
                request, timeout = opener.calls[0]
                parsed = urlsplit(request.full_url)
                self.assertEqual(
                    f"{parsed.scheme}://{parsed.netloc}{parsed.path}", endpoint
                )
                self.assertEqual(
                    parse_qs(parsed.query),
                    {key: [value] for key, value in expected_query.items()},
                )
                self.assertEqual(timeout, embed_sharing.REQUEST_TIMEOUT_SECONDS)
                headers = {key.lower(): value for key, value in request.header_items()}
                self.assertEqual(headers["accept"], "application/json")
                self.assertEqual(headers["user-agent"], embed_sharing.USER_AGENT)

    def test_valid_response_returns_only_the_normalized_reference(self):
        reference = embed_sharing.parse_embed_url(YOUTUBE_URL)

        result, opener = self.verify_with_response(
            reference, provider_response(embed_sharing.YOUTUBE)
        )

        self.assertEqual(result, reference)
        self.assertFalse(hasattr(result, "html"))
        self.assertTrue(opener.responses == [])

    def test_definitive_http_failures_are_unsupported_items(self):
        reference = embed_sharing.parse_embed_url(YOUTUBE_URL)

        for status in (400, 401, 403, 404, 410):
            with self.subTest(status=status):
                error = HTTPError(YOUTUBE_URL, status, "provider failure", {}, None)
                with self.assertRaises(embed_sharing.UnsupportedEmbedItem) as raised:
                    self.verify_with_response(reference, error)
                self.assertEqual(
                    str(raised.exception),
                    str(embed_sharing.UNSUPPORTED_EMBED_ITEM_MESSAGE),
                )

        for status in (400, 404):
            with self.subTest(response_status=status):
                with self.assertRaises(embed_sharing.UnsupportedEmbedItem):
                    self.verify_with_response(
                        reference, FakeResponse(b"{}", status=status)
                    )

    def test_transient_http_and_transport_failures_are_unavailable_without_retry(self):
        reference = embed_sharing.parse_embed_url(YOUTUBE_URL)
        failures = (
            HTTPError(YOUTUBE_URL, 429, "rate limited", {}, None),
            HTTPError(YOUTUBE_URL, 500, "provider failure", {}, None),
            TimeoutError("timed out"),
            URLError("connection failed"),
            OSError("connection failed"),
        )

        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                opener = FakeOpener(failure)
                with patch.object(embed_sharing, "build_opener", return_value=opener):
                    with self.assertRaises(embed_sharing.EmbedVerificationUnavailable):
                        embed_sharing.verify_reference(reference)
                self.assertEqual(len(opener.calls), 1)

    def test_response_limits_shape_and_content_type_are_enforced(self):
        reference = embed_sharing.parse_embed_url(YOUTUBE_URL)
        invalid_responses = (
            FakeResponse(b"{}", content_type="text/html"),
            FakeResponse(b"not-json"),
            FakeResponse(b"[]"),
            FakeResponse(
                json.dumps(
                    {"provider_name": "Reddit", "type": "video", "html": "x"}
                ).encode()
            ),
            FakeResponse(
                json.dumps(
                    {"provider_name": "YouTube", "type": "rich", "html": "x"}
                ).encode()
            ),
            FakeResponse(
                json.dumps({"provider_name": "YouTube", "type": "video"}).encode()
            ),
            FakeResponse(
                json.dumps(
                    {"provider_name": "YouTube", "type": "video", "html": ""}
                ).encode()
            ),
            FakeResponse(
                json.dumps(
                    {"provider_name": "YouTube", "type": "video", "html": " \n "}
                ).encode()
            ),
            FakeResponse(b"{}", content_length=embed_sharing.MAX_RESPONSE_BYTES + 1),
            FakeResponse(b"{}", content_length=-1),
        )

        for response in invalid_responses:
            with self.subTest(response=response):
                with self.assertRaises(embed_sharing.EmbedVerificationUnavailable):
                    self.verify_with_response(reference, response)

    def test_unsafe_provider_redirects_are_rejected_and_redirects_are_bounded(self):
        handler = embed_sharing._ProviderRedirectHandler("www.youtube.com")
        request = Request(embed_sharing.YOUTUBE_OEMBED_ENDPOINT)

        with self.assertRaises(embed_sharing.EmbedVerificationUnavailable):
            handler.redirect_request(
                request,
                None,
                302,
                "found",
                {},
                "https://attacker.example/oembed",
            )

        handler = embed_sharing._ProviderRedirectHandler("www.youtube.com")
        for _redirect in range(embed_sharing.MAX_REDIRECTS):
            request = handler.redirect_request(
                request,
                None,
                302,
                "found",
                {},
                "https://www.youtube.com/oembed",
            )
            self.assertIsInstance(request, Request)
        with self.assertRaises(embed_sharing.EmbedVerificationUnavailable):
            handler.redirect_request(
                request,
                None,
                302,
                "found",
                {},
                "https://www.youtube.com/oembed",
            )


class ArticleVerificationTests(SimpleTestCase):
    def test_article_verification_normalizes_all_blocks_then_verifies_in_order(self):
        blocks = (
            SimpleNamespace(pk=11, platform=embed_sharing.YOUTUBE, url=YOUTUBE_URL),
            SimpleNamespace(pk=12, platform=embed_sharing.X, url=X_URL),
            SimpleNamespace(pk=13, platform=embed_sharing.REDDIT, url=REDDIT_URL),
        )
        opener = FakeOpener(
            provider_response(embed_sharing.YOUTUBE),
            provider_response(embed_sharing.X),
            provider_response(embed_sharing.REDDIT),
        )

        with patch.object(embed_sharing, "build_opener", return_value=opener):
            verified = embed_sharing.verify_article_embeds(blocks)

        self.assertEqual([item.block_id for item in verified], [11, 12, 13])
        self.assertEqual(
            [item.reference.platform for item in verified],
            [
                embed_sharing.YOUTUBE,
                embed_sharing.X,
                embed_sharing.REDDIT,
            ],
        )
        self.assertEqual(
            [item.fingerprint for item in verified],
            [
                embed_sharing.EmbedBlockFingerprint(
                    11, embed_sharing.YOUTUBE, YOUTUBE_URL, "dQw4w9WgXcQ"
                ),
                embed_sharing.EmbedBlockFingerprint(
                    12, embed_sharing.X, X_URL, "123456789"
                ),
                embed_sharing.EmbedBlockFingerprint(
                    13,
                    embed_sharing.REDDIT,
                    "https://www.reddit.com/r/python/comments/abc123/",
                    "abc123",
                ),
            ],
        )
        self.assertEqual(len(opener.calls), 3)
        self.assertEqual(
            [urlsplit(request.full_url).netloc for request, _timeout in opener.calls],
            ["www.youtube.com", "publish.x.com", "www.reddit.com"],
        )

    def test_local_failure_prevents_any_provider_request_and_identifies_the_block(self):
        blocks = (
            SimpleNamespace(pk=11, platform=embed_sharing.YOUTUBE, url=YOUTUBE_URL),
            SimpleNamespace(
                pk=12, platform=embed_sharing.X, url="https://x.com/example"
            ),
        )

        with patch.object(embed_sharing, "build_opener") as build_opener:
            with self.assertRaises(embed_sharing.InvalidEmbedReference) as raised:
                embed_sharing.verify_article_embeds(blocks)

        self.assertEqual(raised.exception.block_id, 12)
        build_opener.assert_not_called()

    def test_provider_failure_identifies_the_block_without_returning_provider_data(
        self,
    ):
        block = SimpleNamespace(pk=42, platform=embed_sharing.YOUTUBE, url=YOUTUBE_URL)
        response_error = HTTPError(YOUTUBE_URL, 404, "not found", {}, None)
        opener = FakeOpener(response_error)

        with patch.object(embed_sharing, "build_opener", return_value=opener):
            with self.assertRaises(embed_sharing.UnsupportedEmbedItem) as raised:
                embed_sharing.verify_article_embeds([block])

        self.assertEqual(raised.exception.block_id, 42)

    def test_fingerprint_for_block_uses_normalized_values(self):
        fingerprint = embed_sharing.fingerprint_for_block(
            {
                "id": 7,
                "platform": embed_sharing.YOUTUBE,
                "url": "https://youtu.be/dQw4w9WgXcQ?si=tracking",
            }
        )

        self.assertEqual(
            fingerprint,
            embed_sharing.EmbedBlockFingerprint(
                7, embed_sharing.YOUTUBE, YOUTUBE_URL, "dQw4w9WgXcQ"
            ),
        )
