from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile, UploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from apps.blog.import_contract import validate_blog_import
from apps.blog.import_services import (
    BlogImportPermissionError,
    BlogImportValidationError,
    MAX_DUPLICATE_MATCHES,
    ReviewedImportReferences,
    get_blog_import_review,
    required_blog_import_permissions,
    resolve_blog_import_references,
    validate_and_stage_blog_import,
    validate_reviewed_blog_import,
)
from apps.blog.models import (
    AuthorProfile,
    BlogCategory,
    BlogPost,
    BlogPostPublication,
    BlogSite,
    BlogTag,
    BlogArticleImport,
)

from .site_settings import BLOG_ENABLED_SITE_DEFINITIONS


def image_bytes():
    output = BytesIO()
    Image.new("RGB", (1, 1), "white").save(output, format="PNG")
    return output.getvalue()


def upload(name, content=None, *, size=None):
    content = image_bytes() if content is None else content
    return UploadedFile(
        file=BytesIO(content),
        name=name,
        content_type="image/png",
        size=len(content) if size is None else size,
    )


def source_file(payload, name="article.json"):
    return SimpleUploadedFile(
        name,
        json.dumps(payload).encode("utf-8"),
        content_type="application/json",
    )


def minimal_payload(
    *,
    title="An importable article",
    slug=None,
    author="oli",
    category="development",
    tags=None,
    publication_sites=None,
    canonical_site=None,
    related_articles=None,
    blocks=None,
):
    article = {
        "title": title,
        "summary": "A draft summary.",
        "author": {"slug": author},
        "category": {"slug": category},
        "tags": [{"slug": value} for value in (tags or [])],
        "publication_sites": publication_sites or ["vanta_admin"],
        "blocks": blocks or [{"type": "heading", "level": 2, "text": "A section"}],
    }
    if slug is not None:
        article["slug"] = slug
    if canonical_site is not None:
        article["canonical_site"] = canonical_site
    if related_articles is not None:
        article["related_articles"] = [{"slug": value} for value in related_articles]
    return {
        "format": "blog-article-import",
        "version": 1,
        "article": article,
        "assets": [],
        "comparisons": [],
    }


def image_payload(*, shared_path="images/hero.png", second_path="images/second.png"):
    payload = minimal_payload(
        blocks=[
            {"type": "image", "asset_id": "hero"},
            {"type": "image_comparison", "comparison_id": "comparison"},
        ]
    )
    payload["assets"] = [
        {
            "id": "hero",
            "file": shared_path,
            "name": "Hero",
            "alt_text": "A hero image",
        }
    ]
    payload["comparisons"] = [
        {
            "id": "comparison",
            "name": "Before and after",
            "first": {"file": shared_path, "alt_text": "Before"},
            "second": {"file": second_path, "alt_text": "After"},
        }
    ]
    return payload


def all_block_payload():
    payload = minimal_payload(
        related_articles=["related-article"],
        blocks=[
            {"type": "heading", "level": 2, "text": "Heading"},
            {"type": "rich_text", "body": "<p>Body</p>"},
            {"type": "faq", "items": [{"question": "Question", "answer": "Answer"}]},
            {"type": "checklist", "items": ["One item"]},
            {"type": "code", "code": "print(1)"},
            {
                "type": "embed_sharing",
                "platform": "youtube",
                "url": "https://youtu.be/dQw4w9WgXcQ",
                "caption": "An embedded video",
            },
            {"type": "callout", "body": "<p>Note</p>"},
            {"type": "source_link", "url": "https://example.com/source"},
            {
                "type": "link_group",
                "label": "Further reading",
                "links": [{"label": "Docs", "url": "https://example.com/docs"}],
            },
            {"type": "internal_link", "destination_key": "vanta-home", "label": "Vanta home"},
            {"type": "image", "asset_id": "hero"},
            {"type": "image_comparison", "comparison_id": "comparison"},
        ]
    )
    payload["assets"] = [
        {
            "id": "hero",
            "file": "images/hero.png",
            "name": "Hero",
            "alt_text": "A hero image",
        }
    ]
    payload["comparisons"] = [
        {
            "id": "comparison",
            "name": "Before and after",
            "first": {"file": "images/hero.png", "alt_text": "Before"},
            "second": {"file": "images/after.png", "alt_text": "After"},
        }
    ]
    return payload


