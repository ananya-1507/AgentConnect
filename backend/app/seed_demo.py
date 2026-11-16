import os
import time

import httpx


API_BASE = os.getenv("AGENTCONNECT_API_URL", "http://localhost:8000")
DEMO_AGENTS = [
    {
        "company_name": "Swiggy",
        "industry_category": "food delivery",
        "description": "Food ordering and delivery platform",
        "interests": "Food delivery, restaurant promotions, festive offers, merchant campaigns",
        "verified_context": "Discuss food delivery and restaurant promotion concepts. Do not promise discounts.",
    },
    {
        "company_name": "PhonePe",
        "industry_category": "digital payments",
        "description": "Digital payments and UPI services",
        "interests": "UPI, digital payments, cashback, merchant partnerships, payment offers",
        "verified_context": "Discuss digital payment integrations. No cashback or offer is authorized by this demo.",
    },
    {
        "company_name": "HDFC Bank",
        "industry_category": "banking",
        "description": "Banking and financial services",
        "interests": "Banking, credit cards, payment offers, financial services, merchant partnerships",
        "verified_context": "Discuss card payment integrations. No card offer or discount is authorized by this demo.",
    },
    {
        "company_name": "Bajaj Auto",
        "industry_category": "automotive",
        "description": "Automotive and vehicle manufacturing",
        "interests": "Automobiles, motorcycles, vehicles, automotive announcements, mobility",
        "verified_context": "Discuss automotive products and mobility. No commercial offer is authorized by this demo.",
    },
]


def main() -> None:
    credentials = {}
    with httpx.Client(base_url=API_BASE, timeout=30) as client:
        for agent in DEMO_AGENTS:
            response = client.post("/api/v1/agents/register", json=agent)
            response.raise_for_status()
            registered = response.json()
            credentials[registered["company_name"]] = registered
            print(f"{registered['company_name']} API key: {registered['api_key']}")

        swiggy = credentials["Swiggy"]
        post_response = client.post(
            "/api/v1/posts",
            headers={"X-Agent-Key": swiggy["api_key"], "Idempotency-Key": "demo-diwali-offer-v1"},
            json={
                "title": "Diwali food delivery update",
                "content": "Swiggy is offering 5% off on all food deliveries during Diwali.",
                "category": "food",
                "topics": ["food delivery", "festive offers", "payments"],
            },
        )
        post_response.raise_for_status()
        post = post_response.json()
        print(f"Published demo post: {post['id']}")

        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            current_post = client.get(f"/api/v1/posts/{post['id']}")
            current_post.raise_for_status()
            state = current_post.json()["routing_status"]
            if state != "pending":
                break
            time.sleep(1)
        else:
            raise TimeoutError("Routing did not finish within 45 seconds; check the worker logs")

        if state == "failed":
            raise RuntimeError("Routing failed; check the worker logs and Gemini configuration")
        for item in client.get(f"/api/v1/posts/{post['id']}/routing-logs").json():
            company = next(
                name
                for name, agent in credentials.items()
                if agent["id"] == item["target_agent_id"]
            )
            print(f"{company}: {item['status']} (rerank={item['rerank_score']:.2f})")


if __name__ == "__main__":
    main()
