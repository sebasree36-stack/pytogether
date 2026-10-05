from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from .models import ClassPermission, Group, permissions_for
from .broadcast import notify_class_permissions_changed
from .guests import clean_name, create_guest, unique_name_in_class
from .serializers import GroupCreateSerializer, GroupDetailSerializer, GroupJoinSerializer, GroupUpdateSerializer
from utils.permissions import guest_forbidden, owner_required
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone


class JoinClassThrottle(AnonRateThrottle):
    """A whole class signs in within the same minute, often through one school
    router that makes them share an address. The global 20/minute anon limit
    would lock half of them out, so this route gets a classroom-sized one."""

    scope = "join_class"


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([JoinClassThrottle])
def join_as_guest(request):
    """ View for a child to enter a class with a code and a name, no email """

    code = (request.data.get("access_code") or "").strip().upper()
    name = clean_name(request.data.get("name"))

    if not name:
        return Response({"error": "A name is required."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        # The row stays locked across the numbering and the creation, so two
        # children typing the same name at the same moment cannot both end up
        # as "Juan" after each read a list that did not yet hold the other.
        with transaction.atomic():
            group = Group.objects.select_for_update().get(access_code=code)
            guest = create_guest(group, unique_name_in_class(group, name))
    except Group.DoesNotExist:
        return Response({"error": "Invalid class code."}, status=status.HTTP_404_NOT_FOUND)

    refresh = RefreshToken.for_user(guest)
    response = Response(
        {
            "access": str(refresh.access_token),
            "name": guest.name,
            "group_id": group.id,
            "group_name": group.group_name,
        },
        status=status.HTTP_201_CREATED,
    )
    response.set_cookie(
        key="refresh_token",
        value=str(refresh),
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite="Lax",
        max_age=30 * 24 * 60 * 60,
        path="/",
    )
    return response

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_group(request):
    """ View to create a group """

    denied = guest_forbidden(request.user)
    if denied:
        return denied

    # Deserialize request
    serializer = GroupCreateSerializer(data=request.data)

    if serializer.is_valid():
        # Create group but inject owner manually
        group = Group.objects.create(
            owner=request.user,
            group_name=serializer.validated_data["group_name"]
        )
        # Add owner as first member
        group.group_members.add(request.user)
        group.save()

        # Serialize and send back
        return Response(GroupDetailSerializer(group, context={"request": request}).data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def join_group(request):
    """ View to join a group via acesss code """

    # Guests are placed into their class by /unirse, not through this route
    denied = guest_forbidden(request.user)
    if denied:
        return denied

    serializer = GroupJoinSerializer(data=request.data, context={'request': request})

    if serializer.is_valid():
        group = Group.objects.get(access_code=serializer.validated_data["access_code"])
        group.group_members.add(request.user)

        return Response(GroupDetailSerializer(group, context={"request": request}).data)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_groups(request):
    """ View to list all the groups a user is in """

    user = request.user
    
    now = timezone.now()
    # Check if last_login is None (new user) OR if it has been more than 24 hours
    if user.last_login is None or (now - user.last_login) > timedelta(days=1):
        user.last_login = now
        user.save(update_fields=['last_login'])

    groups = Group.objects.filter(group_members=user)

    serializer = GroupDetailSerializer(groups, many=True, context={"request": request})
    return Response(serializer.data, status=status.HTTP_200_OK)

@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def leave_group(request):
    """ View to leave a specified group """

    serializer = GroupUpdateSerializer(data=request.data, context={'request': request})

    if serializer.is_valid():
        group = serializer.get_group()

        # The owner is the teacher, and only the owner can manage the class.
        # Walking out on members still in it would strand them with a class
        # nobody can rename or add projects to. Leaving last is still allowed,
        # since that deletes the group and is the only way to close one.
        if group.owner_id == request.user.id and group.group_members.count() > 1:
            return Response(
                {"error": "You are the teacher of this class, so you cannot leave "
                          "it while others are still in it. The class closes on "
                          "its own once everyone else has left."},
                status=status.HTTP_403_FORBIDDEN,
            )

        group.group_members.remove(request.user)

        if group.group_members.count() == 0:
            group.delete()
            print("deleted the group")

        return Response({"message": f"Left group {group.group_name}"})
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def edit_group(request):
    """ View to change the name of a group """

    serializer = GroupUpdateSerializer(data=request.data, context={"request": request})

    if serializer.is_valid():
        group = serializer.get_group()

        denied = owner_required(request.user, group)
        if denied:
            return denied

        group.group_name = serializer.validated_data["group_name"]
        group.save()
        return Response({"message": "Group updated successfully."}, status=status.HTTP_200_OK)

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


PERMISSION_FIELDS = ("can_code", "can_draw", "can_chat")


def _class_roster(group):
    """Every pupil in the class with what they are currently allowed to do.

    The teacher is left out: the owner is never restricted, so a row for them
    would only be a switch that does nothing.
    """
    rows = {p.user_id: p for p in ClassPermission.objects.filter(group=group)}

    roster = []
    for member in group.group_members.exclude(id=group.owner_id).order_by("display_name", "email"):
        row = rows.get(member.id)
        if row is not None:
            allowed = {f: getattr(row, f) for f in PERMISSION_FIELDS}
        else:
            # No row yet: a normal account may do everything, a guest nothing
            allowed = {f: not member.is_guest for f in PERMISSION_FIELDS}

        roster.append({
            "id": member.id,
            "name": member.name,
            "is_guest": member.is_guest,
            **allowed,
        })

    return roster


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def class_permissions(request, group_id):
    """ View for the teacher to see and change what each pupil may do """

    try:
        group = Group.objects.get(id=group_id)
    except Group.DoesNotExist:
        return Response({"error": "Class not found."}, status=status.HTTP_404_NOT_FOUND)

    denied = owner_required(request.user, group)
    if denied:
        return denied

    if request.method == "GET":
        return Response({"members": _class_roster(group)})

    changes = {f: bool(request.data[f]) for f in PERMISSION_FIELDS if f in request.data}
    if not changes:
        return Response({"error": "Nothing to change."}, status=status.HTTP_400_BAD_REQUEST)

    # No user_id means the whole class at once, which is how a lesson usually
    # goes: everyone writes, then everyone stops and looks at the board.
    members = group.group_members.exclude(id=group.owner_id)
    if request.data.get("user_id") is not None:
        members = members.filter(id=request.data["user_id"])
        if not members.exists():
            return Response({"error": "That pupil is not in this class."}, status=status.HTTP_404_NOT_FOUND)

    for member in members:
        # Seeded from what the member may do now, not from the model defaults,
        # so flipping one switch cannot silently close the other two.
        ClassPermission.objects.update_or_create(
            group=group,
            user=member,
            defaults={**permissions_for(group, member), **changes},
        )

    notify_class_permissions_changed(group.id)
    return Response({"members": _class_roster(group)})
