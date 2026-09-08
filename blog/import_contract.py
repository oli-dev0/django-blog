"""Runtime contract and parser for Blog article imports.

This module deliberately stops at normalized editorial data. It never resolves
database objects, opens image paths, or creates storage files.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from html import unescape
import json
from pathlib import Path
from typing import Any, BinaryIO, Callable, ClassVar, Literal, TypeAlias
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.utils.html import strip_tags
from django.utils.text import slugify
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as SchemaValidationError

from .embed_sharing import InvalidEmbedReference, normalize_embed_reference


CONTRACT_FORMAT = 'blog-article-import'
CONTRACT_VERSION = 2
SUPPORTED_CONTRACT_VERSIONS = (1, 2)
MAX_SOURCE_BYTES = 1 * 1024 * 1024
SCHEMA_PATHS = {
    version: Path(__file__).with_name('schemas') / f'blog-article-import-v{version}.schema.json'
    for version in SUPPORTED_CONTRACT_VERSIONS
}
# Compatibility for callers that explicitly inspect the original v1 schema.
SCHEMA_PATH = SCHEMA_PATHS[1]

IssueSeverity: TypeAlias = Literal['error', 'warning']


@dataclass(frozen=True, slots=True)
class ImportIssue:
    """A safe, stable validation result suitable for displaying in Admin."""

    code: str
    location: str
    message: str
    severity: IssueSeverity = 'error'


@dataclass(frozen=True, slots=True)
class ImportReference:
    slug: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ImportSEO:
    title: str = ''
    description: str = ''


@dataclass(frozen=True, slots=True)
class ImportAsset:
    id: str
    file: str
    name: str
    alt_text: str
    is_decorative: bool = False
    is_feature: bool = False
    caption_title: str = ''
    caption_text: str = ''


@dataclass(frozen=True, slots=True)
class ImportComparisonSide:
    file: str
    alt_text: str


@dataclass(frozen=True, slots=True)
class ImportComparison:
    id: str
    name: str
    first: ImportComparisonSide
    second: ImportComparisonSide
    caption_title: str = ''
    caption_text: str = ''


@dataclass(frozen=True, slots=True)
class HeadingImportBlock:
    level: int
    text: str
    type: ClassVar[str] = 'heading'


@dataclass(frozen=True, slots=True)
class RichTextImportBlock:
    body: str
    type: ClassVar[str] = 'rich_text'


@dataclass(frozen=True, slots=True)
class FAQImportItem:
    question: str
    answer: str


@dataclass(frozen=True, slots=True)
class FAQImportBlock:
    items: tuple[FAQImportItem, ...]
    type: ClassVar[str] = 'faq'


@dataclass(frozen=True, slots=True)
class ChecklistImportBlock:
    marker: str
    items: tuple[str, ...]
    type: ClassVar[str] = 'checklist'


@dataclass(frozen=True, slots=True)
class CodeImportBlock:
    language: str
    code: str
    caption: str
    type: ClassVar[str] = 'code'


@dataclass(frozen=True, slots=True)
class EmbedSharingImportBlock:
    platform: str
    url: str
    caption: str
    type: ClassVar[str] = 'embed_sharing'


@dataclass(frozen=True, slots=True)
class CalloutImportBlock:
    callout_type: str
    title: str
    body: str
    type: ClassVar[str] = 'callout'


@dataclass(frozen=True, slots=True)
class SourceLinkImportBlock:
    label: str
    url: str
    note: str
    type: ClassVar[str] = 'source_link'


@dataclass(frozen=True, slots=True)
class ExternalLink:
    label: str
    url: str


@dataclass(frozen=True, slots=True)
class LinkGroupImportBlock:
    label: str
    links: tuple[ExternalLink, ...]
    type: ClassVar[str] = 'link_group'


@dataclass(frozen=True, slots=True)
class InternalLinkImportBlock:
    destination_key: str
    label: str
    note: str
    type: ClassVar[str] = 'internal_link'


@dataclass(frozen=True, slots=True)
class ImageImportBlock:
    asset_id: str
    is_expandable: bool = True
    type: ClassVar[str] = 'image'


@dataclass(frozen=True, slots=True)
class ImageComparisonImportBlock:
    comparison_id: str
    type: ClassVar[str] = 'image_comparison'


ImportBlock: TypeAlias = (
    HeadingImportBlock
    | RichTextImportBlock
    | FAQImportBlock
    | ChecklistImportBlock
    | CodeImportBlock
    | EmbedSharingImportBlock
    | CalloutImportBlock
    | SourceLinkImportBlock
    | LinkGroupImportBlock
    | InternalLinkImportBlock
    | ImageImportBlock
    | ImageComparisonImportBlock
)


@dataclass(frozen=True, slots=True)
class ImportArticle:
    title: str
    slug: str
    type: str
    summary: str
    author: ImportReference
    seo: ImportSEO
    category: ImportReference
    tags: tuple[ImportReference, ...]
    publication_sites: tuple[str, ...]
    canonical_site: str | None
    featured_image: str | None
    related_articles: tuple[ImportReference, ...]
    blocks: tuple[ImportBlock, ...]


@dataclass(frozen=True, slots=True)
class ParsedBlogImport:
    """Immutable, ORM-free normalized data for one valid import package."""

    format: str
    version: int
    article: ImportArticle
    assets: tuple[ImportAsset, ...]
    comparisons: tuple[ImportComparison, ...]

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible copy for staging in a later workflow."""

        return {
            'format': self.format,
            'version': self.version,
            'article': _article_to_dict(self.article),
            'assets': [_asset_to_dict(asset) for asset in self.assets],
            'comparisons': [_comparison_to_dict(comparison) for comparison in self.comparisons],
        }


