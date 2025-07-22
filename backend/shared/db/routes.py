from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status, Body
from typing import List, Optional
from datetime import datetime
from bson import ObjectId
from fastapi_limiter.depends import RateLimiter
from shared.db.database import DatabaseConnection
from shared.db.models import Notification, User, GuestProfile
try:
    from shared.security.auth import get_current_user, is_admin_user, is_staff_user
except ModuleNotFoundError:
    import sys
    import os
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from shared.security.auth import get_current_user, is_admin_user, is_staff_user
from shared.services.email import send_verification_email

router = APIRouter(prefix="/api", tags=["Guest", "User", "Notification"])

# ---------------------- GUEST CRUD ----------------------
@router.post("/guests", response_model=GuestProfile, dependencies=[Depends(RateLimiter(times=5, seconds=60))])
async def create_guest(guest: GuestProfile):
    async with DatabaseConnection.get_connection() as conn:
        await conn["virtualbutler"]["guest_profiles"].insert_one(guest.dict())
        return guest

@router.get("/guests", response_model=List[GuestProfile])
async def list_guests(q: Optional[str] = Query(None), skip: int = 0, limit: int = 10):
    async with DatabaseConnection.get_connection() as conn:
        query = {"$or": [{"name": {"$regex": q, "$options": "i"}}, {"email": {"$regex": q, "$options": "i"}}]} if q else {}
        cursor = conn["virtualbutler"]["guest_profiles"].find(query).skip(skip).limit(limit)
        return [GuestProfile(**doc) async for doc in cursor]

@router.get("/guests/{guest_id}", response_model=GuestProfile)
async def get_guest(guest_id: str):
    async with DatabaseConnection.get_connection() as conn:
        doc = await conn["virtualbutler"]["guest_profiles"].find_one({"guest_id": guest_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Guest not found")
        return GuestProfile(**doc)

@router.put("/guests/{guest_id}", response_model=GuestProfile)
async def update_guest(guest_id: str, guest: GuestProfile):
    async with DatabaseConnection.get_connection() as conn:
        await conn["virtualbutler"]["guest_profiles"].update_one({"guest_id": guest_id}, {"$set": guest.dict()})
        return guest

@router.delete("/guests/{guest_id}", status_code=204)
async def delete_guest(guest_id: str):
    async with DatabaseConnection.get_connection() as conn:
        result = await conn["virtualbutler"]["guest_profiles"].delete_one({"guest_id": guest_id})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Guest not found")


# ---------------------- USER CRUD ----------------------
@router.get("/users", response_model=List[User], dependencies=[Depends(is_admin_user)])
async def list_users(skip: int = 0, limit: int = 10):
    async with DatabaseConnection.get_connection() as conn:
        if conn is None:
            raise HTTPException(status_code=500, detail="Database connection failed")
        db = conn["virtualbutler"]
        cursor = db["users"].find().skip(skip).limit(limit)
        return [User(**doc) async for doc in cursor]

@router.get("/users/{user_id}", response_model=User)
async def get_user(user_id: str, current_user=Depends(get_current_user)):
    async with DatabaseConnection.get_connection() as conn:
        if conn is None:
            raise HTTPException(status_code=500, detail="Database connection failed")
        doc = await conn["users"].find_one({"_id": ObjectId(user_id)})
        if not doc:
            raise HTTPException(status_code=404, detail="User not found")
        return User(**doc)

@router.put("/users/{user_id}", response_model=User)
async def update_user(user_id: str, user: User, current_user=Depends(is_admin_user)):
    async with DatabaseConnection.get_connection() as conn:
        if conn is None:
            raise HTTPException(status_code=500, detail="Database connection failed")
        await conn["users"].update_one({"_id": ObjectId(user_id)}, {"$set": user.dict(exclude_unset=True)})
        return user

@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: str, current_user=Depends(is_admin_user)):
    async with DatabaseConnection.get_connection() as conn:
        if conn is None:
            raise HTTPException(status_code=500, detail="Database connection failed")
        result = await conn["users"].delete_one({"_id": ObjectId(user_id)})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="User not found")


# ---------------------- NOTIFICATION CRUD ----------------------
@router.post("/notifications", response_model=Notification)
async def create_notification(notification: Notification, user: User = Depends(get_current_user)):
    async with DatabaseConnection.get_connection() as conn:
        if conn is None:
            raise HTTPException(status_code=500, detail="Database connection failed")
        await conn["notifications"].insert_one(notification.dict(by_alias=True))
        return notification

@router.get("/notifications", response_model=List[Notification])
async def list_notifications(user: User = Depends(get_current_user), skip: int = 0, limit: int = 10):
    async with DatabaseConnection.get_connection() as conn:
        # Use user.room_id or user.username or user.id as appropriate
        cursor = conn["virtualbutler"]["notifications"].find({"guest_id": getattr(user, "room_id", None) or getattr(user, "username", None)}).skip(skip).limit(limit)
        return [Notification(**doc) async for doc in cursor]

@router.get("/notifications/{notification_id}", response_model=Notification)
async def get_notification(notification_id: str, user: User = Depends(get_current_user)):
    async with DatabaseConnection.get_connection() as conn:
        doc = await conn["virtualbutler"]["notifications"].find_one({"notification_id": notification_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Notification not found")
        return Notification(**doc)

@router.delete("/notifications/{notification_id}", status_code=204)
async def delete_notification(notification_id: str, user: User = Depends(get_current_user)):
    async with DatabaseConnection.get_connection() as conn:
        result = await conn["virtualbutler"]["notifications"].delete_one({"notification_id": notification_id})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Notification not found")


# ---------------------- REAL-TIME CHAT ----------------------

# --- Real-time Chat WebSocket (single endpoint, persistence + broadcast) ---
from shared.db.models import ChatRequest, DepartmentEnum, StatusEnum

active_connections: dict[str, WebSocket] = {}

@router.websocket("/ws/{guest_id}")
async def chat_websocket(websocket: WebSocket, guest_id: str):
    await websocket.accept()
    active_connections[guest_id] = websocket
    try:
        while True:
            data = await websocket.receive_json()
            message = data.get("message")
            if not message:
                continue
            # Store chat request
            chat_doc = ChatRequest(
                request_id=f"req_{datetime.utcnow().timestamp()}",
                guest_id=guest_id,
                message=message,
                department=DepartmentEnum.FRONT_DESK,  # Optionally route
                status=StatusEnum.PENDING,
                sentiment=0.0,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            async with DatabaseConnection.get_connection() as conn:
                await conn["virtualbutler"]["chat_requests"].insert_one(chat_doc.model_dump(by_alias=True))
            # Echo to all connected staff
            for ws in active_connections.values():
                if ws != websocket:
                    await ws.send_json({"guest_id": guest_id, "message": message})
    except WebSocketDisconnect:
        active_connections.pop(guest_id, None)

# Chat CRUD
@router.get("/requests", response_model=List[ChatRequest], dependencies=[Depends(RateLimiter(times=5, seconds=60))])
async def get_chat_requests(skip: int = 0, limit: int = 10, user=Depends(get_current_user)):
    async with DatabaseConnection.get_connection() as conn:
        cursor = conn["virtualbutler"]["chat_requests"].find().skip(skip).limit(limit)
        return [ChatRequest(**doc) async for doc in cursor]