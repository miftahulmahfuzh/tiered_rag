"""Generate xlsx/knowledge_base.xlsx — 20 Q&A pairs for the game-store support desk.

Run once to (re)produce the committed artifact:

    python scripts/build_knowledge_base.py

Answers are kept self-contained and factual so retrieval is unambiguous.
Categories: Account, Billing, Orders, Items, General.
"""
from pathlib import Path

import pandas as pd

QA_PAIRS = [
    # --- Account ---
    {
        "id": 1,
        "question": "How do I reset my password?",
        "answer": "Open Settings > Security > Reset Password, then follow the email link we send to your registered address.",
        "category": "Account",
    },
    {
        "id": 2,
        "question": "How do I change the email address on my account?",
        "answer": "Go to Settings > Account > Email, enter the new address, and confirm it via the verification link we email you.",
        "category": "Account",
    },
    {
        "id": 3,
        "question": "How do I enable two-factor authentication?",
        "answer": "Open Settings > Security > Two-Factor Authentication and scan the QR code with an authenticator app to enable 2FA.",
        "category": "Account",
    },
    {
        "id": 4,
        "question": "How do I delete my account?",
        "answer": "Go to Settings > Account > Delete Account. Deletion is permanent and removes your game library after a 14-day grace period.",
        "category": "Account",
    },
    # --- Billing ---
    {
        "id": 5,
        "question": "What payment methods do you accept?",
        "answer": "We accept Visa, Mastercard, American Express, PayPal, and store gift cards for all purchases.",
        "category": "Billing",
    },
    {
        "id": 6,
        "question": "How do I request a refund for a game?",
        "answer": "You can request a refund within 14 days of purchase if playtime is under 2 hours, via Settings > Purchases > Request Refund.",
        "category": "Billing",
    },
    {
        "id": 7,
        "question": "Why was my card declined at checkout?",
        "answer": "Card declines are usually caused by insufficient funds, an expired card, or a region mismatch. Verify your card details under Settings > Payment Methods.",
        "category": "Billing",
    },
    {
        "id": 8,
        "question": "How do I view my purchase history and invoices?",
        "answer": "Your full purchase history and downloadable invoices are available under Settings > Purchases > History.",
        "category": "Billing",
    },
    {
        "id": 9,
        "question": "How do I redeem a gift card or promo code?",
        "answer": "Go to Wallet > Redeem Code, enter your gift card or promo code, and the balance is added to your store wallet instantly.",
        "category": "Billing",
    },
    # --- Orders ---
    {
        "id": 10,
        "question": "How do I track my order?",
        "answer": "Open the Orders tab and select an order to see its live status and tracking number for physical items.",
        "category": "Orders",
    },
    {
        "id": 11,
        "question": "How long does shipping take for physical merchandise?",
        "answer": "Standard shipping takes 5-7 business days; express shipping takes 2-3 business days within the same region.",
        "category": "Orders",
    },
    {
        "id": 12,
        "question": "Can I cancel an order after placing it?",
        "answer": "Orders can be cancelled from the Orders tab within 1 hour of placement, before they enter the fulfillment stage.",
        "category": "Orders",
    },
    {
        "id": 13,
        "question": "My order arrived damaged. What do I do?",
        "answer": "Report a damaged order within 7 days via Orders > Report a Problem, attach photos, and we will ship a free replacement.",
        "category": "Orders",
    },
    # --- Items ---
    {
        "id": 14,
        "question": "How do I find the details and price of an in-game item?",
        "answer": "Open the Store, search the item by name, and its detail page lists the price, rarity, and stock availability.",
        "category": "Items",
    },
    {
        "id": 15,
        "question": "Can I trade or gift items to other players?",
        "answer": "Yes. Open the item in your Inventory, choose Gift or Trade, and select a friend who has been on your list for at least 3 days.",
        "category": "Items",
    },
    {
        "id": 16,
        "question": "Why is an item missing from my inventory?",
        "answer": "Items can be missing due to a pending purchase, a recent trade, or a regional restriction. Check Inventory > Pending and your trade history.",
        "category": "Items",
    },
    {
        "id": 17,
        "question": "How do limited-time event items work?",
        "answer": "Limited-time event items are only purchasable during the event window and remain usable in your inventory permanently afterward.",
        "category": "Items",
    },
    # --- General ---
    {
        "id": 18,
        "question": "What are your customer support hours?",
        "answer": "Live chat support is available 24/7, while phone support runs from 9 AM to 9 PM local time, seven days a week.",
        "category": "General",
    },
    {
        "id": 19,
        "question": "What are the minimum system requirements to play?",
        "answer": "The minimum requirements are a quad-core CPU, 8 GB RAM, 20 GB free storage, and a DirectX 11 compatible graphics card.",
        "category": "General",
    },
    {
        "id": 20,
        "question": "How do I contact a human support agent?",
        "answer": "Open the Help Center and choose Contact Us > Live Agent to be connected to a human support specialist via chat.",
        "category": "General",
    },
]


def main() -> None:
    out = Path("xlsx/knowledge_base.xlsx")
    out.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(QA_PAIRS, columns=["id", "question", "answer", "category"])
    df.to_excel(out, index=False)
    print(f"wrote {len(df)} Q&A pairs to {out}")


if __name__ == "__main__":
    main()
