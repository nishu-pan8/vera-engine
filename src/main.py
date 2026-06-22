import os
import json
from datetime import datetime, timezone
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
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0"
    }

@app.get("/v1/metadata")
async def metadata():
    """Return bot metadata"""
    return {
        "team_name": "Vera Engine",
        "model": "Groq Llama 3.1 70B",
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
        
        logger.info(f"Stored context: {payload.scope}/{payload.context_id} v{payload.version}")
        
        return {
            "accepted": True,
            "ack_id": ack_id,
            "stored_at": datetime.now(timezone.utc).isoformat()
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
            return {
                "actions": [],
                "rationale": "No merchant context found",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        
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
        
        if message_output:
            logger.info(f"Composed message for {merchant_id}: {message_output.get('message', '')[:50]}...")
            return {
                "actions": [message_output],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        else:
            logger.warning(f"No message composed for {merchant_id}")
            return {
                "actions": [],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    except Exception as e:
        logger.error(f"Tick error for {request.merchant_id}: {str(e)}", exc_info=True)
        return {
            "actions": [],
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

@app.post("/v1/reply")
async def handle_reply(request: ReplyRequest):
    """
    Handle customer reply to bot message.
    Update conversation state and learn from feedback.
    """
    try:
        reply_text = request.reply_text.lower()
        
        # Detect hostile messages
        hostile_words = ["spam", "stop", "useless", "unwanted", "hate", "block", "quit"]
        is_hostile = any(word in reply_text for word in hostile_words)
        
        # Detect auto-replies
        auto_reply_patterns = ["team will respond", "thank you for", "automatically generated", "out of office", "away"]
        is_auto_reply = any(pattern in reply_text for pattern in auto_reply_patterns)
        
        # Detect commitment/intent
        commitment_words = ["yes", "ok", "let's", "lets", "sure", "interested", "good", "perfect", "great"]
        has_commitment = any(word in reply_text for word in commitment_words)
        
        # Log reply
        logger.info(f"Reply from {request.customer_id}: hostile={is_hostile}, auto={is_auto_reply}, commit={has_commitment}")
        
        # Store reply
        context_manager.record_reply(
            merchant_id=request.merchant_id,
            customer_id=request.customer_id,
            message_id=request.message_id,
            reply_text=request.reply_text,
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        
        # Determine action
        action_response = {
            "acknowledged": True,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "next_action": None
        }
        
        if is_hostile:
            action_response["next_action"] = {
                "type": "end",
                "reason": "Hostile message detected",
                "message": "Sorry to hear that. We'll stop messaging you."
            }
        elif is_auto_reply:
            action_response["next_action"] = {
                "type": "wait",
                "wait_seconds": 3600,
                "reason": "Auto-reply detected"
            }
        elif has_commitment:
            action_response["next_action"] = {
                "type": "send",
                "message": "Great! Here's what we can do for you next...",
                "reason": "Customer showed interest"
            }
        else:
            action_response["next_action"] = {
                "type": "send",
                "message": "Thanks for your feedback. We're here to help!",
                "reason": "Acknowledging reply"
            }
        
        return action_response
    
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