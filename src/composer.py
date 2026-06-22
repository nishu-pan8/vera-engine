import json
import os
import re
import logging
from typing import Dict, Any, Optional
from datetime import datetime

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

logger = logging.getLogger(__name__)


class MessageComposer:
    def __init__(self):
        if Anthropic is None:
            raise ImportError("anthropic package not installed. Run: pip install anthropic")
        
        self.client = Anthropic()
        self.model = "claude-3-5-sonnet-20241022"
        self.category_guidelines = {}
        self.load_category_guidelines()

    def load_category_guidelines(self):
        """Load category-specific voice and offer guidelines"""
        categories_dir = "dataset/expanded/categories"
        
        # Fallback to seed categories if expanded doesn't exist
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
        else:
            logger.warning(f"Categories directory not found at {categories_dir}")

    def compose(
        self,
        merchant_id: str,
        context: Dict[str, Any],
        actions_remaining: int
    ) -> Optional[Dict[str, Any]]:
        """
        Deterministic message composition.
        
        Returns:
        {
            "message": "...",
            "cta": "...",
            "send_as_identity": "...",
            "suppression_key": "...",
            "rationale": "...",
            "merchant_id": "...",
            "timestamp": "..."
        }
        """
        try:
            category = context.get('category', 'unknown')
            merchant = context.get('merchant', {})
            triggers = context.get('triggers', [])
            customer_data = context.get('customer_data', None)
            
            # Get category guidelines
            guidelines = self.category_guidelines.get(category, {})
            voice = guidelines.get('voice', {})
            offers = guidelines.get('offer_catalog', [])
            
            # Build context for Claude
            prompt = self._build_composition_prompt(
                merchant=merchant,
                category=category,
                voice=voice,
                triggers=triggers,
                offers=offers,
                customer_data=customer_data,
                guidelines=guidelines
            )
            
            # Call Claude with structured reasoning
            message = self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )
            
            response_text = message.content[0].text
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
        """Build detailed prompt for Claude"""
        
        # Select best trigger
        primary_trigger = triggers[0] if triggers else {}
        
        # Select relevant offer
        relevant_offers = self._select_offers(merchant, offers, primary_trigger)
        
        vocab_taboo = voice.get('vocab_taboo', [])
        vocab_allowed = voice.get('vocab_allowed', [])
        
        prompt = f"""You are Vera, a merchant AI assistant for {category} businesses in India.

**Category Voice & Rules:**
- Tone: {voice.get('tone', 'professional')}
- Register: {voice.get('register', 'friendly')}
- Forbidden words: {', '.join(vocab_taboo[:5]) if vocab_taboo else 'None'}
- Allowed specialty terms: {', '.join(vocab_allowed[:3]) if vocab_allowed else 'None'}

**Merchant Profile:**
- Name: {merchant.get('name', 'Partner')}
- Category: {category}
- Rating: {merchant.get('metrics', {}).get('rating', 'N/A')}
- Recent performance: {merchant.get('metrics', {}).get('performance_summary', 'Stable')}

**Current Trigger:**
- Type: {primary_trigger.get('trigger_type', 'unknown')}
- Description: {primary_trigger.get('description', '')}
- Urgency: {primary_trigger.get('urgency', 'normal')}

**Best Offers to Highlight:**
{json.dumps(relevant_offers[:2], indent=2) if relevant_offers else 'No offers available'}

**Live Conversation History:**
{json.dumps(merchant.get('conversation_history', [])[-3:], indent=2) if merchant.get('conversation_history') else 'No history'}

{f"**Direct Customer Context:**\\n- Name: {customer_data.get('name', 'N/A')}\\n- Relationship: {customer_data.get('relationship', 'N/A')}\\n- Last purchase: {customer_data.get('last_purchase', 'N/A')}" if customer_data else ""}

**Task: Compose a single-CTA message that:**
1. Is grounded in the trigger and merchant state (not generic)
2. Uses specific numbers, dates, offers from the data above
3. Matches the category voice exactly
4. Gives ONE compelling reason to reply now
5. Makes the next action low-effort (yes/no, single tap)

**Output Format (JSON):**
{{
  "message": "...",
  "cta": "...",
  "identity": "...",
  "suppression_key": "...",
  "rationale": "Why this message now + key decision factors"
}}
"""
        return prompt

    def _select_offers(self, merchant: Dict, offers: list, trigger: Dict) -> list:
        """Select most relevant offers based on trigger and merchant state"""
        if not offers:
            return []
        
        trigger_type = trigger.get('trigger_type', '')
        
        try:
            # Map trigger to offer relevance
            if trigger_type == 'research':
                # Educational trigger → free service offers
                return [o for o in offers if o.get('type') == 'free_service'][:2]
            elif trigger_type == 'spike':
                # Demand spike → service offers at mid-price
                return [o for o in offers if 500 < float(o.get('value', 0)) < 5000][:2]
            elif trigger_type == 'dip':
                # Performance dip → entry-level or free offers
                return [o for o in offers if float(o.get('value', 0)) < 1000][:2]
            else:
                # Default: recommend best-performing offers
                return offers[:2]
        except Exception as e:
            logger.warning(f"Offer selection error: {e}")
            return offers[:2]

    def _parse_response(self, response_text: str) -> Dict:
        """Extract JSON from Claude response"""
        try:
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"JSON parse error: {e}")
        
        return {
            "message": response_text[:200] if response_text else "Default message",
            "cta": "Learn more",
            "identity": "Vera",
            "suppression_key": "default",
            "rationale": "Fallback composition"
        }

    def _get_timestamp(self) -> str:
        """Get current UTC timestamp"""
        return datetime.utcnow().isoformat()