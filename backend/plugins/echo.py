import asyncio
from typing import Any, Dict

async def run_plugin(payload: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """
    Echo plugin: Returns the payload and user info, with additional metadata.
    - If payload contains a 'message', returns it uppercased and reversed.
    - Includes a timestamp and user role validation.
    - Demonstrates async logic and error handling.
    """
    import datetime

    await asyncio.sleep(0)  # Simulate async work

    # Extract and process message if present
    message = payload.get("message")
    processed = None
    if message and isinstance(message, str):
        processed = {
            "original": message,
            "upper": message.upper(),
            "reversed": message[::-1],
            "length": len(message)
        }

    # User info and role check
    user_info = {
        "sub": user.get("sub"),
        "role": user.get("role"),
        "room": user.get("room"),
        "is_guest": user.get("role") == "guest",
        "is_staff": user.get("role") == "staff",
        "is_admin": user.get("role") == "admin"
    }

    # Compose response
    response = {
        "echo": payload,
        "processed_message": processed,
        "user": user_info,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
    }

    # Example: Add a warning if message is too long
    if processed and processed["length"] > 100:
        response["warning"] = "Message is too long, consider shortening it."

    return response