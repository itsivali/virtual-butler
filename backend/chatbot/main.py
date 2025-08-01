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

# --- Security Best Practices ---
# 1. Use HTTPS in production (enforce via proxy or ASGI middleware)
# 2. Use strong JWT secrets and rotate regularly
# 3. Validate and sanitize all user input (Pydantic models, regex, etc.)
# 4. Use rate limiting (already implemented)
# 5. Use CORS with allowlist in production
# 6. Use HTTPOnly and Secure cookies for session tokens if using cookies
# 7. Log sensitive actions and errors securely (already implemented)
# 8. Principle of least privilege for database/service credentials
# 9. Do not expose stack traces or internal errors to clients
# 10. Use environment variables for all secrets and keys
# 11. Use dependency injection for authentication and authorization (already implemented)
# 12. Use role-based access control for all endpoints (already implemented)
# 13. Use up-to-date dependencies and monitor for vulnerabilities
# 14. Use secure password hashing (bcrypt, already implemented)
# 15. Use API gateway or firewall for additional protection in production
# 16. Use Azure Managed Identities and Key Vault for production secrets
# 17. Use logging and monitoring for all API access and errors
# 18. Use input/output escaping for all user-facing data
# 19. Use Content Security Policy (CSP) headers for frontend
# 20. Use automated security testing in CI/CD
#

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
    if JWT_SECRET is None:
        logger.error("jwt_secret_missing", error="JWT_SECRET environment variable is not set")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT secret is not configured"
        )
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
    """
    Uses Azure LUIS (Language Understanding) to extract the top intent from a message.
    Requires the following environment variables:
      - AZURE_LUIS_ENDPOINT: e.g. https://<your-resource-name>.cognitiveservices.azure.com
      - AZURE_LUIS_KEY: your LUIS authoring or prediction key
      - AZURE_LUIS_APP_ID: your LUIS app ID (GUID)
      - AZURE_LUIS_SLOT: slot name, usually 'production' (optional)
    """
    if not AZURE_LUIS_ENDPOINT or not AZURE_LUIS_KEY:
        logger.warning("LUIS not configured, falling back to keyword intent.")
        return classify_intent(message)
    luis_app_id = os.getenv("AZURE_LUIS_APP_ID")
    luis_slot = os.getenv("AZURE_LUIS_SLOT", "production")
    if not luis_app_id:
        logger.error("luis_app_id_missing", error="AZURE_LUIS_APP_ID environment variable is not set")
        return classify_intent(message)
    luis_url = f"{AZURE_LUIS_ENDPOINT}/luis/prediction/v3.0/apps/{luis_app_id}/slots/{luis_slot}/predict"
    params = {
        "subscription-key": AZURE_LUIS_KEY,
        "query": message,
        "verbose": True,
        "show-all-intents": True,
        "log": False
    }
    try:
        async with httpx.AsyncClient() as client:
            luis_response = await client.get(luis_url, params=params)
            if luis_response.status_code != 200:
                logger.error("luis_api_failed", status=luis_response.status_code, body=luis_response.text)
                return classify_intent(message)
            luis_data = luis_response.json()
            # Example LUIS response structure:
            # {
            #   "query": "I need towels",
            #   "prediction": {
            #     "topIntent": "Housekeeping",
            #     "intents": { ... },
            #     ...
            #   }
            # }
            prediction = luis_data.get("prediction", {})
            top_intent = prediction.get("topIntent", "").lower()
            logger.info("luis_prediction", top_intent=top_intent, all_intents=prediction.get("intents"))
            # Map LUIS intents to DepartmentEnum
            intent_map = {
                "housekeeping": DepartmentEnum.HOUSEKEEPING,
                "maintenance": DepartmentEnum.MAINTENANCE,
                "roomservice": DepartmentEnum.ROOM_SERVICE,
                "room_service": DepartmentEnum.ROOM_SERVICE,
                "it": DepartmentEnum.IT,
                "frontdesk": DepartmentEnum.FRONT_DESK,
                "front_desk": DepartmentEnum.FRONT_DESK,
                "security": DepartmentEnum.SECURITY,
                "concierge": DepartmentEnum.CONCIERGE,
            }
            for key, value in intent_map.items():
                if key in top_intent.replace(" ", "").replace("_", "").lower():
                    return value
        # fallback
        return classify_intent(message)
    except Exception as e:
        logger.error("luis_intent_failed", error=str(e))
        return classify_intent(message)

