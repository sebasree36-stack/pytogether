from rest_framework import serializers
from django.contrib.auth import get_user_model

from .models import Group


User = get_user_model()

class GroupCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = ["group_name"]

class GroupMemberSerializer(serializers.ModelSerializer):
    name = serializers.ReadOnlyField()

    class Meta:
        model = User
        # No email: a guest in the room must never be handed one, and nothing
        # on screen needs it now that members are shown by name.
        fields = ["id", "name", "is_guest"]

class GroupDetailSerializer(serializers.ModelSerializer):
    # Name rather than __str__, which is the email
    owner = serializers.ReadOnlyField(source="owner.name")
    owner_id = serializers.ReadOnlyField(source="owner.id")
    group_members = GroupMemberSerializer(many=True, read_only=True)

    class Meta:
        model = Group
        fields = ["id", "group_name", "access_code", "owner", "owner_id", "group_members"]

class GroupJoinSerializer(serializers.Serializer):
    access_code = serializers.CharField()

    # will automatically get called from .is_valid()
    def validate_access_code(self, value):

        # Check if the group exists with that access code
        try:
            group = Group.objects.get(access_code=value)
        except Group.DoesNotExist:
            raise serializers.ValidationError("Invalid access code.")

        # Access the current user via the serializer context
        user = self.context['request'].user
        if user in group.group_members.all():
            raise serializers.ValidationError("You are already a member of this group.")
        
        return value
    
class GroupUpdateSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    group_name = serializers.CharField()

    def validate_id(self, value):
        user = self.context['request'].user
        # Check if the group exists
        try:
            group = Group.objects.get(id=value)
        except Group.DoesNotExist:
            raise serializers.ValidationError("Invalid group ID.")
        
        if user not in group.group_members.all():
            raise serializers.ValidationError("You are not a member of this group.")

        return value

    def get_group(self):
        """Helper to fetch group after validation"""
        return Group.objects.get(id=self.validated_data["id"])