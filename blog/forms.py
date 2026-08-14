import json

from django import forms
from django.contrib.admin.widgets import AdminSplitDateTime
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.forms.models import BaseInlineFormSet
from django.db.models import Q
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.core.sites import get_blog_site_slug_choices

from .models import (
    AuthorProfile,
    BlogCalloutBlock,
    BlogChecklistBlock,
    BlogCategory,
    BlogSite,
    BlogTag,
    BlogHeadingBlock,
    BlogFAQBlock,
    BlogEmbedSharingBlock,
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
    RELATED_POST_COMPATIBILITY_ERROR,
    BLOG_BLOCK_MODELS,
)
from .faq import normalize_faq_items
from .embed_sharing import (
    INVALID_EMBED_REFERENCE_MESSAGE,
    PLATFORM_CHOICES,
    EmbedVerificationUnavailable,
    InvalidEmbedReference,
    UnsupportedEmbedItem,
    normalize_embed_reference,
    verify_reference,
)
from .internal_links import get_internal_link_choices, validate_internal_link_destination
from .image_services import validate_image_bytes
from .widgets import BlogFAQItemsWidget
from .selectors import (
    are_related_posts_compatible,
    get_compatible_related_posts,
    get_incompatible_incoming_related_links,
    get_publication_site_slugs,
    get_selectable_image_comparisons,
)


def _faq_has_complete_items(value):
    try:
        return bool(normalize_faq_items(value))
    except ValidationError:
        return False


class BlogTagCheckboxSelectMultiple(forms.CheckboxSelectMultiple):
    template_name = 'admin/blog/widgets/tag_checkbox_select.html'


