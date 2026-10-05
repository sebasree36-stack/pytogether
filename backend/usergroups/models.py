from django.db import models
from users.models import User
import secrets

# Uppercase letters and digits, minus the characters that are easy to confuse
# when a code is read out loud or copied off a screen: O/0, I/L/1.
ACCESS_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

def generate_access_code(length=6):
    """ Function to randomly generate a group's access code """
    return ''.join(secrets.choice(ACCESS_CODE_ALPHABET) for _ in range(length))

class Group(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    group_name = models.CharField(max_length=100)
    group_members = models.ManyToManyField(User, related_name="custom_groups")

    # Each group will have an auto-generated code to join
    access_code = models.CharField(max_length=20, unique=True, default=generate_access_code)

    def __str__(self):
        return self.group_name

class ClassPermission(models.Model):
    """What one member of a class may do inside its projects.

    A row exists once somebody's permissions have been decided: a guest gets one
    the moment they join, and the teacher's panel writes one for anybody it
    touches. Without a row a normal account may do everything and a guest may do
    nothing, so a missing row never hands a child more than they should have.
    """

    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="permissions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="class_permissions")

    can_code = models.BooleanField(default=False)
    can_draw = models.BooleanField(default=False)
    can_chat = models.BooleanField(default=False)

    class Meta:
        unique_together = ("group", "user")

    def __str__(self):
        return f"{self.user.name} in {self.group.group_name}"


def permissions_for(group, user):
    """The three permissions of this member, as a plain dict.

    The owner is the teacher and is never restricted, which also means the
    teacher can never lock themselves out of their own class.
    """
    if group.owner_id == user.id:
        return {"can_code": True, "can_draw": True, "can_chat": True}

    row = ClassPermission.objects.filter(group=group, user=user).first()
    if row is not None:
        return {"can_code": row.can_code, "can_draw": row.can_draw, "can_chat": row.can_chat}

    allowed = not user.is_guest
    return {"can_code": allowed, "can_draw": allowed, "can_chat": allowed}
