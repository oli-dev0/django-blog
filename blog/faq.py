from html import unescape

from django.core.exceptions import ValidationError
from django.utils.html import strip_tags
from django.utils.translation import gettext_lazy as _

FAQ_ITEM_KEYS = frozenset({'question', 'answer'})
FAQ_QUESTION_MAX_LENGTH = 300


def rich_text_has_visible_content(value):
    text = unescape(strip_tags(value or '')).replace('\xa0', ' ')
    return bool(text.strip())


def normalize_faq_items(value):
    from .models import sanitize_rich_text

    if not isinstance(value, list):
        raise ValidationError(_('FAQ content must be a list of questions and answers.'))

    normalized = []
    errors = []
    for index, item in enumerate(value, start=1):
        prefix = _('Question %(number)s:') % {'number': index}
        if not isinstance(item, dict) or set(item) != FAQ_ITEM_KEYS:
            errors.append(_('%(prefix)s Use one question and one answer.') % {'prefix': prefix})
            continue
        if not all(isinstance(item[key], str) for key in FAQ_ITEM_KEYS):
            errors.append(_('%(prefix)s Question and answer values must be text.') % {'prefix': prefix})
            continue

        question = item['question'].strip()
        answer = sanitize_rich_text(item['answer'])
        if not question:
            errors.append(_('%(prefix)s Enter a question.') % {'prefix': prefix})
        elif strip_tags(question) != question:
            errors.append(_('%(prefix)s Enter the question as plain text.') % {'prefix': prefix})
        elif len(question) > FAQ_QUESTION_MAX_LENGTH:
            errors.append(
                _('%(prefix)s Keep the question to %(limit)s characters or fewer.')
                % {'prefix': prefix, 'limit': FAQ_QUESTION_MAX_LENGTH}
            )
        if not rich_text_has_visible_content(answer):
            errors.append(_('%(prefix)s Enter an answer.') % {'prefix': prefix})
        normalized.append({'question': question, 'answer': answer})

    if errors:
        raise ValidationError(errors)
    return normalized


def iter_faq_answers(items):
    for item in normalize_faq_items(items):
        yield item['answer']