class BlogTaxonomyAdminForm(forms.ModelForm):
    available_websites = forms.ModelMultipleChoiceField(
        label=_('Available on websites'),
        queryset=BlogSite.objects.none(),
        required=True,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        enabled_slugs = [slug for slug, _label in get_blog_site_slug_choices()]
        BlogSite.objects.bulk_create(
            [BlogSite(slug=slug) for slug in enabled_slugs],
            ignore_conflicts=True,
        )
        self.fields['available_websites'].queryset = BlogSite.objects.filter(slug__in=enabled_slugs)
        if self.instance.pk:
            self.initial['available_websites'] = self.instance.websites.all()

    def clean_available_websites(self):
        websites = self.cleaned_data['available_websites']
        if self.instance.pk:
            removed = set(self.instance.websites.values_list('slug', flat=True)) - set(
                websites.values_list('slug', flat=True)
            )
            used = set(
                self.instance.posts.filter(publications__site_slug__in=removed)
                .values_list('publications__site_slug', flat=True)
            )
            if used:
                raise ValidationError(
                    _('These websites still use this term in articles: %(sites)s.')
                    % {'sites': ', '.join(sorted(used))}
                )
        return websites

    def _save_m2m(self):
        super()._save_m2m()
        self.instance.websites.set(self.cleaned_data['available_websites'])


class BlogCategoryAdminForm(BlogTaxonomyAdminForm):
    class Meta:
        model = BlogCategory
        fields = ('name',)


class BlogTagAdminForm(BlogTaxonomyAdminForm):
    class Meta:
        model = BlogTag
        fields = ('name',)


class BlogImageComparisonSelect(forms.Select):
    template_name = 'admin/blog/widgets/image_comparison_select.html'

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        comparisons = getattr(self, 'comparisons', {})
        for _group_name, options, _index in context['widget']['optgroups']:
            for option in options:
                comparison = comparisons.get(str(option['value']))
                if not comparison:
                    continue
                option['attrs'].update({
                    'data-first-preview': comparison.first_rendition_480.url,
                    'data-second-preview': comparison.second_rendition_480.url,
                    'data-first-alt': comparison.first_alt_text,
                    'data-second-alt': comparison.second_alt_text,
                    'data-caption-title': comparison.caption_title,
                    'data-caption-text': comparison.caption_text,
                })
        return context


class AuthorProfileAdminForm(forms.ModelForm):
    class Meta:
        model = AuthorProfile
        fields = ('user', 'profile_picture', 'public_author_name', 'slug')

    def clean_profile_picture(self):
        profile_picture = self.cleaned_data.get('profile_picture')
        if profile_picture and 'profile_picture' in self.changed_data:
            validate_image_bytes(profile_picture)
        return profile_picture


class BlogPostQuickStartForm(forms.Form):
    title = forms.CharField(
        label=_('Title'),
        max_length=BlogPost._meta.get_field('title').max_length,
        widget=forms.TextInput(attrs={'autofocus': True}),
    )
    site_slug = forms.ChoiceField(label=_('Site'))
    type = forms.ChoiceField(
        label=_('Type'),
        choices=BlogPost.Type.choices,
        initial=BlogPost.Type.ARTICLE,
    )
    category = forms.ModelChoiceField(
        label=_('Category'),
        queryset=BlogCategory.objects.all(),
        empty_label=None,
    )
    author = forms.ModelChoiceField(
        label=_('Author'),
        queryset=AuthorProfile.objects.all(),
        empty_label=None,
    )
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site_slug'].choices = get_blog_site_slug_choices()
        site_slug = self.data.get('site_slug') if self.is_bound else None
        queryset = BlogCategory.objects.all()
        if site_slug:
            queryset = queryset.filter(websites__slug=site_slug)
        else:
            queryset = queryset.filter(websites__slug__in=dict(get_blog_site_slug_choices()))
        self.fields['category'].queryset = queryset.distinct()


class PreviewWebsiteForm(forms.Form):
    site = forms.ChoiceField(label=_('Preview website'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site'].choices = get_blog_site_slug_choices()
        self.fields['site'].widget.attrs['aria-describedby'] = 'id_site_errors'


class BlogPostAdminForm(forms.ModelForm):
    publication_sites = forms.MultipleChoiceField(
        label=_('Publication sites'),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'blog-publication-picker__choices'}),
    )
    canonical_site_slug = forms.ChoiceField(
        label=_('Canonical site'),
        required=False,
        help_text=_('Preferred website for this article’s SEO URL; it must be one of the publication sites.'),
    )

    class Meta:
        model = BlogPost
        fields = (
            'publication_sites',
            'title',
            'type',
            'summary',
            'author',
            'seo_title',
            'seo_description',
            'featured_image',
            'category',
            'tags',
            'canonical_site_slug',
        )
        widgets = {
            'summary': forms.Textarea(attrs={'rows': 4}),
            'seo_description': forms.Textarea(attrs={'rows': 3}),
            'tags': BlogTagCheckboxSelectMultiple(attrs={'class': 'blog-tag-picker__choices'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['author'].required = True
        self.fields['author'].empty_label = None
        self.fields['category'].empty_label = None
        self.fields['publication_sites'].choices = get_blog_site_slug_choices()
        if self.instance.pk:
            self.initial['publication_sites'] = list(
                self.instance.publications.values_list('site_slug', flat=True)
            )
        self.fields['featured_image'].help_text = _(
            'Main image shown with this article on blog lists and article pages. '
            'Preferred source resolution: 1600 × 900 px (16:9).'
        )
        self.fields['featured_image'].queryset = BlogImage.objects.filter(is_feature=True)
        self.fields['canonical_site_slug'].choices = [
            ('', '---------'),
            *get_blog_site_slug_choices(),
        ]
        self._filter_taxonomy_choices()

    def _filter_taxonomy_choices(self):
        sites = self._projected_publication_sites()
        if not sites and self.instance.pk:
            sites = set(self.instance.publications.values_list('site_slug', flat=True))

        categories = BlogCategory.objects.all()
        tags = BlogTag.objects.all()
        for site_slug in sites:
            categories = categories.filter(websites__slug=site_slug)
            tags = tags.filter(websites__slug=site_slug)
        if self.instance.pk:
            categories = BlogCategory.objects.filter(
                Q(pk=self.instance.category_id) | Q(pk__in=categories.values('pk'))
            )
            tags = BlogTag.objects.filter(
                Q(posts=self.instance) | Q(pk__in=tags.values('pk'))
            )
        self.fields['category'].queryset = categories.distinct()
        self.fields['tags'].queryset = tags.distinct()

        def label(term):
            websites = ', '.join(str(site) for site in term.websites.all()) or str(_('No websites'))
            return f'{term.name} — {websites}'

        self.fields['category'].label_from_instance = label
        self.fields['tags'].label_from_instance = label

    def clean(self):
        cleaned_data = super().clean()
        site_slugs = self._projected_publication_sites()
        category = cleaned_data.get('category')
        if category and site_slugs:
            missing = site_slugs - set(category.websites.values_list('slug', flat=True))
            if missing:
                self.add_error(
                    'category',
                    _('The category is not available on every selected website: %(sites)s.')
                    % {'sites': ', '.join(sorted(missing))},
                )
        tags = cleaned_data.get('tags')
        if tags is not None and site_slugs:
            invalid = [
                tag.name
                for tag in tags
                if site_slugs - set(tag.websites.values_list('slug', flat=True))
            ]
            if invalid:
                self.add_error(
                    'tags',
                    _('These tags are not available on every selected website: %(tags)s.')
                    % {'tags': ', '.join(invalid)},
                )
        self._validate_incoming_related_links()
        self._validate_internal_links()
        if self.instance.pk and self.instance.is_effectively_public():
            site_slugs = self._projected_publication_sites()
            canonical_site_slug = cleaned_data.get('canonical_site_slug')
            if not site_slugs:
                self.add_error(None, _('A public article must remain assigned to at least one site.'))
            elif canonical_site_slug not in site_slugs:
                self.add_error('canonical_site_slug', _('Canonical site must match one assigned site.'))
            if not self._projected_body_exists():
                self.add_error(None, _('A public article must retain at least one content block.'))

        return cleaned_data

    def _validate_internal_links(self):
        if not self.instance.pk or self.errors.get('publication_sites'):
            return
        site_slugs = self._projected_publication_sites()
        prefix = BlogInternalLinkBlock._meta.get_field('parent').remote_field.get_accessor_name()
        total_key = f'{prefix}-TOTAL_FORMS'
        if total_key not in self.data:
            blocks = BlogInternalLinkBlock.objects.filter(parent=self.instance)
            invalid_labels = []
            for block in blocks:
                try:
                    validate_internal_link_destination(block.destination_key, site_slugs)
                except ValidationError:
                    invalid_labels.append(block.label or block.destination_key)
        else:
            invalid_labels = []
            for index in range(self._form_count(total_key)):
                form_prefix = f'{prefix}-{index}-'
                if self.data.get(f'{form_prefix}DELETE'):
                    continue
                key = self.data.get(f'{form_prefix}destination_key', '').strip()
                if not key:
                    continue
                try:
                    validate_internal_link_destination(key, site_slugs)
                except ValidationError:
                    invalid_labels.append(self.data.get(f'{form_prefix}label', '').strip() or key)
        if invalid_labels:
            self.add_error(
                'publication_sites',
                _('These internal links are not available on every selected website: %(labels)s.')
                % {'labels': ', '.join(invalid_labels)},
            )
        from .internal_links import validate_inline_internal_links

        rich_prefix = BlogRichTextBlock._meta.get_field('parent').remote_field.get_accessor_name()
        rich_total_key = f'{rich_prefix}-TOTAL_FORMS'
        if rich_total_key not in self.data:
            rich_bodies = BlogRichTextBlock.objects.filter(parent=self.instance).values_list('body', flat=True)
        else:
            rich_bodies = [
                self.data.get(f'{rich_prefix}-{index}-body', '')
                for index in range(self._form_count(rich_total_key))
                if not self.data.get(f'{rich_prefix}-{index}-DELETE')
            ]
        rich_invalid = False
        for body in rich_bodies:
            try:
                validate_inline_internal_links(body, site_slugs)
            except ValidationError:
                rich_invalid = True
                break
        if rich_invalid:
            self.add_error(
                'publication_sites',
                _('Some rich-text internal links are not available on every selected publication website.'),
            )

        faq_prefix = BlogFAQBlock._meta.get_field('parent').remote_field.get_accessor_name()
        faq_total_key = f'{faq_prefix}-TOTAL_FORMS'
        if faq_total_key not in self.data:
            faq_values = BlogFAQBlock.objects.filter(parent=self.instance).values_list('items', flat=True)
        else:
            faq_values = [
                self.data.get(f'{faq_prefix}-{index}-items', '[]')
                for index in range(self._form_count(faq_total_key))
                if not self.data.get(f'{faq_prefix}-{index}-DELETE')
            ]
        faq_invalid = False
        for value in faq_values:
            try:
                items = json.loads(value) if isinstance(value, str) else value
                for answer in (item['answer'] for item in normalize_faq_items(items)):
                    validate_inline_internal_links(answer, site_slugs)
            except (TypeError, ValueError, ValidationError):
                faq_invalid = True
                break
        if faq_invalid:
            self.add_error(
                'publication_sites',
                _('Some FAQ answers contain invalid internal links or content.'),
            )

    def _validate_incoming_related_links(self):
        if not self.instance.pk or self.errors.get('publication_sites'):
            return

        current_sites = get_publication_site_slugs(self.instance)
        projected_sites = self._projected_publication_sites()
        if projected_sites == current_sites:
            return

        conflicting_links = list(
            get_incompatible_incoming_related_links(
                target_post=self.instance,
                target_site_slugs=projected_sites,
            )
        )
        if not conflicting_links:
            return

        titles = ', '.join(link.post.title for link in conflicting_links)
        self.add_error(
            'publication_sites',
            _(
                'Cannot remove a publication website while these related articles depend on it: '
                '%(titles)s. Remove or replace the relationships first.'
            ) % {'titles': titles},
        )

    def _projected_publication_sites(self):
        if self.fields['publication_sites'].disabled:
            return set(self.instance.publications.values_list('site_slug', flat=True))

        if 'publication_sites' in self.data:
            values = (
                self.data.getlist('publication_sites')
                if hasattr(self.data, 'getlist')
                else self.data.get('publication_sites', ())
            )
            return set(values)

        return set()

    def _projected_body_exists(self):
        projected_count = 0
        for block_model in BLOG_BLOCK_MODELS:
            prefix = block_model._meta.get_field('parent').remote_field.get_accessor_name()
            total_key = f'{prefix}-TOTAL_FORMS'
            if total_key not in self.data:
                if block_model is BlogFAQBlock:
                    projected_count += sum(
                        _faq_has_complete_items(items)
                        for items in block_model.objects.filter(
                            parent=self.instance,
                            region='main',
                        ).values_list('items', flat=True)
                    )
                elif block_model is BlogEmbedSharingBlock:
                    projected_count += sum(
                        self._embed_has_complete_content(block)
                        for block in block_model.objects.filter(parent=self.instance, region='main')
                    )
                else:
                    projected_count += block_model.objects.filter(parent=self.instance, region='main').count()
                continue
            for index in range(self._form_count(total_key)):
                form_prefix = f'{prefix}-{index}-'
                if self.data.get(f'{form_prefix}DELETE'):
                    continue
                if block_model is BlogFAQBlock:
                    try:
                        faq_items = json.loads(self.data.get(f'{form_prefix}items', '[]'))
                        projected_count += _faq_has_complete_items(faq_items)
                    except (TypeError, ValueError, ValidationError):
                        pass
                elif block_model is BlogEmbedSharingBlock:
                    projected_count += self._embed_has_complete_content(
                        {
                            'platform': self.data.get(f'{form_prefix}platform', ''),
                            'url': self.data.get(f'{form_prefix}url', ''),
                        }
                    )
                elif self.data.get(f'{form_prefix}id') or any(
                    value
                    for key, value in self.data.items()
                    if key.startswith(form_prefix)
                    and key.removeprefix(form_prefix) not in {'id', 'parent', 'region', 'ordering', 'ORDER', 'DELETE'}
                ):
                    projected_count += 1
        return projected_count > 0

    @staticmethod
    def _embed_has_complete_content(block):
        try:
            normalize_embed_reference(
                block.get('platform') if isinstance(block, dict) else block.platform,
                block.get('url') if isinstance(block, dict) else block.url,
            )
        except (InvalidEmbedReference, TypeError, ValueError):
            return False
        return True

    def _form_count(self, key):
        try:
            return max(0, int(self.data.get(key, 0)))
        except (TypeError, ValueError):
            return 0


class BlogPostPublicationForm(forms.ModelForm):
    site_slug = forms.ChoiceField(label=_('Site'))

    class Meta:
        model = BlogPostPublication
        fields = ('site_slug',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site_slug'].choices = get_blog_site_slug_choices()


class BlogPostRelatedForm(forms.ModelForm):
    def __init__(
        self,
        *args,
        source_post=None,
        source_site_slugs=None,
        related_post_choices=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if source_post is not None:
            self.source_post = source_post
        elif self.instance.post_id:
            self.source_post = self.instance.post
        else:
            self.source_post = None
        self.source_site_slugs = (
            get_publication_site_slugs(self.source_post)
            if source_site_slugs is None
            else set(source_site_slugs)
        )
        # ModelForm calls model.clean() after field cleaning, so keep that
        # validation aligned with the publication sites projected by the formset.
        self.instance._validation_source_site_slugs = self.source_site_slugs
        self.fields['related_post'].queryset = get_compatible_related_posts(
            source_post=self.source_post,
            source_site_slugs=self.source_site_slugs,
        )
        if related_post_choices is not None:
            self.fields['related_post'].choices = related_post_choices
        self.fields['related_post'].error_messages['invalid_choice'] = RELATED_POST_COMPATIBILITY_ERROR
        site_names = dict(get_blog_site_slug_choices())
        required_names = [
            site_names[site_slug]
            for site_slug, _name in get_blog_site_slug_choices()
            if site_slug in self.source_site_slugs
        ]
        if required_names:
            self.fields['related_post'].help_text = _(
                'Choose an article assigned to every publication website: %(sites)s.'
            ) % {'sites': ', '.join(required_names)}
        else:
            self.fields['related_post'].help_text = _(
                'Assign this article to a publication website before choosing related articles.'
            )

    def clean_related_post(self):
        related_post = self.cleaned_data.get('related_post')
        if related_post and not are_related_posts_compatible(
            source_post=self.source_post,
            target_post=related_post,
            source_site_slugs=self.source_site_slugs,
        ):
            raise ValidationError(RELATED_POST_COMPATIBILITY_ERROR)
        return related_post

    class Meta:
        model = BlogPostRelated
        fields = ('related_post', 'position')


class BlogRelatedInlineFormSet(BaseInlineFormSet):
    def __init__(self, *args, publication_sites_editable=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.publication_sites_editable = publication_sites_editable
        if self.is_bound and publication_sites_editable and 'publication_sites' in self.data:
            values = (
                self.data.getlist('publication_sites')
                if hasattr(self.data, 'getlist')
                else self.data.get('publication_sites', ())
            )
            if isinstance(values, str):
                values = (values,)
            self.source_site_slugs = set(values)
        elif self.is_bound and self.publication_sites_editable:
            self.source_site_slugs = set()
        else:
            self.source_site_slugs = get_publication_site_slugs(self.instance)
        related_posts = list(
            get_compatible_related_posts(
                source_post=self.instance,
                source_site_slugs=self.source_site_slugs,
            )
        )
        related_post_field = self.form.base_fields['related_post']
        self.related_post_choices = [
            *(
                [('', related_post_field.empty_label)]
                if related_post_field.empty_label is not None
                else []
            ),
            *[
                (post.pk, related_post_field.label_from_instance(post))
                for post in related_posts
            ],
        ]

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs.update(
            {
                'source_post': self.instance,
                'source_site_slugs': self.source_site_slugs,
                'related_post_choices': self.related_post_choices,
            }
        )
        return kwargs

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        for form in self.forms:
            if self._should_delete_form(form):
                continue
            related_post = form.cleaned_data.get('related_post')
            if related_post and not are_related_posts_compatible(
                source_post=self.instance,
                target_post=related_post,
                source_site_slugs=self.source_site_slugs,
            ):
                form.add_error('related_post', RELATED_POST_COMPATIBILITY_ERROR)


class BlogHeadingBlockForm(forms.ModelForm):
    anchor = forms.CharField(
        required=False,
        disabled=True,
        widget=forms.HiddenInput(),
    )

    class Meta:
        model = BlogHeadingBlock
        fields = ('level', 'text', 'anchor', 'region', 'ordering')

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('text'):
            cleaned_data['anchor'] = slugify(cleaned_data['text'])
        return cleaned_data


class BlogEmbedSharingBlockForm(forms.ModelForm):
    platform = forms.ChoiceField(
        label=_('Platform'),
        choices=PLATFORM_CHOICES,
        required=False,
        error_messages={'invalid_choice': _('Choose a supported platform.')},
    )
    url = forms.CharField(
        label=_('Content URL'),
        required=False,
        max_length=500,
        help_text=_('Paste a public YouTube video, X post, or Reddit post URL.'),
    )
    caption = forms.CharField(
        label=_('Caption (optional)'),
        required=False,
        max_length=300,
        help_text=_('Briefly explain why this content is relevant to the article.'),
        widget=forms.TextInput(attrs={'maxlength': '300'}),
        error_messages={'max_length': _('Caption must be 300 characters or fewer.')},
    )

    class Meta:
        model = BlogEmbedSharingBlock
        fields = ('platform', 'url', 'caption', 'region', 'ordering')
        widgets = {
            'region': forms.HiddenInput(),
            'ordering': forms.HiddenInput(),
        }

    def clean(self):
        cleaned_data = super().clean()
        platform = (
            cleaned_data.get('platform')
            if 'platform' in cleaned_data
            else self.data.get(self.add_prefix('platform'), '')
        )
        platform = (platform or '').strip()
        url = (cleaned_data.get('url') or '').strip()
        caption = (cleaned_data.get('caption') or '').strip()
        cleaned_data.update({'platform': platform, 'url': url, 'caption': caption})

        if not platform and not url and not caption:
            return cleaned_data

        if not platform:
            if not self.errors.get('platform'):
                self.add_error('platform', _('Choose a platform.'))
        elif platform not in {choice[0] for choice in PLATFORM_CHOICES}:
            if not self.errors.get('platform'):
                self.add_error('platform', _('Choose a supported platform.'))

        if not url:
            self.add_error('url', _('Enter a content URL.'))

        reference = None
        if platform in {choice[0] for choice in PLATFORM_CHOICES} and url:
            try:
                reference = normalize_embed_reference(platform, url)
            except InvalidEmbedReference:
                self.add_error('url', INVALID_EMBED_REFERENCE_MESSAGE)
            else:
                cleaned_data['url'] = reference.canonical_url

        if len(caption) > 300:
            self.add_error('caption', _('Caption must be 300 characters or fewer.'))
        elif strip_tags(caption) != caption:
            self.add_error('caption', _('Caption must be plain text.'))

        if reference is not None and not self.errors and self._requires_live_verification():
            try:
                verify_reference(reference)
            except (UnsupportedEmbedItem, EmbedVerificationUnavailable) as error:
                self.add_error('url', str(error))

        return cleaned_data

    def _post_clean(self):
        super()._post_clean()
        # Model.clean() repeats the same local checks so direct model writes
        # stay safe; collapse duplicate messages in the Admin form rendering.
        for field_name, errors in self._errors.items():
            unique_messages = []
            seen = set()
            for error in errors.as_data():
                for message in error.messages:
                    if message not in seen:
                        seen.add(message)
                        unique_messages.append(message)
            self._errors[field_name] = self.error_class(unique_messages)

    def _requires_live_verification(self):
        if not self.instance.parent_id:
            return False
        parent = self.instance.parent
        if (
            parent.status != BlogPost.Status.SCHEDULED
            and not parent.is_effectively_public()
        ):
            return False
        return bool({'platform', 'url'} & set(self.changed_data))


class BlogRichTextBlockForm(forms.ModelForm):
    class Meta:
        model = BlogRichTextBlock
        fields = ('body', 'region', 'ordering')

    def __init__(self, *args, site_slugs=None, **kwargs):
        super().__init__(*args, **kwargs)
        if site_slugs is None and self.instance.parent_id:
            site_slugs = get_publication_site_slugs(self.instance.parent)
        self.site_slugs = set(site_slugs or ())
        self.fields['body'].required = False
        self.fields['body'].widget.attrs['data-blog-rich-text'] = 'true'

    def clean_body(self):
        body = self.cleaned_data.get('body', '')
        from .internal_links import validate_inline_internal_links

        validate_inline_internal_links(body, self.site_slugs)
        return body


class BlogRichTextInlineFormSet(BaseInlineFormSet):
    def __init__(self, *args, publication_sites_editable=True, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound and publication_sites_editable and 'publication_sites' in self.data:
            values = self.data.getlist('publication_sites') if hasattr(self.data, 'getlist') else self.data.get('publication_sites', ())
            self.site_slugs = {values} if isinstance(values, str) else set(values)
        elif self.is_bound and publication_sites_editable:
            self.site_slugs = set()
        else:
            self.site_slugs = get_publication_site_slugs(self.instance)

    def get_form_kwargs(self, index):
        return {**super().get_form_kwargs(index), 'site_slugs': self.site_slugs}


class BlogFAQItemsField(forms.Field):
    default_error_messages = {
        'invalid_json': _('The FAQ content could not be read. Review the questions and try again.'),
    }

    def __init__(self, *args, site_slugs=None, **kwargs):
        kwargs.setdefault('required', False)
        kwargs.setdefault('widget', BlogFAQItemsWidget())
        super().__init__(*args, **kwargs)
        self.site_slugs = set(site_slugs or ())

    def clean(self, value):
        if isinstance(value, str):
            try:
                value = json.loads(value or '[]')
            except (TypeError, ValueError):
                raise ValidationError(self.error_messages['invalid_json'], code='invalid_json')
        items = normalize_faq_items(value if value is not None else [])
        from .internal_links import validate_inline_internal_links

        for answer in (item['answer'] for item in items):
            validate_inline_internal_links(answer, self.site_slugs)
        return items


class BlogFAQBlockForm(forms.ModelForm):
    items = BlogFAQItemsField(label=_('FAQ'))

    class Meta:
        model = BlogFAQBlock
        fields = ('items', 'region', 'ordering')

    def __init__(self, *args, site_slugs=None, **kwargs):
        super().__init__(*args, **kwargs)
        if site_slugs is None and self.instance.parent_id:
            site_slugs = get_publication_site_slugs(self.instance.parent)
        self.fields['items'].site_slugs = set(site_slugs or ())


class BlogFAQInlineFormSet(BaseInlineFormSet):
    def __init__(self, *args, publication_sites_editable=True, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound and publication_sites_editable and 'publication_sites' in self.data:
            values = self.data.getlist('publication_sites') if hasattr(self.data, 'getlist') else self.data.get('publication_sites', ())
            self.site_slugs = {values} if isinstance(values, str) else set(values)
        elif self.is_bound and publication_sites_editable:
            self.site_slugs = set()
        else:
            self.site_slugs = get_publication_site_slugs(self.instance)

    def get_form_kwargs(self, index):
        return {**super().get_form_kwargs(index), 'site_slugs': self.site_slugs}


class BlogChecklistBlockForm(forms.ModelForm):
    marker = forms.ChoiceField(
        label=_('Checklist marker'),
        choices=BlogChecklistBlock.Marker.choices,
    )
    items = forms.CharField(
        label=_('Checklist items'),
        widget=forms.Textarea(attrs={'rows': 5}),
        help_text=_('Enter one item per line.'),
    )

    class Meta:
        model = BlogChecklistBlock
        fields = ('marker', 'items', 'region', 'ordering')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and isinstance(self.instance.items, list):
            self.initial['items'] = '\n'.join(self.instance.items)

    def clean_items(self):
        items = [strip_tags(item).strip() for item in self.cleaned_data['items'].splitlines() if strip_tags(item).strip()]
        if not items:
            raise ValidationError(_('Add at least one checklist item.'))
        return items


class BlogLinkGroupBlockForm(forms.ModelForm):
    links = forms.CharField(
        label=_('Links'),
        widget=forms.Textarea(attrs={'rows': 5}),
        help_text=_('Enter one link per line as: Label | https://example.com'),
    )

    class Meta:
        model = BlogLinkGroupBlock
        fields = ('label', 'links', 'region', 'ordering')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and isinstance(self.instance.links, list):
            self.initial['links'] = '\n'.join(
                f"{link.get('label', '')} | {link.get('url', '')}"
                for link in self.instance.links
                if isinstance(link, dict)
            )

    def clean_links(self):
        links = []
        for line in self.cleaned_data['links'].splitlines():
            line = line.strip()
            if not line:
                continue
            if '|' not in line:
                raise ValidationError(_('Use one link per line as: Label | https://example.com'))
            label, url = (part.strip() for part in line.split('|', 1))
            if not label:
                raise ValidationError(_('Each link must have a label.'))
            try:
                URLValidator(schemes=['http', 'https'])(url)
            except ValidationError:
                raise ValidationError(_('Each link must use an absolute HTTP(S) URL.'))
            links.append({'label': label, 'url': url})
        if not links:
            raise ValidationError(_('Add at least one link.'))
        return links


class BlogInternalLinkBlockForm(forms.ModelForm):
    destination_key = forms.ChoiceField(
        label=_('Destination'),
        help_text=_('Choose a destination available on all selected sites.'),
    )

    class Meta:
        model = BlogInternalLinkBlock
        fields = ('destination_key', 'label', 'note', 'region', 'ordering')
        help_texts = {
            'label': _('Write anchor text that describes where the link goes and helps the reader.'),
            'note': _('Optional short context for the reader.'),
        }

    def __init__(self, *args, site_slugs=None, **kwargs):
        super().__init__(*args, **kwargs)
        if site_slugs is None and self.instance.parent_id:
            site_slugs = get_publication_site_slugs(self.instance.parent)
        self.site_slugs = set(site_slugs or ())
        self.instance._validation_site_slugs = self.site_slugs
        self.fields['destination_key'].choices = get_internal_link_choices(self.site_slugs)

    def clean_destination_key(self):
        key = self.cleaned_data['destination_key']
        validate_internal_link_destination(key, self.site_slugs)
        return key


class BlogInternalLinkInlineFormSet(BaseInlineFormSet):
    def __init__(self, *args, publication_sites_editable=True, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound and publication_sites_editable and 'publication_sites' in self.data:
            values = self.data.getlist('publication_sites') if hasattr(self.data, 'getlist') else self.data.get('publication_sites', ())
            self.site_slugs = {values} if isinstance(values, str) else set(values)
        elif self.is_bound and publication_sites_editable:
            self.site_slugs = set()
        else:
            self.site_slugs = get_publication_site_slugs(self.instance)

    def get_form_kwargs(self, index):
        return {**super().get_form_kwargs(index), 'site_slugs': self.site_slugs}


class BlogImageAdminForm(forms.ModelForm):
    class Meta:
        model = BlogImage
        fields = (
            'name',
            'original',
            'alt_text',
            'is_decorative',
            'is_feature',
            'caption_title',
            'caption_text',
        )
        labels = {
            'caption_title': _('Caption title (bold)'),
            'caption_text': _('Caption text'),
        }

    def clean_original(self):
        original = self.cleaned_data.get('original')
        if original and hasattr(original, 'read'):
            validate_image_bytes(original)
        return original


class BlogImageBlockForm(forms.ModelForm):
    class Meta:
        model = BlogImageBlock
        fields = ('image', 'is_expandable', 'region', 'ordering')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['image'].queryset = BlogImage.objects.filter(is_feature=False)


class BlogImageComparisonAdminForm(forms.ModelForm):
    first_original = forms.ImageField(label=_('First image'), required=False)
    first_alt_text = forms.CharField(label=_('Alternative text'), max_length=255, required=False)
    second_original = forms.ImageField(label=_('Second image'), required=False)
    second_alt_text = forms.CharField(label=_('Alternative text'), max_length=255, required=False)

    class Meta:
        model = BlogImageComparison
        fields = ('name', 'caption_title', 'caption_text')
        labels = {
            'caption_title': _('Caption title (bold)'),
            'caption_text': _('Caption text'),
        }
        help_texts = {
            'name': _('Internal name used to identify this reusable pair in the Admin.'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields([
            'name',
            'first_original',
            'first_alt_text',
            'second_original',
            'second_alt_text',
            'caption_title',
            'caption_text',
        ])
        for side in ('first', 'second'):
            original = getattr(self.instance, f'{side}_original', None)
            alt_text = getattr(self.instance, f'{side}_alt_text', '')
            self.initial[f'{side}_alt_text'] = alt_text
            if original:
                self.initial[f'{side}_original'] = original.name
            self.fields[f'{side}_original'].required = not bool(self.instance.pk and original)
            self.fields[f'{side}_alt_text'].required = True

    def _clean_uploaded_original(self, side):
        original = self.cleaned_data.get(f'{side}_original')
        if original and hasattr(original, 'read'):
            validate_image_bytes(original)
        return original

    def clean_first_original(self):
        return self._clean_uploaded_original('first')

    def clean_second_original(self):
        return self._clean_uploaded_original('second')

    def clean(self):
        cleaned_data = super().clean()
        errors = {}
        for side, label in (('first', _('first')), ('second', _('second'))):
            original = cleaned_data.get(f'{side}_original')
            alt_text = cleaned_data.get(f'{side}_alt_text', '')
            existing_original = getattr(self.instance, f'{side}_original', None)
            if not original and not existing_original:
                errors[f'{side}_original'] = _('Choose the %(side)s comparison image.') % {'side': label}
            if not alt_text.strip():
                errors[f'{side}_alt_text'] = _(
                    'Describe the %(side)s comparison image for readers who cannot see it.'
                ) % {'side': label}

            if original:
                setattr(self.instance, f'{side}_original', original)
            setattr(self.instance, f'{side}_alt_text', alt_text)

        for field_name, message in errors.items():
            self.add_error(field_name, message)
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        for side in ('first', 'second'):
            original = self.cleaned_data.get(f'{side}_original')
            if original:
                setattr(instance, f'{side}_original', original)
            setattr(instance, f'{side}_alt_text', self.cleaned_data[f'{side}_alt_text'])
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class BlogImageComparisonBlockForm(forms.ModelForm):
    class Meta:
        model = BlogImageComparisonBlock
        fields = ('comparison', 'region', 'ordering')
        widgets = {
            'comparison': BlogImageComparisonSelect(attrs={'data-blog-comparison-select': 'true'}),
        }
        help_texts = {
            'comparison': _(
                'Upload exactly two images. They will be shown together and can be opened as a two-image comparison.'
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = get_selectable_image_comparisons()
        self.fields['comparison'].queryset = queryset
        self.fields['comparison'].widget.comparisons = {
            str(comparison.pk): comparison
            for comparison in queryset
        }
        self.fields['comparison'].error_messages['invalid_choice'] = _(
            'Choose a fully processed comparison with both stored image pairs available.'
        )

    def clean_comparison(self):
        comparison = self.cleaned_data.get('comparison')
        if comparison and not comparison.is_ready_for_publication():
            raise ValidationError(_('Both comparison images must be ready before this can be saved or published.'))
        return comparison


class SchedulePostForm(forms.Form):
    publish_at = forms.SplitDateTimeField(
        label=_('Publish at (Europe/Brussels)'),
        widget=AdminSplitDateTime,
    )

    def clean_publish_at(self):
        value = self.cleaned_data['publish_at']
        if timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_current_timezone())
        if value <= timezone.now():
            raise ValidationError(_('Choose a future publication time.'))
        return value


class MarkReviewedForm(forms.Form):
    reviewed_on = forms.DateField(
        label=_('Reviewed on'),
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )

    def clean_reviewed_on(self):
        value = self.cleaned_data['reviewed_on']
        if value > timezone.localdate():
            raise ValidationError(_('The review date cannot be in the future.'))
        return value


class ConfirmActionForm(forms.Form):
    confirm = forms.BooleanField(label=_('Confirm this action.'), required=True)


class BlogCalloutBlockForm(forms.ModelForm):
    class Meta:
        model = BlogCalloutBlock
        fields = ('callout_type', 'title', 'body', 'region', 'ordering')