# --- Conversational Language Understanding (CLU) Intent Classification ---
from typing import Optional

async def classify_intent_clu(message: str, conversation_id: Optional[str] = None, user_id: Optional[str] = None) -> Optional[DepartmentEnum]:
    """
    Uses Azure Conversational Language Understanding (CLU) to extract the top intent from a message.
    Requires the following environment variables:
      - AZURE_CLU_ENDPOINT: e.g. https://<your-resource-name>.cognitiveservices.azure.com
      - AZURE_CLU_KEY: your CLU key
      - AZURE_CLU_PROJECT: your CLU project name
      - AZURE_CLU_DEPLOYMENT: your CLU deployment name
    """
    import httpx
    AZURE_CLU_ENDPOINT = os.getenv("AZURE_CLU_ENDPOINT")
    AZURE_CLU_KEY = os.getenv("AZURE_CLU_KEY")
    AZURE_CLU_PROJECT = os.getenv("AZURE_CLU_PROJECT")
    AZURE_CLU_DEPLOYMENT = os.getenv("AZURE_CLU_DEPLOYMENT")
    if not (AZURE_CLU_ENDPOINT and AZURE_CLU_KEY and AZURE_CLU_PROJECT and AZURE_CLU_DEPLOYMENT):
        logger.warning("CLU not configured, falling back to keyword intent.")
        return classify_intent(message)
    url = f"{AZURE_CLU_ENDPOINT}/language/:analyze-conversations?api-version=2023-04-01"
    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_CLU_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "kind": "Conversation",
        "analysisInput": {
            "conversationItem": {
                "id": conversation_id or str(uuid.uuid4()),
                "participantId": user_id or "user",
                "modality": "text",
                "language": "en",
                "text": message
            }
        },
        "parameters": {
            "projectName": AZURE_CLU_PROJECT,
            "deploymentName": AZURE_CLU_DEPLOYMENT,
            "verbose": True
        }
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code != 200:
                logger.error("clu_api_failed", status=response.status_code, body=response.text)
                return classify_intent(message)
            data = response.json()
            # Example CLU response structure:
            # {
            #   "result": {
            #     "prediction": {
            #       "topIntent": "Housekeeping",
            #       "intents": { ... },
            #       ...
            #     }
            #   }
            # }
            prediction = data.get("result", {}).get("prediction", {})
            top_intent = prediction.get("topIntent", "").lower()
            logger.info("clu_prediction", top_intent=top_intent, all_intents=prediction.get("intents"))
            # Map CLU intents to DepartmentEnum
            intent_map = {
                "housekeeping": DepartmentEnum.HOUSEKEEPING,
                "maintenance": DepartmentEnum.MAINTENANCE,
                "roomservice": DepartmentEnum.ROOM_SERVICE,
                "room_service": DepartmentEnum.ROOM_SERVICE,
                "it": DepartmentEnum.IT,
                "frontdesk": DepartmentEnum.FRONT_DESK,
                "front_desk": DepartmentEnum.FRONT_DESK,
                "security": DepartmentEnum.SECURITY,
                "concierge": DepartmentEnum.CONCIERGE,
                "human_assistant": DepartmentEnum.CONCIERGE,  # Example for human handoff intent
            }
            for key, value in intent_map.items():
                if key in top_intent.replace(" ", "").replace("_", "").lower():
                    return value
        return classify_intent(message)
    except Exception as e:
        logger.error("clu_intent_failed", error=str(e))
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

from fastapi import Path

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


# --- Multi-turn Conversation Context ---
@app.get("/api/v1/chat/history", response_model=List[ChatRequest], tags=["Chat"])
async def get_chat_history(user=Depends(verify_jwt)):
    guest_id = user["sub"]
    return await get_chat_history_for_guest(guest_id)