@dataclass(frozen=True, slots=True)
class ImportParseResult:
    parsed: ParsedBlogImport | None
    issues: tuple[ImportIssue, ...]

    @property
    def valid(self) -> bool:
        return self.parsed is not None and not self.issues

    @property
    def data(self) -> ParsedBlogImport | None:
        return self.parsed

    @property
    def errors(self) -> tuple[ImportIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == 'error')


ValidationResult = ImportParseResult


BLOCK_MODEL_NAMES = (
    'BlogHeadingBlock',
    'BlogRichTextBlock',
    'BlogFAQBlock',
    'BlogChecklistBlock',
    'BlogCodeBlock',
    'BlogEmbedSharingBlock',
    'BlogCalloutBlock',
    'BlogSourceLinkBlock',
    'BlogLinkGroupBlock',
    'BlogInternalLinkBlock',
    'BlogImageBlock',
    'BlogImageComparisonBlock',
)


def _location(path: tuple[str | int, ...] | list[str | int]) -> str:
    result = '$' if not path else ''
    for part in path:
        if isinstance(part, int):
            result += f'[{part}]'
        else:
            result += f"{'.' if result else ''}{part}"
    return result


def _issue(code: str, path: tuple[str | int, ...] | list[str | int], message: str) -> ImportIssue:
    return ImportIssue(code=code, location=_location(path), message=message)


@lru_cache(maxsize=len(SUPPORTED_CONTRACT_VERSIONS))
def _schema_validator(version: int) -> Draft202012Validator:
    with SCHEMA_PATHS[version].open(encoding='utf-8') as schema_file:
        schema = json.load(schema_file)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result


def _read_source(source: bytes | bytearray | memoryview | str | BinaryIO) -> bytes | ImportIssue:
    if isinstance(source, str):
        try:
            raw = source.encode('utf-8')
        except UnicodeEncodeError:
            return _issue('invalid_utf8', (), 'The article file must be valid UTF-8.')
    elif isinstance(source, (bytes, bytearray, memoryview)):
        raw = bytes(source)
    elif hasattr(source, 'read'):
        raw = source.read(MAX_SOURCE_BYTES + 1)
        if not isinstance(raw, bytes):
            return _issue('invalid_source_type', (), 'The article file could not be read safely.')
    else:
        return _issue('invalid_source_type', (), 'The article file must be UTF-8 JSON data.')

    if len(raw) > MAX_SOURCE_BYTES:
        return _issue('source_too_large', (), 'The article JSON file is too large.')
    return raw


def parse_blog_import(source: bytes | bytearray | memoryview | str | BinaryIO) -> ImportParseResult:
    """Parse and validate one bounded UTF-8 import document."""

    raw = _read_source(source)
    if isinstance(raw, ImportIssue):
        return ImportParseResult(None, (raw,))

    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return ImportParseResult(None, (_issue('invalid_utf8', (), 'The article file must be valid UTF-8.'),))

    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except _DuplicateKeyError:
        issue = _issue('duplicate_key', (), 'The article JSON file contains a duplicate field.')
        return ImportParseResult(None, (issue,))
    except (json.JSONDecodeError, RecursionError):
        issue = _issue('malformed_json', (), 'The article file is not valid JSON.')
        return ImportParseResult(None, (issue,))

    return validate_blog_import(payload)


def parse_blog_import_json(source: bytes | bytearray | memoryview | str | BinaryIO) -> ImportParseResult:
    """Compatibility spelling for callers that name the JSON boundary."""

    return parse_blog_import(source)


