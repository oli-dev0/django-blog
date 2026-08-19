"""Reference root URL wiring for the Blog app."""

from django.urls import include, path


urlpatterns = [
    path('blog/', include('apps.blog.urls')),
]
