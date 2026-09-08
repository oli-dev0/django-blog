from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from datetime import timedelta
import json
import logging
from pathlib import PurePosixPath
import re
from typing import Any, Iterable, Mapping
from uuid import UUID

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.db.models.functions import Lower, Trim
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.core.sites import get_blog_site_slug_choices

from .import_contract import ImportIssue, ParsedBlogImport, parse_blog_import, validate_blog_import
from .image_services import (
    IMAGE_FILE_SUFFIXES,
    process_comparison_image,
    process_image,
    validate_image_bytes,
)
from .models import BlogArticleImport, BlogArticleImportFile, blog_import_upload_path
from .models import (
    AuthorProfile,
    BlogCalloutBlock,
    BlogCategory,
    BlogCategorySite,
    BlogChecklistBlock,
    BlogCodeBlock,
    BlogEmbedSharingBlock,
    BlogFAQBlock,
    BlogHeadingBlock,
    BlogImage,
    BlogImageBlock,
    BlogImageComparison,
    BlogImageComparisonBlock,
    BlogInternalLinkBlock,
    BlogLinkGroupBlock,
    BlogPost,
    BlogPostPublication,
    BlogPostRelated,
    BlogRichTextBlock,
    BlogSourceLinkBlock,
    BlogTag,
    BlogTagSite,
    BlogSite,
)
from .selectors import get_publication_site_slugs


logger = logging.getLogger(__name__)

DEFAULT_CLEANUP_BATCH_SIZE = 100
MAX_CLEANUP_BATCH_SIZE = 1000
_SAFE_STAGED_FILENAME = re.compile(r'^[^/\\]+$')


class BlogImportUnavailable(Exception):
    """The requested staging session cannot be used as a pending import."""


@dataclass(frozen=True)
class ImportCleanupResult:
    rows_deleted: int = 0
    files_deleted: int = 0
    file_failures: int = 0


@dataclass(frozen=True)
class ImportCleanupSummary:
    expired_deleted: int = 0
    consumed_deleted: int = 0
    files_deleted: int = 0
    file_failures: int = 0


def _require_authenticated(actor):
    if actor is None or not getattr(actor, 'is_authenticated', False):
        raise PermissionDenied


def _as_uuid(import_id) -> UUID:
    try:
        return import_id if isinstance(import_id, UUID) else UUID(str(import_id))
    except (AttributeError, TypeError, ValueError) as error:
        raise BlogImportUnavailable from error


def _selected_basename(name: str) -> str:
    normalized = str(name or '').replace('\\', '/')
    basename = PurePosixPath(normalized).name
    if (
        not basename
        or basename in {'.', '..'}
        or not _SAFE_STAGED_FILENAME.fullmatch(basename)
        or any(ord(character) < 32 for character in basename)
        or len(basename) > 255
    ):
        raise ValueError('Selected files must have safe, bounded basenames.')
    return basename


def _normalized_payload(payload: Any) -> dict[str, Any]:
    if hasattr(payload, 'as_dict'):
        payload = payload.as_dict()
    if not isinstance(payload, dict):
        raise TypeError('The staged payload must be a JSON object.')
    try:
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise TypeError('The staged payload must contain JSON-compatible values.') from error
    return deepcopy(payload)


