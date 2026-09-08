from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.html import strip_tags
from django.utils.translation import gettext_lazy as _
from django_prose_editor.fields import ProseEditorField
from django_prose_editor.config import allowlist_from_extensions, expand_extensions
from content_editor.models import Region, create_plugin_base
import nh3

from apps.core.sites import get_blog_site_definitions, get_site_definition

from .embed_sharing import InvalidEmbedReference, normalize_embed_reference
from .storage import get_private_blog_import_storage


RELATED_POST_COMPATIBILITY_ERROR = _('Choose an article available on the same website.')
AUTHOR_SLUG_VALIDATOR = RegexValidator(
    regex=r'^[a-z0-9]+(?:-[a-z0-9]+)*$',
    message=_('Enter a lowercase slug containing only letters, numbers, and hyphens.'),
)


def _uuid_upload_path(prefix, filename, *, extension=None):
    suffix = extension or Path(filename).suffix.lower()
    if suffix not in {'.jpg', '.jpeg', '.png', '.webp'}:
        suffix = '.img'
    return f'{prefix}/{timezone.now():%Y/%m}/{uuid4().hex}{suffix}'


def blog_original_upload_path(instance, filename):
    return _uuid_upload_path('blog/originals', filename)


def blog_rendition_upload_path(instance, filename):
    return _uuid_upload_path('blog/renditions', filename, extension='.webp')


def blog_comparison_original_upload_path(instance, filename):
    return _uuid_upload_path('blog/comparisons/originals', filename)


def blog_comparison_rendition_upload_path(instance, filename):
    return _uuid_upload_path('blog/comparisons/renditions', filename, extension='.webp')


def author_profile_upload_path(instance, filename):
    return _uuid_upload_path('blog/authors', filename)


def blog_import_upload_path(instance, filename):
    """Store each staged file below its owning import UUID only."""

    if not instance.import_session_id:
        raise ValueError('A staged file must belong to an import session.')

    suffix = Path(filename).suffix.lower()
    if suffix not in {'.jpg', '.jpeg', '.png', '.webp'}:
        suffix = '.img'
    return f'{instance.import_session_id}/{uuid4().hex}{suffix}'


class AuthorProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='author_profile',
    )
    profile_picture = models.ImageField(
        upload_to=author_profile_upload_path,
        blank=True,
    )
    public_author_name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=120, unique=True, validators=[AUTHOR_SLUG_VALIDATOR])

    class Meta:
        ordering = ['public_author_name', 'pk']
        verbose_name = _('author')
        verbose_name_plural = _('authors')

    def __str__(self):
        return self.public_author_name or str(self.user)

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.public_author_name) or 'author'
            base = base[:120].strip('-') or 'author'
            candidate = base
            suffix = 2
            while AuthorProfile.objects.exclude(pk=self.pk).filter(slug=candidate).exists():
                marker = f'-{suffix}'
                candidate = f'{base[: 120 - len(marker)].rstrip("-")}{marker}'
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        stored_picture = self.profile_picture
        result = super().delete(*args, **kwargs)
        if stored_picture:
            stored_picture.delete(save=False)
        return result


