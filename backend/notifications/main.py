from fastapi import FastAPI, HTTPException, Depends, status, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import structlog
import os
import asyncio
from shared.db.database import DatabaseConnection
from shared.db.models import Notification, NotificationTypeEnum, PriorityEnum
from jose import jwt, JWTError

logger = structlog.get_logger()
app = FastAPI(
    title="Virtual Butler Notification Service",
    description="Real-time, secure, and scalable notification delivery for guests and staff.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()
JWT_SECRET = os.getenv("JWT_SECRET", "supersecret")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
NOTIFICATION_TTL_DAYS = int(os.getenv("NOTIFICATION_TTL_DAYS", "30"))


def verify_jwt(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError as e:
        logger.error("jwt_verification_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

async def ensure_ttl_index():
    async with DatabaseConnection.get_connection() as conn:
        if conn is None:
            logger.error("database_connection_failed", error="Connection object is None")
            return
        await conn.virtualbutler.notifications.create_index(
            [("created_at", 1)],
            expireAfterSeconds=NOTIFICATION_TTL_DAYS * 24 * 3600,
            name="ttl_created_at"
        )

# --- Event-Driven: Service Bus/SignalR Integration (Mocked) ---

async def subscribe_to_status_events():
    # TODO: Integrate with Azure Service Bus/Event Grid
    while True:
        # Simulate event consumption
        await asyncio.sleep(60)
        logger.info("mock_status_event_consumed")

async def push_signalr_notification(notification: dict, guest_id: str):
    # TODO: Integrate with Azure SignalR Service
    logger.info("signalr_notification_pushed", guest_id=guest_id, notification_id=notification.get("notification_id"))

async def push_mobile_notification(notification: dict, guest_id: str):
    # TODO: Integrate with APNS/FCM
    logger.info("mobile_notification_pushed", guest_id=guest_id, notification_id=notification.get("notification_id"))

# --- Notification Formatting & Localization (Stub) ---

def format_notification_message(event: dict, lang: str = "en") -> str:
    # TODO: Pull from i18n templates
    status = event.get("status", "update")
    if status == "pending":
        return "Your request has been received."
    if status == "in_progress":
        return "Your request is now in progress."
    if status == "completed":
        return "Your request has been completed."
    return "You have a new update."


@app.on_event("startup")
async def startup_db_client():
    await DatabaseConnection.connect()
    await ensure_ttl_index()
    asyncio.create_task(subscribe_to_status_events())

@app.on_event("shutdown")
async def shutdown_db_client():
    await DatabaseConnection.close()

@app.post("/api/v1/notifications", response_model=Notification, status_code=201)
async def create_notification(
    notification: Notification,
    user=Depends(verify_jwt)
):
    try:
        async with DatabaseConnection.get_connection() as conn:
            if conn is None:
                logger.error("database_connection_failed", error="Connection object is None in create_notification")
                raise HTTPException(status_code=500, detail="Database connection failed")
            result = await conn.virtualbutler.notifications.insert_one(notification.model_dump(by_alias=True))
            notification_data = notification.model_dump()
            notification_data["id"] = str(result.inserted_id)

        await push_signalr_notification(notification_data, notification.guest_id)
        await push_mobile_notification(notification_data, notification.guest_id)
        logger.info("notification_created", notification_id=notification.notification_id, guest_id=notification.guest_id)
        return notification
    except Exception as e:
        logger.error("notification_creation_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to create notification")

@app.get("/api/v1/notifications/history", response_model=List[Notification])
async def get_notification_history(
    user=Depends(verify_jwt)
):
    guest_id = user["sub"]
    try:
        notifications = []
        async with DatabaseConnection.get_connection() as conn:
            if conn is None:
                logger.error("database_connection_failed", error="Connection object is None in get_notification_history")
                raise HTTPException(status_code=500, detail="Database connection failed")
            cursor = conn.virtualbutler.notifications.find({"guest_id": guest_id})
            async for doc in cursor:
                notifications.append(Notification(**doc))
        return notifications
    except Exception as e:
        logger.error("get_notification_history_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch notification history")

@app.patch("/api/v1/notifications/{notification_id}/read", status_code=204)
async def mark_notification_read(
    notification_id: str,
    user=Depends(verify_jwt)
):
    guest_id = user["sub"]
    try:
        async with DatabaseConnection.get_connection() as conn:
            if conn is None:
                logger.error("database_connection_failed", error="Connection object is None in mark_notification_read")
                raise HTTPException(status_code=500, detail="Database connection failed")
            result = await conn.virtualbutler.notifications.update_one(
                {"notification_id": notification_id, "guest_id": guest_id},
                {"$set": {"read": True, "read_at": datetime.utcnow()}}
            )
            if result.modified_count == 0:
                raise HTTPException(status_code=404, detail="Notification not found or already read")
        logger.info("notification_marked_read", notification_id=notification_id, guest_id=guest_id)
    except Exception as e:
        logger.error("mark_notification_read_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to mark notification as read")

@app.get("/healthz")
async def health_check():
    return await DatabaseConnection.health_check()

@app.get("/readiness")
async def readiness_check():
    return {"status": "ready"}

async def audit_log(event: str, data: dict):
    async with DatabaseConnection.get_connection() as conn:
        if conn is None:
            logger.error("database_connection_failed", error="Connection object is None in audit_log")
            return
        await conn.virtualbutler.notification_logs.insert_one({
            "event": event,
            "data": data,
            "timestamp": datetime.utcnow()
        })


from fastapi.responses import JSONResponse

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again."}
    )