def validate_blog_import(payload: object) -> ImportParseResult:
    """Validate an already-decoded JSON object without performing file I/O."""

    if not isinstance(payload, dict):
        return ImportParseResult(None, (_issue('root_not_object', (), 'The article JSON root must be an object.'),))

    schema_issues = _schema_issues(payload)
    if schema_issues:
        return ImportParseResult(None, tuple(schema_issues))
    integer_issues = _strict_integer_issues(payload)
    if integer_issues:
        return ImportParseResult(None, tuple(integer_issues))

    issues: list[ImportIssue] = []
    _validate_taxonomy_references(payload, issues)
    _validate_local_ids_and_references(payload, issues)
    _validate_image_paths(payload, issues)
    _validate_content_references(payload, issues)
    if issues:
        return ImportParseResult(None, tuple(issues))

    _assert_block_model_alignment()
    normalized = _normalize_payload(payload, issues)
    if issues or normalized is None:
        return ImportParseResult(None, tuple(issues))
    return ImportParseResult(normalized, ())


def _schema_issues(payload: dict[str, Any]) -> list[ImportIssue]:
    version = payload.get('version')
    if type(version) is not int or version not in SUPPORTED_CONTRACT_VERSIONS:
        return [_issue('unsupported_version', ('version',), 'The import version is not supported.')]
    errors = sorted(
        _schema_validator(version).iter_errors(payload),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            tuple(str(part) for part in error.absolute_schema_path),
        ),
    )
    issues: list[ImportIssue] = []
    seen: set[tuple[str, str]] = set()
    for error in errors:
        issue = _schema_error_to_issue(error)
        key = (issue.code, issue.location)
        if key not in seen:
            issues.append(issue)
            seen.add(key)
    return issues


def _strict_integer_issues(payload: dict[str, Any]) -> list[ImportIssue]:
    """Reject JSON numeric spellings that would normalize to Python floats."""

    issues: list[ImportIssue] = []
    if payload['version'] in SUPPORTED_CONTRACT_VERSIONS and type(payload['version']) is not int:
        issues.append(_issue('unsupported_version', ('version',), 'The import version is not supported.'))
    for index, block in enumerate(payload['article']['blocks']):
        if block['type'] == 'heading' and block['level'] in {2, 3} and type(block['level']) is not int:
            issues.append(
                _issue(
                    'invalid_type',
                    ('article', 'blocks', index, 'level'),
                    'The value has the wrong type.',
                )
            )
    return issues


def _schema_error_to_issue(error: SchemaValidationError) -> ImportIssue:
    path = tuple(error.absolute_path)
    validator = error.validator
    if validator == 'oneOf':
        block_type = error.instance.get('type') if isinstance(error.instance, dict) else None
        if block_type not in BLOCK_DISPATCH:
            return _issue('unsupported_block_type', path, 'The article contains an unsupported content block.')
        branch_index = tuple(BLOCK_DISPATCH).index(block_type)
        matching_context = [
            nested
            for nested in _schema_error_context(error)
            if _schema_branch_index(nested) == branch_index
        ]
        for nested in matching_context:
            if nested.validator == 'additionalProperties':
                return _issue('unknown_field', path, 'Unknown fields are not allowed.')
            if nested.validator == 'required':
                return _issue('missing_required_field', tuple(nested.absolute_path), 'A required field is missing.')
        for nested in matching_context:
            if nested.validator in {'type', 'enum', 'const', 'format', 'minItems', 'minLength', 'maxLength', 'pattern', 'uniqueItems'}:
                return _schema_error_to_issue(nested)
        return _issue('invalid_block', path, 'The content block does not match its contract.')
    if path == ('format',) and validator == 'const':
        return _issue('unsupported_format', path, 'The import format is not supported.')
    if path == ('version',) and validator == 'const':
        return _issue('unsupported_version', path, 'The import version is not supported.')
    code_by_validator = {
        'additionalProperties': 'unknown_field',
        'required': 'missing_required_field',
        'type': 'invalid_type',
        'enum': 'invalid_choice',
        'const': 'invalid_value',
        'format': 'invalid_format',
        'minItems': 'too_few_items',
        'minLength': 'empty_or_short_value',
        'maxLength': 'value_too_long',
        'pattern': 'invalid_value',
        'uniqueItems': 'duplicate_value',
    }
    code = code_by_validator.get(validator, 'invalid_contract')
    messages = {
        'additionalProperties': 'Unknown fields are not allowed.',
        'required': 'A required field is missing.',
        'type': 'The value has the wrong type.',
        'enum': 'The value is not supported.',
        'const': 'The value is not supported.',
        'format': 'The value has an invalid format.',
        'minItems': 'Add at least one item.',
        'minLength': 'The value cannot be empty.',
        'maxLength': 'The value is too long.',
        'pattern': 'The value has an invalid format.',
        'uniqueItems': 'Values must be unique.',
    }
    return _issue(code, path, messages.get(validator, 'The value does not match the import contract.'))