class BlogPost(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Draft')
        READY = 'ready', _('Ready')
        SCHEDULED = 'scheduled', _('Scheduled')
        PUBLISHED = 'published', _('Published')
        UNPUBLISHED = 'unpublished', _('Unpublished')

    class Type(models.TextChoices):
        ARTICLE = 'article', _('Article')
        GUIDE = 'guide', _('Guide')
        COMPARISON = 'comparison', _('Comparison')
        TOP_LIST = 'top_list', _('Top list')
        SHOWCASE = 'showcase', _('Showcase')

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.ARTICLE)
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    summary = models.TextField(blank=True)
    search_body_text = models.TextField(blank=True, editable=False)
    author = models.ForeignKey(
        AuthorProfile,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='articles',
        verbose_name=_('Author'),
    )
    published_at = models.DateTimeField(blank=True, null=True)
    last_reviewed_on = models.DateField(blank=True, null=True)
    content_updated_at = models.DateTimeField(blank=True, editable=False, null=True)
    seo_title = models.CharField(max_length=70, blank=True)
    seo_description = models.CharField(max_length=160, blank=True)
    featured_image = models.ForeignKey(
        'BlogImage',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='featured_posts',
    )
    category = models.ForeignKey(
        'BlogCategory',
        on_delete=models.PROTECT,
        related_name='posts',
    )
    tags = models.ManyToManyField('BlogTag', blank=True, related_name='posts')
    related_posts = models.ManyToManyField(
        'self',
        blank=True,
        symmetrical=False,
        through='BlogPostRelated',
        through_fields=('post', 'related_post'),
        related_name='related_to_posts',
    )
    canonical_site_slug = models.CharField(max_length=40, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='created_blog_posts',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='updated_blog_posts',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    regions = [Region(key='main', title=_('Article content'))]

    class Meta:
        verbose_name = _('article')
        verbose_name_plural = _('articles')
        ordering = ['-published_at', '-pk']
        indexes = [
            models.Index(fields=['status', 'published_at']),
        ]
        permissions = [
            ('organize_blogpost', 'Can organize blog posts'),
            ('publish_blogpost', 'Can publish blog posts'),
            ('unpublish_blogpost', 'Can unpublish blog posts'),
        ]

    def __str__(self):
        return self.title or f'Blog post #{self.pk}'

    @property
    def is_publicly_visible(self):
        return self.is_effectively_public()

    def is_effectively_public(self, now=None):
        now = now or timezone.now()
        return self.status == self.Status.PUBLISHED or (
            self.status == self.Status.SCHEDULED and self.published_at is not None and self.published_at <= now
        )

    @property
    def effective_status(self):
        if self.status == self.Status.SCHEDULED and self.published_at and self.published_at <= timezone.now():
            return self.Status.PUBLISHED
        return self.status

    def clean(self):
        super().clean()
        errors = {}

        if self.canonical_site_slug and self.canonical_site_slug not in get_blog_site_definitions():
            errors['canonical_site_slug'] = _('Choose a configured Blog website.')

        if self.status in {self.Status.SCHEDULED, self.Status.PUBLISHED}:
            if not self.published_at:
                errors['published_at'] = _('Published articles must have a publication date.')
            if not self.summary.strip():
                errors['summary'] = _('Published articles must have a summary.')
            if not self.canonical_site_slug:
                errors['canonical_site_slug'] = _('Published articles must have a canonical site.')

            if self.pk:
                assigned_sites = set(self.publications.values_list('site_slug', flat=True))
                if not assigned_sites:
                    errors.setdefault('__all__', []).append(_('Published articles must be assigned to at least one site.'))
                elif self.canonical_site_slug not in assigned_sites:
                    errors['canonical_site_slug'] = _('Canonical site must match one assigned site.')

                if not any(
                    block_model.objects.filter(parent=self, region='main').exists()
                    for block_model in BLOG_BLOCK_MODELS
                ):
                    errors.setdefault('__all__', []).append(_('Published articles must contain body content.'))

        if errors:
            raise ValidationError(errors)


class BlogPostPublication(models.Model):
    post = models.ForeignKey(BlogPost, on_delete=models.CASCADE, related_name='publications')
    site_slug = models.CharField(max_length=40)

    class Meta:
        verbose_name = _('publication')
        verbose_name_plural = _('publications')
        ordering = ['site_slug']
        constraints = [
            models.UniqueConstraint(fields=['post', 'site_slug'], name='blog_one_publication_per_site'),
        ]
        indexes = [models.Index(fields=['site_slug'])]

    def __str__(self):
        return f'{self.post_id} on {self.site_slug}'

    def clean(self):
        super().clean()
        if self.site_slug not in get_blog_site_definitions():
            raise ValidationError({'site_slug': _('Choose a configured Blog website.')})


class BlogCategory(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    websites = models.ManyToManyField(
        'BlogSite',
        through='BlogCategorySite',
        related_name='categories',
        blank=True,
    )

    class Meta:
        verbose_name = _('category')
        verbose_name_plural = _('categories')
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class BlogTag(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    websites = models.ManyToManyField(
        'BlogSite',
        through='BlogTagSite',
        related_name='tags',
        blank=True,
    )

    class Meta:
        verbose_name = _('tag')
        verbose_name_plural = _('tags')
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class BlogSite(models.Model):
    """Database handle for taxonomy assignments; activation remains code-owned."""

    slug = models.CharField(max_length=40, primary_key=True)

    class Meta:
        ordering = ['slug']
        verbose_name = _('website')
        verbose_name_plural = _('websites')

    def __str__(self):
        site = get_site_definition(self.slug)
        return site.name if site else self.slug

    def clean(self):
        super().clean()
        if self.slug not in get_blog_site_definitions():
            raise ValidationError({'slug': _('Choose a configured Blog website.')})


class TaxonomySiteAssignmentMixin(models.Model):
    site = models.ForeignKey(BlogSite, on_delete=models.CASCADE)

    class Meta:
        abstract = True


class BlogCategorySite(TaxonomySiteAssignmentMixin):
    taxonomy = models.ForeignKey(BlogCategory, on_delete=models.CASCADE, related_name='site_assignments')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['taxonomy', 'site'], name='blog_category_site_unique'),
        ]
        verbose_name = _('category website')
        verbose_name_plural = _('category websites')


class BlogTagSite(TaxonomySiteAssignmentMixin):
    taxonomy = models.ForeignKey(BlogTag, on_delete=models.CASCADE, related_name='site_assignments')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['taxonomy', 'site'], name='blog_tag_site_unique'),
        ]
        verbose_name = _('tag website')
        verbose_name_plural = _('tag websites')


