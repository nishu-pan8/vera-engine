import json
import os
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

class ContextManager:
    def __init__(self):
        self.store = {}
        self.version_tracker = {}
        
    def load_dataset(self, dataset_path: str):
        """Load expanded dataset on startup"""
        try:
            # Try expanded first, fall back to seed
            paths_to_try = [
                os.path.join(dataset_path, "expanded"),
                dataset_path
            ]
            
            actual_path = None
            for path in paths_to_try:
                if os.path.exists(path):
                    actual_path = path
                    break
            
            if not actual_path:
                logger.warning(f"No dataset found at {dataset_path}")
                return
            
            # Load merchants
            merchants_file = os.path.join(actual_path, "merchants.json")
            if os.path.exists(merchants_file):
                with open(merchants_file, 'r') as f:
                    merchants = json.load(f)
                    for m in merchants:
                        merchant_id = m.get('id', m.get('merchant_id'))
                        self.store[f"merchant_{merchant_id}"] = m
                logger.info(f"Loaded {len(merchants)} merchants")
            
            # Load customers
            customers_file = os.path.join(actual_path, "customers.json")
            if os.path.exists(customers_file):
                with open(customers_file, 'r') as f:
                    customers = json.load(f)
                    for c in customers:
                        customer_id = c.get('id', c.get('customer_id'))
                        self.store[f"customer_{customer_id}"] = c
                logger.info(f"Loaded {len(customers)} customers")
            
            # Load triggers
            triggers_file = os.path.join(actual_path, "triggers.json")
            if os.path.exists(triggers_file):
                with open(triggers_file, 'r') as f:
                    triggers = json.load(f)
                    for t in triggers:
                        trigger_id = t.get('id', t.get('trigger_id'))
                        self.store[f"trigger_{trigger_id}"] = t
                logger.info(f"Loaded {len(triggers)} triggers")
            
            logger.info(f"✓ Dataset loaded: {len(self.store)} total context items")
        
        except Exception as e:
            logger.error(f"Dataset load error: {e}", exc_info=True)
    
    def store_context(self, scope: str, context_id: str, version: int, data: Dict) -> str:
        """Store context atomically. Idempotent by scope + version."""
        key = f"{scope}_{context_id}"
        version_key = f"{key}_version"
        
        # Check if we already have this version or newer
        if version_key in self.version_tracker:
            if self.version_tracker[version_key] >= version:
                return f"ack_{context_id}"
        
        # Store atomically
        self.store[key] = {**data, "_stored_at": datetime.utcnow().isoformat()}
        self.version_tracker[version_key] = version
        
        return f"ack_{context_id}_{version}"
    
    def get_context(self, scope: str, context_id: str) -> Optional[Dict]:
        """Retrieve stored context"""
        key = f"{scope}_{context_id}"
        return self.store.get(key)
    
    def get_all_merchant_ids(self) -> list:
        """Get all merchant IDs for batch operations"""
        return [k.replace("merchant_", "") for k in self.store.keys() if k.startswith("merchant_")]
    
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
            self.store[merchant_key] = {"id": merchant_id}
        
        if 'conversation_history' not in self.store[merchant_key]:
            self.store[merchant_key]['conversation_history'] = []
        
        self.store[merchant_key]['conversation_history'].append({
            "timestamp": timestamp,
            "message_id": message_id,
            "customer_id": customer_id,
            "reply": reply_text
        })
        
        logger.info(f"Recorded reply from {customer_id} for merchant {merchant_id}")

from datetime import datetime