def _normalized_warnings(warnings: Iterable[Any] | None) -> list[dict[str, Any]]:
    normalized = []
    for warning in warnings or ():
        if is_dataclass(warning):
            warning = asdict(warning)
        elif isinstance(warning, Mapping):
            warning = dict(warning)
        else:
            raise TypeError('Staging warnings must be structured JSON records.')
        try:
            json.dumps(warning, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise TypeError('Staging warnings must contain JSON-compatible values.') from error
        normalized.append(deepcopy(warning))
    return normalized


def _private_storage():
    return storages['blog_imports']


def _remove_stage_directory(import_id, storage) -> bool:
    directory_name = f'{_as_uuid(import_id)}/'
    try:
        storage.delete(directory_name)
    except Exception as error:
        logger.warning('Blog import directory cleanup failed: %s (%s)', import_id, type(error).__name__)
        return False
    return True


def is_safe_import_path(import_id, name: str) -> bool:
    """Return whether a stored name is directly below one import UUID."""

    try:
        import_uuid = str(_as_uuid(import_id))
    except BlogImportUnavailable:
        return False
    path = PurePosixPath(str(name or ''))
    return (
        len(path.parts) == 2
        and path.parts[0] == import_uuid
        and path.parts[1] not in {'.', '..'}
        and not any(part in {'.', '..'} for part in path.parts)
    )


def stage_import(*, actor, source_filename, payload, warnings=None, files=(), now=None):
    """Copy a validated package into one owner-bound staging session."""

    _require_authenticated(actor)
    source_filename = _selected_basename(source_filename)
    normalized_payload = _normalized_payload(payload)
    normalized_warnings = _normalized_warnings(warnings)
    selected_files = list(files)
    selected_names = [_selected_basename(getattr(upload, 'name', '')) for upload in selected_files]
    if len(selected_names) != len(set(selected_names)):
        raise ValueError('Selected files must have unique basenames.')

    now = now or timezone.now()
    retention_hours = getattr(settings, 'BLOG_IMPORT_RETENTION_HOURS', 24)
    if retention_hours <= 0:
        raise ValueError('BLOG_IMPORT_RETENTION_HOURS must be positive.')

    saved_names: list[str] = []
    attempted_names: list[str] = []
    import_session_id = None
    storage = _private_storage()
    try:
        with transaction.atomic():
            import_session = BlogArticleImport.objects.create(
                created_by=actor,
                source_filename=source_filename,
                payload=normalized_payload,
                warnings=normalized_warnings,
                expires_at=now + timedelta(hours=retention_hours),
            )
            import_session_id = import_session.id
            for selected_name, upload in zip(selected_names, selected_files):
                staged_file = BlogArticleImportFile(
                    import_session=import_session,
                    selected_name=selected_name,
                )
                generated_name = blog_import_upload_path(staged_file, selected_name)
                attempted_names.append(generated_name)
                stored_name = storage.save(generated_name, upload)
                attempted_names.append(stored_name)
                if not is_safe_import_path(import_session.id, stored_name):
                    storage.delete(stored_name)
                    raise ValueError('Generated staging path escaped its import directory.')
                staged_file.file.name = stored_name
                staged_file.save(force_insert=True)
                saved_names.append(stored_name)
    except Exception as error:
        for name in {*saved_names, *attempted_names}:
            try:
                if is_safe_import_path(import_session_id, name):
                    storage.delete(name)
            except Exception:
                logger.warning('Blog import staging cleanup failed: %s', type(error).__name__)
        if import_session_id is not None:
            _remove_stage_directory(import_session_id, storage)
        raise
    return import_session


def _get_import(import_id, *, actor=None, allow_expired=True):
    import_uuid = _as_uuid(import_id)
    try:
        import_session = BlogArticleImport.objects.get(pk=import_uuid)
    except BlogArticleImport.DoesNotExist as error:
        raise BlogImportUnavailable from error
    if actor is not None and import_session.created_by_id != getattr(actor, 'pk', None):
        raise PermissionDenied
    if not allow_expired and import_session.expires_at <= timezone.now():
        raise BlogImportUnavailable
    return import_session


def get_pending_import(*, actor, import_id, now=None):
    """Load a stage only while it is owned, unexpired, and unconsumed."""

    _require_authenticated(actor)
    import_session = _get_import(import_id, actor=actor, allow_expired=True)
    now = now or timezone.now()
    if import_session.completed_post_id or import_session.consumed_at or import_session.expires_at <= now:
        raise BlogImportUnavailable
    return import_session


def mark_import_consumed(*, actor, import_id, completed_post):
    """Mark a pending stage as consumed after its permanent draft is committed."""

    import_session = get_pending_import(actor=actor, import_id=import_id)
    if not getattr(completed_post, 'pk', None):
        raise ValueError('A saved BlogPost is required to consume an import.')
    import_session.completed_post = completed_post
    import_session.consumed_at = timezone.now()
    import_session.save(update_fields=['completed_post', 'consumed_at'])
    transaction.on_commit(lambda: cleanup_staged_import(import_session.id))
    return import_session


def _delete_stage_files(import_session, files):
    storage = _private_storage()
    deleted = 0
    failures = 0
    for staged_file in files:
        name = staged_file.file.name
        if not is_safe_import_path(import_session.id, name):
            failures += 1
            logger.warning('Blog import file path rejected during cleanup: %s', import_session.id)
            continue
        try:
            storage.delete(name)
        except Exception as error:
            failures += 1
            logger.warning('Blog import file cleanup failed: %s (%s)', import_session.id, type(error).__name__)
        else:
            deleted += 1
    if not failures and not _remove_stage_directory(import_session.id, storage):
        failures += 1
    return deleted, failures


def cleanup_staged_import(import_id) -> ImportCleanupResult:
    """Delete one stage and its files, retaining rows when file cleanup fails."""

    import_uuid = _as_uuid(import_id)
    with transaction.atomic():
        import_session = (
            BlogArticleImport.objects.select_for_update()
            .prefetch_related('files')
            .filter(pk=import_uuid)
            .first()
        )
        if import_session is None:
            return ImportCleanupResult()
        permanent_paths, permanent_failures = _cleanup_persisted_permanent_media(import_session)
        if permanent_failures:
            import_session.permanent_cleanup_paths = permanent_paths
            import_session.save(update_fields=['permanent_cleanup_paths'])
        elif import_session.permanent_cleanup_paths:
            import_session.permanent_cleanup_paths = []
            import_session.save(update_fields=['permanent_cleanup_paths'])
        files_deleted, file_failures = _delete_stage_files(import_session, list(import_session.files.all()))
        if file_failures or permanent_failures:
            return ImportCleanupResult(
                files_deleted=files_deleted,
                file_failures=file_failures + permanent_failures,
            )
        import_session.delete()
        return ImportCleanupResult(rows_deleted=1, files_deleted=files_deleted)


def discard_staged_import(*, actor, import_id):
    """Discard an owned pending stage and remove its private files."""

    _require_authenticated(actor)
    import_session = _get_import(import_id, actor=actor, allow_expired=True)
    if import_session.completed_post_id or import_session.consumed_at:
        raise BlogImportUnavailable
    result = cleanup_staged_import(import_session.id)
    if result.file_failures:
        raise OSError('One or more staged files could not be removed.')
    return result


def cleanup_staged_imports(*, batch_size=None, now=None) -> ImportCleanupSummary:
    """Clean at most one bounded batch of expired or consumed sessions."""

    if batch_size is None:
        batch_size = getattr(settings, 'BLOG_IMPORT_CLEANUP_BATCH_SIZE', DEFAULT_CLEANUP_BATCH_SIZE)
    if not 1 <= batch_size <= MAX_CLEANUP_BATCH_SIZE:
        raise ValueError(f'batch_size must be between 1 and {MAX_CLEANUP_BATCH_SIZE}.')
    now = now or timezone.now()
    candidates = list(
        BlogArticleImport.objects.filter(
            models.Q(expires_at__lte=now)
            | models.Q(consumed_at__isnull=False)
            | models.Q(completed_post__isnull=False)
        )
        .order_by('expires_at', 'id')[:batch_size]
    )
    expired_deleted = 0
    consumed_deleted = 0
    files_deleted = 0
    file_failures = 0
    for candidate in candidates:
        was_consumed = candidate.consumed_at is not None or candidate.completed_post_id is not None
        result = cleanup_staged_import(candidate.id)
        files_deleted += result.files_deleted
        file_failures += result.file_failures
        if result.rows_deleted:
            if was_consumed:
                consumed_deleted += 1
            else:
                expired_deleted += 1
    return ImportCleanupSummary(
        expired_deleted=expired_deleted,
        consumed_deleted=consumed_deleted,
        files_deleted=files_deleted,
        file_failures=file_failures,
    )


# Package-review limits are deliberately kept here, beside the staging
# boundary, so callers that do not use the upload form receive the same gates.
MAX_IMAGE_FILE_COUNT = 50
MAX_IMAGE_AGGREGATE_BYTES = 150 * 1024 * 1024
MAX_IMPORT_WARNINGS = 100
MAX_DUPLICATE_MATCHES = 20
MAX_IMPORT_SLUG_ATTEMPTS = 3

_MEDIA_FILE_SUFFIXES = IMAGE_FILE_SUFFIXES
MAX_PERMANENT_CLEANUP_PATHS = 1000
_PERMANENT_MEDIA_ROOTS = (
    ('blog', 'originals'),
    ('blog', 'renditions'),
    ('blog', 'comparisons', 'originals'),
    ('blog', 'comparisons', 'renditions'),
)


class BlogImportError(Exception):
    """Base error for safe, form-displayable import failures."""


class BlogImportValidationError(BlogImportError):
    def __init__(self, issues: Iterable[ImportIssue]):
        self.issues = tuple(issues)
        super().__init__('The Blog import package could not be validated.')


class BlogImportPermissionError(PermissionDenied):
    """A package-wide permission failure with no partial-import fallback."""

    def __init__(self, missing_permissions: Iterable[str]):
        self.missing_permissions = tuple(sorted(set(missing_permissions)))
        super().__init__('The account is missing a required Blog import permission.')


@dataclass(frozen=True, slots=True)
class ImportReferenceResolution:
    author: AuthorProfile | None
    category: BlogCategory | None
    tags: tuple[BlogTag, ...]
    resolved_publication_sites: tuple[str, ...]
    resolved_canonical_site: str | None
    related_posts: tuple[BlogPost | None, ...]
    unresolved: tuple[ImportIssue, ...] = ()

    @property
    def resolved_author(self):
        return self.author

    @property
    def resolved_category(self):
        return self.category

    @property
    def resolved_tags(self):
        return self.tags

    @property
    def publication_sites(self):
        return self.resolved_publication_sites


@dataclass(frozen=True, slots=True)
class ImportImageStatus:
    selected_name: str
    source_locations: tuple[str, ...]
    valid: bool
    size: int = 0
    message: str = ''


@dataclass(frozen=True, slots=True)
class ImportDuplicateMatch:
    title: str
    slug: str
    status: str
    change_url: str = ''


@dataclass(frozen=True, slots=True)
class BlogImportReview:
    import_session: BlogArticleImport
    parsed: ParsedBlogImport
    references: ImportReferenceResolution
    image_statuses: tuple[ImportImageStatus, ...]
    issues: tuple[ImportIssue, ...]
    warnings: tuple[ImportIssue, ...]
    duplicate_matches: tuple[ImportDuplicateMatch, ...] = ()

    @property
    def valid(self):
        return not any(issue.severity == 'error' for issue in self.issues)

    @property
    def errors(self):
        return tuple(issue for issue in self.issues if issue.severity == 'error')


@dataclass(frozen=True, slots=True)
class ReviewedImportReferences:
    author: AuthorProfile | None
    category: BlogCategory | None
    tags: tuple[BlogTag, ...]
    publication_sites: tuple[str, ...]
    canonical_site: str
    invalid_tag_choices: tuple[Any, ...] = ()
    create_category: bool = False
    create_tags: tuple[str, ...] = ()
    expand_taxonomy_websites: bool = False


@dataclass(frozen=True, slots=True)
class ReviewedImportValidation:
    references: ReviewedImportReferences | None
    issues: tuple[ImportIssue, ...]
    warnings: tuple[ImportIssue, ...] = ()
    duplicate_matches: tuple[ImportDuplicateMatch, ...] = ()

    @property
    def valid(self):
        return self.references is not None and not self.issues


_BLOCK_MODELS_BY_TYPE = {
    'heading': BlogHeadingBlock,
    'rich_text': BlogRichTextBlock,
    'faq': BlogFAQBlock,
    'checklist': BlogChecklistBlock,
    'code': BlogCodeBlock,
    'embed_sharing': BlogEmbedSharingBlock,
    'callout': BlogCalloutBlock,
    'source_link': BlogSourceLinkBlock,
    'link_group': BlogLinkGroupBlock,
    'internal_link': BlogInternalLinkBlock,
    'image': BlogImageBlock,
    'image_comparison': BlogImageComparisonBlock,
}


def required_blog_import_permissions(parsed: ParsedBlogImport) -> tuple[str, ...]:
    """Return the complete permission set needed for this package."""

    permissions = {
        'blog.add_blogpost',
        'blog.change_blogpost',
        'blog.organize_blogpost',
    }
    block_types = {block.type for block in parsed.article.blocks}
    for block_type in block_types:
        model = _BLOCK_MODELS_BY_TYPE[block_type]
        permissions.add(f'blog.add_{model._meta.model_name}')

    used_asset_ids = {asset_id for asset_id in (parsed.article.featured_image,) if asset_id}
    used_asset_ids.update(
        block.asset_id for block in parsed.article.blocks if block.type == 'image'
    )
    if used_asset_ids:
        permissions.add(f'blog.add_{BlogImage._meta.model_name}')

    used_comparison_ids = {
        block.comparison_id for block in parsed.article.blocks if block.type == 'image_comparison'
    }
    if used_comparison_ids:
        permissions.add(f'blog.add_{BlogImageComparison._meta.model_name}')

    if parsed.article.related_articles:
        permissions.add(f'blog.add_{BlogPostRelated._meta.model_name}')
    return tuple(sorted(permissions))


def require_blog_import_permissions(actor, parsed: ParsedBlogImport) -> tuple[str, ...]:
    _require_authenticated(actor)
    missing = [permission for permission in required_blog_import_permissions(parsed) if not actor.has_perm(permission)]
    if missing:
        raise BlogImportPermissionError(missing)
    return required_blog_import_permissions(parsed)


check_blog_import_permissions = require_blog_import_permissions


def _json_location(parts: Iterable[str | int]) -> str:
    result = ''
    for part in parts:
        if isinstance(part, int):
            result += f'[{part}]'
        else:
            result += f'{"." if result else ""}{part}'
    return result or '$'


def _dedupe_issues(issues: Iterable[ImportIssue], *, severity=None) -> tuple[ImportIssue, ...]:
    result = []
    seen = set()
    for issue in issues:
        if severity is not None and issue.severity != severity:
            continue
        key = (issue.code, issue.location, issue.message, issue.severity)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    if severity == 'warning':
        return tuple(result[:MAX_IMPORT_WARNINGS])
    return tuple(result)


def _validation_message(error: ValidationError) -> str:
    messages = error.messages if hasattr(error, 'messages') else [str(error)]
    return str(messages[0] if messages else _('The image could not be validated.'))


def _upload_size(upload) -> int:
    try:
        size = int(getattr(upload, 'size', 0) or 0)
    except (AttributeError, OSError, TypeError, ValueError):
        return -1
    return size


def _image_reference_locations(parsed: ParsedBlogImport):
    references: dict[str, list[str]] = {}
    used_assets = {asset_id for asset_id in (parsed.article.featured_image,) if asset_id}
    used_assets.update(block.asset_id for block in parsed.article.blocks if block.type == 'image')
    used_comparisons = {
        block.comparison_id for block in parsed.article.blocks if block.type == 'image_comparison'
    }
    for index, asset in enumerate(parsed.assets):
        if asset.id not in used_assets:
            continue
        references.setdefault(asset.file, []).append(_json_location(('assets', index, 'file')))
    for index, comparison in enumerate(parsed.comparisons):
        if comparison.id not in used_comparisons:
            continue
        for side in ('first', 'second'):
            references.setdefault(
                getattr(comparison, side).file,
                [],
            ).append(_json_location(('comparisons', index, side, 'file')))
    return references


def _media_reference_issues(parsed: ParsedBlogImport) -> tuple[ImportIssue, ...]:
    """Apply publication media rules that are outside the JSON schema."""

    assets_by_id = {asset.id: asset for asset in parsed.assets}
    issues = []
    featured_image = parsed.article.featured_image
    if featured_image:
        asset = assets_by_id.get(featured_image)
        if asset is not None and asset.is_decorative:
            issues.append(
                ImportIssue(
                    'decorative_featured_image',
                    'article.featured_image',
                    'Decorative image assets cannot be used as the featured image.',
                )
            )
    for index, block in enumerate(parsed.article.blocks):
        if block.type != 'image':
            continue
        asset = assets_by_id.get(block.asset_id)
        if asset is not None and asset.is_decorative:
            issues.append(
                ImportIssue(
                    'decorative_body_image',
                    _json_location(('article', 'blocks', index, 'asset_id')),
                    'Decorative image assets cannot be used in body blocks.',
                )
            )
    return _dedupe_issues(issues)


def _selected_name(upload_or_stage) -> str:
    name = getattr(upload_or_stage, 'selected_name', None)
    if name is None:
        name = getattr(upload_or_stage, 'name', '')
    return _selected_basename(name)


def _validation_file(upload_or_stage):
    if hasattr(upload_or_stage, 'selected_name'):
        return upload_or_stage.file
    return upload_or_stage


def _media_field_names(instance):
    if isinstance(instance, BlogImage):
        return tuple(_MEDIA_FILE_SUFFIXES)
    if isinstance(instance, BlogImageComparison):
        return tuple(
            f'{side}_{suffix}'
            for side in ('first', 'second')
            for suffix in _MEDIA_FILE_SUFFIXES
        )
    return ()


def _media_file_state(instance):
    state = {}
    for field_name in _media_field_names(instance):
        field = getattr(instance, field_name)
        # An assigned ContentFile has a name before storage.save() runs. Only
        # committed FieldFiles are permanent paths that rollback may delete.
        if field and field.name and field._committed:
            state[field_name] = (field.storage, field.name)
    return state


def _track_new_media_files(instance, previous_state, tracked_files):
    tracked_keys = {(id(storage), name) for storage, name in tracked_files}
    for field_name, (storage, name) in _media_file_state(instance).items():
        previous = previous_state.get(field_name)
        if previous is not None and previous[0] is storage and previous[1] == name:
            continue
        key = (id(storage), name)
        if key not in tracked_keys:
            tracked_files.append((storage, name))
            tracked_keys.add(key)


def _cleanup_new_media_files(tracked_files):
    failed_paths = []
    for storage, name in reversed(tracked_files):
        try:
            storage.delete(name)
        except Exception as error:
            logger.warning(
                'Blog import permanent media cleanup failed: %s (%s)',
                name,
                type(error).__name__,
            )
            if _is_safe_permanent_media_path(name):
                failed_paths.append(name)
    return tuple(dict.fromkeys(failed_paths))


def _is_safe_permanent_media_path(name):
    if not isinstance(name, str) or not name or len(name) > 100 or '\\' in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {'', '.', '..'} for part in path.parts):
        return False
    for prefix in _PERMANENT_MEDIA_ROOTS:
        if path.parts[:len(prefix)] == prefix and len(path.parts) == len(prefix) + 3:
            year, month, filename = path.parts[-3:]
            return (
                len(year) == 4
                and year.isdecimal()
                and len(month) == 2
                and month.isdecimal()
                and bool(filename)
            )
    return False


