from fastapi import FastAPI, HTTPException, Depends, status, Body, Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
import structlog
import os
import re
import asyncio
from shared.db.database import DatabaseConnection
from shared.db.models import WorkOrder, StatusEnum, DepartmentEnum, PriorityEnum
from jose import jwt, JWTError

logger = structlog.get_logger()
app = FastAPI(
    title="Virtual Butler Work Orders API",
    description="API for intelligent work order routing and management.",
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
WORKORDER_TTL_DAYS = int(os.getenv("WORKORDER_TTL_DAYS", "7"))

# --- Security ---

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

def require_staff_role(payload=Depends(verify_jwt)):
    if payload.get("role") not in ("staff", "admin"):
        raise HTTPException(status_code=403, detail="Insufficient privileges")
    return payload

# --- Routing Logic ---

DEPARTMENT_KEYWORDS = {
    DepartmentEnum.HOUSEKEEPING: [r"towel|clean|linen|sheet|pillow|blanket"],
    DepartmentEnum.MAINTENANCE: [r"ac|air.?condition|fix|repair|leak|broken|light|bulb|plumbing"],
    DepartmentEnum.ROOM_SERVICE: [r"food|order|menu|breakfast|dinner|lunch|drink|water|coffee"],
    DepartmentEnum.IT: [r"wifi|internet|tv|remote|network|connect"],
    DepartmentEnum.FRONT_DESK: [r"checkout|check.?out|late|early|bill|invoice|key|card"],
    DepartmentEnum.SECURITY: [r"safe|security|lost|theft|emergency|alarm"],
    DepartmentEnum.CONCIERGE: [r"taxi|tour|spa|reservation|booking|recommend|restaurant"],
}

DEFAULT_DEPARTMENT = DepartmentEnum.FRONT_DESK

def route_department(message: str) -> DepartmentEnum:
    text = message.lower()
    for dept, patterns in DEPARTMENT_KEYWORDS.items():
        for pattern in patterns:
            if re.search(pattern, text):
                return dept
    return DEFAULT_DEPARTMENT

# --- Models ---

class WorkOrderCreate(BaseModel):
    guest_id: str
    room_number: Optional[str]
    message: str = Field(..., min_length=1, max_length=1000)
    priority: Optional[PriorityEnum] = PriorityEnum.MEDIUM

class WorkOrderStatusUpdate(BaseModel):
    status: StatusEnum

# --- MongoDB TTL Index Setup ---

async def ensure_ttl_index():
    async with DatabaseConnection.get_connection() as conn:
        if conn is None:
            logger.error("database_connection_failed", error="No connection returned from get_connection()")
            return
        await conn["virtualbutler"]["work_orders"].create_index(
            [("updated_at", 1)],
            expireAfterSeconds=WORKORDER_TTL_DAYS * 24 * 3600,
            name="ttl_updated_at"
        )

# --- Event Publishing (Mocked for Local) ---

async def publish_status_event(work_order: dict):
    # TODO: Integrate with Azure Service Bus or Event Grid
    logger.info("status_event_published", work_order_id=work_order.get("work_order_id"), status=work_order.get("status"))

async def notify_status_change(work_order: dict):
    # TODO: Integrate with Notification Service (HTTP/gRPC/Webhook)
    logger.info("notification_sent", guest_id=work_order.get("guest_id"), status=work_order.get("status"))

# --- Audit Logging ---

async def log_status_change(work_order_id: str, old_status: str, new_status: str, actor: str):
    async with DatabaseConnection.get_connection() as conn:
        await conn["virtualbutler"]["work_order_logs"].insert_one({
            "work_order_id": work_order_id,
            "old_status": old_status,
            "new_status": new_status,
            "changed_by": actor,
            "timestamp": datetime.now(timezone.utc)
        })

# --- API Endpoints ---

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    await DatabaseConnection.connect()
    await ensure_ttl_index()
    task = asyncio.create_task(mock_service_bus_consumer())
    yield
    task.cancel()
    await DatabaseConnection.close()

async def create_work_order(
    data: WorkOrderCreate,
):
    try:
        department = route_department(data.message)
        now = datetime.now(datetime.timezone.utc)
        work_order = WorkOrder(
            request_id=f"req_{now.timestamp()}",
            work_order_id=f"wo_{now.timestamp()}",
            guest_id=data.guest_id,
            department=department,
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
        await publish_status_event(work_order.model_dump())
        await notify_status_change(work_order.model_dump())
        logger.info("work_order_created", work_order_id=work_order.work_order_id, guest_id=work_order.guest_id)
        return work_order
    except Exception as e:
        logger.error("work_order_creation_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to create work order")
    except Exception as e:
        logger.error("work_order_creation_failed", error=str(e))
async def get_work_order(
    work_order_id: str,
):
    try:
        async with DatabaseConnection.get_connection() as conn:
            doc = await conn["virtualbutler"]["work_orders"].find_one({"work_order_id": work_order_id})
            if not doc:
                raise HTTPException(status_code=404, detail="Work order not found")
            return WorkOrder(**doc)
    except Exception as e:
        logger.error("get_work_order_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch work order")
    except Exception as e:
        logger.error("get_work_order_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch work order")
async def update_work_order_status(
    work_order_id: str,
    update: WorkOrderStatusUpdate = Body(...),
    user=Depends(require_staff_role)
):
    try:
        async with DatabaseConnection.get_connection() as conn:
            doc = await conn["virtualbutler"]["work_orders"].find_one({"work_order_id": work_order_id})
            if not doc:
                raise HTTPException(status_code=404, detail="Work order not found")
            old_status = doc["status"]
            new_status = update.status
            result = await conn["virtualbutler"]["work_orders"].find_one_and_update(
                {"work_order_id": work_order_id},
                {"$set": {"status": new_status, "updated_at": datetime.now(datetime.timezone.utc)}},
                return_document=True
            )
            await log_status_change(work_order_id, old_status, new_status, user.get("sub", "unknown"))
            await publish_status_event(result)
            await notify_status_change(result)
            logger.info("work_order_status_updated", work_order_id=work_order_id, status=new_status)
            return WorkOrder(**result)
    except Exception as e:
        logger.error("update_work_order_status_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to update work order status")
        logger.error("update_work_order_status_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to update work order status")

@app.get("/healthz")
async def health_check():
    return await DatabaseConnection.health_check()

@app.get("/readiness")
async def readiness_check():
    return {"status": "ready"}

# --- Background Consumer Example (Mocked) ---

async def mock_service_bus_consumer():
    while True:
        # Simulate consuming a message and creating a work order
       await asyncio.sleep(60)
       logger.info("mock_service_bus_message_consumed")