def _schema_error_context(error: SchemaValidationError) -> list[SchemaValidationError]:
    result: list[SchemaValidationError] = []
    for nested in error.context:
        result.append(nested)
        result.extend(_schema_error_context(nested))
    return result


def _schema_branch_index(error: SchemaValidationError) -> int | None:
    schema_path = list(error.absolute_schema_path)
    for index in range(len(schema_path) - 1, -1, -1):
        if schema_path[index] == 'oneOf' and index + 1 < len(schema_path):
            branch = schema_path[index + 1]
            return branch if isinstance(branch, int) else None
    return None


def _validate_taxonomy_references(payload: dict[str, Any], issues: list[ImportIssue]) -> None:
    if payload['version'] != 2:
        return

    article = payload['article']
    references = [
        (article['category'], ('article', 'category', 'name')),
    ]
    references.extend(
        (tag, ('article', 'tags', index, 'name'))
        for index, tag in enumerate(article.get('tags', []))
    )
    for reference, path in references:
        if not reference['name'].strip():
            issues.append(
                _issue(
                    'empty_taxonomy_name',
                    path,
                    'Taxonomy names must contain visible text.',
                )
            )

    seen_tag_slugs = set()
    for index, tag in enumerate(article.get('tags', [])):
        if tag['slug'] in seen_tag_slugs:
            issues.append(
                _issue(
                    'duplicate_tag_slug',
                    ('article', 'tags', index, 'slug'),
                    'Tag slugs must be unique within one import.',
                )
            )
        seen_tag_slugs.add(tag['slug'])


def _validate_local_ids_and_references(payload: dict[str, Any], issues: list[ImportIssue]) -> None:
    assets = payload['assets']
    comparisons = payload['comparisons']
    asset_ids: dict[str, int] = {}
    comparison_ids: dict[str, int] = {}
    for index, asset in enumerate(assets):
        asset_id = asset['id']
        if asset_id in asset_ids:
            issues.append(_issue('duplicate_asset_id', ('assets', index, 'id'), 'Image asset IDs must be unique.'))
        else:
            asset_ids[asset_id] = index
        if not asset['name'].strip():
            issues.append(_issue('empty_asset_name', ('assets', index, 'name'), 'Image asset names cannot be empty.'))
        if asset.get('is_decorative', False):
            if asset['alt_text'].strip():
                issues.append(
                    _issue('decorative_alt_text', ('assets', index, 'alt_text'), 'Decorative images use empty alternative text.')
                )
        elif not asset['alt_text'].strip():
            issues.append(
                _issue('missing_alt_text', ('assets', index, 'alt_text'), 'Non-decorative images need alternative text.')
            )

    for index, comparison in enumerate(comparisons):
        comparison_id = comparison['id']
        if comparison_id in comparison_ids:
            issues.append(
                _issue('duplicate_comparison_id', ('comparisons', index, 'id'), 'Comparison IDs must be unique.')
            )
        else:
            comparison_ids[comparison_id] = index
        if not comparison['name'].strip():
            issues.append(_issue('empty_comparison_name', ('comparisons', index, 'name'), 'Comparison names cannot be empty.'))
        for side in ('first', 'second'):
            if not comparison[side]['alt_text'].strip():
                issues.append(
                    _issue(
                        'missing_alt_text',
                        ('comparisons', index, side, 'alt_text'),
                        'Comparison images need alternative text.',
                    )
                )

    article = payload['article']
    if article.get('featured_image') is not None and article['featured_image'] not in asset_ids:
        issues.append(
            _issue('unknown_asset_reference', ('article', 'featured_image'), 'The referenced image asset does not exist.')
        )
    for index, block in enumerate(article['blocks']):
        if block['type'] == 'image':
            asset_id = block['asset_id']
            if asset_id not in asset_ids:
                issues.append(
                    _issue('unknown_asset_reference', ('article', 'blocks', index, 'asset_id'), 'The referenced image asset does not exist.')
                )
            elif assets[asset_ids[asset_id]].get('is_decorative', False):
                issues.append(
                    _issue(
                        'decorative_body_image',
                        ('article', 'blocks', index, 'asset_id'),
                        'Decorative image assets cannot be used in body blocks.',
                    )
                )
        elif block['type'] == 'image_comparison' and block['comparison_id'] not in comparison_ids:
            issues.append(
                _issue(
                    'unknown_comparison_reference',
                    ('article', 'blocks', index, 'comparison_id'),
                    'The referenced image comparison does not exist.',
                )
            )