def _stored_permanent_cleanup_paths(import_session):
    raw_paths = import_session.permanent_cleanup_paths
    if not isinstance(raw_paths, list) or len(raw_paths) > MAX_PERMANENT_CLEANUP_PATHS:
        logger.warning('Blog import permanent cleanup metadata is invalid: %s', import_session.id)
        return [], 1
    safe_paths = []
    invalid_count = 0
    for path in raw_paths:
        if not _is_safe_permanent_media_path(path):
            invalid_count += 1
        elif path not in safe_paths:
            safe_paths.append(path)
    if invalid_count:
        logger.warning('Blog import permanent cleanup metadata contains unsafe paths: %s', import_session.id)
    return safe_paths, invalid_count


def _cleanup_persisted_permanent_media(import_session):
    raw_paths = import_session.permanent_cleanup_paths
    paths, failures = _stored_permanent_cleanup_paths(import_session)
    if not paths and not failures:
        return [], 0
    if not paths and failures:
        return list(raw_paths) if isinstance(raw_paths, list) else [], failures
    storage = storages['default']
    failed_paths = []
    for path in paths:
        try:
            storage.delete(path)
        except Exception as error:
            failures += 1
            failed_paths.append(path)
            logger.warning(
                'Blog import permanent media retry failed: %s (%s)',
                path,
                type(error).__name__,
            )
    if failures:
        return [
            *failed_paths,
            *[path for path in raw_paths if not _is_safe_permanent_media_path(path)],
        ], failures
    return [], 0


def _persist_permanent_cleanup_paths(import_id, paths):
    paths = list(dict.fromkeys(paths))
    if not paths:
        return
    if len(paths) > MAX_PERMANENT_CLEANUP_PATHS:
        raise RuntimeError('Blog import permanent cleanup metadata exceeded its bound.')
    try:
        BlogArticleImport.objects.filter(pk=import_id).update(permanent_cleanup_paths=paths)
    except Exception:
        logger.exception('Blog import permanent cleanup metadata could not be persisted: %s', import_id)


def _staged_files_by_name(import_session):
    files_by_name = {}
    for staged_file in import_session.files.all():
        try:
            selected_name = _selected_name(staged_file)
        except ValueError as error:
            raise BlogImportValidationError(
                (
                    ImportIssue(
                        'unsafe_staged_filename',
                        '$.image_files',
                        'The staged image filename is not safe.',
                    ),
                )
            ) from error
        if not is_safe_import_path(import_session.id, staged_file.file.name):
            raise BlogImportValidationError(
                (
                    ImportIssue(
                        'unsafe_staged_path',
                        '$.image_files',
                        'The staged image path is not safe.',
                    ),
                )
            )
        if selected_name in files_by_name:
            raise BlogImportValidationError(
                (
                    ImportIssue(
                        'duplicate_staged_filename',
                        '$.image_files',
                        'The staged image filenames are ambiguous.',
                    ),
                )
            )
        files_by_name[selected_name] = staged_file
    return files_by_name


