from fastapi import APIRouter, Depends, HTTPException, status, Body
from typing import List
from datetime import datetime
from jose import JWTError
from shared.db.database import DatabaseConnection
from shared.db.models import WorkOrder, WorkOrderStatusUpdate, WorkOrderCreate, User, UserLogin, UserCreate
from shared.security.auth import (
    authenticate_user,
    create_access_token,
    create_refresh_token,
    get_current_user,
    get_refresh_token_user,
    hash_password,
    verify_email_token,
    generate_email_token
)

router = APIRouter(prefix="/api", tags=["API"])

@router.post("/auth/register", response_model=User, status_code=201)
async def register_user(user: UserCreate):
    async with DatabaseConnection.get_connection() as conn:
        if await conn["users"].find_one({"email": user.email}):
            raise HTTPException(status_code=409, detail="Email already registered")
        user_dict = user.dict()
        user_dict["hashed_password"] = hash_password(user.password)
        del user_dict["password"]
        user_dict["email_verified"] = False
        result = await conn["users"].insert_one(user_dict)
        token = generate_email_token(user.email)
        # TODO: Send email with verification link using token
        return User(**user_dict, id=result.inserted_id)

@router.get("/auth/verify-email")
async def verify_email(token: str):
    email = verify_email_token(token)
    if not email:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")
    async with DatabaseConnection.get_connection() as conn:
        result = await conn["users"].update_one({"email": email}, {"$set": {"email_verified": True}})
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="User not found or already verified")
    return {"detail": "Email verified successfully"}

@router.post("/auth/login")
async def login_user(user: UserLogin):
    db_user = await authenticate_user(user.email, user.password)
    if not db_user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not db_user.email_verified:
        raise HTTPException(status_code=403, detail="Email not verified")
    return {
        "access_token": create_access_token({"sub": str(db_user.id)}),
        "refresh_token": create_refresh_token({"sub": str(db_user.id)}),
        "token_type": "bearer"
    }

@router.post("/auth/refresh")
async def refresh_access_token(refresh_token: str = Body(...)):
    user = await get_refresh_token_user(refresh_token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    return {
        "access_token": create_access_token({"sub": str(user.id)}),
        "token_type": "bearer"
    }

@router.post("/work-orders", response_model=WorkOrder)
async def create_work_order(data: WorkOrderCreate, user: User = Depends(get_current_user)):
    now = datetime.utcnow()
    work_order = WorkOrder(
        request_id=f"req_{now.timestamp()}",
        work_order_id=f"wo_{now.timestamp()}",
        guest_id=data.guest_id,
        department=data.department,
        description=data.message,
        priority=data.priority,
        status="pending",
        created_at=now,
        updated_at=now,
        metadata={"created_by": str(user.id)}
    )
    async with DatabaseConnection.get_connection() as conn:
        await conn["work_orders"].insert_one(work_order.dict(by_alias=True))
    return work_order

@router.get("/work-orders", response_model=List[WorkOrder])
async def list_work_orders(user: User = Depends(get_current_user)):
    async with DatabaseConnection.get_connection() as conn:
        cursor = conn["work_orders"].find({"guest_id": user.email})
        return [WorkOrder(**doc) async for doc in cursor]

@router.get("/work-orders/{work_order_id}", response_model=WorkOrder)
async def get_work_order(work_order_id: str, user: User = Depends(get_current_user)):
    async with DatabaseConnection.get_connection() as conn:
        doc = await conn["work_orders"].find_one({"work_order_id": work_order_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Work order not found")
        return WorkOrder(**doc)

@router.patch("/work-orders/{work_order_id}", response_model=WorkOrder)
async def update_work_order_status(
    work_order_id: str,
    update: WorkOrderStatusUpdate = Body(...),
    user: User = Depends(get_current_user)
):
    async with DatabaseConnection.get_connection() as conn:
        doc = await conn["work_orders"].find_one({"work_order_id": work_order_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Work order not found")
        await conn["work_orders"].update_one({"work_order_id": work_order_id}, {"$set": {"status": update.status}})
        updated = await conn["work_orders"].find_one({"work_order_id": work_order_id})
        return WorkOrder(**updated)

@router.delete("/work-orders/{work_order_id}", status_code=204)
async def delete_work_order(work_order_id: str, user: User = Depends(get_current_user)):
    async with DatabaseConnection.get_connection() as conn:
        result = await conn["work_orders"].delete_one({"work_order_id": work_order_id})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Work order not found")