def _validate_image_paths(payload: dict[str, Any], issues: list[ImportIssue]) -> None:
    seen_basenames: dict[str, tuple[str | int, ...]] = {}
    paths: list[tuple[str, tuple[str | int, ...]]] = []
    for index, asset in enumerate(payload['assets']):
        paths.append((asset['file'], ('assets', index, 'file')))
    for index, comparison in enumerate(payload['comparisons']):
        paths.append((comparison['first']['file'], ('comparisons', index, 'first', 'file')))
        paths.append((comparison['second']['file'], ('comparisons', index, 'second', 'file')))

    for value, path in paths:
        path_issue = _image_path_issue(value, path)
        if path_issue:
            issues.append(path_issue)
            continue
        basename = value.rsplit('/', 1)[-1]
        previous_path = seen_basenames.get(basename)
        if previous_path is not None and value != _path_value(payload, previous_path):
            issues.append(
                _issue(
                    'ambiguous_image_basename',
                    path,
                    'Different image paths cannot share the same selected filename.',
                )
            )
        else:
            seen_basenames[basename] = path


def _path_value(payload: dict[str, Any], path: tuple[str | int, ...]) -> str:
    value: Any = payload
    for part in path:
        value = value[part]
    return value


def _image_path_issue(value: str, path: tuple[str | int, ...]) -> ImportIssue | None:
    if not value:
        return _issue('empty_image_path', path, 'Image paths cannot be empty.')
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return _issue('control_character_image_path', path, 'Image paths cannot contain control characters.')
    if '\\' in value:
        return _issue('backslash_image_path', path, 'Image paths must use POSIX separators.')
    if value.startswith('/'):
        return _issue('absolute_image_path', path, 'Image paths must be relative.')
    if '://' in value:
        return _issue('url_image_path', path, 'Image paths cannot be URLs.')
    try:
        parsed = urlsplit(value)
    except ValueError:
        return _issue('url_image_path', path, 'Image paths cannot be URLs.')
    if parsed.scheme:
        return _issue('url_image_path', path, 'Image paths cannot be URLs.')
    segments = value.split('/')
    if any(segment == '' for segment in segments):
        return _issue('empty_image_path_segment', path, 'Image paths cannot contain empty segments.')
    if any(segment in {'.', '..'} for segment in segments):
        return _issue('traversal_image_path', path, 'Image paths cannot contain traversal segments.')
    return None


def _validate_content_references(payload: dict[str, Any], issues: list[ImportIssue]) -> None:
    from .internal_links import validate_inline_internal_links, validate_internal_link_destination

    site_slugs = payload['article']['publication_sites']
    for index, block in enumerate(payload['article']['blocks']):
        path = ('article', 'blocks', index)
        if block['type'] == 'internal_link':
            try:
                validate_internal_link_destination(block['destination_key'], site_slugs)
            except ValidationError:
                issues.append(
                    _issue(
                        'invalid_internal_link_destination',
                        path + ('destination_key',),
                        'Choose an approved internal destination for every selected publication site.',
                    )
                )
        inline_values: list[tuple[str, tuple[str | int, ...]]] = []
        if block['type'] in {'rich_text', 'callout'}:
            inline_values.append((block['body'], path + ('body',)))
        elif block['type'] == 'faq':
            inline_values.extend(
                (item['answer'], path + ('items', item_index, 'answer'))
                for item_index, item in enumerate(block['items'])
            )
        for value, value_path in inline_values:
            try:
                validate_inline_internal_links(value, site_slugs)
            except ValidationError:
                issues.append(
                    _issue(
                        'invalid_inline_internal_link',
                        value_path,
                        'Use approved internal destinations for every selected publication site.',
                    )
                )


