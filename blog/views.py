from dataclasses import replace

from django.core.paginator import EmptyPage, InvalidPage, Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template import TemplateDoesNotExist, TemplateSyntaxError
from django.template.loader import get_template
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.sites import (
    PERSONAL_SITE,
    build_site_absolute_url,
    get_blog_site_slug_choices,
    get_site_template_name,
    require_site_for_host,
)

from .image_services import image_sources
from .filters import (
    DATE_PRESETS,
    SEARCH_PARAMETER,
    TYPE_PARAMETER,
    FilterState,
    active_filters as build_active_filters,
    date_filter_options,
    parse_filter_state,
    serialize_filter_state,
)
from .rendering import (
    build_article_context,
    get_reading_time_minutes,
    get_blog_stylesheet,
    get_rss_feed_metadata,
    get_site_social_image,
)
from .models import AuthorProfile, BlogCategory, BlogTag
from .selectors import (
    get_public_filter_options,
    get_public_post_by_slug,
    get_public_posts,
    get_related_public_posts,
)
from .urls_helpers import reverse_blog

PAGE_SIZE = 12
BLOG_LIST_IMAGE_SIZES = (
    '(min-width: 1024px) calc((min(100vw, 1440px) - 7rem) / 3), '
    '(min-width: 640px) calc((100vw - 5.5rem) / 2), '
    'calc(100vw - 3rem)'
)


def get_blog_template(site, template_name):
    return get_site_template_name(site, f'blog/{template_name}.html')


def get_preview_blog_template(site, template_name='detail'):
    if site is None:
        return None
    template_name = get_blog_template(site, template_name)
    try:
        get_template(template_name)
    except (TemplateDoesNotExist, TemplateSyntaxError):
        return None
    return template_name


def resolve_preview_site_slug(post, choices=None):
    if choices is None:
        choices = get_blog_site_slug_choices()
    available_slugs = {site_slug for site_slug, _site_name in choices}
    if post.canonical_site_slug in available_slugs:
        return post.canonical_site_slug

    assigned_slugs = {
        site_slug
        for site_slug in post.publications.values_list('site_slug', flat=True)
    }
    for site_slug, _site_name in choices:
        if site_slug in assigned_slugs:
            return site_slug
    return choices[0][0] if choices else None


def _english_only(request):
    if request.LANGUAGE_CODE != 'en':
        raise Http404(_('That article is not available.'))


def _filter_query_string(request):
    query = request.GET.copy()
    query.pop('page', None)
    return query.urlencode()


def _valid_page_number(request):
    raw_page = request.GET.get('page')
    if raw_page is None:
        return None
    try:
        page_number = int(raw_page)
    except (TypeError, ValueError):
        return None
    return page_number if page_number > 0 else None


def _reverse_blog(request, view_name, *, kwargs=None):
    return reverse_blog(
        view_name,
        current_app=request.resolver_match.namespace,
        kwargs=kwargs,
    )


def _filter_redirect_url(request, state):
    page_number = _valid_page_number(request)
    query_string = serialize_filter_state(state, page=page_number)
    list_path = _reverse_blog(request, 'list')
    return f'{list_path}?{query_string}' if query_string else list_path


def _archive_search_redirect(request, site, *, tag=None, category=None, author=None):
    if SEARCH_PARAMETER not in request.GET:
        return None

    now = timezone.now()
    options = get_public_filter_options(site_slug=site.slug, now=now)
    if tag and tag.slug not in options.tag_values:
        raise Http404(_('That tag is not available.'))
    if category and category.slug not in options.category_values:
        raise Http404(_('That category is not available.'))
    if author and author.slug not in options.author_values:
        raise Http404(_('That author is not available.'))

    state = parse_filter_state(request.GET, options)
    if state.search_query:
        if tag:
            selected_tags = set(state.tag_slugs) | {tag.slug}
            state = replace(
                state,
                tag_slugs=tuple(
                    option.value
                    for option in options.tags
                    if option.value in selected_tags
                ),
            )
        elif category:
            state = replace(state, category_slug=category.slug)
        else:
            state = replace(state, author_slug=author.slug)
        return redirect(_filter_redirect_url(request, state))

    if tag:
        archive_path = _reverse_blog(request, 'tag', kwargs={'slug': tag.slug})
    elif category:
        archive_path = _reverse_blog(request, 'category', kwargs={'slug': category.slug})
    else:
        archive_path = _reverse_blog(request, 'author', kwargs={'author_slug': author.slug})
    page_number = _valid_page_number(request)
    return redirect(
        f'{archive_path}?page={page_number}' if page_number is not None else archive_path
    )


