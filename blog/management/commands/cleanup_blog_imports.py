from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.blog.import_services import MAX_CLEANUP_BATCH_SIZE, cleanup_staged_imports


class Command(BaseCommand):
    help = 'Remove expired and consumed private Blog import staging data.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=getattr(settings, 'BLOG_IMPORT_CLEANUP_BATCH_SIZE', 100),
            help=f'Maximum number of staging sessions to inspect (1-{MAX_CLEANUP_BATCH_SIZE}).',
        )

    def handle(self, *args, **options):
        try:
            result = cleanup_staged_imports(batch_size=options['batch_size'])
        except ValueError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                f'Deleted {result.expired_deleted} expired and {result.consumed_deleted} consumed import rows; '
                f'deleted {result.files_deleted} private files; '
                f'{result.file_failures} file cleanup failures.'
            )
        )