def _copy_staged_image_file(staged_file, *, location):
    """Read a private staged file into an isolated upload for final processing."""

    try:
        staged_file.file.open('rb')
        try:
            content = staged_file.file.read()
        finally:
            staged_file.file.close()
    except (OSError, ValueError, TypeError) as error:
        raise BlogImportValidationError(
            (
                ImportIssue(
                    'invalid_image_file',
                    location,
                    'The staged image could not be read safely.',
                ),
            )
        ) from error

    copied_file = ContentFile(content, name=staged_file.selected_name)
    try:
        validate_image_bytes(copied_file)
    except (ValidationError, OSError, ValueError) as error:
        message = (
            _validation_message(error)
            if isinstance(error, ValidationError)
            else 'The staged image could not be read safely.'
        )
        raise BlogImportValidationError(
            (ImportIssue('invalid_image_file', location, message),)
        ) from error
    copied_file.seek(0)
    return copied_file


@dataclass(frozen=True, slots=True)
class _ImagePackageCheck:
    matched_files: tuple[Any, ...]
    statuses: tuple[ImportImageStatus, ...]
    issues: tuple[ImportIssue, ...]
    warnings: tuple[ImportIssue, ...]


def _check_image_package(parsed: ParsedBlogImport, image_files: Iterable[Any]) -> _ImagePackageCheck:
    selected_files = list(image_files or ())
    references = _image_reference_locations(parsed)
    reference_basenames = {path.rsplit('/', 1)[-1] for path in references}
    selected_by_name: dict[str, list[Any]] = {}
    issues: list[ImportIssue] = []
    warnings: list[ImportIssue] = []
    for index, upload in enumerate(selected_files):
        try:
            selected_by_name.setdefault(_selected_name(upload), []).append(upload)
        except ValueError:
            issues.append(
                ImportIssue(
                    'unsafe_selected_filename',
                    _json_location(('image_files', index)),
                    'Selected image filenames must have safe, bounded basenames.',
                )
            )

    for selected_name, matching_files in selected_by_name.items():
        if len(matching_files) <= 1:
            continue
        matching_locations = [
            location
            for image_path, locations in references.items()
            if image_path.rsplit('/', 1)[-1] == selected_name
            for location in locations
        ]
        if matching_locations:
            issues.extend(
                ImportIssue(
                    'ambiguous_selected_basename',
                    location,
                    'More than one selected image has this basename, so the reference is ambiguous.',
                )
                for location in matching_locations
            )
        else:
            issues.append(
                ImportIssue(
                    'duplicate_selected_basename',
                    '$.image_files',
                    'Selected image basenames must be unique.',
                )
            )

    if len(selected_files) > MAX_IMAGE_FILE_COUNT:
        issues.append(
            ImportIssue(
                'too_many_image_files',
                '$',
                f'Choose no more than {MAX_IMAGE_FILE_COUNT} local image files.',
            )
        )
    sizes = [_upload_size(upload) for upload in selected_files]
    if any(size < 0 for size in sizes):
        issues.append(ImportIssue('invalid_image_size', '$', 'The selected image files could not be measured.'))
    elif sum(sizes) > MAX_IMAGE_AGGREGATE_BYTES:
        issues.append(ImportIssue('image_files_too_large', '$', 'The selected image files are too large together.'))

    matched_files: list[Any] = []
    statuses: list[ImportImageStatus] = []
    for image_path, locations in references.items():
        basename = image_path.rsplit('/', 1)[-1]
        matching_files = selected_by_name.get(basename, [])
        if not matching_files:
            for location in locations:
                issues.append(
                    ImportIssue(
                        'missing_image_file',
                        location,
                        'The article references a local image that was not selected.',
                    )
                )
            continue
        if len(matching_files) > 1:
            for location in locations:
                issues.append(
                    ImportIssue(
                        'ambiguous_selected_basename',
                        location,
                        'More than one selected image has this basename, so the reference is ambiguous.',
                    )
                )
            continue

        upload = matching_files[0]
        image_file = _validation_file(upload)
        size = _upload_size(image_file)
        valid = True
        message = ''
        try:
            if hasattr(upload, 'selected_name'):
                image_file.open('rb')
                try:
                    validate_image_bytes(image_file)
                finally:
                    image_file.close()
            else:
                validate_image_bytes(image_file)
        except (ValidationError, OSError, ValueError) as error:
            valid = False
            message = _validation_message(error) if isinstance(error, ValidationError) else 'The image could not be read safely.'
            for location in locations:
                issues.append(ImportIssue('invalid_image_file', location, message))
        if valid:
            matched_files.append(upload)
        statuses.append(
            ImportImageStatus(
                selected_name=basename,
                source_locations=tuple(locations),
                valid=valid,
                size=max(size, 0),
                message=message,
            )
        )

    for selected_name in selected_by_name:
        if selected_name not in reference_basenames:
            warnings.append(
                ImportIssue(
                    'extra_image_file',
                    '$',
                    f'The selected image {selected_name!r} is not referenced by the article and will be ignored.',
                    'warning',
                )
            )

    return _ImagePackageCheck(
        matched_files=tuple(matched_files),
        statuses=tuple(statuses),
        issues=_dedupe_issues(issues),
        warnings=_dedupe_issues(warnings, severity='warning'),
    )


def _unused_definition_warnings(parsed: ParsedBlogImport) -> tuple[ImportIssue, ...]:
    used_assets = {asset_id for asset_id in (parsed.article.featured_image,) if asset_id}
    used_assets.update(block.asset_id for block in parsed.article.blocks if block.type == 'image')
    used_comparisons = {
        block.comparison_id for block in parsed.article.blocks if block.type == 'image_comparison'
    }
    warnings = [
        ImportIssue(
            'unused_asset_definition',
            _json_location(('assets', index, 'id')),
            f'Image asset {asset.id!r} is not used by the article and will be ignored.',
            'warning',
        )
        for index, asset in enumerate(parsed.assets)
        if asset.id not in used_assets
    ]
    warnings.extend(
        ImportIssue(
            'unused_comparison_definition',
            _json_location(('comparisons', index, 'id')),
            f'Image comparison {comparison.id!r} is not used by the article and will be ignored.',
            'warning',
        )
        for index, comparison in enumerate(parsed.comparisons)
        if comparison.id not in used_comparisons
    )
    return _dedupe_issues(warnings, severity='warning')


def _resolve_import_references(parsed: ParsedBlogImport) -> ImportReferenceResolution:
    article = parsed.article
    issues: list[ImportIssue] = []
    author = AuthorProfile.objects.filter(slug=article.author.slug).first()
    if author is None:
        issues.append(
            ImportIssue(
                'unresolved_author',
                'article.author.slug',
                f'No existing author matches {article.author.slug!r}. Choose an existing author during review.',
                'warning',
            )
        )
    category = BlogCategory.objects.filter(slug=article.category.slug).first()
    if category is None:
        issues.append(
            ImportIssue(
                'unresolved_category',
                'article.category.slug',
                f'No existing category matches {article.category.slug!r}. Choose an existing category during review.',
                'warning',
            )
        )
    elif article.category.name and category.name != article.category.name:
        issues.append(
            ImportIssue(
                'category_name_mismatch',
                'article.category.name',
                f'The existing category is named {category.name!r}; the package proposed {article.category.name!r}.',
                'warning',
            )
        )

    tag_slugs = [tag_reference.slug for tag_reference in article.tags]
    tags_by_slug = {
        tag.slug: tag
        for tag in BlogTag.objects.filter(slug__in=tag_slugs)
    }
    tags = []
    for index, tag_reference in enumerate(article.tags):
        tag = tags_by_slug.get(tag_reference.slug)
        if tag is None:
            issues.append(
                ImportIssue(
                    'unresolved_tag',
                    _json_location(('article', 'tags', index, 'slug')),
                    f'No existing tag matches {tag_reference.slug!r}. Review the tag choices explicitly.',
                    'warning',
                )
            )
        else:
            tags.append(tag)
            if tag_reference.name and tag.name != tag_reference.name:
                issues.append(
                    ImportIssue(
                        'tag_name_mismatch',
                        _json_location(('article', 'tags', index, 'name')),
                        f'The existing tag is named {tag.name!r}; the package proposed {tag_reference.name!r}.',
                        'warning',
                    )
                )

    configured_sites = dict(get_blog_site_slug_choices())
    resolved_sites = tuple(site for site in article.publication_sites if site in configured_sites)
    for index, site_slug in enumerate(article.publication_sites):
        if site_slug not in configured_sites:
            issues.append(
                ImportIssue(
                    'unresolved_publication_site',
                    _json_location(('article', 'publication_sites', index)),
                    f'No configured Blog site matches {site_slug!r}. Choose existing publication sites during review.',
                    'warning',
                )
            )

    resolved_canonical_site = article.canonical_site if article.canonical_site in resolved_sites else None
    if article.canonical_site and article.canonical_site not in configured_sites:
        issues.append(
            ImportIssue(
                'unresolved_canonical_site',
                'article.canonical_site',
                f'No configured Blog site matches canonical site {article.canonical_site!r}.',
                'warning',
            )
        )

    related_slugs = [related_reference.slug for related_reference in article.related_articles]
    related_posts_by_slug = {
        post.slug: post
        for post in BlogPost.objects.filter(slug__in=related_slugs)
    }
    related_posts: list[BlogPost | None] = []
    for index, related_reference in enumerate(article.related_articles):
        related_post = related_posts_by_slug.get(related_reference.slug)
        related_posts.append(related_post)
        if related_post is None:
            issues.append(
                ImportIssue(
                    'missing_related_article',
                    _json_location(('article', 'related_articles', index, 'slug')),
                    f'No existing article matches related slug {related_reference.slug!r}.',
                )
            )

    return ImportReferenceResolution(
        author=author,
        category=category,
        tags=tuple(tags),
        resolved_publication_sites=resolved_sites,
        resolved_canonical_site=resolved_canonical_site,
        related_posts=tuple(related_posts),
        unresolved=_dedupe_issues(issues),
    )