def _filter_summaries(state, options):
    category_labels = {option.value: option.label for option in options.categories}
    author_labels = {option.value: option.label for option in options.authors}
    tag_labels = {option.value: option.label for option in options.tags}
    date_labels = {value: label for value, label, _days in DATE_PRESETS}
    return {
        'category': (
            str(category_labels[state.category_slug])
            if state.category_slug
            else _('Any category')
        ),
        'author': (
            str(author_labels[state.author_slug])
            if state.author_slug
            else _('Any author')
        ),
        'date': (
            str(date_labels[state.date_preset])
            if state.date_preset
            else str(state.year)
            if state.year
            else _('Any date')
        ),
        'tags': ', '.join(str(tag_labels[value]) for value in state.tag_slugs) or _('Any tags'),
    }


def _archive_heading(*, tag=None, category=None, author=None):
    if tag:
        return _('%(tag_name)s articles') % {'tag_name': tag.name}
    if category:
        return _('%(category_name)s articles') % {'category_name': category.name}
    if author:
        return _('Articles by %(author_name)s') % {
            'author_name': author.public_author_name,
        }
    return ''


def _post_list_pagination_url(list_path, state, page_number):
    query_string = serialize_filter_state(state, page=page_number)
    return f'{list_path}?{query_string}'


def _article_type_links(list_path, state, options, excluded_values=()):
    links = []
    article_types = ((None, _('All')),)
    article_type_labels = {
        'article': _('Articles'),
        'guide': _('Guides'),
        'comparison': _('Comparisons'),
        'top_list': _('Top lists'),
        'showcase': _('Showcases'),
    }
    article_types += tuple(
        (option.value, article_type_labels.get(option.value, option.label))
        for option in options.article_types
        if option.value not in excluded_values
    )
    for value, label in article_types:
        target_state = state.with_article_type(value)
        query_string = serialize_filter_state(target_state)
        links.append(
            {
                'label': label,
                'url': f'{list_path}?{query_string}' if query_string else list_path,
                'selected': state.article_type == value,
            }
        )
    return links


