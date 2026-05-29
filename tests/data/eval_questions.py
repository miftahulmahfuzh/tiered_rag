"""Labeled abstention eval set, used by the real-ollama integration test (Task 7).

IN_SCOPE: paraphrases of the 20 knowledge_base.xlsx questions (should be ANSWERED).
OUT_OF_SCOPE: clearly out-of-domain questions (should ABSTAIN / "I don't know").
"""

# Paraphrases of each KB row (id in comment) — worded differently than the stored question.
IN_SCOPE = [
    "how can I change my password?",                       # 1
    "I want to update the email on my profile",            # 2
    "how do I turn on 2FA?",                                # 3
    "what's the process to permanently close my account?", # 4
    "which cards and wallets can I pay with?",             # 5
    "can I get my money back for a game I bought?",         # 6
    "my credit card got rejected when I tried to pay",      # 7
    "where can I download my invoices?",                    # 8
    "how do I use a promo code?",                           # 9
    "where do I see the status of my order?",               # 10
    "how many days will delivery take?",                    # 11
    "is it possible to cancel an order I just made?",       # 12
    "the package I received was broken, what now?",         # 13
    "how do I check the price of an item in the shop?",     # 14
    "can I send an item as a gift to a friend?",            # 15
    "one of my items disappeared from my inventory",        # 16
    "do event items expire after the event ends?",          # 17
    "when can I reach customer support?",                   # 18
    "what hardware do I need to run the game?",             # 19
    "how do I talk to a real person in support?",           # 20
]

OUT_OF_SCOPE = [
    "who won the 1998 world cup?",
    "what is the capital of France?",
    "what's the weather on Mars today?",
    "how do I bake a chocolate sourdough loaf?",
    "what is the square root of 2025?",
    "translate 'good morning' into Japanese",
    "who painted the Mona Lisa?",
    "what year did the Roman Empire fall?",
    "give me a recipe for chicken curry",
    "what is the boiling point of mercury?",
]
