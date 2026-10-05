"""Telling open rooms that something about their class changed.

A class owns the permissions, but pupils sit in project rooms, so a change the
teacher makes in one place has to reach rooms it does not name. Every live
connection joins a channel group for its class as well as for its project; this
is what talks to it.
"""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def class_channel_group(group_id):
    """The channel group holding every connection belonging to this class."""
    return f"class_g{group_id}"


def notify_class_permissions_changed(group_id):
    """Ask every open room of this class to re-read its permissions.

    Each consumer reloads its own rather than being handed somebody else's, so
    one message is enough no matter who the change was about.
    """
    async_to_sync(get_channel_layer().group_send)(
        class_channel_group(group_id),
        {"type": "permissions_changed"},
    )