def _post_list_response(
    request,
    site,
    *,
    tag=None,
    category=None,
    author=None,
    filter_state=None,
    filter_options=None,
    now=None,
):
    now = now or timezone.now()
    active_archive = tag or category or author
    if active_archive and (filter_state is None or filter_options is None):
        filter_options = get_public_filter_options(site_slug=site.slug, now=now)
        filter_state = FilterState(
            category_slug=category.slug if category else None,
            author_slug=author.slug if author else None,
            tag_slugs=(tag.slug,) if tag else (),
        )
    posts = get_public_posts(
        site_slug=site.slug,
        now=now,
        tag_slug=tag.slug if tag else None,
        category_slug=category.slug if category else None,
        author=author,
        filters=filter_state,
    )
    if tag and not posts.exists():
        raise Http404(_('That tag is not available.'))
    if category and not posts.exists():
        raise Http404(_('That category is not available.'))
    if author and not posts.exists():
        raise Http404(_('That author is not available.'))

    paginator = Paginator(posts, PAGE_SIZE)
    page_number = request.GET.get('page', '1')
    try:
        page_obj = paginator.page(page_number)
    except (EmptyPage, InvalidPage):
        raise Http404(_('That page is not available.'))

    for post in page_obj.object_list:
        post.reading_time_minutes = get_reading_time_minutes(post)
        post.featured_image_data = image_sources(
            post.featured_image,
            sizes=BLOG_LIST_IMAGE_SIZES,
        )

    if tag:
        list_path = _reverse_blog(request, 'tag', kwargs={'slug': tag.slug})
    elif category:
        list_path = _reverse_blog(request, 'category', kwargs={'slug': category.slug})
    elif author:
        list_path = _reverse_blog(request, 'author', kwargs={'author_slug': author.slug})
    else:
        list_path = _reverse_blog(request, 'list')
    filter_list_path = _reverse_blog(request, 'list')
    list_url = build_site_absolute_url(site.slug, list_path)
    filter_active = bool(filter_state and filter_state.is_active and not active_archive)
    filters_selected = bool(filter_state and filter_state.is_active)
    search_active = bool(filter_state and filter_state.search_query)
    canonical_url = list_url
    if not filter_active and page_obj.number > 1:
        canonical_url = f'{list_url}?page={page_obj.number}'
    social_image = get_site_social_image(site.slug)
    pagination_title_suffix = ''
    pagination_description_suffix = ''
    if page_obj.number > 1:
        pagination_title_suffix = _(' — Page %(page_number)s') % {
            'page_number': page_obj.number,
        }
        pagination_description_suffix = _(' Page %(page_number)s.') % {
            'page_number': page_obj.number,
        }
    active_filter_context = []
    filter_query_string = ''
    filter_summaries = {}
    clear_tag_filters_url = filter_list_path
    clear_search_url = filter_list_path
    if filter_state is not None and filter_options is not None:
        filter_query_string = serialize_filter_state(filter_state)
        clear_tag_state = replace(filter_state, tag_slugs=())
        clear_tag_query = serialize_filter_state(clear_tag_state)
        clear_tag_filters_url = f'{filter_list_path}?{clear_tag_query}' if clear_tag_query else filter_list_path
        clear_search_state = filter_state.without(SEARCH_PARAMETER)
        clear_search_query = serialize_filter_state(clear_search_state)
        clear_search_url = (
            f'{filter_list_path}?{clear_search_query}'
            if clear_search_query
            else filter_list_path
        )
        for active_filter in build_active_filters(filter_state, filter_options):
            if active_filter.dimension == TYPE_PARAMETER:
                continue
            removed_state = filter_state.without(active_filter.dimension, active_filter.value)
            removed_query = serialize_filter_state(removed_state)
            active_filter_context.append(
                {
                    'dimension': active_filter.dimension_label,
                    'value': active_filter.value_label,
                    'url': f'{filter_list_path}?{removed_query}' if removed_query else filter_list_path,
                }
            )
        filter_summaries = _filter_summaries(filter_state, filter_options)
    previous_page_url = (
        _post_list_pagination_url(
            list_path,
            FilterState() if active_archive else filter_state or FilterState(),
            page_obj.previous_page_number(),
        )
        if page_obj.has_previous()
        else ''
    )
    next_page_url = (
        _post_list_pagination_url(
            list_path,
            FilterState() if active_archive else filter_state or FilterState(),
            page_obj.next_page_number(),
        )
        if page_obj.has_next()
        else ''
    )
    context = {
        'posts': page_obj.object_list,
        'page_obj': page_obj,
        'active_tag': tag,
        'active_category': category,
        'active_author_name': author.public_author_name if author else None,
        'active_archive': active_archive,
        'archive_heading': _archive_heading(tag=tag, category=category, author=author),
        'archive_indexable': not (tag or category or author) or paginator.count >= 2,
        'canonical_url': canonical_url,
        'seo_canonical_url': canonical_url,
        'seo_translation_urls': {},
        'seo_x_default_url': '',
        'seo_og_image_url': social_image['url'],
        'seo_og_image_alt': social_image['alt'],
        'pagination_title_suffix': pagination_title_suffix,
        'pagination_description_suffix': pagination_description_suffix,
        'blog_stylesheet': get_blog_stylesheet(site.slug),
        'show_blog_filters': filter_state is not None and filter_options is not None,
        'filter_active': filter_active,
        'filters_selected': filters_selected,
        'search_active': search_active,
        'filter_state': filter_state,
        'filter_options': filter_options,
        'article_type_links': (
            _article_type_links(
                filter_list_path,
                filter_state,
                filter_options,
                excluded_values=(
                    'comparison',
                    'top_list',
                    'showcase',
                ) if site.slug == PERSONAL_SITE else (),
            )
            if filter_state is not None and filter_options is not None
            else ()
        ),
        'date_filter_options': date_filter_options(filter_options.years) if filter_options else (),
        'active_filters': active_filter_context,
        'additional_filter_count': len(active_filter_context),
        'filter_query_string': filter_query_string,
        'filter_form_action': filter_list_path,
        'clear_filters_url': filter_list_path,
        'clear_search_url': clear_search_url,
        'clear_tag_filters_url': clear_tag_filters_url,
        'has_non_search_filters': bool(filter_state and filter_state.has_filters),
        'filter_summaries': filter_summaries,
        'previous_page_url': previous_page_url,
        'next_page_url': next_page_url,
        'pagination_form_action': list_path,
    }
    context.update(
        get_rss_feed_metadata(
            site.slug,
            current_app=request.resolver_match.namespace,
        )
    )
    response = render(
        request,
        get_blog_template(site, 'list'),
        context,
    )
    if filter_active:
        response['X-Robots-Tag'] = 'noindex, follow'
    elif (tag or category or author) and paginator.count < 2:
        response['X-Robots-Tag'] = 'noindex, follow'
    response['Content-Language'] = 'en'
    return response


