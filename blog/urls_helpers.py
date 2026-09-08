from django.urls import reverse

from django.core.exceptions import ImproperlyConfigured

from apps.core.sites import get_blog_site_definitions


def get_blog_url_namespace(site_slug):
    site = get_blog_site_definitions().get(site_slug)
    if site is None:
        raise ImproperlyConfigured(f'Site {site_slug!r} is not configured for Blog URLs.')
    return site.blog_url_namespace


def reverse_blog(view_name, *, site_slug=None, current_app=None, kwargs=None):
    if current_app is None:
        current_app = get_blog_url_namespace(site_slug)
    return reverse(f"blog:{view_name}", current_app=current_app, kwargs=kwargs)
