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