def _normalize_payload(payload: dict[str, Any], issues: list[ImportIssue]) -> ParsedBlogImport | None:
    article_payload = payload['article']
    sites = tuple(article_payload['publication_sites'])
    canonical_site = article_payload.get('canonical_site')
    if canonical_site is None:
        if len(sites) == 1:
            canonical_site = sites[0]
        else:
            issues.append(
                _issue(
                    'canonical_site_required',
                    ('article', 'canonical_site'),
                    'Choose a canonical site when more than one publication site is selected.',
                )
            )
    elif canonical_site not in sites:
        issues.append(
            _issue(
                'canonical_site_not_selected',
                ('article', 'canonical_site'),
                'The canonical site must be one of the publication sites.',
            )
        )

    blocks = _normalize_blocks(article_payload['blocks'], issues)
    if blocks is None:
        return None

    title = article_payload['title'].strip()
    if not title:
        issues.append(_issue('empty_title', ('article', 'title'), 'The article title cannot be empty.'))
    requested_slug = article_payload.get('slug')
    slug_candidate = (requested_slug or slugify(title) or 'article').strip('-')[:220] or 'article'
    seo_payload = article_payload.get('seo') or {}
    category_name = article_payload['category'].get('name')
    if category_name is not None:
        category_name = category_name.strip()
    article = ImportArticle(
        title=title,
        slug=slug_candidate,
        type=article_payload.get('type', 'article'),
        summary=article_payload['summary'].strip(),
        author=ImportReference(article_payload['author']['slug']),
        seo=ImportSEO(
            title=seo_payload.get('title', '').strip(),
            description=seo_payload.get('description', '').strip(),
        ),
        category=ImportReference(
            article_payload['category']['slug'],
            category_name,
        ),
        tags=tuple(
            ImportReference(
                item['slug'],
                item['name'].strip() if 'name' in item else None,
            )
            for item in article_payload.get('tags', [])
        ),
        publication_sites=sites,
        canonical_site=canonical_site,
        featured_image=article_payload.get('featured_image'),
        related_articles=tuple(
            ImportReference(item['slug']) for item in article_payload.get('related_articles', [])
        ),
        blocks=blocks,
    )
    assets = tuple(
        ImportAsset(
            id=asset['id'],
            file=asset['file'],
            name=asset['name'].strip(),
            alt_text=asset['alt_text'].strip(),
            is_decorative=asset.get('is_decorative', False),
            is_feature=asset.get('is_feature', False),
            caption_title=asset.get('caption_title', '').strip(),
            caption_text=asset.get('caption_text', '').strip(),
        )
        for asset in payload['assets']
    )
    comparisons = tuple(
        ImportComparison(
            id=comparison['id'],
            name=comparison['name'].strip(),
            first=ImportComparisonSide(
                file=comparison['first']['file'],
                alt_text=comparison['first']['alt_text'].strip(),
            ),
            second=ImportComparisonSide(
                file=comparison['second']['file'],
                alt_text=comparison['second']['alt_text'].strip(),
            ),
            caption_title=comparison.get('caption_title', '').strip(),
            caption_text=comparison.get('caption_text', '').strip(),
        )
        for comparison in payload['comparisons']
    )
    return ParsedBlogImport(
        format=payload['format'],
        version=payload['version'],
        article=article,
        assets=assets,
        comparisons=comparisons,
    )


def _normalize_blocks(blocks: list[dict[str, Any]], issues: list[ImportIssue]) -> tuple[ImportBlock, ...] | None:
    normalized: list[ImportBlock] = []
    for index, block in enumerate(blocks):
        parser = BLOCK_DISPATCH.get(block['type'])
        if parser is None:
            issues.append(
                _issue('unsupported_block_type', ('article', 'blocks', index, 'type'), 'The article contains an unsupported content block.')
            )
            continue
        try:
            normalized.append(parser(block, ('article', 'blocks', index), issues))
        except ValidationError:
            issues.append(
                _issue('invalid_block_content', ('article', 'blocks', index), 'The content block contains unsupported content.')
            )
    return tuple(normalized) if not issues else None


BlockParser: TypeAlias = Callable[[dict[str, Any], tuple[str | int, ...], list[ImportIssue]], ImportBlock]


def _normalize_heading(block: dict[str, Any], path: tuple[str | int, ...], issues: list[ImportIssue]) -> HeadingImportBlock:
    text = block['text'].strip()
    if not text:
        issues.append(_issue('empty_block_content', path + ('text',), 'The heading cannot be empty.'))
    return HeadingImportBlock(level=block['level'], text=text)


def _normalize_rich_text(block: dict[str, Any], path: tuple[str | int, ...], issues: list[ImportIssue]) -> RichTextImportBlock:
    body = _sanitize_rich_text(block['body'])
    if not _has_visible_content(body):
        issues.append(_issue('empty_block_content', path + ('body',), 'The block must contain visible content.'))
    return RichTextImportBlock(body=body)


def _normalize_faq(block: dict[str, Any], path: tuple[str | int, ...], issues: list[ImportIssue]) -> FAQImportBlock:
    from .faq import normalize_faq_items

    try:
        items = normalize_faq_items(block['items'])
    except ValidationError:
        issues.append(_issue('invalid_faq', path + ('items',), 'FAQ items do not match the Blog contract.'))
        return FAQImportBlock(items=())
    return FAQImportBlock(items=tuple(FAQImportItem(**item) for item in items))


def _normalize_checklist(block: dict[str, Any], path: tuple[str | int, ...], issues: list[ImportIssue]) -> ChecklistImportBlock:
    items = tuple(item.strip() for item in block['items'])
    if not any(items):
        issues.append(_issue('empty_block_content', path + ('items',), 'The checklist must contain an item.'))
    if any(strip_tags(item) != item for item in items):
        issues.append(_issue('invalid_checklist_item', path + ('items',), 'Checklist items must be plain text.'))
    return ChecklistImportBlock(marker=block.get('marker', 'checkmark'), items=items)


