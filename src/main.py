import os
import json
from datetime import datetime
import logging
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Configure logging FIRST
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create FastAPI app BEFORE importing other modules
app = FastAPI(
    title="Vera Message Engine",
    version="1.0.0",
    description="AI-powered merchant engagement bot for magicpin"
)

# NOW import after app is created
from src.composer import MessageComposer
from src.context_manager import ContextManager

# Initialize global state
context_manager = ContextManager()
composer = MessageComposer()

# Request/Response Models
class ContextPayload(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: str

class TickRequest(BaseModel):
    merchant_id: str
    timestamp: str
    actions_remaining: int

class ReplyRequest(BaseModel):
    merchant_id: str
    customer_id: Optional[str] = None
    message_id: str
    reply_text: str

# Endpoints
@app.get("/v1/healthz")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0"
    }

@app.get("/v1/metadata")
async def metadata():
    """Return bot metadata"""
    return {
        "name": "Vera Message Engine",
        "version": "1.0.0",
        "description": "AI-powered merchant engagement bot for magicpin",
        "capabilities": [
            "message_composition",
            "context_awareness",
            "merchant_personalization",
            "trigger_based_engagement"
        ],
        "max_payload_kb": 500,
        "max_timeout_seconds": 30,
        "max_actions_per_tick": 20
    }

@app.post("/v1/context")
async def receive_context(payload: ContextPayload):
    """
    Receive and store merchant/customer/trigger context.
    Idempotent by scope + version.
    """
    try:
        # Store context atomically
        ack_id = context_manager.store_context(
            scope=payload.scope,
            context_id=payload.context_id,
            version=payload.version,
            data=payload.payload
        )
        
        return {
            "accepted": True,
            "ack_id": ack_id,
            "stored_at": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Context storage error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/v1/tick")
async def tick(request: TickRequest):
    """
    Called every 5 minutes during test window.
    Compose up to `actions_remaining` messages.
    """
    try:
        merchant_id = request.merchant_id
        
        # Fetch current merchant context
        merchant_context = context_manager.get_context("merchant", merchant_id)
        if not merchant_context:
            logger.warning(f"No merchant context found for {merchant_id}")
            return {"actions": [], "rationale": "No merchant context found"}
        
        # Build full context for composition
        full_context = {
            "merchant": merchant_context,
            "category": merchant_context.get("category", "unknown"),
            "triggers": merchant_context.get("active_triggers", []),
            "customer_data": None
        }
        
        # Compose message
        message_output = composer.compose(
            merchant_id=merchant_id,
            context=full_context,
            actions_remaining=request.actions_remaining
        )
        
        return {
            "actions": [message_output] if message_output else [],
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Tick error: {str(e)}", exc_info=True)
        return {"actions": [], "error": str(e)}

@app.post("/v1/reply")
async def handle_reply(request: ReplyRequest):
    """
    Handle customer reply to bot message.
    Update conversation state and learn from feedback.
    """
    try:
        # Store reply and update merchant state
        context_manager.record_reply(
            merchant_id=request.merchant_id,
            customer_id=request.customer_id,
            message_id=request.message_id,
            reply_text=request.reply_text,
            timestamp=datetime.utcnow().isoformat()
        )
        
        return {
            "acknowledged": True,
            "processed_at": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Reply handling error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@app.on_event("startup")
async def startup():
    """Load dataset and category guidelines on startup"""
    logger.info("🚀 Starting Vera Message Engine...")
    
    # Load category guidelines
    logger.info("Loading category guidelines...")
    composer.load_category_guidelines()
    
    # Load dataset
    logger.info("Loading expanded dataset...")
    context_manager.load_dataset("dataset")
    
    logger.info("✓ Ready to receive context and compose messages")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)