# New: Admin/staff can view any guest's chat history
@app.get("/api/v1/chat/history/{guest_id}", response_model=List[ChatRequest], tags=["Chat"])
async def get_chat_history_for_guest_id(guest_id: str = Path(...), user=Depends(verify_jwt)):
    # Only allow staff/admin
    if user.get("role") not in ("staff", "admin"):
        raise HTTPException(status_code=403, detail="Insufficient privileges")
    return await get_chat_history_for_guest(guest_id)

async def get_chat_history_for_guest(guest_id: str):
    try:
        chats = []
        async with DatabaseConnection.get_connection() as conn:
            if conn is None or not hasattr(conn, "virtualbutler"):
                logger.error("db_connection_failed", error="Database connection is None or missing 'virtualbutler' attribute")
                raise HTTPException(status_code=500, detail="Database connection error")
            cursor = conn.virtualbutler.chat_requests.find({"guest_id": guest_id}).sort("created_at", 1)
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
import json

def load_translations():
    translations = {}
    i18n_dir = os.path.join(os.path.dirname(__file__), "i18n")
    allowed_langs = {"en", "fr", "es", "zh"}
    for lang_code in allowed_langs:
        # Only allow known language codes to prevent path traversal
        path = os.path.join(i18n_dir, f"{lang_code}.json")
        try:
            with open(path, encoding="utf-8") as f:
                translations[lang_code] = json.load(f)
        except Exception as e:
            logger.error("translation_load_failed", lang=lang_code, error=str(e))
            translations[lang_code] = {}
    return translations

TRANSLATIONS = load_translations()

@app.get("/api/v1/chat/i18n/{lang}", tags=["i18n"])
async def get_translations(lang: str):
    """
    Returns translation dictionary for supported languages.
    Supported: en (English), fr (French), es (Spanish), zh (Mandarin Chinese)
    """
    allowed_langs = {"en", "fr", "es", "zh"}
    lang = lang.lower()
    if lang not in allowed_langs or not TRANSLATIONS.get(lang):
        return JSONResponse(
            status_code=404,
            content={
                "detail": f"Language '{lang}' not supported or translation file missing.",
                "supported_languages": [k for k, v in TRANSLATIONS.items() if v]
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


# --- Multi-turn Chat: Store and retrieve context ---
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

            # Retrieve last context for this guest (if any)
            last_context = await conn.virtualbutler.chat_contexts.find_one({"guest_id": guest_id})

        session_id = request.headers.get("X-Session-Id", str(uuid.uuid4()))
        msg_text = message.text or message.voice_transcript or ""
        if not msg_text.strip():
            raise HTTPException(status_code=400, detail="Message text required.")

        # Use Azure CLU for intent classification
        department = await classify_intent_clu(msg_text, conversation_id=session_id, user_id=guest_id)
        if not department:
            department = DepartmentEnum.FRONT_DESK

        # Build/extend context
        context_history = last_context["history"] if last_context and "history" in last_context else []
        context_history.append({
            "message": msg_text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "department": str(department)
        })
        context_obj = {
            "guest_id": guest_id,
            "session_id": session_id,
            "last_intent": str(department),
            "last_department": str(department),
            "history": context_history,
            "updated_at": datetime.now(timezone.utc)
        }

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
                "guest_name": guest_profile.name if guest_profile else None,
                "context": context_obj
            },
            sentiment=None
        )

        async with DatabaseConnection.get_connection() as conn:
            if conn is None or not hasattr(conn, "virtualbutler"):
                logger.error("db_connection_failed", error="Database connection is None or missing 'virtualbutler' attribute")
                raise HTTPException(status_code=500, detail="Database connection error")
            await conn.virtualbutler.chat_requests.insert_one(chat_request.dict(by_alias=True))
            # Upsert context for guest
            await conn.virtualbutler.chat_contexts.update_one(
                {"guest_id": guest_id},
                {"$set": context_obj},
                upsert=True
            )
            await publish_to_service_bus(chat_request.dict())
            await audit_log("chat_created", chat_request.dict())
            logger.info("chat_created", request_id=chat_request.request_id, guest_id=guest_id)
            return chat_request
    except Exception as e:
        logger.error("chat_creation_failed", error=str(e))
        await audit_log("chat_creation_failed", {"error": str(e), "guest_id": guest_id, "message": message.dict()})
        raise HTTPException(status_code=500, detail="Failed to create chat request")