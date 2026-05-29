"""Labeled routing eval set over the 6-category taxonomy (see MAJOR_PHASES.md §2).

Each item: {"q": str, "expected_tier": 1|2|3, "category": str}.
Categories: greeting, simple_faq, classification (T1); function_calling,
structured_extraction (T2); multi_step (T3).
"""

ROUTING_QUESTIONS = [
    # --- Tier 1: greeting ---
    {"q": "hi there!", "expected_tier": 1, "category": "greeting"},
    {"q": "good morning, hope you're well", "expected_tier": 1, "category": "greeting"},
    {"q": "hey", "expected_tier": 1, "category": "greeting"},
    # --- Tier 1: simple FAQ ---
    {"q": "how do I reset my password?", "expected_tier": 1, "category": "simple_faq"},
    {"q": "what payment methods do you accept?", "expected_tier": 1, "category": "simple_faq"},
    {"q": "what are your customer support hours?", "expected_tier": 1, "category": "simple_faq"},
    # --- Tier 1: classification ---
    {"q": "Is 'I keep getting logged out' a Billing, Technical, or Account issue?",
     "expected_tier": 1, "category": "classification"},
    {"q": "Classify this message as Billing, Orders, or Account: 'my card was charged twice'",
     "expected_tier": 1, "category": "classification"},
    # --- Tier 2: function calling ---
    {"q": "what's the status of order #12345?", "expected_tier": 2, "category": "function_calling"},
    {"q": "how much does the Dragon Skin item cost right now?",
     "expected_tier": 2, "category": "function_calling"},
    {"q": "what account tier am I on?", "expected_tier": 2, "category": "function_calling"},
    # --- Tier 2: structured extraction ---
    {"q": "give me the full details for item SKU-42", "expected_tier": 2, "category": "structured_extraction"},
    {"q": "look up the rarity and stock for item id 7", "expected_tier": 2, "category": "structured_extraction"},
    # --- Tier 3: multi-step / sensitive ---
    {"q": "I was double-charged, the refund failed, and now I'm locked out of my account — please help",
     "expected_tier": 3, "category": "multi_step"},
    {"q": "My order never arrived, support closed my ticket, and I want to escalate a complaint",
     "expected_tier": 3, "category": "multi_step"},
    {"q": "First my payment bounced, then my items vanished, and now 2FA won't send codes — walk me through fixing all of it",
     "expected_tier": 3, "category": "multi_step"},
]
