from django.urls import path, include
from .views import class_permissions, create_group, join_group, leave_group, list_groups, edit_group
from projects.views import global_project_search

urlpatterns = [
    path("", list_groups, name="list_groups"),
    path("create/", create_group, name="create_group"),
    path("join/", join_group, name="join_group"),
    path("leave/", leave_group, name="leave_group"),
    path("edit/", edit_group, name="edit_group"),

    # What each pupil in a class may do, read and written by its teacher
    path("<int:group_id>/permissions/", class_permissions, name="class_permissions"),

    # Nested project endpoints
    path("projects/search/", global_project_search, name="global_project_search"),
    path("<int:group_id>/projects/", include("projects.urls")),
]