def _normalize_code(block: dict[str, Any], path: tuple[str | int, ...], issues: list[ImportIssue]) -> CodeImportBlock:
    code = block['code']
    if not code.strip():
        issues.append(_issue('empty_block_content', path + ('code',), 'The code block cannot be empty.'))
    return CodeImportBlock(language=block.get('language', 'text'), code=code, caption=block.get('caption', '').strip())


def _normalize_embed_sharing(
    block: dict[str, Any], path: tuple[str | int, ...], issues: list[ImportIssue]
) -> EmbedSharingImportBlock:
    caption = block.get('caption', '').strip()
    if strip_tags(caption) != caption:
        issues.append(
            _issue(
                'invalid_embed_caption',
                path + ('caption',),
                'Embed captions must be plain text.',
            )
        )
    try:
        reference = normalize_embed_reference(block['platform'], block['url'])
    except InvalidEmbedReference:
        issues.append(
            _issue(
                'invalid_embed_url',
                path + ('url',),
                'Enter a valid URL from the selected platform.',
            )
        )
        canonical_url = block['url'].strip()
    else:
        canonical_url = reference.canonical_url
    return EmbedSharingImportBlock(
        platform=block['platform'],
        url=canonical_url,
        caption=caption,
    )


def _normalize_callout(block: dict[str, Any], path: tuple[str | int, ...], issues: list[ImportIssue]) -> CalloutImportBlock:
    body = _sanitize_rich_text(block['body'], callout=True)
    if not _has_visible_content(body):
        issues.append(_issue('empty_block_content', path + ('body',), 'The block must contain visible content.'))
    return CalloutImportBlock(
        callout_type=block.get('callout_type', 'note'),
        title=block.get('title', '').strip(),
        body=body,
    )


def _normalize_source_link(block: dict[str, Any], path: tuple[str | int, ...], issues: list[ImportIssue]) -> SourceLinkImportBlock:
    _validate_http_url(block['url'], path + ('url',), issues)
    return SourceLinkImportBlock(
        label=block.get('label', 'Source:').strip() or 'Source:',
        url=block['url'],
        note=block.get('note', '').strip(),
    )


def _normalize_link_group(block: dict[str, Any], path: tuple[str | int, ...], issues: list[ImportIssue]) -> LinkGroupImportBlock:
    links = tuple(
        ExternalLink(label=link['label'].strip(), url=link['url'])
        for link in block['links']
    )
    if not block['label'].strip():
        issues.append(_issue('empty_block_label', path + ('label',), 'The link-group label cannot be empty.'))
    for index, link in enumerate(links):
        if not link.label:
            issues.append(_issue('empty_link_label', path + ('links', index, 'label'), 'Link labels cannot be empty.'))
        _validate_http_url(link.url, path + ('links', index, 'url'), issues)
    return LinkGroupImportBlock(label=block['label'].strip(), links=links)


def _normalize_internal_link(
    block: dict[str, Any], path: tuple[str | int, ...], issues: list[ImportIssue]
) -> InternalLinkImportBlock:
    label = ' '.join(block['label'].casefold().split())
    if not block['label'].strip():
        issues.append(_issue('empty_block_label', path + ('label',), 'The internal-link label cannot be empty.'))
    if label in {'click here', 'here', 'read more'}:
        issues.append(_issue('generic_internal_link_label', path + ('label',), 'Use descriptive internal-link text.'))
    return InternalLinkImportBlock(
        destination_key=block['destination_key'],
        label=block['label'].strip(),
        note=block.get('note', '').strip(),
    )


def _normalize_image(block: dict[str, Any], path: tuple[str | int, ...], issues: list[ImportIssue]) -> ImageImportBlock:
    return ImageImportBlock(asset_id=block['asset_id'], is_expandable=block.get('is_expandable', True))


def _normalize_image_comparison(
    block: dict[str, Any], path: tuple[str | int, ...], issues: list[ImportIssue]
) -> ImageComparisonImportBlock:
    return ImageComparisonImportBlock(comparison_id=block['comparison_id'])


BLOCK_DISPATCH: dict[str, BlockParser] = {
    'heading': _normalize_heading,
    'rich_text': _normalize_rich_text,
    'faq': _normalize_faq,
    'checklist': _normalize_checklist,
    'code': _normalize_code,
    'embed_sharing': _normalize_embed_sharing,
    'callout': _normalize_callout,
    'source_link': _normalize_source_link,
    'link_group': _normalize_link_group,
    'internal_link': _normalize_internal_link,
    'image': _normalize_image,
    'image_comparison': _normalize_image_comparison,
}


