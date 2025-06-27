from fastapi import FastAPI, HTTPException, Depends, status, Request, Body, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import structlog
import os
import re
import asyncio
from jose import jwt
from jose.exceptions import JWTError
from pymongo import ReturnDocument
from shared.db.database import DatabaseConnection
from shared.db.models import ChatRequest, StatusEnum, DepartmentEnum, GuestProfile
import uuid
from passlib.context import CryptContext

logger = structlog.get_logger()
app = FastAPI(
    title="Virtual Butler Chatbot API",
    description="Conversational guest service chatbot with multi-modal input, smart routing, and secure personalized workflows.",
    version="2.0.0"
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

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

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

RATE_LIMIT = 10 
rate_limit_cache: Dict[str, List[datetime]] = {}

def rate_limit(guest_id: str):
    now = datetime.utcnow()
    window = [t for t in rate_limit_cache.get(guest_id, []) if (now - t).seconds < 60]
    if len(window) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please wait.")
    window.append(now)
    rate_limit_cache[guest_id] = window

def classify_intent(message: str) -> Optional[DepartmentEnum]:
    # TODO: Integrate Azure LUIS
    # Fallback: keyword matching
    text = message.lower()
    if re.search(r"towel|clean|linen|sheet|pillow|blanket", text):
        return DepartmentEnum.HOUSEKEEPING
    if re.search(r"ac|air.?condition|fix|repair|leak|broken|light|bulb|plumbing", text):
        return DepartmentEnum.MAINTENANCE
    if re.search(r"food|order|menu|breakfast|dinner|lunch|drink|water|coffee", text):
        return DepartmentEnum.ROOM_SERVICE
    if re.search(r"wifi|internet|tv|remote|network|connect", text):
        return DepartmentEnum.IT
    if re.search(r"checkout|check.?out|late|early|bill|invoice|key|card", text):
        return DepartmentEnum.FRONT_DESK
    if re.search(r"safe|security|lost|theft|emergency|alarm", text):
        return DepartmentEnum.SECURITY
    if re.search(r"taxi|tour|spa|reservation|booking|recommend|restaurant", text):
        return DepartmentEnum.CONCIERGE
    return None

class ChatMessage(BaseModel):
    text: Optional[str] = None
    voice_transcript: Optional[str] = None
    images: Optional[List[str]] = None  
    quick_reply: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class ChatSessionContext(BaseModel):
    guest_id: str
    session_id: str
    last_intent: Optional[str] = None
    last_department: Optional[DepartmentEnum] = None
    history: List[Dict[str, Any]] = Field(default_factory=list)
    preferences: Dict[str, Any] = Field(default_factory=dict)

class AuthRequest(BaseModel):
    room_number: str
    pin: str

class AuthResponse(BaseModel):
    token: str
    guest_id: str

class MenuItem(BaseModel):
    item_id: str
    name: str
    description: Optional[str] = None
    price: float
    category: str
    image_url: Optional[str] = None
    available: bool = True

class FoodOrderRequest(BaseModel):
    items: List[Dict[str, Any]]  # e.g. [{"item_id": "burger1", "quantity": 2, "notes": "No onions"}]
    special_instructions: Optional[str] = None

# --- Authentication Endpoint with PIN and Room Lookup ---

@app.post("/auth", response_model=AuthResponse, tags=["Auth"])
async def authenticate_guest(auth: AuthRequest):
    async with DatabaseConnection.get_connection() as conn:
        if conn is None or not hasattr(conn, "virtualbutler"):
            logger.error("db_connection_failed", error="Database connection is None or missing 'virtualbutler' attribute")
            raise HTTPException(status_code=500, detail="Database connection error")
        guest_doc = await conn.virtualbutler.guest_profiles.find_one({"room_number": auth.room_number})
        if not guest_doc:
            logger.warning("auth_failed", reason="Room not found", room_number=auth.room_number)
            raise HTTPException(status_code=401, detail="Invalid room number or PIN")
        stored_pin_hash = guest_doc.get("pin")
        if not stored_pin_hash or not pwd_context.verify(auth.pin, stored_pin_hash):
            logger.warning("auth_failed", reason="Invalid PIN", room_number=auth.room_number)
            raise HTTPException(status_code=401, detail="Invalid room number or PIN")
        guest_id = guest_doc["guest_id"]
        payload = {"sub": guest_id, "room": auth.room_number, "role": "guest"}
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        return AuthResponse(token=token, guest_id=guest_id)

# --- Chat Endpoints ---

@app.post("/api/v1/chat", response_model=ChatRequest, status_code=201, tags=["Chat"])
async def create_chat_request(
    message: ChatMessage,
    request: Request,
    user=Depends(verify_jwt)
):
    guest_id = user["sub"]
    rate_limit(guest_id)
    try:
        # Fetch guest profile for room number and name
        async with DatabaseConnection.get_connection() as conn:
            if conn is None or not hasattr(conn, "virtualbutler"):
                logger.error("db_connection_failed", error="Database connection is None or missing 'virtualbutler' attribute")
                raise HTTPException(status_code=500, detail="Database connection error")
            guest_doc = await conn.virtualbutler.guest_profiles.find_one({"guest_id": guest_id})
            guest_profile = GuestProfile(**guest_doc) if guest_doc else None

        session_id = request.headers.get("X-Session-Id", str(uuid.uuid4()))
        context = ChatSessionContext(
            guest_id=guest_id,
            session_id=session_id
        )
        msg_text = message.text or message.voice_transcript or ""
        if not msg_text.strip():
            raise HTTPException(status_code=400, detail="Message text required.")

        department = classify_intent(msg_text)
        if not department:
            department = DepartmentEnum.FRONT_DESK

        chat_request = ChatRequest(
            request_id=f"req_{datetime.now(timezone.utc).timestamp()}",
            guest_id=guest_id,
            guest_profile=guest_profile,  # Attach profile
            message=msg_text,
            voice_transcript=message.voice_transcript,
            department=department,
            status=StatusEnum.PENDING,
            tags=[message.quick_reply] if message.quick_reply else [],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            metadata={
                "session_id": session_id,
                "images": message.images or [],
                "room_number": guest_profile.room_number if guest_profile else None,
                "guest_name": guest_profile.name if guest_profile else None
            },
            sentiment=None
        )

        async with DatabaseConnection.get_connection() as conn:
            if conn is None or not hasattr(conn, "virtualbutler"):
                logger.error("db_connection_failed", error="Database connection is None or missing 'virtualbutler' attribute")
                raise HTTPException(status_code=500, detail="Database connection error")
            await conn.virtualbutler.chat_requests.insert_one(chat_request.dict(by_alias=True))
            asyncio.create_task(publish_to_service_bus(chat_request.dict()))
            logger.info("chat_created", request_id=chat_request.request_id, guest_id=guest_id)
            return chat_request
    except Exception as e:
        logger.error("chat_creation_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to create chat request")

@app.get("/api/v1/chat/history", response_model=List[ChatRequest], tags=["Chat"])
async def get_chat_history(
    user=Depends(verify_jwt)
):
    guest_id = user["sub"]
    try:
        chats = []
        async with DatabaseConnection.get_connection() as conn:
            if conn is None or not hasattr(conn, "virtualbutler"):
                logger.error("db_connection_failed", error="Database connection is None or missing 'virtualbutler' attribute")
                raise HTTPException(status_code=500, detail="Database connection error")
            cursor = conn.virtualbutler.chat_requests.find({"guest_id": guest_id})
            async for doc in cursor:
                chats.append(ChatRequest(**doc))
        return chats
    except Exception as e:
        logger.error("get_chat_history_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch chat history")

# --- Service Bus Integration (Mocked) ---

async def publish_to_service_bus(message: dict):
    # TODO: Integrate Azure Service Bus SDK
    logger.info("published_to_service_bus", message=message)

# --- Notification Subscription (Mocked) ---

@app.get("/api/v1/chat/notifications", tags=["Chat"])
async def get_notifications(user=Depends(verify_jwt)):
    guest_id = user["sub"]
    # TODO: Subscribe to Notification Service (webhook)
    return {"notifications": []}

# --- Error Handling & Health Checks ---

@app.get("/healthz")
async def health_check():
    try:
        return await DatabaseConnection.health_check()
    except Exception as e:
        logger.error("health_check_failed", error=str(e))
        return {"status": "unhealthy", "error": str(e)}

@app.get("/readiness")
async def readiness_check():
    return {"status": "ready"}

# --- Audit Logging Example ---

async def audit_log(event: str, data: dict):
    # TODO: Write anonymized logs to a secure collection
    logger.info("audit_log", event=event, data=data)

# --- Plugin Architecture & Extensibility (Stub) ---

@app.post("/api/v1/chat/plugin/{plugin_name}", tags=["Plugins"])
async def plugin_handler(plugin_name: str, payload: Dict[str, Any], user=Depends(verify_jwt)):
    # TODO: Dynamically load and execute plugin logic
    logger.info("plugin_invoked", plugin=plugin_name, guest_id=user["sub"])
    return {"result": f"Plugin {plugin_name} executed."}

# --- Multi-language Support (Stub) ---

@app.get("/api/v1/chat/i18n/{lang}", tags=["i18n"])
async def get_translations(lang: str):
    # TODO: Integrate with translation service or serve static translations
    return {"lang": lang, "translations": {}}

# --- Fallback for Unhandled Errors ---

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", error=str(exc))
    return HTTPException(status_code=500, detail="An unexpected error occurred. Please try again later.")

@app.on_event("startup")
async def startup_db_client():
    await DatabaseConnection.connect()

@app.on_event("shutdown")
async def shutdown_db_client():
    await DatabaseConnection.close()

# --- Food/Beverage Order Endpoints ---

@app.post("/api/v1/order", tags=["Room Service"])
async def place_food_order(
    order: FoodOrderRequest,
    request: Request,
    user=Depends(verify_jwt)
):
    guest_id = user["sub"]
    rate_limit(guest_id)
    try:
        async with DatabaseConnection.get_connection() as conn:
            if conn is None or not hasattr(conn, "virtualbutler"):
                logger.error("db_connection_failed", error="Database connection is None or missing 'virtualbutler' attribute")
                raise HTTPException(status_code=500, detail="Database connection error")
            guest_doc = await conn.virtualbutler.guest_profiles.find_one({"guest_id": guest_id})
            guest_profile = GuestProfile(**guest_doc) if guest_doc else None

        session_id = request.headers.get("X-Session-Id", str(uuid.uuid4()))
        order_summary = ", ".join([f"{item['quantity']}x {item['item_id']}" for item in order.items])
        msg_text = f"Room service order: {order_summary}"
        if order.special_instructions:
            msg_text += f" | Instructions: {order.special_instructions}"

        chat_request = ChatRequest(
            request_id=f"req_{datetime.now(timezone.utc).timestamp()}",
            guest_id=guest_id,
            guest_profile=guest_profile,
            message=msg_text,
            department=DepartmentEnum.ROOM_SERVICE,
            status=StatusEnum.PENDING,
            tags=["room_service", "order"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            metadata={
                "session_id": session_id,
                "room_number": guest_profile.room_number if guest_profile else None,
                "guest_name": guest_profile.name if guest_profile else None,
                "order_items": order.items,
                "special_instructions": order.special_instructions
            },
            sentiment=None
        )

        async with DatabaseConnection.get_connection() as conn:
            if conn is None or not hasattr(conn, "virtualbutler"):
                logger.error("db_connection_failed", error="Database connection is None or missing 'virtualbutler' attribute")
                raise HTTPException(status_code=500, detail="Database connection error")
            await conn.virtualbutler.chat_requests.insert_one(chat_request.dict(by_alias=True))
            asyncio.create_task(publish_to_service_bus(chat_request.dict()))
            logger.info("food_order_created", request_id=chat_request.request_id, guest_id=guest_id)
            return {"status": "order_placed", "request_id": chat_request.request_id}
    except Exception as e:
        logger.error("food_order_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to place food order")

@app.get("/api/v1/order/history", tags=["Room Service"])
async def get_order_history(user=Depends(verify_jwt)):
    guest_id = user["sub"]
    try:
        orders = []
        async with DatabaseConnection.get_connection() as conn:
            if conn is None or not hasattr(conn, "virtualbutler"):
                logger.error("db_connection_failed", error="Database connection is None or missing 'virtualbutler' attribute")
                raise HTTPException(status_code=500, detail="Database connection error")
            cursor = conn.virtualbutler.chat_requests.find({
                "guest_id": guest_id,
                "department": DepartmentEnum.ROOM_SERVICE
            })
            async for doc in cursor:
                orders.append(ChatRequest(**doc))
        return orders
    except Exception as e:
        logger.error("get_order_history_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch order history")

@app.get("/api/v1/order/status/{request_id}", tags=["Room Service"])
async def get_order_status(request_id: str, user=Depends(verify_jwt)):
    guest_id = user["sub"]
    try:
        async with DatabaseConnection.get_connection() as conn:
            if conn is None or not hasattr(conn, "virtualbutler"):
                logger.error("db_connection_failed", error="Database connection is None or missing 'virtualbutler' attribute")
                raise HTTPException(status_code=500, detail="Database connection error")
            doc = await conn.virtualbutler.chat_requests.find_one({
                "request_id": request_id,
                "guest_id": guest_id,
                "department": DepartmentEnum.ROOM_SERVICE
            })
            if not doc:
                raise HTTPException(status_code=404, detail="Order not found")
            return {"request_id": request_id, "status": doc.get("status"), "updated_at": doc.get("updated_at")}
    except Exception as e:
        logger.error("get_order_status_failed", error=str(e))
        raise HTTPException(status_code=500, detail=