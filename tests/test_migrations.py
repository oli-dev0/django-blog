from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class AuthorSlugMigrationTests(TransactionTestCase):
    migrate_from = ('blog', '0007_alter_blogpost_category')
    migrate_to = ('blog', '0008_authorprofile_slug')

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        User = old_apps.get_model('auth', 'User')
        AuthorProfile = old_apps.get_model('blog', 'AuthorProfile')

        long_name = 'Very Long Author Name ' * 5
        self.author_pks = []
        for username, name in (
            ('first', 'Jâne Döe!'),
            ('second', 'Jane Doe'),
            ('third', '!!!'),
            ('fourth', long_name),
            ('fifth', long_name),
        ):
            user = User.objects.create(username=username)
            author = AuthorProfile.objects.create(user=user, public_author_name=name)
            self.author_pks.append(author.pk)

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_authors_receive_deterministic_collision_safe_slugs(self):
        AuthorProfile = self.apps.get_model('blog', 'AuthorProfile')

        slugs = list(AuthorProfile.objects.order_by('pk').values_list('slug', flat=True))
        _, second_pk, third_pk, _, fifth_pk = self.author_pks

        self.assertEqual(slugs[:3], ['jane-doe', f'jane-doe-{second_pk}', f'author-{third_pk}'])
        self.assertGreater(len(slugs[3]), 100)
        self.assertLessEqual(len(slugs[3]), 120)
        self.assertLessEqual(len(slugs[4]), 120)
        self.assertTrue(slugs[4].endswith(f'-{fifth_pk}'))
        self.assertEqual(len(slugs), len(set(slugs)))


class ImportStagingMigrationTests(TransactionTestCase):
    migrate_from = ('blog', '0016_blogfaqblock')
    migrate_to = ('blog', '0017_blogarticleimport_blogarticleimportfile')

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_0017_creates_empty_private_staging_schema(self):
        ImportSession = self.apps.get_model('blog', 'BlogArticleImport')
        ImportFile = self.apps.get_model('blog', 'BlogArticleImportFile')

        self.assertEqual(ImportSession.objects.count(), 0)
        self.assertEqual(ImportFile.objects.count(), 0)
        self.assertTrue(ImportSession._meta.get_field('id').primary_key)
        self.assertTrue(ImportSession._meta.get_field('expires_at').db_index)
        self.assertEqual(ImportSession._meta.get_field('created_by').remote_field.related_name, 'blog_article_imports')
        self.assertEqual(ImportFile._meta.get_field('import_session').remote_field.related_name, 'files')
        self.assertEqual(
            {constraint.name for constraint in ImportFile._meta.constraints},
            {'blog_import_file_selected_name'},
        )
        self.assertEqual(
            ImportSession._meta.get_field('completed_post').remote_field.on_delete.__name__,
            'SET_NULL',
        )
        self.assertTrue(ImportSession._meta.get_field('consumed_at').null)


class BlogSearchBodyMigrationTests(TransactionTestCase):
    migrate_from = ('blog', '0020_alter_blogarticleimport_created_by')
    migrate_to = ('blog', '0021_add_blog_search_body')

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        BlogCategory = old_apps.get_model('blog', 'BlogCategory')
        BlogPost = old_apps.get_model('blog', 'BlogPost')
        BlogHeadingBlock = old_apps.get_model('blog', 'BlogHeadingBlock')
        BlogRichTextBlock = old_apps.get_model('blog', 'BlogRichTextBlock')
        BlogFAQBlock = old_apps.get_model('blog', 'BlogFAQBlock')
        BlogCodeBlock = old_apps.get_model('blog', 'BlogCodeBlock')

        category = BlogCategory.objects.create(name='Migration', slug='migration')
        post = BlogPost.objects.create(
            title='Migration article',
            slug='migration-article',
            summary='Summary',
            category=category,
        )
        self.post_pk = post.pk
        BlogHeadingBlock.objects.create(
            parent=post,
            region='main',
            text='Backfilled heading',
            anchor='backfilled-heading',
        )
        BlogRichTextBlock.objects.create(
            parent=post,
            region='main',
            body='<p>Back<strong>filled</strong> &amp; rich</p><p>text</p>',
        )
        BlogFAQBlock.objects.create(
            parent=post,
            region='main',
            items=[{'question': 'Backfilled question', 'answer': '<p>Backfilled answer</p>'}],
        )
        BlogCodeBlock.objects.create(
            parent=post,
            region='main',
            code='ExcludedMigrationCode',
        )

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_reader_content_is_backfilled_without_code(self):
        BlogPost = self.apps.get_model('blog', 'BlogPost')

        search_body_text = BlogPost.objects.get(pk=self.post_pk).search_body_text

        self.assertIn('Backfilled heading', search_body_text)
        self.assertIn('Backfilled & rich text', search_body_text)
        self.assertNotIn('richtext', search_body_text)
        self.assertIn('Backfilled question Backfilled answer', search_body_text)
        self.assertNotIn('ExcludedMigrationCode', search_body_text)


