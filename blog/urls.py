from django.urls import path

from . import views
from .feeds import BlogFeed


app_name = 'blog'

urlpatterns = [
    path('', views.post_list, name='list'),
    path('rss/', BlogFeed(), name='rss'),
    path('tag/<slug:slug>/', views.tag_post_list, name='tag'),
    path('category/<slug:slug>/', views.category_post_list, name='category'),
    path('author/<slug:author_slug>/', views.author_post_list, name='author'),
    path('<slug:slug>/', views.post_detail, name='detail'),
]
