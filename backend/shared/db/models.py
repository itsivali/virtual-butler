from pydantic import BaseModel, Field, validator
from datetime import datetime
from typing import Optional, List
from bson import ObjectId
from enum import Enum

class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid ObjectId")
        return ObjectId(v)

class StatusEnum(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class PriorityEnum(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"

class NotificationTypeEnum(str, Enum):
    CHAT = "chat"
    WORK_ORDER = "work_order"
    SYSTEM = "system"
    ALERT = "alert"

class BaseDBModel(BaseModel):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_encoders = {ObjectId: str}
        populate_by_name = True
        use_enum_values = True

class ChatRequest(BaseDBModel):
    request_id: str
    guest_id: str
    message: str
    status: StatusEnum = StatusEnum.PENDING
    department: Optional[str] = None
    tags: List[str] = []

    @validator('message')
    def message_not_empty(cls, v):
        if not v.strip():
            raise ValueError('Message cannot be empty')
        return v.strip()

class WorkOrder(BaseDBModel):
    request_id: str
    guest_id: str
    staff_id: Optional[str] = None
    description: str
    status: StatusEnum = StatusEnum.ASSIGNED
    priority: PriorityEnum = PriorityEnum.MEDIUM
    department: str
    completed_at: Optional[datetime] = None
    notes: List[str] = []

    @validator('description')
    def description_not_empty(cls, v):
        if not v.strip():
            raise ValueError('Description cannot be empty')
        return v.strip()

class Notification(BaseDBModel):
    request_id: str
    guest_id: str
    message: str
    type: NotificationTypeEnum
    read: bool = False
    action_url: Optional[str] = None
    metadata: dict = {}

    @validator('message')
    def message_not_empty(cls, v):
        if not v.strip():
            raise ValueError('Message cannot be empty')
        return v.strip()