import logging
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.core.image_processing import (
    encode_image_bytes,
    normalize_image,
    resize_to_max_width,
    validate_image_bytes as validate_shared_image_bytes,
)

from .models import (
    COMPARISON_SIDES,
    AuthorProfile,
    BlogImage,
    BlogImageComparison,
)


logger = logging.getLogger(__name__)

ALLOWED_FORMATS = {'JPEG', 'PNG', 'WEBP'}
RENDITION_WIDTHS = (480, 800, 1200, 1600)
AUTHOR_PROFILE_SIZE = (96, 96)
IMAGE_FILE_SUFFIXES = (
    'original',
    'rendition_480',
    'rendition_800',
    'rendition_1200',
    'rendition_1600',
)
IMAGE_STATE_SUFFIXES = (
    *IMAGE_FILE_SUFFIXES,
    'width',
    'height',
    'processing_status',
    'processing_error',
)


def _limits():
    return (
        getattr(settings, 'BLOG_IMAGE_MAX_BYTES', 15 * 1024 * 1024),
        getattr(settings, 'BLOG_IMAGE_MAX_PIXELS', 40_000_000),
    )


def validate_image_bytes(uploaded_file):
    max_bytes, max_pixels = _limits()
    validate_shared_image_bytes(
        uploaded_file,
        max_bytes=max_bytes,
        max_pixels=max_pixels,
        size_message=_('Images must be 15 MB or smaller.'),
        format_message=_('Use a JPEG, PNG, or WebP image.'),
        animation_message=_('Animated images are not supported.'),
        pixel_message=_('Images must contain 40 megapixels or fewer.'),
        invalid_message=_('The uploaded file is not a valid image.'),
    )


def _save_image_bytes(image, *, image_format, quality=100):
    return encode_image_bytes(image, image_format=image_format, quality=quality)


def _field_name(prefix, suffix):
    return f'{prefix}{suffix}' if prefix else suffix


def _process_image(instance, *, prefix='', status_model=BlogImage):
    original_name = _field_name(prefix, 'original')
    status_name = _field_name(prefix, 'processing_status')
    error_name = _field_name(prefix, 'processing_error')
    width_name = _field_name(prefix, 'width')
    height_name = _field_name(prefix, 'height')
    rendition_names = [_field_name(prefix, f'rendition_{width}') for width in RENDITION_WIDTHS]
    original = getattr(instance, original_name)
    if not original:
        raise ValidationError({original_name: _('Choose an image to process.')})

    with original:
        validate_image_bytes(original.file)
        old_values = {
            field_name: getattr(instance, field_name).name
            for field_name in [original_name, *rendition_names]
        }
        source_name = original.name
        instance_field_names = [
            original_name,
            *rendition_names,
            width_name,
            height_name,
            status_name,
            error_name,
            'updated_at',
        ]
        setattr(instance, status_name, status_model.ProcessingStatus.PENDING)
        setattr(instance, error_name, '')
        instance.save(update_fields=[status_name, error_name, 'updated_at'])
        generated_names = {}

        try:
            original.open('rb')
            source_bytes = original.read()
            with Image.open(BytesIO(source_bytes)) as source:
                source_format = source.format
                normalized = normalize_image(source, source_format=source_format)

                original_bytes = _save_image_bytes(normalized, image_format=source_format)
                extension = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}[source_format]
                original_field = getattr(instance, original_name)
                original_field.save(f'original{extension}', ContentFile(original_bytes), save=False)
                generated_names[original_name] = original_field.name

                setattr(instance, width_name, normalized.width)
                setattr(instance, height_name, normalized.height)
                for width in RENDITION_WIDTHS:
                    rendition = resize_to_max_width(normalized, width)
                    rendition_bytes = _save_image_bytes(rendition, image_format='WEBP')
                    field_name = _field_name(prefix, f'rendition_{width}')
                    field = getattr(instance, field_name)
                    field.save(f'rendition_{width}.webp', ContentFile(rendition_bytes), save=False)
                    generated_names[field_name] = field.name

            setattr(instance, status_name, status_model.ProcessingStatus.READY)
            setattr(instance, error_name, '')
            instance.save(update_fields=instance_field_names)
        except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError) as error:
            for field_name, generated_name in generated_names.items():
                if generated_name and generated_name != old_values[field_name]:
                    getattr(instance, field_name).storage.delete(generated_name)

            setattr(instance, original_name, source_name)
            for field_name in rendition_names:
                setattr(instance, field_name, '')

            setattr(instance, status_name, status_model.ProcessingStatus.FAILED)
            setattr(instance, error_name, _('Image processing failed.'))
            instance.save(update_fields=instance_field_names)
            if prefix:
                logger.warning(
                    'Blog comparison image processing failed for comparison %s side %s: %s',
                    instance.pk,
                    prefix.rstrip('_'),
                    error.__class__.__name__,
                )
            else:
                logger.warning('Blog image processing failed for image %s: %s', instance.pk, error.__class__.__name__)
            raise ValidationError({original_name: _('The image could not be processed.')}) from error

        for field_name, old_name in old_values.items():
            current_name = getattr(instance, field_name).name
            if old_name and old_name != current_name:
                getattr(instance, field_name).storage.delete(old_name)


def _image_state(instance, *, prefix=''):
    state = {}
    for suffix in IMAGE_STATE_SUFFIXES:
        value = getattr(instance, _field_name(prefix, suffix))
        state[suffix] = value.name if suffix in IMAGE_FILE_SUFFIXES else value
    return state