class BlogPostRelated(models.Model):
    post = models.ForeignKey(BlogPost, on_delete=models.CASCADE, related_name='related_links')
    related_post = models.ForeignKey(BlogPost, on_delete=models.CASCADE, related_name='incoming_related_links')
    position = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _('related article')
        verbose_name_plural = _('related articles')
        ordering = ['position', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['post', 'related_post'], name='blog_one_related_post'),
        ]

    def clean(self):
        super().clean()
        if self.post_id and self.post_id == self.related_post_id:
            raise ValidationError({'related_post': _('An article cannot be related to itself.')})
        if self.post_id and self.related_post_id:
            from .selectors import are_related_posts_compatible

            if not are_related_posts_compatible(
                source_post=self.post,
                target_post=self.related_post,
                source_site_slugs=getattr(
                    self,
                    '_validation_source_site_slugs',
                    None,
                ),
            ):
                raise ValidationError({'related_post': RELATED_POST_COMPATIBILITY_ERROR})

    def __str__(self):
        return f'{self.post} -> {self.related_post}'


class BlogImage(models.Model):
    class ProcessingStatus(models.TextChoices):
        PENDING = 'pending', _('Pending')
        READY = 'ready', _('Ready')
        FAILED = 'failed', _('Failed')

    name = models.CharField(max_length=200)
    original = models.ImageField(upload_to=blog_original_upload_path)
    rendition_480 = models.ImageField(upload_to=blog_rendition_upload_path, blank=True)
    rendition_800 = models.ImageField(upload_to=blog_rendition_upload_path, blank=True)
    rendition_1200 = models.ImageField(upload_to=blog_rendition_upload_path, blank=True)
    rendition_1600 = models.ImageField(upload_to=blog_rendition_upload_path, blank=True)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    alt_text = models.CharField(max_length=255, blank=True)
    is_decorative = models.BooleanField(default=False)
    is_feature = models.BooleanField(default=False)
    caption_title = models.CharField(max_length=255, blank=True)
    caption_text = models.CharField(max_length=255, blank=True)
    processing_status = models.CharField(
        max_length=12,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.PENDING,
    )
    processing_error = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='created_blog_images',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('image')
        verbose_name_plural = _('images')
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return self.name or f'Blog image #{self.pk}'

    def clean(self):
        super().clean()
        if self.is_decorative and self.alt_text.strip():
            raise ValidationError({'alt_text': _('Decorative images must use empty alternative text.')})
        if not self.is_decorative and not self.alt_text.strip():
            raise ValidationError({'alt_text': _('Describe this image for readers who cannot see it.')})

    def has_publication_files(self):
        fields = (self.original, self.rendition_480, self.rendition_800, self.rendition_1200)
        if self.processing_status != self.ProcessingStatus.READY or not self.width or not self.height:
            return False
        try:
            return all(field and field.storage.exists(field.name) for field in fields)
        except OSError:
            return False

    def delete(self, *args, **kwargs):
        stored_files = [
            self.original,
            self.rendition_480,
            self.rendition_800,
            self.rendition_1200,
            self.rendition_1600,
        ]
        result = super().delete(*args, **kwargs)
        for stored_file in stored_files:
            if stored_file:
                stored_file.delete(save=False)
        return result