def post_list(request):
    _english_only(request)
    site = require_site_for_host(request.get_host())
    now = timezone.now()
    filter_options = get_public_filter_options(site_slug=site.slug, now=now)
    filter_state = parse_filter_state(request.GET, filter_options)
    if _filter_query_string(request) != serialize_filter_state(filter_state):
        return redirect(_filter_redirect_url(request, filter_state))
    return _post_list_response(
        request,
        site,
        filter_state=filter_state,
        filter_options=filter_options,
        now=now,
    )


def tag_post_list(request, slug):
    _english_only(request)
    site = require_site_for_host(request.get_host())
    tag = get_object_or_404(BlogTag, slug=slug)
    search_redirect = _archive_search_redirect(request, site, tag=tag)
    if search_redirect is not None:
        return search_redirect
    return _post_list_response(request, site, tag=tag)


def category_post_list(request, slug):
    _english_only(request)
    site = require_site_for_host(request.get_host())
    category = get_object_or_404(BlogCategory, slug=slug)
    search_redirect = _archive_search_redirect(request, site, category=category)
    if search_redirect is not None:
        return search_redirect
    return _post_list_response(request, site, category=category)


def author_post_list(request, author_slug):
    _english_only(request)
    author = get_object_or_404(AuthorProfile, slug=author_slug)
    site = require_site_for_host(request.get_host())
    search_redirect = _archive_search_redirect(request, site, author=author)
    if search_redirect is not None:
        return search_redirect
    return _post_list_response(
        request,
        site,
        author=author,
    )


def post_detail(request, slug):
    _english_only(request)
    site = require_site_for_host(request.get_host())
    post = get_public_post_by_slug(slug=slug, site_slug=site.slug)
    related_posts = get_related_public_posts(post=post, site_slug=site.slug)
    context = build_article_context(
        post,
        request=request,
        site_slug=site.slug,
        related_posts=related_posts,
    )
    seo = context['seo']
    context.update(
        {
            'canonical_url': seo['canonical_url'],
            'seo_canonical_url': seo['canonical_url'],
            'seo_translation_urls': {},
            'seo_x_default_url': '',
            'seo_og_image_url': seo['og_image_url'],
        }
    )
    response = render(request, get_blog_template(site, 'detail'), context)
    response['Content-Language'] = 'en'
    return response
