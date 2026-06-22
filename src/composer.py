import json
import os
import re
import logging
from typing import Dict, Any, Optional
from datetime import datetime

try:
    from groq import Groq
except ImportError:
    Groq = None

logger = logging.getLogger(__name__)


class MessageComposer:
    def __init__(self):
        if Groq is None:
            raise ImportError("groq package not installed. Run: pip install groq")
        
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY environment variable not set")
        
        self.client = Groq(api_key=api_key)
        self.model = "mixtral-8x7b-32768"
        self.category_guidelines = {}
        self.load_category_guidelines()

    def load_category_guidelines(self):
        """Load category-specific voice and offer guidelines"""
        categories_dir = "dataset/expanded/categories"
        
        if not os.path.exists(categories_dir):
            categories_dir = "dataset/categories"
        
        if os.path.exists(categories_dir):
            try:
                for filename in os.listdir(categories_dir):
                    if filename.endswith('.json'):
                        filepath = os.path.join(categories_dir, filename)
                        with open(filepath, 'r') as f:
                            category = json.load(f)
                            slug = category.get('slug', filename.replace('.json', ''))
                            self.category_guidelines[slug] = category
                logger.info(f"Loaded {len(self.category_guidelines)} category guidelines")
            except Exception as e:
                logger.warning(f"Failed to load category guidelines: {e}")

    def compose(
        self,
        merchant_id: str,
        context: Dict[str, Any],
        actions_remaining: int
    ) -> Optional[Dict[str, Any]]:
        """Deterministic message composition using Groq."""
        try:
            category = context.get('category', 'unknown')
            merchant = context.get('merchant', {})
            triggers = context.get('triggers', [])
            customer_data = context.get('customer_data', None)
            
            guidelines = self.category_guidelines.get(category, {})
            voice = guidelines.get('voice', {})
            offers = guidelines.get('offer_catalog', [])
            
            prompt = self._build_composition_prompt(
                merchant=merchant,
                category=category,
                voice=voice,
                triggers=triggers,
                offers=offers,
                customer_data=customer_data,
                guidelines=guidelines
            )
            
            message = self.client.messages.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_tokens=500,
                temperature=0.7
            )
            
            response_text = message.choices[0].message.content
            parsed = self._parse_response(response_text)
            
            return {
                "message": parsed.get('message', ''),
                "cta": parsed.get('cta', ''),
                "send_as_identity": parsed.get('identity', 'Vera'),
                "suppression_key": parsed.get('suppression_key', ''),
                "rationale": parsed.get('rationale', ''),
                "merchant_id": merchant_id,
                "category": category,
                "timestamp": self._get_timestamp()
            }
        
        except Exception as e:
            logger.error(f"Composition error for {merchant_id}: {str(e)}", exc_info=True)
            return None

    def _build_composition_prompt(
        self,
        merchant: Dict,
        category: str,
        voice: Dict,
        triggers: list,
        offers: list,
        customer_data: Optional[Dict],
        guidelines: Dict
    ) -> str:
        """Build detailed prompt for Groq"""
        
        primary_trigger = triggers[0] if triggers else {}
        relevant_offers = self._select_offers(merchant, offers, primary_trigger)
        
        vocab_taboo = voice.get('vocab_taboo', [])
        vocab_allowed = voice.get('vocab_allowed', [])
        
        prompt = f"""You are Vera, a merchant AI assistant for {category} businesses in India.

**Category Voice & Rules:**
- Tone: {voice.get('tone', 'professional')}
- Register: {voice.get('register', 'friendly')}
- Forbidden words: {', '.join(vocab_taboo[:5]) if vocab_taboo else 'None'}

**Merchant Profile:**
- Name: {merchant.get('name', 'Partner')}
- Category: {category}
- Rating: {merchant.get('metrics', {}).get('rating', 'N/A')}

**Current Trigger:**
- Type: {primary_trigger.get('trigger_type', 'unknown')}
- Description: {primary_trigger.get('description', '')}

**Best Offers:**
{json.dumps(relevant_offers[:2], indent=2) if relevant_offers else 'No offers available'}

**Task: Compose a single-CTA message that is grounded in the trigger and merchant state.**

**Output Format (JSON):**
{{
  "message": "...",
  "cta": "...",
  "identity": "Vera",
  "suppression_key": "...",
  "rationale": "..."
}}
"""
        return prompt

    def _select_offers(self, merchant: Dict, offers: list, trigger: Dict) -> list:
        """Select most relevant offers"""
        if not offers:
            return []
        
        return offers[:2]

    def _parse_response(self, response_text: str) -> Dict:
        """Extract JSON from response"""
        try:
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except (json.JSONDecodeError, AttributeError):
            pass
        
        return {
            "message": response_text[:200] if response_text else "Default message",
            "cta": "Learn more",
            "identity": "Vera",
            "suppression_key": "default",
            "rationale": "Fallback"
        }

    def _get_timestamp(self) -> str:
        return datetime.utcnow().isoformat()