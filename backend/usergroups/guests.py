"""Letting a child into a class with nothing but a name.

A guest account is real but disposable: a synthetic address nobody ever sees,
the name the child typed on /unirse, and membership in exactly one class. It
exists so that joining a lesson never requires owning an email address.
"""

import re
import uuid

from django.utils import timezone

from users.models import User
from .models import ClassPermission

# Nothing is ever delivered here. `.invalid` is reserved by RFC 2606 precisely
# so that an address built from it can never collide with a real one.
GUEST_EMAIL_DOMAIN = "guest.invalid"

# Long enough for "Maria Fernanda", short enough to fit an avatar tooltip. The
# numbering suffix is added on top of this, and display_name holds 50.
MAX_NAME_LENGTH = 24

# Letters with the accents Spanish needs, digits, spaces, and the punctuation
# that turns up in real names. Anything else is dropped rather than refused, so
# a child who types an emoji still gets into the lesson.
_DISALLOWED = re.compile(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿ \-'.]")


def clean_name(raw):
    """The name as the rest of the class will see it, or "" if nothing is left."""
    name = _DISALLOWED.sub("", raw or "")
    name = re.sub(r"\s+", " ", name).strip(" -'.")
    return name[:MAX_NAME_LENGTH]


def unique_name_in_class(group, wanted):
    """"Juan", then "Juan 2", "Juan 3"...

    Numbered against every guest who entered this class today, not only the ones
    connected right now: two children called Juan must stay told apart for the
    whole lesson, including after one of them closes the tab. "Today" is the
    calendar day in the server's TIME_ZONE.
    """
    taken = set(
        group.group_members.filter(
            is_guest=True,
            date_joined__date=timezone.localdate(),
        ).values_list("display_name", flat=True)
    )

    if wanted not in taken:
        return wanted

    suffix = 2
    while f"{wanted} {suffix}" in taken:
        suffix += 1
    return f"{wanted} {suffix}"


def create_guest(group, name):
    """A new guest in this class, starting with everything locked.

    Children arrive unable to type, draw or chat; the teacher opens each of
    those from the class panel once the lesson is ready for it.
    """
    user = User.objects.create_user(
        email=f"guest-{uuid.uuid4().hex}@{GUEST_EMAIL_DOMAIN}",
        password=None,  # unusable: a guest only ever holds the session they got
        is_guest=True,
        display_name=name,
    )

    group.group_members.add(user)
    ClassPermission.objects.create(
        group=group,
        user=user,
        can_code=False,
        can_draw=False,
        can_chat=False,
    )

    return user
