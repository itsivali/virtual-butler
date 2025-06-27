import asyncio
from typing import Any, Dict

async def run_plugin(payload: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """
    Room Info plugin: Returns detailed guest room information and request context.
    - Returns guest_id, room number, and any extra payload.
    - Flags if the user is a guest, staff, or admin.
    - Includes a timestamp and a summary message.
    - Demonstrates async logic and error handling.
    """
    import datetime

    await asyncio.sleep(0)  # Simulate async work

    guest_id = user.get("sub")
    room = user.get("room")
    role = user.get("role")

    # Compose user info
    user_info = {
        "guest_id": guest_id,
        "room": room,
        "role": role,
        "is_guest": role == "guest",
        "is_staff": role == "staff",
        "is_admin": role == "admin"
    }

    # Compose response
    response = {
        "user_info": user_info,
        "payload": payload,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "summary": f"User {guest_id} ({role}) requested room info for room {room}."
    }

    # Example: Add a warning if room is missing
    if not room:
        response["warning"] = "Room number is missing from user context."

    # Example: Add a note if extra details are requested
    if payload.get("details") == "full":
        response["note"] = "Full details requested, including all available user info."
    return response