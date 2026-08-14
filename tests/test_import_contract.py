from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from apps.blog.import_contract import (
    BLOCK_DISPATCH,
    BLOCK_MODEL_NAMES,
    MAX_SOURCE_BYTES,
    SCHEMA_PATH,
    CalloutImportBlock,
    ChecklistImportBlock,
    CodeImportBlock,
    FAQImportBlock,
    EmbedSharingImportBlock,
    HeadingImportBlock,
    ImageComparisonImportBlock,
    ImageImportBlock,
    InternalLinkImportBlock,
    LinkGroupImportBlock,
    RichTextImportBlock,
    SourceLinkImportBlock,
    parse_blog_import,
    validate_blog_import,
)


EXAMPLE_PATH = Path(__file__).resolve().parents[2] / (
    'docs/blog/features/import/example-blog-article.json'
)


def example_payload():
    return json.loads(EXAMPLE_PATH.read_text(encoding='utf-8'))


def minimal_payload(*, blocks=None):
    return {
        'format': 'blog-article-import',
        'version': 1,
        'article': {
            'title': 'An importable article',
            'summary': 'A draft summary.',
            'author': {'slug': 'oli'},
            'category': {'slug': 'development'},
            'publication_sites': ['vanta_admin'],
            'blocks': blocks or [{'type': 'heading', 'level': 2, 'text': 'A section'}],
        },
        'assets': [],
        'comparisons': [],
    }


def issue_pairs(result):
    return tuple((issue.code, issue.location) for issue in result.issues)