resolve_blog_import_references = _resolve_import_references


def _validate_site_dependencies(
    parsed: ParsedBlogImport,
    references: ImportReferenceResolution,
    site_slugs: Iterable[str],
) -> tuple[ImportIssue, ...]:
    site_slugs = set(site_slugs)
    issues: list[ImportIssue] = []
    from .internal_links import validate_inline_internal_links, validate_internal_link_destination

    for index, block in enumerate(parsed.article.blocks):
        location = ('article', 'blocks', index)
        if block.type == 'internal_link':
            try:
                validate_internal_link_destination(block.destination_key, site_slugs)
            except ValidationError:
                issues.append(
                    ImportIssue(
                        'invalid_internal_link_destination',
                        _json_location((*location, 'destination_key')),
                        'Choose an approved internal destination for every selected publication site.',
                    )
                )
        inline_values = []
        if block.type in {'rich_text', 'callout'}:
            inline_values.append((block.body, (*location, 'body')))
        elif block.type == 'faq':
            inline_values.extend(
                (item.answer, (*location, 'items', item_index, 'answer'))
                for item_index, item in enumerate(block.items)
            )
        for value, value_location in inline_values:
            try:
                validate_inline_internal_links(value, site_slugs)
            except ValidationError:
                issues.append(
                    ImportIssue(
                        'invalid_inline_internal_link',
                        _json_location(value_location),
                        'Use approved internal destinations for every selected publication site.',
                    )
                )

    if site_slugs:
        for index, related_post in enumerate(references.related_posts):
            if related_post is None:
                continue
            target_sites = get_publication_site_slugs(related_post)
            if not site_slugs.issubset(target_sites):
                issues.append(
                    ImportIssue(
                        'incompatible_related_article',
                        _json_location(('article', 'related_articles', index, 'slug')),
                        'Every related article must be available on every selected publication site.',
                    )
                )
    return _dedupe_issues(issues)


def _duplicate_matches(parsed: ParsedBlogImport, *, admin_site_name='admin') -> tuple[ImportDuplicateMatch, ...]:
    title = parsed.article.title.strip()
    slug = parsed.article.slug
    queryset = (
        BlogPost.objects.annotate(normalized_title=Lower(Trim('title')))
        .filter(Q(normalized_title=title.lower()) | Q(slug=slug))
        .order_by('pk')[:MAX_DUPLICATE_MATCHES]
    )
    matches = []
    for post in queryset:
        try:
            change_url = reverse(
                f'{admin_site_name}:blog_blogpost_change',
                args=(post.pk,),
            )
        except NoReverseMatch:
            change_url = ''
        matches.append(
            ImportDuplicateMatch(
                title=post.title,
                slug=post.slug,
                status=str(post.get_status_display()),
                change_url=change_url,
            )
        )
    return tuple(matches)


def _duplicate_warnings(matches: Iterable[ImportDuplicateMatch]) -> tuple[ImportIssue, ...]:
    return _dedupe_issues(
        (
            ImportIssue(
                'duplicate_article_match',
                'article.title',
                f'An existing article may match: {match.title!r} ({match.slug}, {match.status}).',
                'warning',
            )
            for match in matches
        ),
        severity='warning',
    )


def _raise_for_errors(issues: Iterable[ImportIssue]):
    errors = tuple(issue for issue in issues if issue.severity == 'error')
    if errors:
        raise BlogImportValidationError(errors)


def validate_and_stage_blog_import(source_file, image_files, actor):
    """Validate one upload package and stage only its referenced images."""

    _require_authenticated(actor)
    if source_file is None:
        raise BlogImportValidationError((ImportIssue('missing_source_file', '$', 'Choose an article file to import.'),))
    try:
        source_file.seek(0)
    except (AttributeError, OSError):
        pass
    result = parse_blog_import(source_file)
    if not result.valid:
        raise BlogImportValidationError(result.issues)
    parsed = result.parsed
    require_blog_import_permissions(actor, parsed)

    image_check = _check_image_package(parsed, image_files)
    references = _resolve_import_references(parsed)
    issues = list(image_check.issues)
    issues.extend(_media_reference_issues(parsed))
    issues.extend(issue for issue in references.unresolved if issue.severity == 'error')
    source_sites = set(parsed.article.publication_sites)
    if set(references.resolved_publication_sites) == source_sites:
        issues.extend(_validate_site_dependencies(parsed, references, source_sites))
    warnings = list(image_check.warnings)
    warnings.extend(_unused_definition_warnings(parsed))
    warnings.extend(issue for issue in references.unresolved if issue.severity == 'warning')
    matches = _duplicate_matches(parsed)
    warnings.extend(_duplicate_warnings(matches))
    _raise_for_errors(issues)

    source_name = getattr(source_file, 'name', '')
    try:
        import_session = stage_import(
            actor=actor,
            source_filename=_selected_basename(source_name),
            payload=parsed.as_dict(),
            warnings=[asdict(issue) for issue in _dedupe_issues(warnings, severity='warning')],
            files=image_check.matched_files,
        )
    except (OSError, ValueError) as error:
        raise BlogImportValidationError(
            (ImportIssue('staging_failed', '$', 'The package could not be prepared for review.'),)
        ) from error
    return import_session


def _load_staged_parsed(import_session: BlogArticleImport) -> ParsedBlogImport:
    result = validate_blog_import(import_session.payload)
    if not result.valid:
        raise BlogImportValidationError(result.issues)
    return result.parsed


def _review_issues(parsed, references, image_check):
    issues = list(image_check.issues)
    issues.extend(_media_reference_issues(parsed))
    issues.extend(issue for issue in references.unresolved if issue.severity == 'error')
    if set(references.resolved_publication_sites) == set(parsed.article.publication_sites):
        issues.extend(
            _validate_site_dependencies(
                parsed,
                references,
                references.resolved_publication_sites,
            )
        )
    warnings = list(image_check.warnings)
    warnings.extend(_unused_definition_warnings(parsed))
    warnings.extend(issue for issue in references.unresolved if issue.severity == 'warning')
    return _dedupe_issues(issues), _dedupe_issues(warnings, severity='warning')


def get_blog_import_review(import_id, actor, *, admin_site_name='admin') -> BlogImportReview:
    """Build an owner-bound, read-only review from current staged state."""

    import_session = get_pending_import(actor=actor, import_id=import_id)
    parsed = _load_staged_parsed(import_session)
    require_blog_import_permissions(actor, parsed)
    references = _resolve_import_references(parsed)
    staged_files = list(import_session.files.all())
    image_check = _check_image_package(parsed, staged_files)
    issues, warnings = _review_issues(parsed, references, image_check)
    duplicate_matches = _duplicate_matches(parsed, admin_site_name=admin_site_name)
    warnings = _dedupe_issues(
        [*warnings, *_duplicate_warnings(duplicate_matches)],
        severity='warning',
    )
    stored_warnings = tuple(
        ImportIssue(
            str(item.get('code', 'import_warning')),
            str(item.get('location', '$')),
            str(item.get('message', 'The import contains a warning.')),
            'warning',
        )
        for item in import_session.warnings
        if isinstance(item, dict)
    )
    warnings = _dedupe_issues([*stored_warnings, *warnings], severity='warning')
    return BlogImportReview(
        import_session=import_session,
        parsed=parsed,
        references=references,
        image_statuses=image_check.statuses,
        issues=issues,
        warnings=warnings,
        duplicate_matches=duplicate_matches,
    )


