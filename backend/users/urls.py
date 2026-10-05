from django.urls import path
from .views import register, me, google_login, microsoft_login, logout, feedback
from .tokens import CookieTokenRefreshView
from users.views import email_token_obtain_pair
from projects import views as project_views
from usergroups.views import join_as_guest

urlpatterns = [
    # JWT token obtain (email + password) and refresh
    path("auth/token/", email_token_obtain_pair, name="token_obtain_pair"),
    path("auth/token/refresh/", CookieTokenRefreshView.as_view(), name="token_refresh"),
    path("auth/logout/", logout, name="logout"),

    # Google login
    path("auth/google/", google_login, name="google_login"),

    # Microsoft login
    path("auth/microsoft/", microsoft_login, name="microsoft_login"),

    # Validates the token when a guest clicks the link
    path('validate-share-link/', project_views.validate_share_link, name='validate_share_link'),
    # Public read-only snippet content
    path('public/snippet/<str:token>/', project_views.get_snippet_content, name='get_snippet_content'),

    # The only way in that never asks for an email: a class code and a name
    path("join-class/", join_as_guest, name="join_as_guest"),

    # others
    path("auth/register/", register, name="register"),
    path("me/", me, name="me"),
    path("feedback/", feedback, name="feedback"),
]