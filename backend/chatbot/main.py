from fastapi import FastAPI, HTTPException, Depends, status, Request
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
from azure.ai.textanalytics.aio import TextAnalyticsClient
from azure.core.credentials import AzureKeyCredential
from azure.servicebus.aio import ServiceBusClient, ServiceBusSender
from azure.servicebus import ServiceBusMessage
import importlib
import httpx
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

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
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
AZURE_LUIS_ENDPOINT = os.getenv("AZURE_LUIS_ENDPOINT")
AZURE_LUIS_KEY = os.getenv("AZURE_LUIS_KEY")
AZURE_SERVICE_BUS_CONN_STR = os.getenv("AZURE_SERVICE_BUS_CONN_STR")
AZURE_SERVICE_BUS_QUEUE = os.getenv("AZURE_SERVICE_BUS_QUEUE", "chat-requests")
NOTIFICATION_SERVICE_WEBHOOK = os.getenv("NOTIFICATION_SERVICE_WEBHOOK")

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

# --- Azure LUIS Intent Classification ---
async def classify_intent_azure_luis(message: str) -> Optional[DepartmentEnum]:
    if not AZURE_LUIS_ENDPOINT or not AZURE_LUIS_KEY:
        return classify_intent(message)  # fallback to keyword
    client = TextAnalyticsClient(endpoint=AZURE_LUIS_ENDPOINT, credential=AzureKeyCredential(AZURE_LUIS_KEY))
    try:
        response = await client.analyze_sentiment([message])
        # You would use LUIS prediction endpoint for intent, this is a placeholder for demo
        # Replace with actual LUIS intent extraction logic
        sentiment = response[0].sentiment
        if "food" in message.lower():
            return DepartmentEnum.ROOM_SERVICE
        # ...map LUIS intents to DepartmentEnum...
        return classify_intent(message)
    except Exception as e:
        logger.error("luis_intent_failed", error=str(e))
        return classify_intent(message)

# --- Azure Service Bus Integration ---
async def publish_to_service_bus(message: dict):
    if not AZURE_SERVICE_BUS_CONN_STR or not AZURE_SERVICE_BUS_QUEUE:
        logger.warning("service_bus_not_configured")
        return
    try:
        async with ServiceBusClient.from_connection_string(AZURE_SERVICE_BUS_CONN_STR) as sb_client:
            sender: ServiceBusSender = sb_client.get_queue_sender(queue_name=AZURE_SERVICE_BUS_QUEUE)
            async with sender:
                sb_message = ServiceBusMessage(str(message))
                await sender.send_messages(sb_message)
        logger.info("published_to_service_bus", message=message)
        # Notify notification service webhook
        await notify_webhook(message)
    except Exception as e:
        logger.error("service_bus_publish_failed", error=str(e))

# --- Notification Service Webhook Integration ---
async def notify_webhook(message: dict):
    if not NOTIFICATION_SERVICE_WEBHOOK:
        logger.warning("notification_webhook_not_configured")
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(NOTIFICATION_SERVICE_WEBHOOK, json=message)
        logger.info("notified_webhook", webhook=NOTIFICATION_SERVICE_WEBHOOK)
    except Exception as e:
        logger.error("webhook_notify_failed", error=str(e))

# --- Audit Logging: Write anonymized logs to secure collection ---
async def audit_log(event: str, data: dict):
    anonymized_data = {k: ("***" if "pin" in k or "token" in k else v) for k, v in data.items()}
    logger.info("audit_log", event=event, data=anonymized_data)
    try:
        async with DatabaseConnection.get_connection() as conn:
            if conn is not None and hasattr(conn, "virtualbutler"):
                await conn.virtualbutler.audit_logs.insert_one({
                    "event": event,
                    "data": anonymized_data,
                    "timestamp": datetime.utcnow()
                })
    except Exception as e:
        logger.error("audit_log_failed", error=str(e))

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
        if JWT_SECRET is None:
            logger.error("jwt_secret_missing", error="JWT_SECRET environment variable is not set")
            raise HTTPException(status_code=500, detail="JWT secret is not configured")
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        return AuthResponse(token=token, guest_id=guest_id)

@app.get("/api/v1/chat/history", response_model=List[ChatRequest], tags=["Chat"])
async def get_chat_history(user=Depends(verify_jwt)):
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

@app.get("/api/v1/chat/notifications", tags=["Chat"])
async def get_notifications(user=Depends(verify_jwt)):
    guest_id = user["sub"]
    try:
        notifications = []
        async with DatabaseConnection.get_connection() as conn:
            if conn is None or not hasattr(conn, "virtualbutler"):
                logger.error("db_connection_failed", error="Database connection is None or missing 'virtualbutler' attribute")
                raise HTTPException(status_code=500, detail="Database connection error")
            # Fetch notifications for the guest, sorted by most recent
            cursor = conn.virtualbutler.notifications.find(
                {"guest_id": guest_id}
            ).sort("created_at", -1)
            async for doc in cursor:
                # Remove sensitive/internal fields if any
                doc.pop("_id", None)
                notifications.append(doc)
        return {"notifications": notifications}
    except Exception as e:
        logger.error("get_notifications_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch notifications")

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

