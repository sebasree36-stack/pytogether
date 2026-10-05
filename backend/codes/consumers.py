import json
import base64
import asyncio
import math
from collections import Counter
import y_py as Y
from urllib.parse import parse_qs

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core import signing 
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from y_py import YDoc, apply_update

from projects.models import Project
from usergroups.models import Group, permissions_for
from usergroups.broadcast import class_channel_group
from utils.redis_helpers import ydoc_key, active_set_key, voice_room_key, user_profile_key, room_colors_key, ACTIVE_PROJECTS_SET, DIRTY_PROJECTS_SET, ASYNC_REDIS
from utils.daily_logger import track_project_opened_async, track_max_room_async, track_max_active_rooms_async, track_ws_connection_async, track_user_async

User = get_user_model()

# What the whiteboard is allowed to hold. The server is the only thing between
# one pupil's browser console and everybody else's screen, so a stroke that does
# not fit this shape is refused rather than stored and handed on.
STROKE_TYPES = {"draw", "highlight", "erase"}
STROKE_KEYS = {"type", "color", "width", "points", "_liveId", "author"}
MAX_STROKES = 500
MAX_POINTS_PER_STROKE = 2000


def _is_number(value):
    """A real number. bool is an int in Python, which here it must not be."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _stroke_problem(stroke):
    """Why this stroke cannot be stored, or None when it is fine."""
    if not isinstance(stroke, dict):
        return "a stroke is not an object"

    unexpected = set(stroke) - STROKE_KEYS
    if unexpected:
        return f"a stroke carries unexpected keys: {sorted(unexpected)}"

    if stroke.get("type") not in STROKE_TYPES:
        return "a stroke has an unknown type"

    if not isinstance(stroke.get("color"), str) or len(stroke["color"]) > 32:
        return "a stroke colour is not a short string"

    if not _is_number(stroke.get("width")) or not 0 < stroke["width"] <= 64:
        return "a stroke width is out of range"

    points = stroke.get("points")
    if not isinstance(points, list) or not 0 < len(points) <= MAX_POINTS_PER_STROKE:
        return "a stroke has too few or too many points"

    for point in points:
        if not isinstance(point, dict) or set(point) != {"x", "y"}:
            return "a point is not an {x, y} pair"
        if not _is_number(point["x"]) or not _is_number(point["y"]):
            return "a coordinate is not a finite number"

    live_id = stroke.get("_liveId")
    if live_id is not None and (not isinstance(live_id, str) or len(live_id) > 64):
        return "a live stroke id is not a short string"

    author = stroke.get("author")
    if author is not None and (not isinstance(author, str) or len(author) > 32):
        return "a stroke author is not a short string"

    return None


def _field(user_data, key, default=""):
    """Read one field out of a redis hash. Profiles cached before a field
    existed simply come back without it, so never assume the key is there."""
    value = user_data.get(key.encode())
    return value.decode("utf-8") if value else default

class YjsCodeConsumer(AsyncJsonWebsocketConsumer):

    async def connect(self):
        self.group_id = int(self.scope["url_route"]["kwargs"]["group_id"])
        self.project_id = int(self.scope["url_route"]["kwargs"]["project_id"])
        self.room = f"project_room_g{self.group_id}_p{self.project_id}"
        self.forced_disconnect = False

        # Locked until the real ones are loaded below. A message that arrives
        # in the gap after accept() is refused rather than crashing on a
        # missing attribute.
        self.permissions = {"can_code": False, "can_draw": False, "can_chat": False}

        # Set once this connection sends something the server will not take.
        # From then on its messages are dropped rather than applied on top of a
        # document it and the server no longer agree about.
        self.poisoned = False

        self.user = self.scope.get("user")

        if not self.user or not self.user.is_authenticated:
            await self.close(code=4001)
            return

        is_member, self.is_teacher = await self._validate_membership(self.user, self.group_id, self.project_id)
        print(self.group_id, self.project_id, self.user.email, "is_member:", is_member, "teacher:", self.is_teacher)
        if not is_member:

            query_string = self.scope['query_string'].decode()
            params = parse_qs(query_string) # Returns dict like {'token': ['...'], 'share_token': ['...']}
            
            # parse_qs returns a list for each key, so we take the first one
            share_token = params.get('share_token', [None])[0]

            if not self._validate_share_token(share_token, self.group_id, self.project_id):
                print(f"Connection rejected: User {self.user.email} is not a member and invalid token.")
                await self.close(code=4003)
                return

        # Connection Accepted
        await self.channel_layer.group_add(self.room, self.channel_name)
        await self.channel_layer.group_add("global_connection_group", self.channel_name)
        # Permissions belong to the class, not to this project, so changes have
        # to reach rooms the teacher is not looking at
        await self.channel_layer.group_add(class_channel_group(self.group_id), self.channel_name)
        await self.accept()
        await track_user_async(self.user.pk)

        # Mark user active with HINCRBY (adds 1 to their tab count)
        current_connections = await ASYNC_REDIS.hincrby(active_set_key(self.project_id), str(self.user.pk), 1)
        
        # 60 second TTL
        await ASYNC_REDIS.expire(active_set_key(self.project_id), 60)
        await ASYNC_REDIS.sadd(ACTIVE_PROJECTS_SET, str(self.project_id))

        # The name is what everyone sees and it can change between sessions,
        # so it is rewritten on every connect rather than cached once.
        await ASYNC_REDIS.hset(user_profile_key(str(self.user.pk)), mapping={
            "email": self.user.email,
            "name": self.user.name,
        })
        await ASYNC_REDIS.expire(user_profile_key(str(self.user.pk)), 86400)

        self.color = await self._claim_room_color()

        # Before anything else the client can act on: a pupil who may not type
        # should never be shown a live editor, not even for a moment.
        self.permissions = await self._load_permissions()
        await self._send_permissions()

        # Always, not only on the first tab: a second tab or a quick reload
        # needs the user list too, since that message is what tells a client
        # its own name and colour.
        await self.channel_layer.group_send(self.room, {"type": "users_changed"})
        if current_connections == 1:
            await track_ws_connection_async(True)
            
        room_users = await ASYNC_REDIS.hlen(active_set_key(self.project_id))
        await track_max_room_async(room_users)
        await track_max_active_rooms_async()

        # Send Initial YJS Sync
        ydoc_bytes = await self._get_or_create_ydoc_bytes()
        
        await self.send_json({
            "type": "sync",
            "ydoc_b64": base64.b64encode(ydoc_bytes).decode()
        })
        
        await self._send_voice_room_update()
        self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def _validate_share_token(self, token, current_gid, current_pid):
        """Helper to validate signed share links"""
        if not token:
            return False
        
        signer = signing.TimestampSigner()
        try:
            data = signer.unsign_object(token)
            
            if str(data.get('pid')) == str(current_pid) and \
               str(data.get('gid')) == str(current_gid) and \
               data.get('type') == 'share_link':
                return True
                
        except (signing.BadSignature, signing.SignatureExpired):
            return False
            
        return False

    async def disconnect(self, close_code):
        try:
            if self.user and self.user.is_authenticated:
                # Always remove from voice room
                await ASYNC_REDIS.srem(voice_room_key(self.project_id), str(self.user.pk))
                await self.channel_layer.group_send(self.room, {"type": "voice_room_update"})
                
                # Subtract 1 from their tab count
                remaining_connections = await ASYNC_REDIS.hincrby(active_set_key(self.project_id), str(self.user.pk), -1)
                await track_ws_connection_async(False)
                
                # broadcast disconnect if their last tab closed
                if remaining_connections <= 0:
                    # Clean them out of the hash entirely
                    await ASYNC_REDIS.hdel(active_set_key(self.project_id), str(self.user.pk))
                    # Hand the colour back so the next student can wear it
                    await ASYNC_REDIS.hdel(room_colors_key(self.project_id), str(self.user.pk))
                    
                    await self.channel_layer.group_send(self.room, {"type": "users_changed"})
                    await self.channel_layer.group_send(
                        self.room,
                        {
                            "type": "broadcast.remove_awareness",
                            "user_id": str(self.user.pk),
                            "sender": self.channel_name
                        }
                    )

                # Check if room is completely empty using HLEN (Hash Length)
                remaining_users = await ASYNC_REDIS.hlen(active_set_key(self.project_id))
                if remaining_users == 0:
                    await ASYNC_REDIS.srem(ACTIVE_PROJECTS_SET, str(self.project_id))

        except Exception as e:
            print(f"Error during disconnect cleanup: {e}")

        if hasattr(self, "heartbeat_task"):
            self.heartbeat_task.cancel()

        await self.channel_layer.group_discard(self.room, self.channel_name)
        await self.channel_layer.group_discard("global_connection_group", self.channel_name)
        await self.channel_layer.group_discard(class_channel_group(self.group_id), self.channel_name)

    async def force_disconnect(self, event):
        self.forced_disconnect = True
        await self.close(code=4000)

    async def permissions_changed(self, event):
        """The teacher changed something; every room of the class reloads its own."""
        self.permissions = await self._load_permissions()
        await self._send_permissions()

    async def _send_permissions(self):
        await self.send_json({
            "type": "permissions",
            "is_teacher": self.is_teacher,
            **self.permissions,
        })

    @database_sync_to_async
    def _load_permissions(self):
        try:
            group = Group.objects.get(id=self.group_id)
        except Group.DoesNotExist:
            return {"can_code": False, "can_draw": False, "can_chat": False}
        return permissions_for(group, self.user)

    async def broadcast_remove_awareness(self, event):
        if event.get("sender") == self.channel_name:
            return
        await self.send_json({"type": "remove_awareness", "user_id": event["user_id"]})

    async def users_changed(self, event):
        try:
            active_user_ids = await ASYNC_REDIS.hkeys(active_set_key(self.project_id))
            active_users = []

            for uid_bytes in active_user_ids:
                uid = int(uid_bytes)
                
                user_data = await ASYNC_REDIS.hgetall(user_profile_key(str(uid)))
                
                if not user_data:
                    continue 

                email = _field(user_data, "email")
                color = await self._color_of(uid)
                active_users.append({
                    "id": str(uid),
                    "name": _field(user_data, "name") or email.split("@")[0],
                    "color": color,
                    "colorLight": color + "33"
                })

            await self.send_json({"type": "connection", "users": active_users})
        except Exception as e:
            print(f"Error in users_changed: {e}")

    async def receive(self, text_data=None, bytes_data=None):
        if self.poisoned or not text_data:
            return

        if len(text_data.encode()) > settings.MAX_MESSAGE_SIZE:
            await self.send_json({"type": "error", "message": "Message too large"})
            return
        
        try:
            msg = json.loads(text_data)
            mtype = msg.get("type")
        except Exception:
            return

        try:
            if mtype == "update":
                update_b64 = msg.get("update_b64")
                if not update_b64: return

                try:
                    update_bytes = base64.b64decode(update_b64, validate=True)
                except Exception:
                    await self._reject_update("the update was not valid base64")
                    return

                # Only hand it on once the server has taken it: a delta the
                # server refused used to still reach every other screen.
                if not await self._apply_update_to_redis_ydoc(self.project_id, update_bytes):
                    return

                await self.channel_layer.group_send(self.room, {
                    "type": "broadcast.update",
                    "update_b64": update_b64,
                    "sender": self.channel_name
                })

            elif mtype == "request_sync":
                ydoc_bytes = await self._get_or_create_ydoc_bytes()
                
                await self.send_json({
                    "type": "sync",
                    "ydoc_b64": base64.b64encode(ydoc_bytes).decode()
                })

            elif mtype == "awareness":
                update_b64 = msg.get("update_b64")
                if not update_b64: return
                await self.channel_layer.group_send(self.room, {
                    "type": "broadcast.awareness",
                    "update_b64": update_b64,
                    "sender": self.channel_name
                })

            elif mtype == "chat_message":
                if not self.permissions["can_chat"]:
                    await self.send_json({"type": "refused", "action": "chat"})
                    return

                message = msg.get("message", "").strip()
                if not message or len(message) > 1000: return
                
                # Fetch everything from the local cache instead of DB
                user_data = await ASYNC_REDIS.hgetall(user_profile_key(str(self.user.pk)))
                
                color = self.color
                name = _field(user_data, "name") or self.user.name

                await self.channel_layer.group_send(self.room, {
                    "type": "broadcast.chat_message",
                    "message": message,
                    "user_id": str(self.user.pk),
                    "user_name": name,
                    "color": color,
                    "timestamp": asyncio.get_event_loop().time()
                })

            elif mtype == "join_voice":
                await ASYNC_REDIS.sadd(voice_room_key(self.project_id), str(self.user.pk))
                await self.channel_layer.group_send(self.room, {"type": "voice_room_update"})

            elif mtype == "leave_voice":
                await ASYNC_REDIS.srem(voice_room_key(self.project_id), str(self.user.pk))
                await self.channel_layer.group_send(self.room, {"type": "voice_room_update"})

            elif mtype == "voice_signal":
                target_user = msg.get("target_user")
                signal_data = msg.get("signal_data")
                if target_user and signal_data:
                    await self.channel_layer.group_send(self.room, {
                        "type": "broadcast.voice_signal",
                        "from_user": str(self.user.pk),
                        "target_user": target_user,
                        "signal_data": signal_data,
                        "sender": self.channel_name
                    })

            elif mtype == "ping":
                await self.send(json.dumps({'type': 'pong', 'timestamp': msg.get('timestamp')}))
            
        except Exception as e:
            print(f"Error processing message: {e}")

    async def broadcast_update(self, event):
        if event.get("sender") == self.channel_name: return
        await self.send_json({"type": "update", "update_b64": event["update_b64"]})
    
    async def broadcast_awareness(self, event):
        if event.get("sender") == self.channel_name: return
        await self.send_json({"type": "awareness", "update_b64": event["update_b64"]})

    async def broadcast_chat_message(self, event):
        await self.send_json({
            "type": "chat_message",
            "message": event["message"],
            "user_id": event["user_id"],
            "user_name": event["user_name"],
            "color": event["color"],
            "timestamp": event["timestamp"]
        })

    async def voice_room_update(self, event):
        await self._send_voice_room_update()

    async def broadcast_voice_signal(self, event):
        if event.get("sender") == self.channel_name: return
        if event["target_user"] == str(self.user.pk):
            await self.send_json({
                "type": "voice_signal",
                "from_user": event["from_user"],
                "signal_data": event["signal_data"]
            })

    async def _send_voice_room_update(self):
        try:
            voice_user_ids = await ASYNC_REDIS.smembers(voice_room_key(self.project_id))
            voice_users = []
            for uid_bytes in voice_user_ids:
                uid = str(int(uid_bytes))
                user_data = await ASYNC_REDIS.hgetall(user_profile_key(uid))
                if user_data:
                    voice_users.append({
                        "id": uid,
                        "name": _field(user_data, "name") or _field(user_data, "email").split("@")[0]
                    })
                    
            await self.send_json({"type": "voice_room_update", "participants": voice_users})
        except Exception as e:
            print(f"Error sending voice room update: {e}")

    @database_sync_to_async
    def _validate_membership(self, user, group_id, project_id):
        """Returns (is_member, is_teacher). The owner of the group is the teacher."""
        try:
            project = Project.objects.select_related("group").get(id=project_id)
        except Project.DoesNotExist:
            return False, False
        if project.group.id != group_id:
            return False, False
        is_member = project.group.group_members.filter(id=user.id).exists()
        return is_member, project.group.owner_id == user.id

    async def _apply_update_to_redis_ydoc(self, project_id, update_bytes: bytes):
        """Take this delta into the stored document, or refuse it.

        Returns True when the update was accepted and may be handed on to the
        rest of the room. Every refusal boots this one connection so that it
        resyncs, because once the server declines a change the client already
        applied locally, the two documents have drifted apart and nothing short
        of a reload brings them back together.
        """
        key = ydoc_key(project_id)
        lock_key = f"{key}:lock"

        try:
            async with ASYNC_REDIS.lock(lock_key, timeout=5, blocking_timeout=5):
                cur = await ASYNC_REDIS.get(key)
                ydoc = YDoc()

                try:
                    # Apply base state
                    if cur:
                        apply_update(ydoc, cur)

                    before = self._snapshot(ydoc)

                    # Apply new delta
                    apply_update(ydoc, update_bytes)

                except Exception as e:
                    print(f"Poison update rejected for project {project_id}: {e}")
                    await self._reject_update("the update could not be read")
                    return False

                refusal = self._refusal_reason(before, self._snapshot(ydoc))
                if refusal:
                    await self._reject_update(refusal)
                    return False

                new_bytes = Y.encode_state_as_update(ydoc)

                if len(new_bytes) > settings.MAX_MESSAGE_SIZE:
                    # Dropping this silently used to leave the client holding
                    # work the server had thrown away, with no sign of it.
                    await self._reject_update("the project has reached its size limit")
                    return False

                # Atomically save the perfectly merged state back to Redis
                await ASYNC_REDIS.set(key, new_bytes)

                # Mark as dirty so celery picks up
                await ASYNC_REDIS.sadd(DIRTY_PROJECTS_SET, str(project_id))

                return True

        except Exception as e:
            print(f"Failed to acquire lock or write to Redis for project {project_id}: {e}")
            return False

    def _snapshot(self, ydoc):
        """The two things a client is allowed to change, as comparable values.

        The drawings stay a JSON string so that "did this update touch the
        board at all?" is a string comparison, and parsing only happens for the
        updates that actually did.
        """
        return {
            "code": str(ydoc.get_text("codetext")),
            "drawings": ydoc.get_array("drawings").to_json(),
        }

    def _refusal_reason(self, before, after):
        """Why this user may not make this change, or None when they may."""
        if after["code"] != before["code"] and not self.permissions["can_code"]:
            return "you cannot change the code right now"

        if after["drawings"] != before["drawings"]:
            if not self.permissions["can_draw"]:
                return "you cannot draw right now"
            return self._drawing_refusal(
                json.loads(before["drawings"]),
                json.loads(after["drawings"]),
            )

        return None

    def _drawing_refusal(self, old_strokes, new_strokes):
        """Why this change to the board is not this user's to make."""
        if len(new_strokes) > MAX_STROKES:
            return "there are too many strokes on the board"

        mine = str(self.user.pk)

        # Counted, not just compared as sets: two identical strokes are a real
        # possibility, and treating them as one let a pupil slip a copy of the
        # teacher's stroke past the signature check by matching it exactly.
        def counted(strokes):
            return Counter(json.dumps(s, sort_keys=True) for s in strokes)

        old = counted(old_strokes)
        new = counted(new_strokes)

        # Rubbing out. A stroke with no author predates the signing and counts
        # as the teacher's, so a pupil cannot clear the board by claiming the
        # unsigned marks on it.
        if not self.is_teacher:
            for gone in (old - new):
                if json.loads(gone).get("author") != mine:
                    return "you can only rub out your own drawing"

        # Adding. What was already stored was checked when it arrived, so only
        # the new marks are inspected.
        for added in (new - old):
            stroke = json.loads(added)

            problem = _stroke_problem(stroke)
            if problem:
                return problem

            if stroke.get("author") != mine:
                return "a drawing cannot be signed with somebody else's name"

            # The rubber works by covering what is underneath, other people's
            # marks included, so it stays with the teacher.
            if stroke["type"] == "erase" and not self.is_teacher:
                return "you cannot use the rubber"

        return None

    async def _reject_update(self, reason):
        """Refuse everything further from this connection and have it resync.

        Only this socket is closed. Sending force_disconnect to the room used to
        take the whole class down over one pupil's bad delta.
        """
        self.poisoned = True
        print(f"Refused an update from {self.user.email} in project {self.project_id}: {reason}")
        await self.send_json({"type": "refused", "action": "update", "reason": reason})
        await self.close(code=4010)

    async def _claim_room_color(self):
        """Give this user a colour nobody else in the room is wearing.

        The teacher sits outside the palette. Students take the lowest free
        slot, under a lock so two joining at once cannot land on the same one.
        Past twelve students colours start repeating, which beats failing.
        """
        if self.is_teacher:
            return settings.TEACHER_COLOR

        palette = settings.CLASS_COLORS
        key = room_colors_key(self.project_id)
        field = str(self.user.pk)

        async with ASYNC_REDIS.lock(f"{key}:lock", timeout=5, blocking_timeout=5):
            existing = await ASYNC_REDIS.hget(key, field)
            if existing is not None:
                # Another tab of this same user already holds a slot
                await ASYNC_REDIS.expire(key, 86400)
                return palette[int(existing) % len(palette)]

            taken = {int(v) for v in (await ASYNC_REDIS.hvals(key))}
            slot = next(
                (i for i in range(len(palette)) if i not in taken),
                len(taken) % len(palette),
            )
            await ASYNC_REDIS.hset(key, field, slot)
            await ASYNC_REDIS.expire(key, 86400)

        return palette[slot]

    async def _color_of(self, user_id):
        """The colour another user is wearing in this room."""
        slot = await ASYNC_REDIS.hget(room_colors_key(self.project_id), str(user_id))
        if slot is None:
            # No slot means the teacher, who never takes one
            return settings.TEACHER_COLOR
        return settings.CLASS_COLORS[int(slot) % len(settings.CLASS_COLORS)]

    async def _heartbeat_loop(self):
        try:
            while True:
                await asyncio.sleep(settings.HEARTBEAT_INTERVAL)
                if self.user and self.user.is_authenticated:
                    # Extend the TTL for another 60 seconds
                    await ASYNC_REDIS.expire(active_set_key(self.project_id), 60)
                    await ASYNC_REDIS.expire(voice_room_key(self.project_id), 60)
        except asyncio.CancelledError:
            return
    
    async def _get_or_create_ydoc_bytes(self):
        """Fetch the YDoc from Redis, or initialise it securely from the DB.

        Creating it happens under the project's lock, re-checking inside. A
        class told to open the same project at once used to race here: several
        connections each built their own document from the same text, each with
        its own client id, the last write won, and everyone whose version lost
        was left editing a document the server had never seen. Their keystrokes
        then vanished without a word, because their updates named items that
        did not exist in the stored document.
        """
        ydoc_bytes = await ASYNC_REDIS.get(ydoc_key(self.project_id))

        if ydoc_bytes:
            return ydoc_bytes

        key = ydoc_key(self.project_id)
        async with ASYNC_REDIS.lock(f"{key}:lock", timeout=5, blocking_timeout=5):
            # Somebody else may have created it while we waited for the lock
            ydoc_bytes = await ASYNC_REDIS.get(key)
            if ydoc_bytes:
                return ydoc_bytes

            # Redis is empty. Fetch the raw text from the database
            code_obj = await database_sync_to_async(lambda: getattr(Project.objects.get(id=self.project_id), "code", None))()
            text = code_obj.content if code_obj else ""

            # Initialize a brand new Yjs Document on the server
            new_ydoc = YDoc()
            ytext = new_ydoc.get_text('codetext')

            # Safely insert the database text into the server's CRDT
            with new_ydoc.begin_transaction() as txn:
                ytext.extend(txn, text)

            # Convert to binary update
            new_bytes = Y.encode_state_as_update(new_ydoc)

            # Save to Redis immediately so it is permanently synchronized
            await ASYNC_REDIS.set(key, new_bytes)

        await track_project_opened_async()   # tracking first opening

        return new_bytes
