import apps.blog.models
from django.db import migrations, models
from django.utils.text import slugify


MAX_SLUG_LENGTH = 120


def populate_author_slugs(apps, schema_editor):
    AuthorProfile = apps.get_model('blog', 'AuthorProfile')
    used_slugs = set()

    for author in AuthorProfile.objects.order_by('pk').iterator():
        base = slugify(author.public_author_name) or f'author-{author.pk}'
        base = base[:MAX_SLUG_LENGTH].strip('-') or f'author-{author.pk}'
        candidate = base
        if candidate in used_slugs:
            suffix = f'-{author.pk}'
            candidate = f'{base[: MAX_SLUG_LENGTH - len(suffix)].rstrip("-")}{suffix}'
        if candidate in used_slugs:
            raise RuntimeError(f'Could not generate a unique slug for AuthorProfile {author.pk}.')
        author.slug = candidate
        author.save(update_fields=['slug'])
        used_slugs.add(candidate)


class Migration(migrations.Migration):
    dependencies = [('blog', '0007_alter_blogpost_category')]

    operations = [
        migrations.AddField(
            model_name='authorprofile',
            name='slug',
            field=models.SlugField(blank=True, db_index=False, max_length=120, null=True),
        ),
        migrations.RunPython(populate_author_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='authorprofile',
            name='slug',
            field=models.SlugField(
                max_length=120,
                unique=True,
                validators=[apps.blog.models.AUTHOR_SLUG_VALIDATOR],
            ),
        ),
    ]