COMPARISON_SIDES = ('first', 'second')


class BlogImageComparison(models.Model):
    class ProcessingStatus(models.TextChoices):
        PENDING = 'pending', _('Pending')
        READY = 'ready', _('Ready')
        FAILED = 'failed', _('Failed')

    name = models.CharField(max_length=200)
    first_original = models.ImageField(upload_to=blog_comparison_original_upload_path)
    first_rendition_480 = models.ImageField(upload_to=blog_comparison_rendition_upload_path, blank=True)
    first_rendition_800 = models.ImageField(upload_to=blog_comparison_rendition_upload_path, blank=True)
    first_rendition_1200 = models.ImageField(upload_to=blog_comparison_rendition_upload_path, blank=True)
    first_rendition_1600 = models.ImageField(upload_to=blog_comparison_rendition_upload_path, blank=True)
    first_width = models.PositiveIntegerField(default=0)
    first_height = models.PositiveIntegerField(default=0)
    first_alt_text = models.CharField(max_length=255)
    first_processing_status = models.CharField(
        max_length=12,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.PENDING,
    )
    first_processing_error = models.CharField(max_length=255, blank=True)
    second_original = models.ImageField(upload_to=blog_comparison_original_upload_path)
    second_rendition_480 = models.ImageField(upload_to=blog_comparison_rendition_upload_path, blank=True)
    second_rendition_800 = models.ImageField(upload_to=blog_comparison_rendition_upload_path, blank=True)
    second_rendition_1200 = models.ImageField(upload_to=blog_comparison_rendition_upload_path, blank=True)
    second_rendition_1600 = models.ImageField(upload_to=blog_comparison_rendition_upload_path, blank=True)
    second_width = models.PositiveIntegerField(default=0)
    second_height = models.PositiveIntegerField(default=0)
    second_alt_text = models.CharField(max_length=255)
    second_processing_status = models.CharField(
        max_length=12,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.PENDING,
    )
    second_processing_error = models.CharField(max_length=255, blank=True)
    caption_title = models.CharField(max_length=255, blank=True)
    caption_text = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='created_blog_image_comparisons',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('comparison image')
        verbose_name_plural = _('comparison images')
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return self.name or f'Image comparison #{self.pk}'

    @staticmethod
    def _validate_side(side):
        if side not in COMPARISON_SIDES:
            raise ValueError(f'Unknown comparison side: {side}')

    def _field(self, side, suffix):
        self._validate_side(side)
        return getattr(self, f'{side}_{suffix}')

    def clean(self):
        super().clean()
        errors = {}
        if not self.first_original:
            errors['first_original'] = _('Choose the first comparison image.')
        if not self.second_original:
            errors['second_original'] = _('Choose the second comparison image.')
        if not self.first_alt_text.strip():
            errors['first_alt_text'] = _('Describe the first comparison image for readers who cannot see it.')
        if not self.second_alt_text.strip():
            errors['second_alt_text'] = _('Describe the second comparison image for readers who cannot see it.')
        if errors:
            raise ValidationError(errors)

    def has_publication_files(self, side):
        self._validate_side(side)
        fields = (
            self._field(side, 'original'),
            self._field(side, 'rendition_480'),
            self._field(side, 'rendition_800'),
            self._field(side, 'rendition_1200'),
        )
        if (
            self._field(side, 'processing_status') != self.ProcessingStatus.READY
            or not self._field(side, 'width')
            or not self._field(side, 'height')
        ):
            return False
        try:
            return all(field and field.storage.exists(field.name) for field in fields)
        except OSError:
            return False

    def is_ready_for_publication(self):
        return all(self.has_publication_files(side) for side in COMPARISON_SIDES)

    def delete(self, *args, **kwargs):
        stored_files = [
            getattr(self, field_name)
            for side in COMPARISON_SIDES
            for field_name in (
                f'{side}_original',
                f'{side}_rendition_480',
                f'{side}_rendition_800',
                f'{side}_rendition_1200',
                f'{side}_rendition_1600',
            )
        ]
        result = super().delete(*args, **kwargs)
        for stored_file in stored_files:
            if stored_file:
                stored_file.delete(save=False)
        return result


