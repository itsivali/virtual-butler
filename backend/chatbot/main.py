from fastapi import FastAPI, HTTPException, Depends, status, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from shared.db.database import DatabaseConnection
from shared.db.models import ChatRequest, StatusEnum, DepartmentEnum
from pydantic import BaseModel
import structlog
from jose import jwt
from jose.exceptions import JWTError
import os
from typing import List, Optional
from datetime import datetime
from pymongo import ReturnDocument

logger = structlog.get_logger()
app = FastAPI(
    title="Virtual Butler Chatbot API",
    description="API for guest chat requests in the Virtual Butler system.",
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

def verify_jwt(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as e:
        logger.error("jwt_verification_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

class ChatRequestUpdate(BaseModel):
    message: Optional[str] = None
    status: Optional[StatusEnum] = None
    department: Optional[DepartmentEnum] = None
    tags: Optional[List[str]] = None
    updated_at: Optional[datetime] = None

@app.on_event("startup")
async def startup_db_client():
    await DatabaseConnection.connect()

@app.on_event("shutdown")
async def shutdown_db_client():
    await DatabaseConnection.close()

@app.get("/", tags=["Root"])
async def root():
    return {"message": "Welcome to the Virtual Butler Chatbot API."}

@app.post("/api/v1/chat", response_model=ChatRequest, status_code=201, tags=["Chat"])
async def create_chat_request(
    chat_request: ChatRequest,
    user=Depends(verify_jwt)
):
    try:
        chat_request.request_id = f"req_{datetime.utcnow().timestamp()}"
        chat_request.status = StatusEnum.PENDING
        chat_request.created_at = datetime.utcnow()
        chat_request.updated_at = datetime.utcnow()
        if not chat_request.department:
            text = chat_request.message.lower()
            if "towel" in text or "clean" in text:
                chat_request.department = DepartmentEnum.HOUSEKEEPING
            elif "food" in text or "order" in text:
                chat_request.department = DepartmentEnum.ROOM_SERVICE
            elif "checkout" in text:
                chat_request.department = DepartmentEnum.FRONT_DESK
            elif "wifi" in text:
                chat_request.department = DepartmentEnum.IT
            else:
                chat_request.department = DepartmentEnum.CONCIERGE

        async with DatabaseConnection.get_connection() as conn:
            if conn is None:
                logger.error("db_connection_failed", error="Database connection is None")
                raise HTTPException(status_code=500, detail="Database connection failed")
            result = await conn.virtualbutler.chat_requests.insert_one(
                chat_request.dict(by_alias=True)
            )
            chat_request.id = result.inserted_id
            logger.info("chat_created", request_id=chat_request.request_id, guest_id=chat_request.guest_id)
            return chat_request
    except Exception as e:
        logger.error("chat_creation_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to create chat request")

@app.get("/api/v1/chat/{request_id}", response_model=ChatRequest, tags=["Chat"])
async def get_chat_request(
    request_id: str,
    user=Depends(verify_jwt)
):
    try:
        async with DatabaseConnection.get_connection() as conn:
            if conn is None:
                logger.error("db_connection_failed", error="Database connection is None")
                raise HTTPException(status_code=500, detail="Database connection failed")
            doc = await conn.virtualbutler.chat_requests.find_one({"request_id": request_id})
            if not doc:
                raise HTTPException(status_code=404, detail="Chat request not found")
            return ChatRequest(**doc)
    except Exception as e:
        logger.error("get_chat_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch chat request")

@app.get("/api/v1/chat/guest/{guest_id}", response_model=List[ChatRequest], tags=["Chat"])
async def get_guest_chats(
    guest_id: str,
    user=Depends(verify_jwt)
):
    try:
        chats = []
        async with DatabaseConnection.get_connection() as conn:
            if conn is None:
                logger.error("db_connection_failed", error="Database connection is None")
                raise HTTPException(status_code=500, detail="Database connection failed")
            cursor = conn.virtualbutler.chat_requests.find({"guest_id": guest_id})
            async for doc in cursor:
                chats.append(ChatRequest(**doc))
        return chats
    except Exception as e:
        logger.error("get_guest_chats_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch guest chats")

@app.patch("/api/v1/chat/{request_id}", response_model=ChatRequest, tags=["Chat"])
async def update_chat_request(
    request_id: str,
    update: ChatRequestUpdate = Body(...),
    user=Depends(verify_jwt)
):
    try:
        update_data = {k: v for k, v in update.dict(exclude_unset=True).items()}
        update_data["updated_at"] = datetime.utcnow()
        async with DatabaseConnection.get_connection() as conn:
            if conn is None:
                logger.error("db_connection_failed", error="Database connection is None")
                raise HTTPException(status_code=500, detail="Database connection failed")
            result = await conn.virtualbutler.chat_requests.find_one_and_update(
                {"request_id": request_id},
                {"$set": update_data},
                return_document=ReturnDocument.AFTER
            )
            if not result:
                raise HTTPException(status_code=404, detail="Chat request not found")
            logger.info("chat_updated", request_id=request_id)
            return ChatRequest(**result)
    except Exception as e:
        logger.error("update_chat_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to update chat request")

@app.delete("/api/v1/chat/{request_id}", status_code=204, tags=["Chat"])
async def delete_chat_request(
    request_id: str,
    user=Depends(verify_jwt)
):
    try:
        async with DatabaseConnection.get_connection() as conn:
            if conn is None:
                logger.error("db_connection_failed", error="Database connection is None")
                raise HTTPException(status_code=500, detail="Database connection failed")
            result = await conn.virtualbutler.chat_requests.delete_one({"request_id": request_id})
            if result.deleted_count == 0:
                raise HTTPException(status_code=404, detail="Chat request not found")
    except Exception as e:
        logger.error("delete_chat_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to delete chat request")