@app.post("/api/v1/chat/plugin/{plugin_name}", tags=["Plugins"])
async def plugin_handler(plugin_name: str, payload: Dict[str, Any], user=Depends(verify_jwt)):
    logger.info("plugin_invoked", plugin=plugin_name, guest_id=user["sub"])
    try:
        module = importlib.import_module(f"backend.plugins.{plugin_name}")
        if hasattr(module, "run_plugin"):
            result = await module.run_plugin(payload, user)
            return {"result": result}
        else:
            raise ImportError(f"Plugin '{plugin_name}' does not have a 'run_plugin' function")
    except Exception as e:
        logger.error("plugin_execution_failed", plugin=plugin_name, error=str(e))
        raise HTTPException(status_code=500, detail=f"Plugin execution failed: {str(e)}")

from fastapi.responses import JSONResponse

# Example static translations for demonstration
TRANSLATIONS = {
    "en": {
        "greeting": "Hello! How can I help you today?",
        "order_placed": "Your order has been placed.",
        "order_failed": "Failed to place your order.",
        "chat_created": "Your message has been received.",
        "rate_limit_exceeded": "Rate limit exceeded. Please wait.",
        "invalid_token": "Invalid or expired token.",
        "db_error": "Database connection error.",
        "not_found": "Resource not found.",
        "unexpected_error": "An unexpected error occurred. Please try again later."
    },
    "fr": {
        "greeting": "Bonjour ! Comment puis-je vous aider aujourd'hui ?",
        "order_placed": "Votre commande a été passée.",
        "order_failed": "Échec de la commande.",
        "chat_created": "Votre message a été reçu.",
        "rate_limit_exceeded": "Limite de requêtes dépassée. Veuillez patienter.",
        "invalid_token": "Jeton invalide ou expiré.",
        "db_error": "Erreur de connexion à la base de données.",
        "not_found": "Ressource non trouvée.",
        "unexpected_error": "Une erreur inattendue s'est produite. Veuillez réessayer plus tard."
    },
    "es": {
        "greeting": "¡Hola! ¿Cómo puedo ayudarte hoy?",
        "order_placed": "Tu pedido ha sido realizado.",
        "order_failed": "No se pudo realizar tu pedido.",
        "chat_created": "Tu mensaje ha sido recibido.",
        "rate_limit_exceeded": "Límite de solicitudes excedido. Por favor espera.",
        "invalid_token": "Token inválido o expirado.",
        "db_error": "Error de conexión a la base de datos.",
        "not_found": "Recurso no encontrado.",
        "unexpected_error": "Ocurrió un error inesperado. Por favor, inténtalo de nuevo más tarde."
    },
    "zh": {
        "greeting": "你好！我能为您做些什么？",
        "order_placed": "您的订单已下达。",
        "order_failed": "下单失败。",
        "chat_created": "您的消息已收到。",
        "rate_limit_exceeded": "请求过于频繁，请稍后再试。",
        "invalid_token": "令牌无效或已过期。",
        "db_error": "数据库连接错误。",
        "not_found": "未找到资源。",
        "unexpected_error": "发生了意外错误，请稍后再试。"
    }
}

@app.get("/api/v1/chat/i18n/{lang}", tags=["i18n"])
async def get_translations(lang: str):
    """
    Returns translation dictionary for supported languages.
    Supported: en (English), fr (French), es (Spanish), zh (Mandarin Chinese)
    """
    lang = lang.lower()
    if lang not in TRANSLATIONS:
        return JSONResponse(
            status_code=404,
            content={
                "detail": f"Language '{lang}' not supported.",
                "supported_languages": list(TRANSLATIONS.keys())
            }
        )
    return {"lang": lang, "translations": TRANSLATIONS[lang]}

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again later."}
    )

@app.on_event("startup")
async def startup_db_client():
    await DatabaseConnection.connect()

@app.on_event("shutdown")
async def shutdown_db_client():
    await DatabaseConnection.close()

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
        raise HTTPException(status_code=500, detail="Failed to fetch order status")

@app.post("/api/v1/chat", response_model=ChatRequest, status_code=201, tags=["Chat"])
async def create_chat_request(
    message: ChatMessage,
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
        msg_text = message.text or message.voice_transcript or ""
        if not msg_text.strip():
            raise HTTPException(status_code=400, detail="Message text required.")

        # Use Azure LUIS for intent classification
        department = await classify_intent_azure_luis(msg_text)
        if not department:
            department = DepartmentEnum.FRONT_DESK

        chat_request = ChatRequest(
            request_id=f"req_{datetime.now(timezone.utc).timestamp()}",
            guest_id=guest_id,
            guest_profile=guest_profile,
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
            await publish_to_service_bus(chat_request.dict())
            await audit_log("chat_created", chat_request.dict())
            logger.info("chat_created", request_id=chat_request.request_id, guest_id=guest_id)
            return chat_request
    except Exception as e:
        logger.error("chat_creation_failed", error=str(e))
        await audit_log("chat_creation_failed", {"error": str(e), "guest_id": guest_id, "message": message.dict()})
        raise HTTPException(status_code=500, detail="Failed to create chat request")        