class BlogArticleImport(models.Model):
    id = models.UUIDField(default=uuid4, editable=False, primary_key=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='blog_article_imports',
    )
    source_filename = models.CharField(max_length=255)
    payload = models.JSONField()
    warnings = models.JSONField(default=list)
    expires_at = models.DateTimeField(db_index=True)
    completed_post = models.OneToOneField(
        BlogPost,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='completed_article_import',
    )
    consumed_at = models.DateTimeField(blank=True, null=True)
    permanent_cleanup_paths = models.JSONField(default=list, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)


class BlogArticleImportFile(models.Model):
    import_session = models.ForeignKey(
        BlogArticleImport,
        on_delete=models.CASCADE,
        related_name='files',
    )
    selected_name = models.CharField(max_length=255)
    file = models.FileField(
        max_length=100,
        storage=get_private_blog_import_storage,
        upload_to=blog_import_upload_path,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['import_session', 'selected_name'],
                name='blog_import_file_selected_name',
            ),
        ]


BlogPostPlugin = create_plugin_base(BlogPost)


class BlogHeadingBlock(BlogPostPlugin):
    class Level(models.IntegerChoices):
        H2 = 2, 'H2'
        H3 = 3, 'H3'

    level = models.PositiveSmallIntegerField(choices=Level.choices, default=Level.H2)
    text = models.CharField(max_length=200)
    anchor = models.SlugField(max_length=220)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['parent', 'anchor'], name='blog_unique_heading_anchor'),
        ]

    def save(self, *args, **kwargs):
        if not self.anchor:
            self.anchor = slugify(self.text)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.text


RICH_TEXT_EXTENSIONS = {
    'Bold': True,
    'Italic': True,
    'Code': True,
    'BulletList': True,
    'OrderedList': True,
    'ListItem': True,
    'Blockquote': True,
    'Link': {'enableTarget': True, 'protocols': ['http', 'https']},
    'Table': True,
    'TableRow': True,
    'TableHeader': True,
    'TableCell': True,
}
CALLOUT_EXTENSIONS = {
    'Bold': True,
    'Italic': True,
    'Link': {'enableTarget': True, 'protocols': ['http', 'https']},
}


def _prose_sanitizer(extensions, *, allow_internal_links=False):
    config = allowlist_from_extensions(expand_extensions(extensions))
    if allow_internal_links:
        config['attributes'].setdefault('a', set()).add('data-blog-internal-key')
    config['attributes'].get('a', set()).discard('rel')
    config['link_rel'] = 'noopener noreferrer'
    cleaner = nh3.Cleaner(**config)
    return cleaner.clean


sanitize_rich_text = _prose_sanitizer(RICH_TEXT_EXTENSIONS, allow_internal_links=True)


class BlogRichTextBlock(BlogPostPlugin):
    body = ProseEditorField(
        extensions=RICH_TEXT_EXTENSIONS,
        sanitize=sanitize_rich_text,
    )

    class Meta:
        verbose_name = _('rich text')
        verbose_name_plural = _('rich text blocks')

    def __str__(self):
        return str(_('Rich text'))