def _object_from_choice(value, model):
    primary_key = getattr(value, 'pk', value)
    if primary_key in (None, ''):
        return None
    return model.objects.filter(pk=primary_key).first()


def _reviewed_values(reviewed_references):
    preexisting_invalid_tag_choices = ()
    if isinstance(reviewed_references, ReviewedImportReferences):
        preexisting_invalid_tag_choices = reviewed_references.invalid_tag_choices
        values = {
            'author': reviewed_references.author,
            'category': reviewed_references.category,
            'tags': reviewed_references.tags,
            'publication_sites': reviewed_references.publication_sites,
            'canonical_site': reviewed_references.canonical_site,
            'create_category': reviewed_references.create_category,
            'create_tags': reviewed_references.create_tags,
            'expand_taxonomy_websites': reviewed_references.expand_taxonomy_websites,
        }
    else:
        values = reviewed_references or {}
    author = _object_from_choice(values.get('author'), AuthorProfile)
    category = _object_from_choice(values.get('category'), BlogCategory)
    raw_tags = values.get('tags') or ()
    if isinstance(raw_tags, str):
        raw_tags = (raw_tags,)
    tags = []
    invalid_tag_choices = list(preexisting_invalid_tag_choices)
    for value in raw_tags:
        tag = _object_from_choice(value, BlogTag)
        if tag is None:
            invalid_tag_choices.append(value)
        else:
            tags.append(tag)
    sites = tuple(dict.fromkeys(str(site) for site in values.get('publication_sites') or ()))
    canonical_site = values.get('canonical_site')
    return ReviewedImportReferences(
        author=author,
        category=category,
        tags=tuple(tags),
        publication_sites=sites,
        canonical_site=str(canonical_site) if canonical_site else '',
        invalid_tag_choices=tuple(invalid_tag_choices),
        create_category=bool(values.get('create_category')),
        create_tags=tuple(values.get('create_tags') or ()),
        expand_taxonomy_websites=bool(values.get('expand_taxonomy_websites')),
    )


def validate_reviewed_blog_import(import_session, reviewed_references, actor):
    """Revalidate current files and reviewed choices before draft creation."""

    pending = get_pending_import(actor=actor, import_id=import_session.id)
    parsed = _load_staged_parsed(pending)
    require_blog_import_permissions(actor, parsed)
    references = _resolve_import_references(parsed)
    image_check = _check_image_package(parsed, list(pending.files.all()))
    reviewed = _reviewed_values(reviewed_references)
    issues = list(image_check.issues)
    issues.extend(_media_reference_issues(parsed))
    issues.extend(issue for issue in references.unresolved if issue.severity == 'error')
    warnings = list(image_check.warnings)
    warnings.extend(_unused_definition_warnings(parsed))
    warnings.extend(issue for issue in references.unresolved if issue.severity == 'warning')
    configured_sites = set(dict(get_blog_site_slug_choices()))
    if reviewed.author is None:
        issues.append(ImportIssue('invalid_review_author', 'author', 'Choose an existing author.'))
    if reviewed.category is None and not (parsed.version == 2 and reviewed.create_category):
        issues.append(ImportIssue('invalid_review_category', 'category', 'Choose an existing category.'))
    if reviewed.category is not None and reviewed.create_category:
        issues.append(
            ImportIssue(
                'conflicting_category_choice',
                'create_category',
                'Choose an existing category or create the proposed category, not both.',
            )
        )
    if reviewed.invalid_tag_choices:
        issues.append(ImportIssue('invalid_review_tag', 'tags', 'Choose existing tags only.'))
    proposed_tags = {tag.slug: tag for tag in parsed.article.tags if tag.name}
    invalid_create_tags = set(reviewed.create_tags) - set(proposed_tags)
    if invalid_create_tags:
        issues.append(ImportIssue('invalid_create_tag', 'create_tags', 'Choose proposed v2 tags only.'))
    if reviewed.create_category:
        reference = parsed.article.category
        if parsed.version != 2 or not reference.name:
            issues.append(ImportIssue('invalid_create_category', 'create_category', 'Only a named v2 category can be created.'))
        if BlogCategory.objects.filter(name=reference.name).exclude(slug=reference.slug).exists():
            issues.append(ImportIssue('category_name_conflict', 'article.category.name', 'That category name already uses another slug.'))
        if BlogCategory.objects.filter(slug=reference.slug).exists():
            issues.append(ImportIssue('category_slug_exists', 'create_category', 'Map the package to the existing category with this slug.'))
        if not actor.has_perm('blog.add_blogcategory'):
            issues.append(ImportIssue('missing_category_permission', 'create_category', 'Creating the category requires add category permission.'))
    for slug in reviewed.create_tags:
        reference = proposed_tags.get(slug)
        if reference is None:
            continue
        if BlogTag.objects.filter(slug=slug).exists():
            issues.append(ImportIssue('tag_slug_exists', 'create_tags', f'Map {slug!r} to the existing tag instead of creating it.'))
        if BlogTag.objects.filter(name=reference.name).exclude(slug=slug).exists():
            issues.append(ImportIssue('tag_name_conflict', 'article.tags', f'Tag name {reference.name!r} already uses another slug.'))
    if reviewed.create_tags and not actor.has_perm('blog.add_blogtag'):
        issues.append(ImportIssue('missing_tag_permission', 'create_tags', 'Creating tags requires add tag permission.'))
    if not reviewed.publication_sites:
        issues.append(ImportIssue('missing_review_sites', 'publication_sites', 'Choose at least one publication site.'))
    invalid_sites = set(reviewed.publication_sites) - configured_sites
    if invalid_sites:
        issues.append(ImportIssue('invalid_review_site', 'publication_sites', 'Choose configured Blog sites only.'))
    if not reviewed.canonical_site:
        issues.append(ImportIssue('missing_review_canonical_site', 'canonical_site', 'Choose a canonical site.'))
    elif reviewed.canonical_site not in set(reviewed.publication_sites):
        issues.append(ImportIssue('canonical_site_not_selected', 'canonical_site', 'The canonical site must be one of the publication sites.'))
    selected_sites = set(reviewed.publication_sites)
    selected_terms = tuple(filter(None, (reviewed.category, *reviewed.tags)))
    unavailable = [
        term for term in selected_terms
        if selected_sites - set(term.websites.values_list('slug', flat=True))
    ]
    if unavailable and not reviewed.expand_taxonomy_websites:
        issues.append(ImportIssue('taxonomy_unavailable', 'expand_taxonomy_websites', 'Confirm website availability for the selected existing terms.'))
    if unavailable and not actor.has_perm('blog.change_blogcategory'):
        category_unavailable = any(isinstance(term, BlogCategory) for term in unavailable)
        if category_unavailable:
            issues.append(ImportIssue('missing_category_change_permission', 'expand_taxonomy_websites', 'Expanding the category requires change category permission.'))
    if unavailable and not actor.has_perm('blog.change_blogtag'):
        tag_unavailable = any(isinstance(term, BlogTag) for term in unavailable)
        if tag_unavailable:
            issues.append(ImportIssue('missing_tag_change_permission', 'expand_taxonomy_websites', 'Expanding tags requires change tag permission.'))
    issues.extend(_validate_site_dependencies(parsed, references, reviewed.publication_sites))
    duplicate_matches = _duplicate_matches(parsed)
    warnings.extend(_duplicate_warnings(duplicate_matches))
    return ReviewedImportValidation(
        references=None if any(issue.severity == 'error' for issue in issues) else reviewed,
        issues=_dedupe_issues(issues),
        warnings=_dedupe_issues(warnings, severity='warning'),
        duplicate_matches=duplicate_matches,
    )


revalidate_blog_import = validate_reviewed_blog_import


def _locked_pending_import(*, import_session, actor):
    """Reload and lock the owner-bound stage at the final write boundary."""

    _require_authenticated(actor)
    import_id = getattr(import_session, 'id', import_session)
    try:
        pending = BlogArticleImport.objects.select_for_update().get(pk=_as_uuid(import_id))
    except BlogArticleImport.DoesNotExist as error:
        raise BlogImportUnavailable from error
    if pending.created_by_id != getattr(actor, 'pk', None):
        raise PermissionDenied
    if pending.completed_post_id:
        return pending
    if pending.consumed_at or pending.expires_at <= timezone.now():
        raise BlogImportUnavailable
    return pending


