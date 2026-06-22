import json
import os
from typing import Dict, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ContextManager:
    def __init__(self):
        self.store = {}  # In-memory store; use Redis for production
        self.version_tracker = {}  # Track versioning for idempotency
        
    def load_dataset(self, dataset_path: str):
        """Load expanded dataset on startup"""
        try:
            # Load merchants
            merchants_file = os.path.join(dataset_path, "merchants.json")
            if os.path.exists(merchants_file):
                with open(merchants_file) as f:
                    merchants = json.load(f)
                    for m in merchants:
                        self.store[f"merchant_{m['id']}"] = m
            
            # Load customers
            customers_file = os.path.join(dataset_path, "customers.json")
            if os.path.exists(customers_file):
                with open(customers_file) as f:
                    customers = json.load(f)
                    for c in customers:
                        self.store[f"customer_{c['id']}"] = c
            
            # Load triggers
            triggers_file = os.path.join(dataset_path, "triggers.json")
            if os.path.exists(triggers_file):
                with open(triggers_file) as f:
                    triggers = json.load(f)
                    for t in triggers:
                        self.store[f"trigger_{t['id']}"] = t
            
            logger.info(f"Loaded {len(self.store)} context items from {dataset_path}")
        except Exception as e:
            logger.warning(f"Dataset load error (non-critical): {e}")
    
    def store_context(self, scope: str, context_id: str, version: int, data: Dict) -> str:
        """
        Store context atomically.
        Idempotent by scope + version.
        """
        key = f"{scope}_{context_id}"
        version_key = f"{key}_version"
        
        # Check if we've already stored this version
        if version_key in self.version_tracker:
            if self.version_tracker[version_key] >= version:
                # Already have this or newer version
                return f"ack_{context_id}"
        
        # Store the new version
        self.store[key] = data
        self.version_tracker[version_key] = version
        
        logger.info(f"Stored context: {key} v{version}")
        return f"ack_{context_id}_{version}"
    
    def get_context(self, scope: str, context_id: str) -> Optional[Dict]:
        """Retrieve stored context"""
        key = f"{scope}_{context_id}"
        return self.store.get(key)
    
    def record_reply(
        self,
        merchant_id: str,
        customer_id: Optional[str],
        message_id: str,
        reply_text: str,
        timestamp: str
    ):
        """Record customer reply for learning"""
        merchant_key = f"merchant_{merchant_id}"
        
        if merchant_key not in self.store:
            self.store[merchant_key] = {"conversation_history": []}
        
        if 'conversation_history' not in self.store[merchant_key]:
            self.store[merchant_key]['conversation_history'] = []
        
        self.store[merchant_key]['conversation_history'].append({
            "timestamp": timestamp,
            "message_id": message_id,
            "customer_id": customer_id,
            "reply": reply_text
        })
        
        logger.info(f"Recorded reply from {customer_id} for merchant {merchant_id}")