from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from urllib.parse import urlencode

from django.utils.translation import gettext as _


TYPE_PARAMETER = 'type'
CATEGORY_PARAMETER = 'category'
AUTHOR_PARAMETER = 'author'
DATE_PARAMETER = 'date'
YEAR_PARAMETER = 'year'
TAG_PARAMETER = 'tag'
SEARCH_PARAMETER = 'q'
SEARCH_MAX_LENGTH = 200
SEARCH_MAX_TERMS = 10

DATE_PRESETS = (
    ('past_7_days', _('Past 7 days'), 7),
    ('past_30_days', _('Past 30 days'), 30),
    ('past_3_months', _('Past 3 months'), 90),
    ('past_year', _('Past 12 months'), 365),
)
DATE_PRESET_VALUES = frozenset(value for value, _label, _days in DATE_PRESETS)


@dataclass(frozen=True)
class FilterOption:
    value: str
    label: str


@dataclass(frozen=True)
class FilterOptions:
    article_types: tuple[FilterOption, ...]
    categories: tuple[FilterOption, ...]
    authors: tuple[FilterOption, ...]
    tags: tuple[FilterOption, ...]
    years: tuple[FilterOption, ...]

    @property
    def article_type_values(self):
        return frozenset(option.value for option in self.article_types)

    @property
    def category_values(self):
        return frozenset(option.value for option in self.categories)

    @property
    def author_values(self):
        return frozenset(option.value for option in self.authors)

    @property
    def tag_values(self):
        return frozenset(option.value for option in self.tags)

    @property
    def year_values(self):
        return frozenset(option.value for option in self.years)


@dataclass(frozen=True)
class FilterState:
    search_query: str | None = None
    article_type: str | None = None
    category_slug: str | None = None
    author_slug: str | None = None
    date_preset: str | None = None
    year: int | None = None
    tag_slugs: tuple[str, ...] = ()

    @property
    def has_filters(self):
        return bool(
            self.article_type
            or self.category_slug
            or self.author_slug
            or self.date_preset
            or self.year
            or self.tag_slugs
        )

    @property
    def is_active(self):
        return bool(self.search_query or self.has_filters)

    def without(self, dimension, value=None):
        if dimension == SEARCH_PARAMETER:
            return replace(self, search_query=None)
        if dimension == TYPE_PARAMETER:
            return replace(self, article_type=None)
        if dimension == CATEGORY_PARAMETER:
            return replace(self, category_slug=None)
        if dimension == AUTHOR_PARAMETER:
            return replace(self, author_slug=None)
        if dimension == DATE_PARAMETER:
            return replace(self, date_preset=None)
        if dimension == YEAR_PARAMETER:
            return replace(self, year=None)
        if dimension == TAG_PARAMETER:
            return replace(
                self,
                tag_slugs=tuple(item for item in self.tag_slugs if item != value),
            )
        raise ValueError(f'Unknown filter dimension: {dimension}')

    def with_article_type(self, value):
        return replace(self, article_type=value)


@dataclass(frozen=True)
class ActiveFilter:
    dimension: str
    value: str
    dimension_label: str
    value_label: str


def date_filter_options(years=()):
    return tuple(FilterOption(value, label) for value, label, _days in DATE_PRESETS) + tuple(years)


def _get_values(query_data, parameter):
    if hasattr(query_data, 'getlist'):
        return query_data.getlist(parameter)
    value = query_data.get(parameter, ())
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value] if value else []


def _first_valid(values, valid_values):
    for value in values:
        if value in valid_values:
            return value
    return None


def normalize_search_query(value):
    normalized = ' '.join(str(value or '').split()[:SEARCH_MAX_TERMS])
    return normalized[:SEARCH_MAX_LENGTH].rstrip()


def _first_search_query(values):
    for value in values:
        normalized = normalize_search_query(value)
        if normalized:
            return normalized
    return None