def _unique_import_slug(slug):
    base = slugify(slug)[:220].strip('-') or 'article'
    candidate = base
    suffix = 2
    while BlogPost.objects.filter(slug=candidate).exists():
        marker = f'-{suffix}'
        candidate = f'{base[:220 - len(marker)].rstrip("-")}{marker}'
        suffix += 1
    return candidate


def _unique_heading_anchor(text, used_anchors):
    base = slugify(text)[:220].strip('-') or 'section'
    candidate = base
    suffix = 2
    while candidate in used_anchors:
        marker = f'-{suffix}'
        candidate = f'{base[:220 - len(marker)].rstrip("-")}{marker}'
        suffix += 1
    used_anchors.add(candidate)
    return candidate


def _staged_file_for_reference(staged_files, path, location):
    selected_name = path.rsplit('/', 1)[-1]
    staged_file = staged_files.get(selected_name)
    if staged_file is None:
        raise BlogImportValidationError(
            (
                ImportIssue(
                    'missing_image_file',
                    location,
                    'The article references a staged image that is no longer available.',
                ),
            )
        )
    return staged_file


def _save_media_instance(instance, tracked_files):
    previous_state = _media_file_state(instance)
    try:
        _save_validated(instance)
    finally:
        _track_new_media_files(instance, previous_state, tracked_files)


def _process_media_instance(instance, processor, tracked_files):
    previous_state = _media_file_state(instance)
    try:
        processor()
    finally:
        _track_new_media_files(instance, previous_state, tracked_files)


def _create_import_image(
    asset,
    *,
    asset_location,
    staged_files,
    actor,
    tracked_files,
):
    staged_file = _staged_file_for_reference(staged_files, asset.file, asset_location)
    image = BlogImage(
        name=asset.name,
        original=_copy_staged_image_file(staged_file, location=asset_location),
        alt_text=asset.alt_text,
        is_decorative=asset.is_decorative,
        is_feature=asset.is_feature,
        caption_title=asset.caption_title,
        caption_text=asset.caption_text,
        created_by=actor,
    )
    _save_media_instance(image, tracked_files)
    _process_media_instance(image, lambda: process_image(image), tracked_files)
    return image


def _create_import_comparison(
    comparison,
    *,
    comparison_location,
    staged_files,
    actor,
    tracked_files,
):
    first_location = f'{comparison_location}.first.file'
    second_location = f'{comparison_location}.second.file'
    first_file = _copy_staged_image_file(
        _staged_file_for_reference(staged_files, comparison.first.file, first_location),
        location=first_location,
    )
    second_file = _copy_staged_image_file(
        _staged_file_for_reference(staged_files, comparison.second.file, second_location),
        location=second_location,
    )
    image_comparison = BlogImageComparison(
        name=comparison.name,
        first_original=first_file,
        first_alt_text=comparison.first.alt_text,
        second_original=second_file,
        second_alt_text=comparison.second.alt_text,
        caption_title=comparison.caption_title,
        caption_text=comparison.caption_text,
        created_by=actor,
    )
    _save_media_instance(image_comparison, tracked_files)
    _process_media_instance(
        image_comparison,
        lambda: process_comparison_image(image_comparison, 'first'),
        tracked_files,
    )
    _process_media_instance(
        image_comparison,
        lambda: process_comparison_image(image_comparison, 'second'),
        tracked_files,
    )
    return image_comparison


def _create_import_media(parsed, import_session, actor, tracked_files):
    staged_files = _staged_files_by_name(import_session)
    used_asset_ids = {
        asset_id
        for asset_id in (parsed.article.featured_image,)
        if asset_id
    }
    used_asset_ids.update(
        block.asset_id for block in parsed.article.blocks if block.type == 'image'
    )
    used_comparison_ids = {
        block.comparison_id
        for block in parsed.article.blocks
        if block.type == 'image_comparison'
    }
    cleanup_path_count = len(used_asset_ids) * len(_MEDIA_FILE_SUFFIXES) + len(used_comparison_ids) * 2 * len(_MEDIA_FILE_SUFFIXES)
    if cleanup_path_count > MAX_PERMANENT_CLEANUP_PATHS:
        raise BlogImportValidationError(
            (
                ImportIssue(
                    'too_many_media_files',
                    'article.blocks',
                    'The import contains too many generated media files to track safely.',
                ),
            )
        )
    asset_locations = {
        asset.id: _json_location(('assets', index, 'file'))
        for index, asset in enumerate(parsed.assets)
    }
    comparison_locations = {
        comparison.id: _json_location(('comparisons', index))
        for index, comparison in enumerate(parsed.comparisons)
    }
    images = {}
    for asset in parsed.assets:
        if asset.id in used_asset_ids:
            images[asset.id] = _create_import_image(
                asset,
                asset_location=asset_locations[asset.id],
                staged_files=staged_files,
                actor=actor,
                tracked_files=tracked_files,
            )

    comparisons = {}
    for comparison in parsed.comparisons:
        if comparison.id in used_comparison_ids:
            comparisons[comparison.id] = _create_import_comparison(
                comparison,
                comparison_location=comparison_locations[comparison.id],
                staged_files=staged_files,
                actor=actor,
                tracked_files=tracked_files,
            )
    return images, comparisons


def _build_image_block(post, block, ordering, *, image_by_id, **_kwargs):
    image = image_by_id.get(block.asset_id)
    if image is None:
        raise BlogImportValidationError(
            (
                ImportIssue(
                    'unknown_asset_reference',
                    'article.blocks',
                    'The referenced image asset could not be created.',
                ),
            )
        )
    return BlogImageBlock(
        parent=post,
        region='main',
        ordering=ordering,
        image=image,
        is_expandable=block.is_expandable,
    )


def _build_image_comparison_block(post, block, ordering, *, comparison_by_id, **_kwargs):
    comparison = comparison_by_id.get(block.comparison_id)
    if comparison is None:
        raise BlogImportValidationError(
            (
                ImportIssue(
                    'unknown_comparison_reference',
                    'article.blocks',
                    'The referenced image comparison could not be created.',
                ),
            )
        )
    return BlogImageComparisonBlock(
        parent=post,
        region='main',
        ordering=ordering,
        comparison=comparison,
    )


def _build_heading_block(post, block, ordering, *, used_anchors, **_kwargs):
    return BlogHeadingBlock(
        parent=post,
        region='main',
        ordering=ordering,
        level=block.level,
        text=block.text,
        anchor=_unique_heading_anchor(block.text, used_anchors),
    )


def _build_rich_text_block(post, block, ordering, **_kwargs):
    return BlogRichTextBlock(parent=post, region='main', ordering=ordering, body=block.body)


def _build_faq_block(post, block, ordering, **_kwargs):
    return BlogFAQBlock(
        parent=post,
        region='main',
        ordering=ordering,
        items=[{'question': item.question, 'answer': item.answer} for item in block.items],
    )


def _build_checklist_block(post, block, ordering, **_kwargs):
    return BlogChecklistBlock(
        parent=post,
        region='main',
        ordering=ordering,
        marker=block.marker,
        items=list(block.items),
    )


def _build_code_block(post, block, ordering, **_kwargs):
    return BlogCodeBlock(
        parent=post,
        region='main',
        ordering=ordering,
        language=block.language,
        code=block.code,
        caption=block.caption,
    )


def _build_embed_sharing_block(post, block, ordering, **_kwargs):
    return BlogEmbedSharingBlock(
        parent=post,
        region='main',
        ordering=ordering,
        platform=block.platform,
        url=block.url,
        caption=block.caption,
    )


def _build_callout_block(post, block, ordering, **_kwargs):
    return BlogCalloutBlock(
        parent=post,
        region='main',
        ordering=ordering,
        callout_type=block.callout_type,
        title=block.title,
        body=block.body,
    )


def _build_source_link_block(post, block, ordering, **_kwargs):
    return BlogSourceLinkBlock(
        parent=post,
        region='main',
        ordering=ordering,
        label=block.label,
        url=block.url,
        note=block.note,
    )


def _build_link_group_block(post, block, ordering, **_kwargs):
    return BlogLinkGroupBlock(
        parent=post,
        region='main',
        ordering=ordering,
        label=block.label,
        links=[{'label': link.label, 'url': link.url} for link in block.links],
    )


def _build_internal_link_block(post, block, ordering, *, site_slugs, **_kwargs):
    instance = BlogInternalLinkBlock(
        parent=post,
        region='main',
        ordering=ordering,
        destination_key=block.destination_key,
        label=block.label,
        note=block.note,
    )
    instance._validation_site_slugs = set(site_slugs)
    return instance


