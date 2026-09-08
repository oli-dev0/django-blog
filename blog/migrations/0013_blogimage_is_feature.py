from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('blog', '0010_blogimagecomparison_blogimagecomparisonblock'),
    ]

    operations = [
        migrations.AddField(
            model_name='blogimage',
            name='is_feature',
            field=models.BooleanField(default=False),
        ),
    ]