def image_state(instance: BlogImage):
    return _image_state(instance)


def comparison_image_state(instance: BlogImageComparison, side):
    if side not in COMPARISON_SIDES:
        raise ValueError(f'Unknown comparison side: {side}')
    return _image_state(instance, prefix=f'{side}_')


def _restore_image_state(instance, previous_state, *, prefix=''):
    files_to_delete = []
    for suffix in IMAGE_FILE_SUFFIXES:
        field = getattr(instance, _field_name(prefix, suffix))
        current_name = field.name
        previous_name = previous_state[suffix]
        if current_name and current_name != previous_name:
            files_to_delete.append((field.storage, current_name))

    for suffix, value in previous_state.items():
        setattr(instance, _field_name(prefix, suffix), value)
    state_fields = [_field_name(prefix, suffix) for suffix in IMAGE_STATE_SUFFIXES]
    instance.save(update_fields=[*state_fields, 'updated_at'])

    deleted_names = set()
    for storage, name in files_to_delete:
        if name in deleted_names:
            continue
        try:
            storage.delete(name)
        except OSError as error:
            if prefix:
                logger.warning(
                    'Blog comparison replacement cleanup failed for comparison %s side %s: %s',
                    instance.pk,
                    prefix.rstrip('_'),
                    error.__class__.__name__,
                )
            else:
                logger.warning(
                    'Blog image replacement cleanup failed for image %s: %s',
                    instance.pk,
                    error.__class__.__name__,
                )
        deleted_names.add(name)


def process_image(instance: BlogImage, *, previous_state=None):
    try:
        _process_image(instance)
    except ValidationError:
        if previous_state is not None:
            _restore_image_state(instance, previous_state)
        raise


def process_comparison_image(instance: BlogImageComparison, side, *, previous_state=None):
    if side not in COMPARISON_SIDES:
        raise ValueError(f'Unknown comparison side: {side}')
    try:
        _process_image(instance, prefix=f'{side}_', status_model=BlogImageComparison)
    except ValidationError:
        if previous_state is not None:
            _restore_image_state(instance, previous_state, prefix=f'{side}_')
        raise


def process_author_profile_picture(instance: AuthorProfile):
    if not instance.profile_picture:
        raise ValidationError({'profile_picture': _('Choose an image to process.')})

    with instance.profile_picture:
        validate_image_bytes(instance.profile_picture.file)
        try:
            instance.profile_picture.open('rb')
            source_bytes = instance.profile_picture.read()
            with Image.open(BytesIO(source_bytes)) as source:
                normalized = ImageOps.exif_transpose(source)
                if normalized.mode not in {'RGB', 'RGBA'}:
                    normalized = normalized.convert('RGBA' if 'A' in normalized.getbands() else 'RGB')
                rendition = ImageOps.fit(normalized, AUTHOR_PROFILE_SIZE, method=Image.Resampling.LANCZOS)
                rendition_bytes = _save_image_bytes(rendition, image_format='WEBP', quality=100)

            instance.profile_picture.save(
                'profile.webp',
                ContentFile(rendition_bytes),
                save=False,
            )
        except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError) as error:
            raise ValidationError({'profile_picture': _('The image could not be processed.')}) from error


def _image_source(instance, *, prefix='', sizes=None):
    status_model = BlogImageComparison if prefix else BlogImage
    status_name = _field_name(prefix, 'processing_status')
    if not instance or getattr(instance, status_name) != status_model.ProcessingStatus.READY:
        return None
    original = getattr(instance, _field_name(prefix, 'original'))
    if not original or not original.storage.exists(original.name):
        return None
    rendition_urls = []
    for width in RENDITION_WIDTHS:
        rendition = getattr(instance, _field_name(prefix, f'rendition_{width}'))
        if width <= getattr(instance, _field_name(prefix, 'width')) and rendition and rendition.storage.exists(rendition.name):
            rendition_urls.append(f'{rendition.url} {width}w')

    rendition_1200 = getattr(instance, _field_name(prefix, 'rendition_1200'))
    src = rendition_1200.url if rendition_1200 and rendition_1200.storage.exists(rendition_1200.name) else original.url
    alt_text = getattr(instance, _field_name(prefix, 'alt_text'))
    return {
        'original': original.url,
        'src': src,
        'srcset': ', '.join(rendition_urls),
        'sizes': sizes or '(min-width: 900px) 820px, calc(100vw - 3rem)',
        'width': getattr(instance, _field_name(prefix, 'width')),
        'height': getattr(instance, _field_name(prefix, 'height')),
        'alt': '' if prefix == '' and instance.is_decorative else alt_text,
    }


def image_sources(image, *, sizes=None):
    sources = _image_source(image, sizes=sizes)
    if not sources:
        return None
    sources.update({
        'caption_title': image.caption_title,
        'caption_text': image.caption_text,
    })
    return sources


def comparison_sources(comparison):
    if not comparison:
        return {'caption_title': '', 'caption_text': '', 'first': None, 'second': None}

    sides = {}
    sizes = (
        '(min-width: 940px) 462px, '
        '(min-width: 640px) calc((100vw - 3rem) / 2), '
        'calc(100vw - 3rem)'
    )
    for side in COMPARISON_SIDES:
        sides[side] = (
            _image_source(comparison, prefix=f'{side}_', sizes=sizes)
            if comparison.has_publication_files(side)
            else None
        )
    return {
        'caption_title': comparison.caption_title,
        'caption_text': comparison.caption_text,
        **sides,
    }
