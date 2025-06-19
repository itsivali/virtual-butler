from pydantic import BaseModel, Field, validator, EmailStr
from datetime import datetime
from typing import Optional, List, Dict, Any
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

    @classmethod
    def __get_pydantic_json_schema__(cls, field_schema):
        field_schema.update(type="string")

class StatusEnum(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ON_HOLD = "on_hold"

class PriorityEnum(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"

class DepartmentEnum(str, Enum):
    HOUSEKEEPING = "housekeeping"
    MAINTENANCE = "maintenance"
    FRONT_DESK = "front_desk"
    ROOM_SERVICE = "room_service"
    IT = "it"
    SECURITY = "security"
    CONCIERGE = "concierge"

class NotificationTypeEnum(str, Enum):
    CHAT = "chat"
    WORK_ORDER = "work_order"
    SYSTEM = "system"
    ALERT = "alert"
    REMINDER = "reminder"

class BaseDBModel(BaseModel):
    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_encoders = {ObjectId: str}
        populate_by_name = True
        use_enum_values = True
        arbitrary_types_allowed = True
        
    @validator("id", pre=True)
    def validate_object_id(cls, v):
        if v is None:
            return None
        if isinstance(v, ObjectId):
            return v
        if ObjectId.is_valid(v):
            return ObjectId(v)
        raise ValueError("Invalid ObjectId")

class GuestProfile(BaseModel):
    guest_id: str = Field(..., description="Unique identifier for the guest")
    room_number: Optional[str] = None
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    vip_status: bool = False
    preferences: Dict[str, Any] = Field(default_factory=dict)

class ChatRequest(BaseDBModel):
    request_id: str = Field(..., description="Unique identifier for the request")
    guest_id: str
    guest_profile: Optional[GuestProfile] = None
    message: str = Field(..., min_length=1, max_length=1000)
    voice_transcript: Optional[str] = None
    department: DepartmentEnum
    status: StatusEnum = StatusEnum.PENDING
    tags: List[str] = Field(default_factory=list)
    sentiment: Optional[float] = Field(None, ge=-1.0, le=1.0)
    language: str = "en"
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        schema_extra = {
            "example": {
                "request_id": "req_123",
                "guest_id": "guest_456",
                "message": "Need extra towels please",
                "department": "housekeeping",
                "status": "pending",
                "tags": ["towels", "housekeeping"]
            }
        }

class WorkOrder(BaseDBModel):
    request_id: str = Field(..., description="Reference to original chat request")
    work_order_id: str = Field(..., description="Unique identifier for the work order")
    guest_id: str
    staff_id: Optional[str] = None
    department: DepartmentEnum
    description: str = Field(..., min_length=1, max_length=500)
    status: StatusEnum = StatusEnum.PENDING
    priority: PriorityEnum = PriorityEnum.MEDIUM
    assigned_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    estimated_duration: Optional[int] = Field(None, description="Estimated minutes to complete")
    actual_duration: Optional[int] = None
    notes: List[str] = Field(default_factory=list)
    location: Optional[str] = None
    materials_needed: List[str] = Field(default_factory=list)
    attachments: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        schema_extra = {
            "example": {
                "request_id": "req_123",
                "work_order_id": "wo_789",
                "guest_id": "guest_456",
                "staff_id": "staff_789",
                "department": "housekeeping",
                "description": "Deliver extra towels to Room 301",
                "status": "assigned",
                "priority": "medium"
            }
        }

class Notification(BaseDBModel):
    notification_id: str = Field(..., description="Unique identifier for the notification")
    request_id: str
    guest_id: str
    type: NotificationTypeEnum
    message: str = Field(..., min_length=1, max_length=500)
    read: bool = False
    read_at: Optional[datetime] = None
    action_url: Optional[str] = None
    action_required: bool = False
    expiry: Optional[datetime] = None
    priority: PriorityEnum = PriorityEnum.MEDIUM
    metadata: Dict[str, Any] = Field(default_factory=dict)
    recipient_channels: List[str] = Field(
        default_factory=lambda: ["app"],
        description="Delivery channels (app, email, sms, etc.)"
    )

    class Config:
        schema_extra = {
            "example": {
                "notification_id": "notif_123",
                "request_id": "req_123",
                "guest_id": "guest_456",
                "type": "chat",
                "message": "Your request has been received",
                "read": False,
                "action_url": "/requests/req_123"
            }
        }

    @validator("expiry")
    def validate_expiry(cls, v, values):
        if v and v < values.get("created_at", datetime.utcnow()):
            raise ValueError("Expiry time must be in the future")
        return v

class MessageThread(BaseDBModel):
    thread_id: str = Field(..., description="Unique identifier for the message thread")
    request_id: str
    guest_id: str
    staff_id: Optional[str] = None
    department: DepartmentEnum
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    status: StatusEnum = StatusEnum.PENDING
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        schema_extra = {
            "example": {
                "thread_id": "thread_123",
                "request_id": "req_123",
                "guest_id": "guest_456",
                "department": "housekeeping",
                "status": "pending"
            }
        }