class BlogFAQBlock(BlogPostPlugin):
    # FAQ entries are one aggregate because content-editor inlines cannot own nested inlines.
    items = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = _('FAQ')
        verbose_name_plural = _('FAQ blocks')

    def clean(self):
        super().clean()
        from .faq import normalize_faq_items

        try:
            self.items = normalize_faq_items(self.items)
        except ValidationError as error:
            raise ValidationError({'items': error.messages})

    def __str__(self):
        return str(_('FAQ'))


class BlogChecklistBlock(BlogPostPlugin):
    class Marker(models.TextChoices):
        CHECKMARK = 'checkmark', _('Checkmark')
        SQUARE = 'square', _('Square checkbox')
        ARROW = 'arrow', _('Arrow')

    marker = models.CharField(
        max_length=20,
        choices=Marker.choices,
        default=Marker.CHECKMARK,
    )
    items = models.JSONField(default=list)

    def clean(self):
        super().clean()
        if not isinstance(self.items, list) or any(strip_tags(str(item)) != str(item) for item in self.items):
            raise ValidationError({'items': _('Checklist items must be plain text.')})
        if not [item for item in self.items if str(item).strip()]:
            raise ValidationError({'items': _('Add at least one checklist item.')})

    def __str__(self):
        return str(_('Checklist'))


class BlogCodeBlock(BlogPostPlugin):
    class Language(models.TextChoices):
        TEXT = 'text', _('Plain text')
        PYTHON = 'python', _('Python')
        SHELL = 'shell', _('Shell')
        HTML = 'html', _('HTML')
        CSS = 'css', _('CSS')
        JAVASCRIPT = 'javascript', _('JavaScript')
        JSON = 'json', _('JSON')
        SQL = 'sql', _('SQL')
        DART = 'dart', _('Dart')

    code = models.TextField()
    language = models.CharField(max_length=20, choices=Language.choices, default=Language.TEXT)
    caption = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return str(_('Code block'))


class BlogEmbedSharingBlock(BlogPostPlugin):
    class Platform(models.TextChoices):
        YOUTUBE = 'youtube', _('YouTube')
        X = 'x', _('X')
        REDDIT = 'reddit', _('Reddit')

    platform = models.CharField(max_length=12, choices=Platform.choices)
    url = models.URLField(max_length=500)
    caption = models.CharField(max_length=300, blank=True)

    def _trim_fields(self):
        self.platform = self.platform.strip() if isinstance(self.platform, str) else ''
        self.url = self.url.strip() if isinstance(self.url, str) else ''
        self.caption = self.caption.strip() if isinstance(self.caption, str) else ''

    def clean_fields(self, exclude=None):
        self._trim_fields()
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        self._trim_fields()

        errors = {}
        if not self.platform:
            errors['platform'] = _('Choose a platform.')
        elif self.platform not in self.Platform.values:
            errors['platform'] = _('Choose a supported platform.')

        if not self.url:
            errors['url'] = _('Enter a content URL.')
        elif self.platform in self.Platform.values:
            try:
                reference = normalize_embed_reference(self.platform, self.url)
            except InvalidEmbedReference:
                errors['url'] = _('Enter a valid URL from the selected platform.')
            else:
                if not reference.canonical_url.startswith('https://'):
                    errors['url'] = _('Enter a valid HTTPS URL from the selected platform.')
                else:
                    self.url = reference.canonical_url

        if len(self.caption) > 300:
            errors['caption'] = _('Caption must be 300 characters or fewer.')
        elif strip_tags(self.caption) != self.caption:
            errors['caption'] = _('Caption must be plain text.')

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return str(_('Embed sharing'))


class BlogCalloutBlock(BlogPostPlugin):
    class CalloutType(models.TextChoices):
        NOTE = 'note', _('Note')
        TIP = 'tip', _('Tip')
        WARNING = 'warning', _('Warning')

    callout_type = models.CharField(max_length=12, choices=CalloutType.choices, default=CalloutType.NOTE)
    title = models.CharField(max_length=200, blank=True)
    body = ProseEditorField(extensions=CALLOUT_EXTENSIONS, sanitize=_prose_sanitizer(CALLOUT_EXTENSIONS))

    def __str__(self):
        return self.title or str(_('Callout'))