def _assert_block_model_alignment() -> None:
    from .models import BLOG_BLOCK_MODELS

    actual_names = tuple(model.__name__ for model in BLOG_BLOCK_MODELS)
    if actual_names != BLOCK_MODEL_NAMES:
        raise RuntimeError('Blog import block dispatch is out of alignment with BLOG_BLOCK_MODELS.')


def _sanitize_rich_text(value: str, *, callout: bool = False) -> str:
    from .models import BlogCalloutBlock, sanitize_rich_text

    sanitizer = sanitize_rich_text
    if callout:
        field = BlogCalloutBlock._meta.get_field('body')
        sanitizer = getattr(field, 'sanitize', None) or sanitizer
    return sanitizer(value)


def _has_visible_content(value: str) -> bool:
    text = unescape(strip_tags(value or '')).replace('\xa0', ' ')
    return bool(text.strip())


def _validate_http_url(value: str, path: tuple[str | int, ...], issues: list[ImportIssue]) -> None:
    try:
        URLValidator(schemes=['http', 'https'])(value)
    except ValidationError:
        issues.append(_issue('invalid_http_url', path, 'Use an absolute HTTP(S) URL.'))
        return
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        issues.append(_issue('invalid_http_url', path, 'Use an absolute HTTP(S) URL.'))


def _article_to_dict(article: ImportArticle) -> dict[str, Any]:
    def taxonomy_reference(reference):
        value = {'slug': reference.slug}
        if reference.name is not None:
            value['name'] = reference.name
        return value

    return {
        'title': article.title,
        'slug': article.slug,
        'type': article.type,
        'summary': article.summary,
        'author': {'slug': article.author.slug},
        'seo': {'title': article.seo.title, 'description': article.seo.description},
        'category': taxonomy_reference(article.category),
        'tags': [taxonomy_reference(tag) for tag in article.tags],
        'publication_sites': list(article.publication_sites),
        'canonical_site': article.canonical_site,
        'featured_image': article.featured_image,
        'related_articles': [{'slug': item.slug} for item in article.related_articles],
        'blocks': [_block_to_dict(block) for block in article.blocks],
    }


def _asset_to_dict(asset: ImportAsset) -> dict[str, Any]:
    return {
        'id': asset.id,
        'file': asset.file,
        'name': asset.name,
        'alt_text': asset.alt_text,
        'is_decorative': asset.is_decorative,
        'is_feature': asset.is_feature,
        'caption_title': asset.caption_title,
        'caption_text': asset.caption_text,
    }


def _comparison_to_dict(comparison: ImportComparison) -> dict[str, Any]:
    return {
        'id': comparison.id,
        'name': comparison.name,
        'first': {'file': comparison.first.file, 'alt_text': comparison.first.alt_text},
        'second': {'file': comparison.second.file, 'alt_text': comparison.second.alt_text},
        'caption_title': comparison.caption_title,
        'caption_text': comparison.caption_text,
    }


def _block_to_dict(block: ImportBlock) -> dict[str, Any]:
    if isinstance(block, HeadingImportBlock):
        return {'type': block.type, 'level': block.level, 'text': block.text}
    if isinstance(block, RichTextImportBlock):
        return {'type': block.type, 'body': block.body}
    if isinstance(block, FAQImportBlock):
        return {
            'type': block.type,
            'items': [{'question': item.question, 'answer': item.answer} for item in block.items],
        }
    if isinstance(block, ChecklistImportBlock):
        return {'type': block.type, 'marker': block.marker, 'items': list(block.items)}
    if isinstance(block, CodeImportBlock):
        return {'type': block.type, 'language': block.language, 'code': block.code, 'caption': block.caption}
    if isinstance(block, EmbedSharingImportBlock):
        return {
            'type': block.type,
            'platform': block.platform,
            'url': block.url,
            'caption': block.caption,
        }
    if isinstance(block, CalloutImportBlock):
        return {'type': block.type, 'callout_type': block.callout_type, 'title': block.title, 'body': block.body}
    if isinstance(block, SourceLinkImportBlock):
        return {'type': block.type, 'label': block.label, 'url': block.url, 'note': block.note}
    if isinstance(block, LinkGroupImportBlock):
        return {
            'type': block.type,
            'label': block.label,
            'links': [{'label': link.label, 'url': link.url} for link in block.links],
        }
    if isinstance(block, InternalLinkImportBlock):
        return {
            'type': block.type,
            'destination_key': block.destination_key,
            'label': block.label,
            'note': block.note,
        }
    if isinstance(block, ImageImportBlock):
        return {'type': block.type, 'asset_id': block.asset_id, 'is_expandable': block.is_expandable}
    if isinstance(block, ImageComparisonImportBlock):
        return {'type': block.type, 'comparison_id': block.comparison_id}
    raise TypeError(f'Unsupported normalized block: {type(block).__name__}')
