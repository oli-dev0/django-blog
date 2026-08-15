from django.core.checks import Error, register
from django.template import TemplateDoesNotExist
from django.template.loader import get_template

from apps.core.sites import get_blog_site_definitions


@register()
def check_blog_site_definitions(app_configs, **kwargs):
    errors = []
    for site in get_blog_site_definitions().values():
        if not site.hosts:
            errors.append(Error(f'Blog site {site.slug!r} has no configured host.', id='blog.E001'))
        if site.blog_url_namespace not in site.route_namespaces:
            errors.append(
                Error(
                    f'Blog site {site.slug!r} does not allow namespace {site.blog_url_namespace!r}.',
                    id='blog.E002',
                )
            )
        for template in ('blog/list.html', 'blog/detail.html'):
            template_name = (
                template
                if site.template_namespace == 'blog'
                else f'{site.template_namespace}/{template}'
            )
            try:
                get_template(template_name)
            except TemplateDoesNotExist:
                errors.append(
                    Error(
                        f'Blog site {site.slug!r} is missing presentation template {template_name!r}.',
                        id='blog.E003',
                    )
                )
    return errors