class BlogSourceLinkBlock(BlogPostPlugin):
    label = models.CharField(max_length=200, default='Source:')
    url = models.URLField(max_length=500)
    note = models.CharField(max_length=255, blank=True)

    def clean(self):
        super().clean()
        try:
            URLValidator(schemes=['http', 'https'])(self.url)
        except ValidationError:
            raise ValidationError({'url': _('Use an absolute HTTP(S) source URL.')})

    def __str__(self):
        return self.label


class BlogLinkGroupBlock(BlogPostPlugin):
    label = models.CharField(max_length=200)
    links = models.JSONField(default=list)

    def clean(self):
        super().clean()
        errors = {}
        if not isinstance(self.links, list) or not self.links:
            errors['links'] = _('Add at least one link.')
        else:
            for index, link in enumerate(self.links):
                if not isinstance(link, dict):
                    errors['links'] = _('Each link must have a label and URL.')
                    break
                if not str(link.get('label', '')).strip():
                    errors['links'] = _('Each link must have a label.')
                    break
                try:
                    URLValidator(schemes=['http', 'https'])(str(link.get('url', '')).strip())
                except ValidationError:
                    errors['links'] = _('Each link must use an absolute HTTP(S) URL.')
                    break
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.label


class BlogInternalLinkBlock(BlogPostPlugin):
    destination_key = models.CharField(max_length=80)
    label = models.CharField(max_length=200)
    note = models.CharField(max_length=255, blank=True)

    def clean(self):
        super().clean()
        from .internal_links import validate_internal_link_destination

        errors = {}
        site_slugs = getattr(self, '_validation_site_slugs', None)
        if site_slugs is None and self.parent_id:
            site_slugs = set(self.parent.publications.values_list('site_slug', flat=True))
        try:
            validate_internal_link_destination(self.destination_key, site_slugs or set())
        except ValidationError as error:
            errors['destination_key'] = error.messages

        normalized_label = ' '.join(self.label.casefold().split())
        if not normalized_label:
            errors['label'] = _('Write anchor text that describes the destination.')
        elif normalized_label in {'click here', 'here', 'read more'}:
            errors['label'] = _('Use descriptive anchor text instead of generic wording.')
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.label


class BlogImageBlock(BlogPostPlugin):
    image = models.ForeignKey(BlogImage, on_delete=models.PROTECT, related_name='content_blocks')
    is_expandable = models.BooleanField(default=True)

    def clean(self):
        super().clean()
        if self.image_id and self.image.is_decorative:
            raise ValidationError({'image': _('Body images must not be decorative.')})
        if self.image_id and self.parent.is_effectively_public() and not self.image.has_publication_files():
            raise ValidationError({'image': _('Choose a fully processed image with all stored files available.')})

    def __str__(self):
        return str(_('Image'))


class BlogImageComparisonBlock(BlogPostPlugin):
    comparison = models.ForeignKey(
        BlogImageComparison,
        on_delete=models.PROTECT,
        related_name='content_blocks',
    )

    def clean(self):
        super().clean()
        if (
            self.comparison_id
            and self.parent_id
            and self.parent.is_effectively_public()
            and not self.comparison.is_ready_for_publication()
        ):
            raise ValidationError({
                'comparison': _('Both comparison images must be ready before this can be saved or published.'),
            })

    def __str__(self):
        return str(_('Image comparison'))


BLOG_BLOCK_MODELS = (
    BlogHeadingBlock,
    BlogRichTextBlock,
    BlogFAQBlock,
    BlogChecklistBlock,
    BlogCodeBlock,
    BlogEmbedSharingBlock,
    BlogCalloutBlock,
    BlogSourceLinkBlock,
    BlogLinkGroupBlock,
    BlogInternalLinkBlock,
    BlogImageBlock,
    BlogImageComparisonBlock,
)
