import json

from django import forms
from django.core.serializers.json import DjangoJSONEncoder
from django.utils.translation import gettext_lazy as _
from django_prose_editor.widgets import AdminProseEditorWidget
from js_asset import JS, Media

from .models import RICH_TEXT_EXTENSIONS


class BlogFAQItemsWidget(forms.Widget):
    template_name = 'admin/blog/widgets/faq_items.html'

    def __init__(self, attrs=None):
        super().__init__(attrs)
        self.answer_widget = AdminProseEditorWidget(
            config={'extensions': RICH_TEXT_EXTENSIONS},
            preset='configurable',
            attrs={'data-blog-rich-text': 'true', 'rows': 5},
        )

    @property
    def media(self):
        return self.answer_widget.media + Media(
            js=[JS('blog/js/faq-admin.js', {'type': 'module'})],
        )

    def format_value(self, value):
        if isinstance(value, list):
            return json.dumps(value, cls=DjangoJSONEncoder, separators=(',', ':'))
        return value or '[]'

    def _items_from_value(self, value):
        if isinstance(value, list):
            return value
        try:
            parsed = json.loads(value or '[]')
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []

    def _answer_context(self, name, value, identifier, described_by=None, invalid=False):
        attrs = {'id': identifier, 'data-faq-answer': ''}
        if described_by:
            attrs['aria-describedby'] = described_by
        if invalid:
            attrs['aria-invalid'] = 'true'
        return self.answer_widget.get_context(
            name,
            value,
            attrs,
        )['widget']

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        formatted_value = self.format_value(value)
        base_id = context['widget']['attrs'].get('id', f'id_{name}')
        described_by = context['widget']['attrs'].get('aria-describedby')
        invalid = bool(context['widget']['attrs'].get('aria-invalid'))
        items = []
        for index, item in enumerate(self._items_from_value(value)):
            item = item if isinstance(item, dict) else {}
            items.append({
                'index': index,
                'number': index + 1,
                'question': item.get('question', '') if isinstance(item.get('question', ''), str) else '',
                'question_id': f'{base_id}__question_{index}',
                'answer_id': f'{base_id}__answer_{index}',
                'answer_widget': self._answer_context(
                    f'{name}__answer_{index}',
                    item.get('answer', '') if isinstance(item.get('answer', ''), str) else '',
                    f'{base_id}__answer_{index}',
                    described_by,
                    invalid,
                ),
                'described_by': described_by,
                'invalid': invalid,
            })
        context['widget'].update({
            'value': formatted_value,
            'items': items,
            'question_label': _('Question'),
            'answer_label': _('Answer'),
            'delete_confirmation': _('Delete this question and its answer?'),
            'template_answer_widget': self._answer_context(
                f'{name}__answer___prefix__',
                '',
                f'{base_id}__answer___prefix__',
                described_by,
                invalid,
            ),
            'described_by': described_by,
            'invalid': invalid,
        })
        return context
