from fastapi import FastAPI, HTTPException, Depends, status, Body, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi_limiter import FastAPILimiter
from fastapi_limiter.depends import RateLimiter
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
from shared.db.database import DatabaseConnection
from shared.db.models import WorkOrder, StatusEnum, DepartmentEnum, PriorityEnum
from jose import jwt, JWTError
import asyncio
import structlog
import os
import re
import httpx

# --- Setup ---
logger = structlog.get_logger()
app = FastAPI(title="Virtual Butler Work Orders API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

security = HTTPBearer()
JWT_SECRET = os.getenv("JWT_SECRET", "supersecret")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
WORKORDER_TTL_DAYS = int(os.getenv("WORKORDER_TTL_DAYS", "7"))
NOTIFICATION_SERVICE_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://localhost:8002/notify")

# --- Auth ---
def verify_jwt(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def require_staff(payload=Depends(verify_jwt)):
    if payload.get("role") not in ("staff", "admin"):
        raise HTTPException(status_code=403, detail="Insufficient privileges")
    return payload

def require_admin(payload=Depends(verify_jwt)):
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return payload

# --- Routing ---
DEPARTMENT_KEYWORDS = {
    DepartmentEnum.HOUSEKEEPING: [r"towel|clean|linen|sheet|pillow|blanket"],
    DepartmentEnum.MAINTENANCE: [r"ac|repair|leak|light|plumbing"],
    DepartmentEnum.ROOM_SERVICE: [r"food|menu|drink|water"],
    DepartmentEnum.IT: [r"wifi|internet|tv|network"],
    DepartmentEnum.FRONT_DESK: [r"checkout|bill|key|card"],
    DepartmentEnum.SECURITY: [r"safe|security|lost|alarm"],
    DepartmentEnum.CONCIERGE: [r"taxi|spa|booking|restaurant"]
}
DEFAULT_DEPARTMENT = DepartmentEnum.FRONT_DESK

def route_department(msg: str) -> DepartmentEnum:
    text = msg.lower()
    for dept, patterns in DEPARTMENT_KEYWORDS.items():
        for pat in patterns:
            if re.search(pat, text):
                return dept
    return DEFAULT_DEPARTMENT

# --- Models ---
class WorkOrderCreate(BaseModel):
    guest_id: str
    room_number: Optional[str]
    message: str
    priority: Optional[PriorityEnum] = PriorityEnum.MEDIUM

class WorkOrderStatusUpdate(BaseModel):
    status: StatusEnum

class WorkOrderAssignUpdate(BaseModel):
    assigned_staff: str

class WorkOrderEstimateUpdate(BaseModel):
    estimated_duration: int  # in minutes

class WorkOrderUpdate(BaseModel):
    description: Optional[str]
    priority: Optional[PriorityEnum]
    metadata: Optional[Dict[str, Any]]
    status: Optional[StatusEnum]
    assigned_staff: Optional[str]
    estimated_duration: Optional[int]

# --- Notifications & Events ---
async def notify_status_change(work_order: dict):
    # Enhanced: add guest name, room, assigned staff, overdue flag
    try:
        payload = dict(work_order)
        guest_name = None
        room_number = None
        async with DatabaseConnection.get_connection() as conn:
            guest = await conn["virtualbutler"]["guest_profiles"].find_one({"guest_id": work_order.get("guest_id")})
            if guest:
                guest_name = guest.get("name")
                room_number = guest.get("room_number")
        payload["guest_name"] = guest_name
        payload["room_number"] = room_number
        payload["assigned_staff"] = work_order.get("assigned_staff")
        # Overdue logic: if pending and created_at + estimated_duration < now
        overdue = False
        if work_order.get("status") == StatusEnum.PENDING and work_order.get("estimated_duration"):
            created = work_order.get("created_at")
            if created and isinstance(created, datetime):
                due = created + timedelta(minutes=work_order["estimated_duration"])
                overdue = datetime.now(timezone.utc) > due
        payload["overdue"] = overdue
        async with httpx.AsyncClient() as client:
            await client.post(NOTIFICATION_SERVICE_URL, json=payload)
    except Exception as e:
        logger.error("notify_failed", error=str(e))

# --- CRUD ---
@app.post("/work-orders", response_model=WorkOrder, dependencies=[Depends(RateLimiter(times=5, seconds=60))])
async def create_work_order(data: WorkOrderCreate, user=Depends(require_staff)):
    now = datetime.now(timezone.utc)
    work_order = WorkOrder(
        request_id=f"req_{now.timestamp()}",
        work_order_id=f"wo_{now.timestamp()}",
        guest_id=data.guest_id,
        department=route_department(data.message),
        description=data.message,
        status=StatusEnum.PENDING,
        priority=data.priority,
        created_at=now,
        updated_at=now,
        metadata={"room_number": data.room_number},
        estimated_duration=None
    )
    async with DatabaseConnection.get_connection() as conn:
        result = await conn["virtualbutler"]["work_orders"].insert_one(work_order.model_dump(by_alias=True))
        work_order.id = result.inserted_id
    await notify_status_change(work_order.model_dump())
    return work_order

@app.get("/work-orders/{work_order_id}", response_model=WorkOrder)
async def get_work_order(work_order_id: str, user=Depends(require_staff)):
    async with DatabaseConnection.get_connection() as conn:
        doc = await conn["virtualbutler"]["work_orders"].find_one({"work_order_id": work_order_id})
        if not doc:
            raise HTTPException(404, detail="Not found")
        return WorkOrder(**doc)

@app.patch("/work-orders/{work_order_id}/assign", response_model=WorkOrder)
async def assign_work_order(work_order_id: str, update: WorkOrderAssignUpdate, user=Depends(require_admin)):
    async with DatabaseConnection.get_connection() as conn:
        doc = await conn["virtualbutler"]["work_orders"].find_one_and_update(
            {"work_order_id": work_order_id},
            {"$set": {"assigned_staff": update.assigned_staff, "updated_at": datetime.now(timezone.utc)}},
            return_document=True
        )
        if not doc:
            raise HTTPException(404, detail="Work order not found")
        await notify_status_change(doc)
        return WorkOrder(**doc)

@app.patch("/work-orders/{work_order_id}/estimate", response_model=WorkOrder)
async def set_estimated_completion(work_order_id: str, update: WorkOrderEstimateUpdate, user=Depends(require_admin)):
    async with DatabaseConnection.get_connection() as conn:
        doc = await conn["virtualbutler"]["work_orders"].find_one_and_update(
            {"work_order_id": work_order_id},
            {"$set": {"estimated_duration": update.estimated_duration, "updated_at": datetime.now(timezone.utc)}},
            return_document=True
        )
        if not doc:
            raise HTTPException(404, detail="Work order not found")
        await notify_status_change(doc)
        return WorkOrder(**doc)

@app.put("/work-orders/{work_order_id}", response_model=WorkOrder)
async def update_work_order(work_order_id: str, update: WorkOrderUpdate, user=Depends(require_staff)):
    async with DatabaseConnection.get_connection() as conn:
        update_data = {k: v for k, v in update.dict(exclude_unset=True).items() if v is not None}
        if not update_data:
            raise HTTPException(400, detail="No data to update")
        update_data["updated_at"] = datetime.now(timezone.utc)
        doc = await conn["virtualbutler"]["work_orders"].find_one_and_update(
            {"work_order_id": work_order_id},
            {"$set": update_data},
            return_document=True
        )
        if not doc:
            raise HTTPException(404, detail="Work order not found")
        await notify_status_change(doc)
        # Webhook notification if completed
        if update_data.get("status") == StatusEnum.COMPLETED:
            await send_work_order_completed_webhook(doc)
        return WorkOrder(**doc)

async def send_work_order_completed_webhook(work_order: dict):
    webhook_url = os.getenv("WORKORDER_COMPLETED_WEBHOOK_URL")
    if not webhook_url:
        return
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            await client.post(webhook_url, json=work_order)
    except Exception as e:
        logger.error("workorder_completed_webhook_failed", error=str(e))

@app.delete("/work-orders/{work_order_id}", status_code=204)
async def delete_work_order(work_order_id: str, user=Depends(require_admin)):
    async with DatabaseConnection.get_connection() as conn:
        result = await conn["virtualbutler"]["work_orders"].delete_one({"work_order_id": work_order_id})
        if result.deleted_count == 0:
            raise HTTPException(404, detail="Not found")
    logger.info("work_order_deleted", work_order_id=work_order_id)

@app.get("/work-orders", response_model=List[WorkOrder])
async def list_work_orders(
    status: Optional[StatusEnum] = None,
    department: Optional[DepartmentEnum] = None,
    guest_id: Optional[str] = None,
    priority: Optional[PriorityEnum] = None,
    assigned_staff: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    user=Depends(require_admin)
):
    query = {}
    if status: query["status"] = status
    if department: query["department"] = department
    if guest_id: query["guest_id"] = guest_id
    if priority: query["priority"] = priority
    if assigned_staff: query["assigned_staff"] = assigned_staff

    async with DatabaseConnection.get_connection() as conn:
        cursor = conn["virtualbutler"]["work_orders"].find(query).skip(skip).limit(limit)
        results = [WorkOrder(**doc) async for doc in cursor]
    return results

@app.get("/reports/work-orders", dependencies=[Depends(require_admin)])
async def report_work_orders():
    async with DatabaseConnection.get_connection() as conn:
        pipeline = [
            {"$group": {
                "_id": "$department",
                "total": {"$sum": 1},
                "pending": {"$sum": {"$cond": [{"$eq": ["$status", "PENDING"]}, 1, 0]}},
                "completed": {"$sum": {"$cond": [{"$eq": ["$status", "COMPLETED"]}, 1, 0]}},
            }}
        ]
        result = await conn["virtualbutler"]["work_orders"].aggregate(pipeline).to_list(length=100)
    return result

# --- Startup ---
@app.on_event("startup")
async def startup_event():
    await DatabaseConnection.connect()
    await FastAPILimiter.init(DatabaseConnection.client["virtualbutler"]["ratelimits"])
