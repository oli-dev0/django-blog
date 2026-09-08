from django.db.models import Case, Count, Exists, F, IntegerField, OuterRef, Q, Value, When
from django.db.models.functions import ExtractYear, Lower
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import override

from .urls_helpers import reverse_blog

from apps.core.sites import build_site_absolute_url

from .models import (
    AuthorProfile,
    BlogCategory,
    BlogImageComparison,
    BlogPost,
    BlogPostPublication,
    BlogPostRelated,
    BlogTag,
)
from .filters import FilterOptions, FilterOption, relative_date_bounds


def _effective_public_filter(now):
    return Q(status=BlogPost.Status.PUBLISHED) | Q(
        status=BlogPost.Status.SCHEDULED,
        published_at__lte=now,
    )


def _public_post_scope(*, site_slug, now):
    return BlogPost.objects.filter(
        _effective_public_filter(now),
        publications__site_slug=site_slug,
    ).distinct()


def _unique_search_terms(query):
    terms = []
    seen = set()
    for term in query.split():
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            terms.append(term)
    return tuple(terms)


def get_public_posts(
    *,
    site_slug,
    now=None,
    tag_slug=None,
    category_slug=None,
    author=None,
    filters=None,
):
    now = now or timezone.now()
    queryset = (
        _public_post_scope(site_slug=site_slug, now=now)
        .select_related('author', 'category', 'featured_image')
        .prefetch_related('tags')
        .defer('search_body_text')
        .order_by('-published_at', '-pk')
    )

    if filters is not None:
        if filters.article_type:
            queryset = queryset.filter(type=filters.article_type)
        if filters.category_slug:
            queryset = queryset.filter(category__slug=filters.category_slug)
        if filters.author_slug:
            queryset = queryset.filter(author__slug=filters.author_slug)
        if filters.date_preset:
            start, end = relative_date_bounds(now, filters.date_preset)
            queryset = queryset.filter(published_at__gte=start, published_at__lte=end)
        elif filters.year:
            queryset = queryset.filter(published_at__year=filters.year, published_at__lte=now)
        for selected_tag_slug in filters.tag_slugs:
            # Each relation predicate is deliberate: selected tags use AND semantics.
            queryset = queryset.filter(tags__slug=selected_tag_slug)
        if filters.search_query:
            query = filters.search_query
            terms = _unique_search_terms(query)
            aliases = {
                f'search_tag_word_{index}': Exists(
                    BlogTag.objects.filter(
                        posts=OuterRef('pk'),
                        name__icontains=word,
                    )
                )
                for index, word in enumerate(terms)
            }
            aliases['search_tag_phrase'] = Exists(
                BlogTag.objects.filter(
                    posts=OuterRef('pk'),
                    name__icontains=query,
                )
            )
            queryset = queryset.alias(**aliases)
            for index, word in enumerate(terms):
                queryset = queryset.filter(
                    Q(title__icontains=word)
                    | Q(summary__icontains=word)
                    | Q(category__name__icontains=word)
                    | Q(search_body_text__icontains=word)
                    | Q(**{f'search_tag_word_{index}': True})
                )
            queryset = queryset.annotate(
                search_relevance=Case(
                    When(title__icontains=query, then=Value(4)),
                    When(
                        Q(category__name__icontains=query)
                        | Q(search_tag_phrase=True),
                        then=Value(3),
                    ),
                    When(summary__icontains=query, then=Value(2)),
                    When(search_body_text__icontains=query, then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            ).order_by('-search_relevance', '-published_at', '-pk')
    else:
        if tag_slug:
            queryset = queryset.filter(tags__slug=tag_slug)
        if category_slug:
            queryset = queryset.filter(category__slug=category_slug)
        if author:
            queryset = queryset.filter(author=author)
    return queryset


def get_public_filter_options(*, site_slug, now=None):
    now = now or timezone.now()
    public_posts = _public_post_scope(site_slug=site_slug, now=now)
    categories = (
        BlogCategory.objects.filter(posts__in=public_posts)
        .distinct()
        .order_by(Lower('name'), 'pk')
    )
    authors = (
        AuthorProfile.objects.filter(articles__in=public_posts)
        .exclude(public_author_name='')
        .distinct()
        .order_by(Lower('public_author_name'), 'pk')
    )
    tags = (
        BlogTag.objects.filter(posts__in=public_posts)
        .distinct()
        .order_by(Lower('name'), 'pk')
    )
    years = (
        public_posts
        .filter(published_at__isnull=False)
        .annotate(publication_year=ExtractYear('published_at', tzinfo=timezone.get_current_timezone()))
        .values_list('publication_year', flat=True)
        .distinct()
        .order_by('-publication_year')
    )
    return FilterOptions(
        article_types=tuple(
            FilterOption(value, label) for value, label in BlogPost.Type.choices
        ),
        categories=tuple(FilterOption(category.slug, category.name) for category in categories),
        authors=tuple(FilterOption(author.slug, author.public_author_name) for author in authors),
        tags=tuple(FilterOption(tag.slug, tag.name) for tag in tags),
        years=tuple(FilterOption(str(year), str(year)) for year in years),
    )


def get_public_post_by_slug(*, slug, site_slug, now=None):
    now = now or timezone.now()
    queryset = get_public_posts(site_slug=site_slug, now=now).prefetch_related('tags')
    return get_object_or_404(queryset, slug=slug)


def get_publication_site_slugs(post):
    if not post or not post.pk:
        return set()
    return set(
        BlogPostPublication.objects.filter(post_id=post.pk).values_list('site_slug', flat=True)
    )


def are_related_posts_compatible(*, source_post, target_post, source_site_slugs=None, target_site_slugs=None):
    required_sites = (
        get_publication_site_slugs(source_post)
        if source_site_slugs is None
        else set(source_site_slugs)
    )
    if not required_sites or not target_post or not target_post.pk:
        return False
    available_sites = (
        get_publication_site_slugs(target_post)
        if target_site_slugs is None
        else set(target_site_slugs)
    )
    return required_sites.issubset(available_sites)


def get_compatible_related_posts(*, source_post=None, source_site_slugs=None):
    required_sites = (
        get_publication_site_slugs(source_post)
        if source_site_slugs is None
        else set(source_site_slugs)
    )
    if not required_sites:
        return BlogPost.objects.none()

    queryset = (
        BlogPost.objects.select_related('category', 'featured_image')
        .annotate(
            compatible_site_count=Count(
                'publications__site_slug',
                filter=Q(publications__site_slug__in=required_sites),
                distinct=True,
            )
        )
        .filter(compatible_site_count=len(required_sites))
        .order_by(Lower('title'), 'pk')
    )
    if source_post and source_post.pk:
        queryset = queryset.exclude(pk=source_post.pk)
    return queryset


def get_incompatible_incoming_related_links(*, target_post, target_site_slugs):
    if not target_post or not target_post.pk:
        return BlogPostRelated.objects.none()

    return (
        BlogPostRelated.objects.filter(related_post_id=target_post.pk)
        .annotate(
            source_site_count=Count('post__publications__site_slug', distinct=True),
            matching_site_count=Count(
                'post__publications__site_slug',
                filter=Q(post__publications__site_slug__in=set(target_site_slugs)),
                distinct=True,
            ),
        )
        .filter(source_site_count__gt=0)
        .filter(source_site_count__gt=F('matching_site_count'))
        .select_related('post')
    )


def get_related_public_posts(*, post, site_slug, now=None, source_site_slugs=None):
    now = now or timezone.now()
    required_sites = (
        get_publication_site_slugs(post)
        if source_site_slugs is None
        else set(source_site_slugs)
    )
    if site_slug not in required_sites:
        return BlogPost.objects.none()

    return (
        get_compatible_related_posts(
            source_post=post,
            source_site_slugs=required_sites,
        )
        .filter(
            _effective_public_filter(now),
            publications__site_slug=site_slug,
            incoming_related_links__post=post,
        )
        .distinct()
        .order_by('incoming_related_links__position', 'pk')
    )


def get_public_posts_for_feed(*, site_slug, now=None, limit=20):
    return get_public_posts(site_slug=site_slug, now=now)[:limit]


def get_selectable_image_comparisons():
    ready = BlogImageComparison.ProcessingStatus.READY
    return (
        BlogImageComparison.objects
        .filter(
            first_processing_status=ready,
            second_processing_status=ready,
        )
        .exclude(first_original='')
        .exclude(first_rendition_480='')
        .exclude(first_rendition_800='')
        .exclude(first_rendition_1200='')
        .exclude(second_original='')
        .exclude(second_rendition_480='')
        .exclude(second_rendition_800='')
        .exclude(second_rendition_1200='')
        .order_by('-created_at', '-pk')
    )


def get_indexable_public_tags(*, site_slug, now=None, minimum_posts=2):
    now = now or timezone.now()
    public_posts = (
        Q(posts__status=BlogPost.Status.PUBLISHED)
        | Q(posts__status=BlogPost.Status.SCHEDULED, posts__published_at__lte=now)
    ) & Q(posts__publications__site_slug=site_slug)
    return (
        BlogTag.objects.annotate(
            public_post_count=Count('posts', filter=public_posts, distinct=True),
        )
        .filter(public_post_count__gte=minimum_posts)
        .order_by('slug')
    )


def get_indexable_public_categories(*, site_slug, now=None, minimum_posts=2):
    now = now or timezone.now()
    public_posts = (
        Q(posts__status=BlogPost.Status.PUBLISHED)
        | Q(posts__status=BlogPost.Status.SCHEDULED, posts__published_at__lte=now)
    ) & Q(posts__publications__site_slug=site_slug)
    return (
        BlogCategory.objects.annotate(
            public_post_count=Count('posts', filter=public_posts, distinct=True),
        )
        .filter(public_post_count__gte=minimum_posts)
        .order_by('slug')
    )


def get_indexable_public_authors(*, site_slug, now=None, minimum_posts=2):
    now = now or timezone.now()
    public_posts = (
        Q(articles__status=BlogPost.Status.PUBLISHED)
        | Q(articles__status=BlogPost.Status.SCHEDULED, articles__published_at__lte=now)
    ) & Q(articles__publications__site_slug=site_slug)
    return (
        AuthorProfile.objects.exclude(public_author_name='')
        .annotate(public_post_count=Count('articles', filter=public_posts, distinct=True))
        .filter(public_post_count__gte=minimum_posts)
        .order_by('slug')
    )


def get_canonical_post_url(post, scheme=None):
    with override('en'):
        path = reverse_blog('detail', site_slug=post.canonical_site_slug, kwargs={'slug': post.slug})
    return build_site_absolute_url(post.canonical_site_slug, path, scheme=scheme)