@contextmanager
def private_roots():
    with TemporaryDirectory() as media_root, TemporaryDirectory() as import_root:
        with override_settings(MEDIA_ROOT=media_root, BLOG_IMPORT_ROOT=import_root):
            yield Path(media_root), Path(import_root)


@override_settings(SITE_DEFINITIONS=BLOG_ENABLED_SITE_DEFINITIONS)
class BlogImportValidationTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username="import-owner")
        self.author = AuthorProfile.objects.create(
            user=get_user_model().objects.create_user(username="article-author"),
            public_author_name="Oli",
            slug="oli",
        )
        self.category = BlogCategory.objects.create(name="Development", slug="development")
        self.tag = BlogTag.objects.create(name="Django", slug="django")
        site, _created = BlogSite.objects.get_or_create(slug="vanta_admin")
        self.category.websites.add(site)
        self.tag.websites.add(site)

    def allow_all_permissions(self):
        return patch.object(self.actor, "has_perm", return_value=True)

    def assert_no_staging(self, import_root):
        self.assertEqual(BlogArticleImport.objects.count(), 0)
        self.assertEqual(list(import_root.rglob("*")), [])

    def stage(self, payload, files=()):
        with self.allow_all_permissions():
            return validate_and_stage_blog_import(
                source_file(payload),
                list(files),
                self.actor,
            )

    def test_required_permissions_include_every_core_and_payload_gate(self):
        parsed = validate_blog_import(all_block_payload()).parsed
        permissions = set(required_blog_import_permissions(parsed))
        expected = {
            "blog.add_blogpost",
            "blog.change_blogpost",
            "blog.organize_blogpost",
            "blog.add_blogheadingblock",
            "blog.add_blogrichtextblock",
            "blog.add_blogfaqblock",
            "blog.add_blogchecklistblock",
            "blog.add_blogcodeblock",
            "blog.add_blogembedsharingblock",
            "blog.add_blogcalloutblock",
            "blog.add_blogsourceLinkblock".lower(),
            "blog.add_bloglinkgroupblock",
            "blog.add_bloginternallinkblock",
            "blog.add_blogimageblock",
            "blog.add_blogimagecomparisonblock",
            "blog.add_blogimage",
            "blog.add_blogimagecomparison",
            "blog.add_blogpostrelated",
        }

        self.assertEqual(permissions, expected)

        for missing_permission in sorted(permissions):
            with self.subTest(missing_permission=missing_permission):
                actor = SimpleNamespace(
                    is_authenticated=True,
                    has_perm=lambda permission, missing=missing_permission: permission != missing,
                )
                with self.assertRaises(BlogImportPermissionError) as raised:
                    from apps.blog.import_services import require_blog_import_permissions

                    require_blog_import_permissions(actor, parsed)
                self.assertEqual(raised.exception.missing_permissions, (missing_permission,))

    def test_embed_permission_is_required_only_when_the_package_uses_the_block(self):
        without_embed = validate_blog_import(minimal_payload()).parsed
        with_embed = validate_blog_import(
            minimal_payload(
                blocks=[
                    {
                        "type": "embed_sharing",
                        "platform": "youtube",
                        "url": "https://youtu.be/dQw4w9WgXcQ",
                    }
                ]
            )
        ).parsed

        self.assertNotIn(
            "blog.add_blogembedsharingblock",
            required_blog_import_permissions(without_embed),
        )
        self.assertIn(
            "blog.add_blogembedsharingblock",
            required_blog_import_permissions(with_embed),
        )

    def test_exact_reference_resolution_does_not_fuzzy_match(self):
        related = BlogPost.objects.create(
            status=BlogPost.Status.DRAFT,
            title="Related article",
            slug="related-article",
            category=self.category,
        )
        BlogPostPublication.objects.create(post=related, site_slug="vanta_admin")
        parsed = validate_blog_import(
            minimal_payload(tags=["django"], related_articles=["related-article"])
        ).parsed

        references = resolve_blog_import_references(parsed)

        self.assertEqual(references.author.pk, self.author.pk)
        self.assertEqual(references.category.pk, self.category.pk)
        self.assertEqual(tuple(tag.pk for tag in references.tags), (self.tag.pk,))
        self.assertEqual(references.resolved_publication_sites, ("vanta_admin",))
        self.assertEqual(references.related_posts[0].pk, related.pk)
        self.assertEqual(references.unresolved, ())

        unresolved = resolve_blog_import_references(
            validate_blog_import(
                minimal_payload(
                    author="oli-foo",
                    category="development-foo",
                    tags=["django-foo"],
                    publication_sites=["vanta-admin"],
                    canonical_site="vanta-admin",
                )
            ).parsed
        )
        self.assertIsNone(unresolved.author)
        self.assertIsNone(unresolved.category)
        self.assertEqual(unresolved.tags, ())
        self.assertEqual(unresolved.resolved_publication_sites, ())
        self.assertEqual(
            {issue.code for issue in unresolved.unresolved},
            {
                "unresolved_author",
                "unresolved_category",
                "unresolved_tag",
                "unresolved_publication_site",
                "unresolved_canonical_site",
            },
        )

    def test_valid_package_stages_only_referenced_images_and_bounded_warnings(self):
        payload = image_payload()
        payload["assets"].append(
            {
                "id": "unused",
                "file": "images/unused.png",
                "name": "Unused",
                "alt_text": "An unused image",
            }
        )
        payload["comparisons"].append(
            {
                "id": "unused-comparison",
                "name": "Unused comparison",
                "first": {"file": "images/unused-first.png", "alt_text": "Unused first"},
                "second": {"file": "images/unused-second.png", "alt_text": "Unused second"},
            }
        )
        with private_roots() as (_media_root, import_root):
            session = self.stage(
                payload,
                [upload("hero.png"), upload("second.png"), upload("extra.png")],
            )

            self.assertEqual(list(session.files.values_list("selected_name", flat=True)), ["hero.png", "second.png"])
            warning_codes = {warning["code"] for warning in session.warnings}
            self.assertEqual(
                warning_codes,
                {
                    "extra_image_file",
                    "unused_asset_definition",
                    "unused_comparison_definition",
                },
            )
            self.assertEqual(len(list(import_root.rglob("*"))), 3)

    def test_distinct_referenced_files_are_byte_validated_once_each(self):
        payload = image_payload()
        with private_roots():
            with patch("apps.blog.import_services.validate_image_bytes") as validate_bytes:
                self.stage(payload, [upload("hero.png"), upload("second.png")])

            self.assertEqual(validate_bytes.call_count, 2)

    def test_missing_duplicate_and_collapsed_image_matches_block_without_staging(self):
        with private_roots() as (_media_root, import_root):
            with self.assertRaises(BlogImportValidationError) as missing:
                self.stage(image_payload(), [])
            self.assertEqual(
                {(issue.code, issue.location) for issue in missing.exception.issues},
                {
                    ("missing_image_file", "assets[0].file"),
                    ("missing_image_file", "comparisons[0].first.file"),
                    ("missing_image_file", "comparisons[0].second.file"),
                },
            )
            self.assert_no_staging(import_root)

            with self.assertRaises(BlogImportValidationError) as duplicate:
                self.stage(
                    image_payload(),
                    [upload("first/hero.png"), upload("second/hero.png"), upload("second.png")],
                )
            self.assertEqual(
                {(issue.code, issue.location) for issue in duplicate.exception.issues},
                {
                    ("ambiguous_selected_basename", "assets[0].file"),
                    ("ambiguous_selected_basename", "comparisons[0].first.file"),
                },
            )
            self.assert_no_staging(import_root)

            collapsed = image_payload(shared_path="first/hero.png", second_path="images/second.png")
            collapsed["assets"].append(
                {
                    "id": "other",
                    "file": "another/hero.png",
                    "name": "Other",
                    "alt_text": "Another hero",
                }
            )
            collapsed["article"]["blocks"].append({"type": "image", "asset_id": "other"})
            with self.assertRaises(BlogImportValidationError) as ambiguous_source:
                self.stage(collapsed, [upload("hero.png"), upload("second.png")])
            self.assertEqual(
                {(issue.code, issue.location) for issue in ambiguous_source.exception.issues},
                {("ambiguous_image_basename", "assets[1].file")},
            )
            self.assert_no_staging(import_root)

    def test_invalid_referenced_image_bytes_and_package_limits_block_without_staging(self):
        with private_roots() as (_media_root, import_root):
            with patch(
                "apps.blog.import_services.validate_image_bytes",
                side_effect=ValidationError("not an image"),
            ):
                with self.assertRaises(BlogImportValidationError) as invalid_image:
                    self.stage(
                        minimal_payload(
                            blocks=[{"type": "image", "asset_id": "hero"}],
                        )
                        | {
                            "assets": [
                                {
                                    "id": "hero",
                                    "file": "images/hero.png",
                                    "name": "Hero",
                                    "alt_text": "A hero",
                                }
                            ]
                        },
                        [upload("hero.png", b"not an image")],
                    )
            self.assertEqual(
                {(issue.code, issue.location) for issue in invalid_image.exception.issues},
                {("invalid_image_file", "assets[0].file")},
            )
            self.assert_no_staging(import_root)

            too_many = [upload(f"image-{index}.png") for index in range(51)]
            with self.assertRaises(BlogImportValidationError) as count_error:
                self.stage(minimal_payload(), too_many)
            self.assertEqual({issue.code for issue in count_error.exception.issues}, {"too_many_image_files"})
            self.assert_no_staging(import_root)

            too_large = upload("large.png", size=150 * 1024 * 1024 + 1)
            with self.assertRaises(BlogImportValidationError) as aggregate_error:
                self.stage(minimal_payload(), [too_large])
            self.assertEqual({issue.code for issue in aggregate_error.exception.issues}, {"image_files_too_large"})
            self.assert_no_staging(import_root)

    def test_all_missing_referenced_image_paths_are_reported(self):
        payload = minimal_payload(
            blocks=[
                {"type": "image", "asset_id": f"asset-{index}"}
                for index in range(101)
            ]
        )
        payload["assets"] = [
            {
                "id": f"asset-{index}",
                "file": f"images/asset-{index}.png",
                "name": f"Asset {index}",
                "alt_text": f"Asset {index}",
            }
            for index in range(101)
        ]

        with private_roots() as (_media_root, import_root):
            with self.assertRaises(BlogImportValidationError) as missing:
                self.stage(payload, [])

            missing_paths = {
                issue.location
                for issue in missing.exception.issues
                if issue.code == "missing_image_file"
            }
            self.assert_no_staging(import_root)

        self.assertEqual(len(missing_paths), 101)

    def test_missing_related_article_and_permission_failure_leave_no_stage(self):
        with private_roots() as (_media_root, import_root):
            payload = minimal_payload(related_articles=["missing-related"])
            with self.allow_all_permissions():
                with self.assertRaises(BlogImportValidationError) as missing_related:
                    validate_and_stage_blog_import(source_file(payload), [], self.actor)
            self.assertEqual(
                {(issue.code, issue.location) for issue in missing_related.exception.issues},
                {("missing_related_article", "article.related_articles[0].slug")},
            )
            self.assert_no_staging(import_root)

            with patch.object(self.actor, "has_perm", return_value=False):
                with self.assertRaises(BlogImportPermissionError) as permission_error:
                    validate_and_stage_blog_import(source_file(minimal_payload()), [], self.actor)
            self.assertEqual(BlogArticleImport.objects.count(), 0)
            self.assertTrue(permission_error.exception.missing_permissions)
            self.assert_no_staging(import_root)

    def test_duplicate_title_and_slug_warnings_are_safe_and_bounded(self):
        BlogPost.objects.create(
            status=BlogPost.Status.PUBLISHED,
            title="An importable article",
            slug="existing-title",
            category=self.category,
        )
        BlogPost.objects.create(
            status=BlogPost.Status.DRAFT,
            title="A different title",
            slug="an-importable-article",
            category=self.category,
        )
        payload = minimal_payload()

        with private_roots():
            session = self.stage(payload)
            with self.allow_all_permissions():
                review = get_blog_import_review(session.id, self.actor)

        self.assertEqual(len(review.duplicate_matches), 2)
        self.assertEqual(
            {(match.title, match.slug, match.status) for match in review.duplicate_matches},
            {
                ("An importable article", "existing-title", "Published"),
                ("A different title", "an-importable-article", "Draft"),
            },
        )
        self.assertTrue(all(match.change_url.startswith("/admin/") for match in review.duplicate_matches))
        duplicate_warnings = [
            warning.message for warning in review.warnings if warning.code == "duplicate_article_match"
        ]
        self.assertEqual(len(duplicate_warnings), 2)
        self.assertTrue(all("An importable article" in message or "A different title" in message for message in duplicate_warnings))
        self.assertTrue(all("private" not in message.lower() for message in duplicate_warnings))

    def test_duplicate_display_matches_are_bounded(self):
        for index in range(MAX_DUPLICATE_MATCHES + 5):
            BlogPost.objects.create(
                status=BlogPost.Status.DRAFT,
                title="Repeated title",
                slug=f"repeated-title-{index}",
                category=self.category,
            )

        with private_roots():
            session = self.stage(minimal_payload(title="Repeated title"))
            with self.allow_all_permissions():
                review = get_blog_import_review(session.id, self.actor)

        self.assertEqual(len(review.duplicate_matches), MAX_DUPLICATE_MATCHES)
        self.assertEqual(
            len([warning for warning in review.warnings if warning.code == "duplicate_article_match"]),
            MAX_DUPLICATE_MATCHES,
        )

    def test_reviewed_choices_restrict_canonical_site_and_revalidate_site_dependencies(self):
        related = BlogPost.objects.create(
            status=BlogPost.Status.DRAFT,
            title="Related article",
            slug="related-article",
            category=self.category,
        )
        BlogPostPublication.objects.create(post=related, site_slug="vanta_admin")
        payload = minimal_payload(
            related_articles=["related-article"],
            blocks=[
                {"type": "internal_link", "destination_key": "vanta-home", "label": "Vanta home"}
            ],
        )

        with private_roots():
            session = self.stage(payload)
            with self.allow_all_permissions():
                invalid = validate_reviewed_blog_import(
                    session,
                    {
                        "author": self.author.pk,
                        "category": self.category.pk,
                        "tags": [],
                        "publication_sites": ["vanta_admin", "my_website"],
                        "canonical_site": "vanta_admin",
                        "expand_taxonomy_websites": True,
                    },
                    self.actor,
                )
                valid = validate_reviewed_blog_import(
                    session,
                    {
                        "author": self.author.pk,
                        "category": self.category.pk,
                        "tags": [],
                        "publication_sites": ["vanta_admin"],
                        "canonical_site": "vanta_admin",
                    },
                    self.actor,
                )

        self.assertFalse(invalid.valid)
        self.assertEqual(
            {issue.code for issue in invalid.issues},
            {"invalid_internal_link_destination", "incompatible_related_article"},
        )
        self.assertTrue(valid.valid, valid.issues)
        self.assertEqual(valid.references.publication_sites, ("vanta_admin",))

        with self.allow_all_permissions():
            canonical_error = validate_reviewed_blog_import(
                session,
                {
                    "author": self.author.pk,
                    "category": self.category.pk,
                    "tags": [],
                    "publication_sites": ["vanta_admin"],
                    "canonical_site": "my_website",
                },
                self.actor,
            )
        self.assertFalse(canonical_error.valid)
        self.assertIn("canonical_site_not_selected", {issue.code for issue in canonical_error.issues})

    def test_v1_existing_taxonomy_expansion_requires_confirmation_and_permission(self):
        payload = minimal_payload(
            publication_sites=["my_website"],
            canonical_site="my_website",
        )

        with private_roots():
            session = self.stage(payload)
            reviewed = ReviewedImportReferences(
                author=self.author,
                category=self.category,
                tags=(),
                publication_sites=("my_website",),
                canonical_site="my_website",
            )
            with patch.object(
                self.actor,
                "has_perm",
                side_effect=lambda permission: permission != "blog.change_blogcategory",
            ):
                validation = validate_reviewed_blog_import(
                    session,
                    reviewed,
                    self.actor,
                )
            confirmed = ReviewedImportReferences(
                author=self.author,
                category=self.category,
                tags=(),
                publication_sites=("my_website",),
                canonical_site="my_website",
                expand_taxonomy_websites=True,
            )
            with self.allow_all_permissions():
                confirmed_validation = validate_reviewed_blog_import(
                    session,
                    confirmed,
                    self.actor,
                )

        self.assertFalse(validation.valid)
        self.assertEqual(
            {issue.code for issue in validation.issues},
            {"taxonomy_unavailable", "missing_category_change_permission"},
        )
        self.assertTrue(confirmed_validation.valid, confirmed_validation.issues)

    def test_v2_rejects_mapping_and_creating_a_category_together(self):
        payload = minimal_payload()
        payload['version'] = 2
        payload['article']['category'] = {
            'name': 'Proposed category',
            'slug': 'proposed-category',
        }

        with private_roots():
            session = self.stage(payload)
            reviewed = ReviewedImportReferences(
                author=self.author,
                category=self.category,
                tags=(),
                publication_sites=('vanta_admin',),
                canonical_site='vanta_admin',
                create_category=True,
            )
            with self.allow_all_permissions():
                validation = validate_reviewed_blog_import(
                    session,
                    reviewed,
                    self.actor,
                )

        self.assertFalse(validation.valid)
        self.assertIn(
            'conflicting_category_choice',
            {issue.code for issue in validation.issues},
        )

    def test_review_rechecks_changed_site_dependencies(self):
        related = BlogPost.objects.create(
            status=BlogPost.Status.DRAFT,
            title="Related article",
            slug="related-article",
            category=self.category,
        )
        publication = BlogPostPublication.objects.create(post=related, site_slug="vanta_admin")
        payload = minimal_payload(related_articles=["related-article"])

        with private_roots():
            session = self.stage(payload)
            publication.delete()
            with self.allow_all_permissions():
                review = get_blog_import_review(session.id, self.actor)

        self.assertIn(
            "incompatible_related_article",
            {issue.code for issue in review.issues},
        )

    def test_reviewed_model_choices_are_reloaded_before_validation(self):
        payload = minimal_payload()

        with private_roots():
            session = self.stage(payload)
            reviewed = ReviewedImportReferences(
                author=self.author,
                category=self.category,
                tags=(self.tag,),
                publication_sites=("vanta_admin",),
                canonical_site="vanta_admin",
            )
            self.author.delete()
            with self.allow_all_permissions():
                validation = validate_reviewed_blog_import(session, reviewed, self.actor)

        self.assertFalse(validation.valid)
        self.assertIn("invalid_review_author", {issue.code for issue in validation.issues})
