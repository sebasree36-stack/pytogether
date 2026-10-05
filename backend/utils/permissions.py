"""Who is allowed to manage a class, as opposed to taking part in one.

Membership lets you open projects, write code and chat. Managing the class
itself — renaming it, creating, renaming or deleting its projects, handing out
share links — belongs to the owner, who is the teacher.

Each helper returns a Response to send back, or None when the user may proceed.
"""

from rest_framework import status
from rest_framework.response import Response


def guest_forbidden(user):
    """Guests arrive through /unirse with a name and a class code. They take
    part in the class they were let into and nothing else."""
    if user.is_guest:
        return Response(
            {"error": "Guest accounts cannot do this."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def owner_required(user, group):
    """Being a member is enough to take part, not to manage the class."""
    if group.owner_id != user.id:
        return Response(
            {"error": "Only the owner of this group can do this."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None