# Keep every contract type explicit so a new block cannot silently bypass
# import-specific validation or persistence mapping.
_BLOCK_BUILDERS_BY_TYPE = {
    'heading': _build_heading_block,
    'rich_text': _build_rich_text_block,
    'faq': _build_faq_block,
    'checklist': _build_checklist_block,
    'code': _build_code_block,
    'embed_sharing': _build_embed_sharing_block,
    'callout': _build_callout_block,
    'source_link': _build_source_link_block,
    'link_group': _build_link_group_block,
    'internal_link': _build_internal_link_block,
    'image': _build_image_block,
    'image_comparison': _build_image_comparison_block,
}


def _save_validated(instance):
    instance.full_clean()
    instance.save()
    return instance


def _create_import_block(
    post,
    block,
    ordering,
    *,
    site_slugs,
    used_anchors,
    image_by_id,
    comparison_by_id,
):
    builder = _BLOCK_BUILDERS_BY_TYPE.get(block.type)
    if builder is None:
        raise BlogImportValidationError(
            (
                ImportIssue(
                    'unsupported_block_type',
                    'article.blocks',
                    'The article contains an unsupported content block.',
                ),
            )
        )
    instance = builder(
        post,
        block,
        ordering,
        site_slugs=site_slugs,
        used_anchors=used_anchors,
        image_by_id=image_by_id,
        comparison_by_id=comparison_by_id,
    )
    return _save_validated(instance)


def _is_slug_conflict(error):
    if isinstance(error, ValidationError):
        return 'slug' in getattr(error, 'message_dict', {})
    if not isinstance(error, IntegrityError):
        return False
    constraint_name = getattr(getattr(error.__cause__, 'diag', None), 'constraint_name', '')
    return 'slug' in f'{constraint_name} {error}'.lower()


def _create_import_post(parsed, references, actor):
    last_conflict = None
    for _attempt in range(MAX_IMPORT_SLUG_ATTEMPTS):
        post = BlogPost(
            status=BlogPost.Status.DRAFT,
            type=parsed.article.type,
            title=parsed.article.title,
            slug=_unique_import_slug(parsed.article.slug),
            summary=parsed.article.summary,
            author=references.author,
            seo_title=parsed.article.seo.title,
            seo_description=parsed.article.seo.description,
            category=references.category,
            canonical_site_slug=references.canonical_site,
            created_by=actor,
            updated_by=actor,
        )
        try:
            with transaction.atomic():
                _save_validated(post)
        except ValidationError as error:
            if not _is_slug_conflict(error):
                raise
            last_conflict = error
        except IntegrityError as error:
            if not _is_slug_conflict(error):
                raise
            last_conflict = error
        else:
            return post

    raise BlogImportValidationError(
        (
            ImportIssue(
                'slug_conflict',
                'article.slug',
                'The article slug was claimed by another draft. Try the import again.',
            ),
        )
    ) from last_conflict


def _materialize_reviewed_taxonomy(parsed, references):
    site_slugs = tuple(references.publication_sites)
    BlogSite.objects.bulk_create(
        [BlogSite(slug=slug) for slug in site_slugs],
        ignore_conflicts=True,
    )
    category = references.category
    if category is None and references.create_category:
        source = parsed.article.category
        category = BlogCategory.objects.create(name=source.name, slug=source.slug)
    tags = list(references.tags)
    proposed_tags = {tag.slug: tag for tag in parsed.article.tags if tag.name}
    for slug in references.create_tags:
        source = proposed_tags[slug]
        tag = BlogTag.objects.create(name=source.name, slug=source.slug)
        tags.append(tag)
    if category is not None:
        BlogCategorySite.objects.bulk_create(
            [BlogCategorySite(taxonomy=category, site_id=slug) for slug in site_slugs],
            ignore_conflicts=True,
        )
    for tag in tags:
        BlogTagSite.objects.bulk_create(
            [BlogTagSite(taxonomy=tag, site_id=slug) for slug in site_slugs],
            ignore_conflicts=True,
        )
    return ReviewedImportReferences(
        author=references.author,
        category=category,
        tags=tuple(tags),
        publication_sites=references.publication_sites,
        canonical_site=references.canonical_site,
    )


def create_blog_post_from_import(import_session, reviewed_references, actor):
    """Create one draft from a revalidated, owner-bound staged import."""

    tracked_files = []
    failed_cleanup_paths = ()
    pending_id = None
    cleanup_handled = False
    creation_error = None
    try:
        with transaction.atomic():
            pending = _locked_pending_import(import_session=import_session, actor=actor)
            pending_id = pending.id
            if pending.completed_post_id:
                completed_post = pending.completed_post
                if completed_post is None:
                    raise BlogImportUnavailable
                return completed_post

            if pending.permanent_cleanup_paths:
                remaining_paths, cleanup_failures = _cleanup_persisted_permanent_media(pending)
                if cleanup_failures:
                    pending.permanent_cleanup_paths = remaining_paths
                    pending.save(update_fields=['permanent_cleanup_paths'])
                    failed_cleanup_paths = tuple(
                        path for path in remaining_paths if _is_safe_permanent_media_path(path)
                    )
                    creation_error = OSError(
                        'Previous permanent media cleanup has not completed; try the import again.'
                    )
                    cleanup_handled = True
                else:
                    pending.permanent_cleanup_paths = []
                    pending.save(update_fields=['permanent_cleanup_paths'])

            if creation_error is None:
                try:
                    with transaction.atomic():
                        validation = validate_reviewed_blog_import(pending, reviewed_references, actor)
                        if not validation.valid:
                            raise BlogImportValidationError(validation.issues)

                        parsed = _load_staged_parsed(pending)
                        references = _reviewed_values(validation.references)
                        references = _materialize_reviewed_taxonomy(parsed, references)
                        if references.author is None or references.category is None or references.invalid_tag_choices:
                            raise BlogImportValidationError(
                                (
                                    ImportIssue(
                                        'stale_reviewed_reference',
                                        'reviewed_references',
                                        'One or more reviewed references are no longer available.',
                                    ),
                                )
                            )

                        current_source_references = _resolve_import_references(parsed)
                        if any(issue.severity == 'error' for issue in current_source_references.unresolved):
                            raise BlogImportValidationError(
                                tuple(issue for issue in current_source_references.unresolved if issue.severity == 'error')
                            )
                        site_slugs = tuple(references.publication_sites)
                        current_site_issues = _validate_site_dependencies(
                            parsed,
                            current_source_references,
                            site_slugs,
                        )
                        if current_site_issues:
                            raise BlogImportValidationError(current_site_issues)
                        related_posts = current_source_references.related_posts
                        if len(related_posts) != len(parsed.article.related_articles) or any(
                            related_post is None for related_post in related_posts
                        ):
                            raise BlogImportValidationError(
                                (
                                    ImportIssue(
                                        'stale_related_article',
                                        'article.related_articles',
                                        'One or more related articles are no longer available.',
                                    ),
                                )
                            )

                        post = _create_import_post(parsed, references, actor)

                        for site_slug in site_slugs:
                            _save_validated(BlogPostPublication(post=post, site_slug=site_slug))
                        post.tags.set(references.tags)

                        for position, related_post in enumerate(related_posts):
                            related = BlogPostRelated(
                                post=post,
                                related_post=related_post,
                                position=position,
                            )
                            related._validation_source_site_slugs = set(site_slugs)
                            _save_validated(related)

                        image_by_id, comparison_by_id = _create_import_media(
                            parsed,
                            pending,
                            actor,
                            tracked_files,
                        )
                        if parsed.article.featured_image:
                            post.featured_image = image_by_id[parsed.article.featured_image]
                            _save_validated(post)

                        used_anchors = set()
                        for index, block in enumerate(parsed.article.blocks, start=1):
                            _create_import_block(
                                post,
                                block,
                                index * 10,
                                site_slugs=site_slugs,
                                used_anchors=used_anchors,
                                image_by_id=image_by_id,
                                comparison_by_id=comparison_by_id,
                            )

                        mark_import_consumed(actor=actor, import_id=pending.id, completed_post=post)
                except Exception as error:
                    failed_cleanup_paths = _cleanup_new_media_files(tracked_files)
                    if failed_cleanup_paths:
                        pending.permanent_cleanup_paths = list(failed_cleanup_paths)
                        pending.save(update_fields=['permanent_cleanup_paths'])
                    tracked_files.clear()
                    cleanup_handled = True
                    creation_error = error
    except Exception:
        if not cleanup_handled:
            failed_cleanup_paths = _cleanup_new_media_files(tracked_files)
        if pending_id and failed_cleanup_paths:
            _persist_permanent_cleanup_paths(pending_id, failed_cleanup_paths)
        raise
    if creation_error is not None:
        raise creation_error
    return post