class BlogImportContractTests(TestCase):
    def test_v2_taxonomy_names_are_validated_and_preserved(self):
        payload = minimal_payload()
        payload['version'] = 2
        payload['article']['category'] = {'name': '  Development  ', 'slug': 'development'}
        payload['article']['tags'] = [{'name': '  JavaScript  ', 'slug': 'javascript'}]

        result = validate_blog_import(payload)

        self.assertTrue(result.valid, result.issues)
        self.assertEqual(result.parsed.article.category.name, 'Development')
        self.assertEqual(result.parsed.article.tags[0].name, 'JavaScript')
        self.assertEqual(result.parsed.as_dict()['article']['category']['name'], 'Development')

    def test_v2_taxonomy_names_require_visible_text(self):
        for taxonomy in ('category', 'tag'):
            with self.subTest(taxonomy=taxonomy):
                payload = minimal_payload()
                payload['version'] = 2
                payload['article']['category'] = {
                    'name': 'Development',
                    'slug': 'development',
                }
                payload['article']['tags'] = [
                    {'name': 'JavaScript', 'slug': 'javascript'},
                ]
                if taxonomy == 'category':
                    payload['article']['category']['name'] = '   '
                    location = 'article.category.name'
                else:
                    payload['article']['tags'][0]['name'] = '   '
                    location = 'article.tags[0].name'

                result = validate_blog_import(payload)

                self.assertFalse(result.valid)
                self.assertEqual(issue_pairs(result), (('empty_taxonomy_name', location),))

    def test_v2_tag_slugs_must_be_unique_even_when_names_differ(self):
        payload = minimal_payload()
        payload['version'] = 2
        payload['article']['category'] = {'name': 'Development', 'slug': 'development'}
        payload['article']['tags'] = [
            {'name': 'First name', 'slug': 'shared-slug'},
            {'name': 'Second name', 'slug': 'shared-slug'},
        ]

        result = validate_blog_import(payload)

        self.assertFalse(result.valid)
        self.assertEqual(
            issue_pairs(result),
            (('duplicate_tag_slug', 'article.tags[1].slug'),),
        )

    def test_v2_taxonomy_lengths_match_database_fields(self):
        cases = (
            ('category', 'name', 'x' * 121, 'article.category.name'),
            ('category', 'slug', 'x' * 141, 'article.category.slug'),
            ('tag', 'name', 'x' * 81, 'article.tags[0].name'),
            ('tag', 'slug', 'x' * 101, 'article.tags[0].slug'),
        )

        for taxonomy, field, value, location in cases:
            with self.subTest(taxonomy=taxonomy, field=field):
                payload = minimal_payload()
                payload['version'] = 2
                payload['article']['category'] = {
                    'name': 'Development',
                    'slug': 'development',
                }
                payload['article']['tags'] = [
                    {'name': 'JavaScript', 'slug': 'javascript'},
                ]
                target = (
                    payload['article']['category']
                    if taxonomy == 'category'
                    else payload['article']['tags'][0]
                )
                target[field] = value

                result = validate_blog_import(payload)

                self.assertFalse(result.valid)
                self.assertEqual(issue_pairs(result), (('value_too_long', location),))

    def test_checked_in_example_parses_with_all_supported_block_variants(self):
        result = parse_blog_import(EXAMPLE_PATH.read_bytes())

        self.assertTrue(result.valid, result.issues)
        self.assertIsNotNone(result.data)
        self.assertEqual(
            tuple(block.type for block in result.data.article.blocks),
            (
                'heading',
                'rich_text',
                'checklist',
                'code',
                'callout',
                'heading',
                'faq',
                'source_link',
                'link_group',
                'internal_link',
                'image',
                'image_comparison',
                'rich_text',
            ),
        )

    def test_schema_is_strict_and_contains_one_branch_for_each_dispatch_type(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        object_definitions = [
            definition
            for definition in schema['$defs'].values()
            if definition.get('type') == 'object'
        ]
        self.assertFalse(schema.get('additionalProperties'))
        self.assertTrue(object_definitions)
        self.assertTrue(all(definition.get('additionalProperties') is False for definition in object_definitions))

        block_refs = {
            item['$ref'].rsplit('/', 1)[-1]
            for item in schema['$defs']['article']['properties']['blocks']['items']['oneOf']
        }
        self.assertEqual(
            block_refs,
            {
                'heading_block',
                'rich_text_block',
                'faq_block',
                'checklist_block',
                'code_block',
                'embed_sharing_block',
                'callout_block',
                'source_link_block',
                'link_group_block',
                'internal_link_block',
                'image_block',
                'image_comparison_block',
            },
        )

    def test_embed_sharing_normalizes_and_round_trips_without_provider_io(self):
        payload = minimal_payload(
            blocks=[
                {
                    'type': 'embed_sharing',
                    'platform': 'youtube',
                    'url': 'https://youtu.be/dQw4w9WgXcQ?si=tracking',
                    'caption': '  A useful video  ',
                },
                {'type': 'heading', 'level': 2, 'text': 'After the embed'},
            ]
        )

        with patch('apps.blog.embed_sharing.build_opener') as build_opener:
            result = validate_blog_import(payload)
            self.assertTrue(result.valid, result.issues)
            block = result.data.article.blocks[0]
            self.assertIsInstance(block, EmbedSharingImportBlock)
            self.assertEqual(block.platform, 'youtube')
            self.assertEqual(block.url, 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')
            self.assertEqual(block.caption, 'A useful video')

            serialized = result.data.as_dict()
            round_trip = validate_blog_import(serialized)

        self.assertTrue(round_trip.valid, round_trip.issues)
        self.assertEqual(round_trip.data.article.blocks, result.data.article.blocks)
        self.assertEqual(
            tuple(block.type for block in round_trip.data.article.blocks),
            ('embed_sharing', 'heading'),
        )
        build_opener.assert_not_called()

    def test_embed_sharing_schema_and_local_validation_reject_unsafe_shapes(self):
        cases = (
            ('missing platform', {'type': 'embed_sharing', 'url': 'https://youtu.be/dQw4w9WgXcQ'}, 'missing_required_field'),
            (
                'extra field',
                {
                    'type': 'embed_sharing',
                    'platform': 'youtube',
                    'url': 'https://youtu.be/dQw4w9WgXcQ',
                    'provider_html': '<iframe>secret</iframe>',
                },
                'unknown_field',
            ),
            (
                'wrong platform URL',
                {
                    'type': 'embed_sharing',
                    'platform': 'youtube',
                    'url': 'https://x.com/example/status/123456789',
                },
                'invalid_embed_url',
            ),
            (
                'long caption',
                {
                    'type': 'embed_sharing',
                    'platform': 'youtube',
                    'url': 'https://youtu.be/dQw4w9WgXcQ',
                    'caption': 'x' * 301,
                },
                'value_too_long',
            ),
        )

        for name, block, code in cases:
            with self.subTest(name=name):
                result = validate_blog_import(minimal_payload(blocks=[block]))
                self.assertFalse(result.valid)
                self.assertIn(code, {issue.code for issue in result.issues})

    def test_dispatch_matches_current_blog_block_models(self):
        from apps.blog.models import BLOG_BLOCK_MODELS

        self.assertEqual(
            tuple(BLOCK_DISPATCH),
            tuple(block['type'] for block in self._all_block_payload()['article']['blocks']),
        )
        self.assertEqual(
            tuple(model.__name__ for model in BLOG_BLOCK_MODELS),
            BLOCK_MODEL_NAMES,
        )

    def test_all_block_variants_normalize_to_their_typed_immutable_values(self):
        result = validate_blog_import(self._all_block_payload())

        self.assertTrue(result.valid, result.issues)
        self.assertEqual(
            tuple(type(block) for block in result.data.article.blocks),
            (
                HeadingImportBlock,
                RichTextImportBlock,
                FAQImportBlock,
                ChecklistImportBlock,
                CodeImportBlock,
                EmbedSharingImportBlock,
                CalloutImportBlock,
                SourceLinkImportBlock,
                LinkGroupImportBlock,
                InternalLinkImportBlock,
                ImageImportBlock,
                ImageComparisonImportBlock,
            ),
        )
        self.assertIsInstance(result.data.article.blocks, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.data.article.title = 'Changed'

    def test_documented_defaults_and_normalization_do_not_add_django_owned_fields(self):
        payload = minimal_payload(
            blocks=[
                {'type': 'heading', 'level': 2, 'text': '  Heading  '},
                {'type': 'checklist', 'items': ['  One  ']},
                {'type': 'code', 'code': '  echo ok  '},
                {'type': 'callout', 'body': '<p>  A note  </p>'},
                {'type': 'source_link', 'url': 'https://example.com/source'},
                {'type': 'image', 'asset_id': 'hero'},
            ],
        )
        payload['article'].update(
            {
                'title': '  A title to slugify  ',
                'summary': '  Draft summary  ',
            }
        )
        payload['assets'] = [
            {
                'id': 'hero',
                'file': 'images/hero.jpg',
                'name': '  Hero image  ',
                'alt_text': '  A hero image  ',
            }
        ]

        result = validate_blog_import(payload)

        self.assertTrue(result.valid, result.issues)
        article = result.data.article
        self.assertEqual(article.title, 'A title to slugify')
        self.assertEqual(article.slug, 'a-title-to-slugify')
        self.assertEqual(article.type, 'article')
        self.assertEqual(article.summary, 'Draft summary')
        self.assertEqual(article.seo.title, '')
        self.assertEqual(article.seo.description, '')
        self.assertEqual(article.tags, ())
        self.assertEqual(article.related_articles, ())
        self.assertEqual(article.canonical_site, 'vanta_admin')
        self.assertIsNone(article.featured_image)

        self.assertEqual(article.blocks[1].marker, 'checkmark')
        self.assertEqual(article.blocks[2].language, 'text')
        self.assertEqual(article.blocks[2].caption, '')
        self.assertEqual(article.blocks[3].callout_type, 'note')
        self.assertEqual(article.blocks[3].title, '')
        self.assertEqual(article.blocks[4].label, 'Source:')
        self.assertEqual(result.data.assets[0].is_decorative, False)
        self.assertEqual(result.data.assets[0].is_feature, False)
        self.assertEqual(result.data.assets[0].name, 'Hero image')
        self.assertEqual(result.data.assets[0].alt_text, 'A hero image')

        serialized = result.data.as_dict()
        self.assertEqual(set(serialized), {'format', 'version', 'article', 'assets', 'comparisons'})
        self.assertNotIn(
            'status',
            serialized['article'],
        )
        self.assertNotIn('published_at', serialized['article'])
        self.assertNotIn('last_reviewed_on', serialized['article'])
        self.assertNotIn('content_updated_at', serialized['article'])
        self.assertNotIn('ordering', json.dumps(serialized))
        self.assertNotIn('pk', json.dumps(serialized))

    def test_structural_boundary_failures_have_stable_safe_issue_pairs(self):
        wrong_format = example_payload()
        wrong_format['format'] = 'other-format'
        wrong_version = example_payload()
        wrong_version['version'] = 3
        decimal_version = example_payload()
        decimal_version['version'] = 1.0
        decimal_heading = minimal_payload()
        decimal_heading['article']['blocks'][0]['level'] = 2.0
        missing_field = example_payload()
        del missing_field['article']['title']
        unknown_field = example_payload()
        unknown_field['unexpected'] = 'do not expose this'

        cases = (
            ('invalid UTF-8', b'\xff', (('invalid_utf8', '$'),)),
            ('malformed JSON', b'{', (('malformed_json', '$'),)),
            ('duplicate keys', b'{"format":"blog-article-import","format":"other"}', (('duplicate_key', '$'),)),
            ('non-object root', b'[]', (('root_not_object', '$'),)),
            (
                'oversized source',
                b'0' * (MAX_SOURCE_BYTES + 1),
                (('source_too_large', '$'),),
            ),
            ('wrong format', wrong_format, (('unsupported_format', 'format'),)),
            ('wrong version', wrong_version, (('unsupported_version', 'version'),)),
            ('decimal version', decimal_version, (('unsupported_version', 'version'),)),
            ('decimal heading level', decimal_heading, (('invalid_type', 'article.blocks[0].level'),)),
            ('missing required field', missing_field, (('missing_required_field', 'article'),)),
            ('unknown field', unknown_field, (('unknown_field', '$'),)),
        )

        for name, source, expected in cases:
            with self.subTest(name=name):
                result = parse_blog_import(json.dumps(source) if isinstance(source, dict) else source)
                self.assertFalse(result.valid)
                self.assertEqual(issue_pairs(result), expected)
                self.assertTrue(all('/home/' not in issue.message for issue in result.issues))

    def test_unknown_fields_are_rejected_at_nested_object_boundaries(self):
        cases = []

        payload = example_payload()
        payload['article']['unexpected'] = True
        cases.append(('article', payload, 'article'))

        payload = example_payload()
        payload['article']['author']['unexpected'] = True
        cases.append(('reference', payload, 'article.author'))

        payload = example_payload()
        payload['article']['seo']['unexpected'] = True
        cases.append(('seo', payload, 'article.seo'))

        payload = example_payload()
        payload['assets'][0]['unexpected'] = True
        cases.append(('asset', payload, 'assets[0]'))

        payload = example_payload()
        payload['comparisons'][0]['unexpected'] = True
        cases.append(('comparison', payload, 'comparisons[0]'))

        payload = example_payload()
        payload['comparisons'][0]['first']['unexpected'] = True
        cases.append(('comparison side', payload, 'comparisons[0].first'))

        payload = example_payload()
        payload['article']['blocks'][0]['unexpected'] = True
        cases.append(('block', payload, 'article.blocks[0]'))

        payload = example_payload()
        payload['article']['blocks'][6]['items'][0]['unexpected'] = True
        cases.append(('faq item', payload, 'article.blocks[6]'))

        payload = example_payload()
        payload['article']['blocks'][8]['links'][0]['unexpected'] = True
        cases.append(('external link', payload, 'article.blocks[8]'))

        for name, payload, location in cases:
            with self.subTest(name=name):
                result = validate_blog_import(payload)
                self.assertIn(('unknown_field', location), issue_pairs(result))
                self.assertTrue(all('/home/' not in issue.message for issue in result.issues))

    def test_missing_or_unsupported_block_shapes_are_rejected_at_the_block_location(self):
        missing_text = minimal_payload()
        del missing_text['article']['blocks'][0]['text']
        unsupported = minimal_payload(blocks=[{'type': 'video'}])

        result = validate_blog_import(missing_text)
        self.assertIn(('missing_required_field', 'article.blocks[0]'), issue_pairs(result))

        result = validate_blog_import(unsupported)
        self.assertEqual(issue_pairs(result), (('unsupported_block_type', 'article.blocks[0]'),))

    def test_path_rules_accept_normal_posix_paths_and_reject_unsafe_paths(self):
        cases = (
            ('absolute', '/etc/passwd', 'absolute_image_path'),
            ('URL', 'https://example.com/image.jpg', 'url_image_path'),
            ('malformed URL', 'http://[invalid', 'url_image_path'),
            ('traversal', '../image.jpg', 'traversal_image_path'),
            ('dot segment', 'images/./image.jpg', 'traversal_image_path'),
            ('backslash', 'images\\image.jpg', 'backslash_image_path'),
            ('empty segment', 'images//image.jpg', 'empty_image_path_segment'),
            ('control character', 'images/\x00image.jpg', 'control_character_image_path'),
        )

        valid_payload = minimal_payload(blocks=[{'type': 'image', 'asset_id': 'hero'}])
        valid_payload['assets'] = [
            {
                'id': 'hero',
                'file': 'images/hero.jpg',
                'name': 'Hero',
                'alt_text': 'A hero image',
            }
        ]
        self.assertTrue(validate_blog_import(valid_payload).valid)

        for name, path, code in cases:
            with self.subTest(name=name):
                payload = deepcopy(valid_payload)
                payload['assets'][0]['file'] = path
                result = validate_blog_import(payload)
                self.assertIn((code, 'assets[0].file'), issue_pairs(result))

    def test_internal_references_use_the_current_blog_destination_registry(self):
        payload = minimal_payload(
            blocks=[
                {
                    'type': 'internal_link',
                    'destination_key': 'not-approved',
                    'label': 'Descriptive link',
                }
            ]
        )
        result = validate_blog_import(payload)

        self.assertIn(
            ('invalid_internal_link_destination', 'article.blocks[0].destination_key'),
            issue_pairs(result),
        )

        payload = minimal_payload(
            blocks=[
                {
                    'type': 'rich_text',
                    'body': '<p><a data-blog-internal-key="not-approved">Bad destination</a></p>',
                }
            ]
        )
        result = validate_blog_import(payload)

        self.assertIn(
            ('invalid_inline_internal_link', 'article.blocks[0].body'),
            issue_pairs(result),
        )

        payload = minimal_payload(
            blocks=[
                {
                    'type': 'faq',
                    'items': [
                        {
                            'question': 'Question',
                            'answer': '<p><a data-blog-internal-key="not-approved">Bad destination</a></p>',
                        }
                    ],
                }
            ]
        )
        result = validate_blog_import(payload)

        self.assertIn(
            ('invalid_inline_internal_link', 'article.blocks[0].items[0].answer'),
            issue_pairs(result),
        )

    def test_empty_image_paths_are_rejected_without_being_read(self):
        payload = minimal_payload(blocks=[{'type': 'image', 'asset_id': 'hero'}])
        payload['assets'] = [
            {
                'id': 'hero',
                'file': '',
                'name': 'Hero',
                'alt_text': 'A hero image',
            }
        ]

        result = validate_blog_import(payload)

        self.assertFalse(result.valid)
        self.assertIn(('empty_or_short_value', 'assets[0].file'), issue_pairs(result))

    def test_distinct_paths_with_the_same_selected_basename_are_ambiguous(self):
        payload = minimal_payload(blocks=[{'type': 'image_comparison', 'comparison_id': 'pair'}])
        payload['assets'] = [
            {
                'id': 'hero',
                'file': 'images/hero.jpg',
                'name': 'Hero',
                'alt_text': 'A hero image',
            }
        ]
        payload['comparisons'] = [
            {
                'id': 'pair',
                'name': 'Pair',
                'first': {'file': 'other/hero.jpg', 'alt_text': 'Before'},
                'second': {'file': 'other/after.jpg', 'alt_text': 'After'},
            }
        ]

        result = validate_blog_import(payload)

        self.assertEqual(
            issue_pairs(result),
            (('ambiguous_image_basename', 'comparisons[0].first.file'),),
        )

    def test_reusing_the_same_exact_path_is_allowed(self):
        payload = minimal_payload(blocks=[{'type': 'image_comparison', 'comparison_id': 'pair'}])
        payload['comparisons'] = [
            {
                'id': 'pair',
                'name': 'Pair',
                'first': {'file': 'images/shared.jpg', 'alt_text': 'Before'},
                'second': {'file': 'images/after.jpg', 'alt_text': 'After'},
            }
        ]

        result = validate_blog_import(payload)

        self.assertTrue(result.valid, result.issues)

    def test_local_ids_and_references_are_checked_before_normalization(self):
        payload = example_payload()
        payload['assets'][1]['id'] = payload['assets'][0]['id']
        payload['comparisons'].append(deepcopy(payload['comparisons'][0]))
        payload['comparisons'][1]['id'] = 'before-after-2'
        payload['article']['featured_image'] = 'missing'
        payload['article']['blocks'][10]['asset_id'] = 'missing-body-asset'
        payload['article']['blocks'][11]['comparison_id'] = 'missing-comparison'

        result = validate_blog_import(payload)

        self.assertEqual(
            issue_pairs(result),
            (
                ('duplicate_asset_id', 'assets[1].id'),
                ('unknown_asset_reference', 'article.featured_image'),
                ('unknown_asset_reference', 'article.blocks[10].asset_id'),
                ('unknown_comparison_reference', 'article.blocks[11].comparison_id'),
            ),
        )

    def test_decorative_and_alt_text_rules_block_unsafe_image_references(self):
        payload = minimal_payload()
        payload['assets'] = [
            {
                'id': 'decorative',
                'file': 'images/decorative.jpg',
                'name': 'Decorative',
                'alt_text': '',
                'is_decorative': True,
            }
        ]
        self.assertTrue(validate_blog_import(payload).valid)

        payload['article']['blocks'] = [{'type': 'image', 'asset_id': 'decorative'}]
        result = validate_blog_import(payload)
        self.assertIn(('decorative_body_image', 'article.blocks[0].asset_id'), issue_pairs(result))

        payload['assets'][0]['alt_text'] = 'Should be empty'
        result = validate_blog_import(payload)
        self.assertIn(('decorative_alt_text', 'assets[0].alt_text'), issue_pairs(result))

        payload['assets'][0]['alt_text'] = ''
        result = validate_blog_import(payload)
        self.assertIn(('decorative_body_image', 'article.blocks[0].asset_id'), issue_pairs(result))

        payload = minimal_payload()
        payload['assets'] = [
            {
                'id': 'hero',
                'file': 'images/hero.jpg',
                'name': 'Hero',
                'alt_text': '',
            }
        ]
        result = validate_blog_import(payload)
        self.assertIn(('missing_alt_text', 'assets[0].alt_text'), issue_pairs(result))

    def test_meaningful_content_rules_reject_empty_or_unsafe_block_content(self):
        cases = (
            ('rich text', {'type': 'rich_text', 'body': '<p>&nbsp;</p>'}, 'empty_block_content', 'article.blocks[0].body'),
            ('heading', {'type': 'heading', 'level': 2, 'text': '  '}, 'empty_block_content', 'article.blocks[0].text'),
            ('checklist', {'type': 'checklist', 'items': ['  ']}, 'empty_block_content', 'article.blocks[0].items'),
            ('code', {'type': 'code', 'code': '  '}, 'empty_block_content', 'article.blocks[0].code'),
            ('callout', {'type': 'callout', 'body': '<p>&nbsp;</p>'}, 'empty_block_content', 'article.blocks[0].body'),
            ('checklist HTML', {'type': 'checklist', 'items': ['<strong>item</strong>']}, 'invalid_checklist_item', 'article.blocks[0].items'),
            ('generic internal link', {'type': 'internal_link', 'destination_key': 'vanta-features', 'label': ' Click here '}, 'generic_internal_link_label', 'article.blocks[0].label'),
            ('invalid source URL', {'type': 'source_link', 'url': 'ftp://example.com/source'}, 'invalid_http_url', 'article.blocks[0].url'),
            (
                'invalid grouped URL',
                {'type': 'link_group', 'label': 'Reading', 'links': [{'label': 'Source', 'url': 'javascript:alert(1)'}]},
                'invalid_http_url',
                'article.blocks[0].links[0].url',
            ),
        )

        for name, block, code, location in cases:
            with self.subTest(name=name):
                result = validate_blog_import(minimal_payload(blocks=[block]))
                self.assertIn((code, location), issue_pairs(result))
                self.assertIsNone(result.data)

    def test_invalid_faq_content_is_rejected_using_the_existing_faq_validator(self):
        payload = minimal_payload(
            blocks=[
                {
                    'type': 'faq',
                    'items': [{'question': '<em>Question</em>', 'answer': '<p>Answer</p>'}],
                }
            ]
        )

        result = validate_blog_import(payload)

        self.assertEqual(issue_pairs(result), (('invalid_faq', 'article.blocks[0].items'),))

    def test_issue_order_and_locations_are_deterministic_and_safe(self):
        payload = minimal_payload()
        payload['unexpected'] = '/home/user/private.json'
        payload['article']['unexpected'] = 'secret'
        payload['article']['blocks'][0]['unexpected'] = '/tmp/source.json'

        first = validate_blog_import(payload)
        second = validate_blog_import(payload)

        self.assertEqual(first.issues, second.issues)
        self.assertEqual(
            issue_pairs(first),
            (
                ('unknown_field', '$'),
                ('unknown_field', 'article'),
                ('unknown_field', 'article.blocks[0]'),
            ),
        )
        self.assertEqual(first.issues[0].location, '$')
        self.assertTrue(all(not issue.location.startswith(('/', '\\')) for issue in first.issues))
        self.assertTrue(all('/home/' not in issue.message and '/tmp/' not in issue.message for issue in first.issues))

    @staticmethod
    def _all_block_payload():
        payload = example_payload()
        payload['article']['blocks'] = [
            payload['article']['blocks'][0],
            payload['article']['blocks'][1],
            payload['article']['blocks'][6],
            payload['article']['blocks'][2],
            payload['article']['blocks'][3],
            {
                'type': 'embed_sharing',
                'platform': 'youtube',
                'url': 'https://youtu.be/dQw4w9WgXcQ',
                'caption': 'An embedded video',
            },
            payload['article']['blocks'][4],
            payload['article']['blocks'][7],
            payload['article']['blocks'][8],
            payload['article']['blocks'][9],
            payload['article']['blocks'][10],
            payload['article']['blocks'][11],
        ]
        return payload
