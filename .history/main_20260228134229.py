"""
Hackapizza 2.0 - Restaurant Management Agent using Regolo AI
"""
import os
from datapizza.agents import Agent
from datapizza.clients.openai_like import OpenAILikeClient
from datapizza.tools import tool
from hp2.core.settings import get_settings


# Load settings from environment
settings = get_settings()


# Define tools for restaurant management
@tool
async def analyze_customer_request(customer_request: str) -> dict:
    """Analyze a customer's order request and extract key information.
    
    Args:
        customer_request: The natural language request from the customer
        
    Returns:
        Dictionary with analyzed request details
    """
    print(f"Analyzing customer request: {customer_request}")
    return {
        "request": customer_request,
        "status": "analyzed"
    }


@tool
async def check_inventory(ingredient: str) -> dict:
    """Check if an ingredient is available in inventory.
    
    Args:
        ingredient: The name of the ingredient to check
        
    Returns:
        Dictionary with inventory status
    """
    print(f"Checking inventory for: {ingredient}")
    return {
        "ingredient": ingredient,
        "available": True,
        "quantity": 0
    }


@tool
async def calculate_bid(ingredient: str, quantity: int) -> dict:
    """Calculate optimal bid for ingredient in closed bid auction.
    
    Args:
        ingredient: The ingredient to bid on
        quantity: Quantity needed
        
    Returns:
        Recommended bid price
    """
    print(f"Calculating bid for {quantity}x {ingredient}")
    return {
        "ingredient": ingredient,
        "quantity": quantity,
        "recommended_price": 10.0
    }


@tool
async def serve_customer(customer_id: str, dish: str) -> dict:
    """Serve a dish to a customer.
    
    Args:
        customer_id: The customer identifier
        dish: The dish to serve
        
    Returns:
        Serving confirmation
    """
    print(f"Serving {dish} to customer {customer_id}")
    return {
        "customer_id": customer_id,
        "dish": dish,
        "status": "served"
    }


# Create Regolo AI client
# Note: Regolo AI is an OpenAI-compatible service
regolo_client = OpenAILikeClient(
    api_key=settings.regolo_api_key,
    model="gpt-oss-120b",  # Available models: gpt-oss-120b, gpt-oss-20b, qwen3-vl-32b
    system_prompt="""You are an expert restaurant management AI for Hackapizza 2.0.
Your goal is to maximize the restaurant's balance by:
- Fulfilling customer orders efficiently
- Managing ingredient inventory (ingredients expire daily)
- Optimizing menu pricing and offerings
- Bidding strategically in ingredient auctions
- Maintaining high reputation
- Checking customer intolerances before serving

You must make decisions quickly and autonomously to succeed in this competitive environment.""",
    base_url="https://api.regolo.ai/v1",  # Regolo AI base URL
)


# Create the agent with tools
agent = Agent(
    name="hackapizza_agent",
    client=regolo_client,
    tools={
        "analyze_customer_request": analyze_customer_request,
        "check_inventory": check_inventory,
        "calculate_bid": calculate_bid,
        "serve_customer": serve_customer,
    }
)


async def main():
    """Main entry point for the agent."""
    print("🍕 Hackapizza 2.0 Agent Starting...")
    print(f"Team ID: {settings.hackapizza_team_id}")
    print(f"Using Regolo AI model: gpt-oss-120b")
    print("-" * 50)
    
    # Example: Run the agent with a test scenario
    response = await agent.run(
        "A customer wants a pizza but mentions they're lactose intolerant. "
        "What should we check and how should we handle this?"
    )
    
    print("\nAgent Response:")
    print(response)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