def parse_filter_state(query_data, options):
    search_query = _first_search_query(_get_values(query_data, SEARCH_PARAMETER))
    article_type = _first_valid(
        _get_values(query_data, TYPE_PARAMETER),
        options.article_type_values,
    )

    tag_values = set(_get_values(query_data, TAG_PARAMETER))
    tags = tuple(option.value for option in options.tags if option.value in tag_values)

    category_slug = _first_valid(
        _get_values(query_data, CATEGORY_PARAMETER),
        options.category_values,
    )
    author_slug = _first_valid(
        _get_values(query_data, AUTHOR_PARAMETER),
        options.author_values,
    )
    date_preset = _first_valid(
        _get_values(query_data, DATE_PARAMETER),
        DATE_PRESET_VALUES,
    )
    date_year_value = _first_valid(_get_values(query_data, DATE_PARAMETER), options.year_values)
    year_value = date_year_value or _first_valid(_get_values(query_data, YEAR_PARAMETER), options.year_values)
    year = int(year_value) if year_value else None

    if date_preset:
        year = None

    return FilterState(
        search_query=search_query,
        article_type=article_type,
        category_slug=category_slug,
        author_slug=author_slug,
        date_preset=date_preset,
        year=year,
        tag_slugs=tags,
    )


def serialize_filter_state(state, *, page=None):
    parameters = []
    if state.search_query:
        parameters.append((SEARCH_PARAMETER, state.search_query))
    if state.article_type:
        parameters.append((TYPE_PARAMETER, state.article_type))
    if state.category_slug:
        parameters.append((CATEGORY_PARAMETER, state.category_slug))
    if state.author_slug:
        parameters.append((AUTHOR_PARAMETER, state.author_slug))
    if state.date_preset:
        parameters.append((DATE_PARAMETER, state.date_preset))
    elif state.year:
        parameters.append((YEAR_PARAMETER, str(state.year)))
    parameters.extend((TAG_PARAMETER, value) for value in state.tag_slugs)
    if page is not None:
        parameters.append(('page', str(page)))
    return urlencode(parameters)


def relative_date_bounds(now: datetime, preset: str):
    for value, _label, days in DATE_PRESETS:
        if value == preset:
            return now - timedelta(days=days), now
    raise ValueError(f'Unknown date preset: {preset}')


def active_filters(state, options):
    labels = {
        TYPE_PARAMETER: _('Article type'),
        CATEGORY_PARAMETER: _('Category'),
        AUTHOR_PARAMETER: _('Author'),
        DATE_PARAMETER: _('Date'),
        YEAR_PARAMETER: _('Year'),
        TAG_PARAMETER: _('Tag'),
    }
    article_type_labels = {
        option.value: option.label for option in options.article_types
    }
    category_labels = {option.value: option.label for option in options.categories}
    author_labels = {option.value: option.label for option in options.authors}
    tag_labels = {option.value: option.label for option in options.tags}
    date_labels = {value: label for value, label, _days in DATE_PRESETS}

    result = []
    if state.article_type:
        result.append(
            ActiveFilter(
                TYPE_PARAMETER,
                state.article_type,
                labels[TYPE_PARAMETER],
                article_type_labels[state.article_type],
            )
        )
    if state.category_slug:
        result.append(
            ActiveFilter(
                CATEGORY_PARAMETER,
                state.category_slug,
                labels[CATEGORY_PARAMETER],
                category_labels[state.category_slug],
            )
        )
    if state.author_slug:
        result.append(
            ActiveFilter(
                AUTHOR_PARAMETER,
                state.author_slug,
                labels[AUTHOR_PARAMETER],
                author_labels[state.author_slug],
            )
        )
    if state.date_preset:
        result.append(
            ActiveFilter(
                DATE_PARAMETER,
                state.date_preset,
                labels[DATE_PARAMETER],
                date_labels[state.date_preset],
            )
        )
    elif state.year:
        result.append(
            ActiveFilter(
                YEAR_PARAMETER,
                str(state.year),
                labels[YEAR_PARAMETER],
                str(state.year),
            )
        )
    result.extend(
        ActiveFilter(TAG_PARAMETER, value, labels[TAG_PARAMETER], tag_labels[value])
        for value in state.tag_slugs
    )
    return tuple(result)
