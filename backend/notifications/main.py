from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from shared.db.models import Notification
except ImportError:
    from pydantic import BaseModel, Field
    from typing import Optional
    from bson import ObjectId

    class Notification(BaseModel):
        id: Optional[str] = Field(alias="_id")
        guest_id: str
        message: str
        read: bool = False

from shared.db.database import notifications
from bson import ObjectId as PyObjectId

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/v1/notifications", response_model=Notification)
async def create_notification(notification: Notification):
    notification_dict = notification.model_dump(by_alias=True)
    await notifications.insert_one(notification_dict)
    return notification

@app.get("/api/v1/notifications/{guest_id}", response_model=list[Notification])
async def get_guest_notifications(guest_id: str):
    notification_list = []
    cursor = notifications.find({"guest_id": guest_id})
    async for notif in cursor:
        notification_list.append(Notification(**notif))
    return notification_list

@app.put("/api/v1/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str):
    update_result = await notifications.update_one(
        {"_id": PyObjectId(notification_id)},
        {"$set": {"read": True}}
    )
    if update_result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Notification marked as read"}

@app.delete("/api/v1/notifications/{notification_id}")
async def delete_notification(notification_id: str):
    delete_result = await notifications.delete_one({"_id": PyObjectId(notification_id)})
    if delete_result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Notification deleted"}