class BlogEmbedSharingBlockMigrationTests(TransactionTestCase):
    migrate_from = ('blog', '0021_add_blog_search_body')
    migrate_to = ('blog', '0022_blogembedsharingblock')

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        BlogCategory = old_apps.get_model('blog', 'BlogCategory')
        BlogPost = old_apps.get_model('blog', 'BlogPost')
        BlogRichTextBlock = old_apps.get_model('blog', 'BlogRichTextBlock')

        category = BlogCategory.objects.create(name='Existing', slug='existing')
        post = BlogPost.objects.create(
            title='Existing article',
            slug='existing-article',
            summary='Existing summary',
            category=category,
        )
        BlogRichTextBlock.objects.create(
            parent=post,
            region='main',
            body='<p>Existing content</p>',
        )
        self.post_pk = post.pk

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_migration_adds_only_the_embed_plugin_schema_and_preserves_existing_content(self):
        BlogPost = self.apps.get_model('blog', 'BlogPost')
        BlogRichTextBlock = self.apps.get_model('blog', 'BlogRichTextBlock')
        BlogEmbedSharingBlock = self.apps.get_model('blog', 'BlogEmbedSharingBlock')

        self.assertEqual(BlogPost.objects.get(pk=self.post_pk).title, 'Existing article')
        self.assertEqual(
            BlogRichTextBlock.objects.get(parent_id=self.post_pk).body,
            '<p>Existing content</p>',
        )
        self.assertEqual(BlogEmbedSharingBlock.objects.count(), 0)
        self.assertEqual(
            {field.name for field in BlogEmbedSharingBlock._meta.local_fields},
            {'id', 'region', 'ordering', 'platform', 'url', 'caption', 'parent'},
        )
        self.assertEqual(BlogEmbedSharingBlock._meta.get_field('platform').max_length, 12)
        self.assertEqual(BlogEmbedSharingBlock._meta.get_field('url').max_length, 500)
        self.assertEqual(BlogEmbedSharingBlock._meta.get_field('caption').max_length, 300)
        self.assertTrue(BlogEmbedSharingBlock._meta.get_field('caption').blank)
        self.assertEqual(
            BlogEmbedSharingBlock._meta.get_field('parent').remote_field.on_delete.__name__,
            'CASCADE',
        )

    def test_migration_contains_only_one_additive_create_model_operation(self):
        from importlib import import_module

        migration = import_module('apps.blog.migrations.0022_blogembedsharingblock').Migration

        self.assertEqual(len(migration.operations), 1)
        self.assertEqual(type(migration.operations[0]).__name__, 'CreateModel')
        self.assertEqual(migration.operations[0].name, 'BlogEmbedSharingBlock')


class BlogTaxonomyWebsiteMigrationTests(TransactionTestCase):
    migrate_from = ('blog', '0022_blogembedsharingblock')
    migrate_to = ('blog', '0023_blogsite_blogcategorysite_blogcategory_websites_and_more')

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        category = old_apps.get_model('blog', 'BlogCategory').objects.create(
            name='Existing', slug='existing'
        )
        tag = old_apps.get_model('blog', 'BlogTag').objects.create(name='Shared', slug='shared')
        post = old_apps.get_model('blog', 'BlogPost').objects.create(
            title='Personal article', slug='personal-article', category=category
        )
        post.tags.add(tag)
        old_apps.get_model('blog', 'BlogPostPublication').objects.create(
            post=post, site_slug='my_website'
        )
        self.category_pk = category.pk
        self.tag_pk = tag.pk

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_terms_gain_vanta_and_current_publication_sites(self):
        category_sites = set(
            self.apps.get_model('blog', 'BlogCategorySite').objects.filter(
                taxonomy_id=self.category_pk
            ).values_list('site_id', flat=True)
        )
        tag_sites = set(
            self.apps.get_model('blog', 'BlogTagSite').objects.filter(
                taxonomy_id=self.tag_pk
            ).values_list('site_id', flat=True)
        )

        self.assertEqual(category_sites, {'vanta_admin', 'my_website'})
        self.assertEqual(tag_sites, {'vanta_admin', 'my_website'})
