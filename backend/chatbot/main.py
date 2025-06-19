from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from shared.db.database import DatabaseConnection
import structlog

logger = structlog.get_logger()
app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_db_client():
    await DatabaseConnection.connect()

@app.on_event("shutdown")
async def shutdown_db_client():
    await DatabaseConnection.close()

@app.post("/api/v1/chat")
async def create_chat_request(chat_request: ChatRequest):
    try:
        async with DatabaseConnection.get_connection() as conn:
            result = await conn.virtualbutler.chat_requests.insert_one(
                chat_request.dict(by_alias=True)
            )
            return {"id": str(result.inserted_id)}
    except DatabaseConnection.ConnectionError as e:
        logger.error("chat_creation_failed", error=str(e))
        raise HTTPException(status_code=503, detail="Database connection error")
    except DatabaseConnection.OperationError as e:
        logger.error("chat_operation_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Operation failed")

@app.get("/health")
async def health_check():
    return await DatabaseConnection.health_check()