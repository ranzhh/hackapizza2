"""
Hackapizza 2.0 - Restaurant Management Agent using Regolo AI with MCP Tools
"""
import os
from datapizza.agents import Agent
from datapizza.clients.openai_like import OpenAILikeClient
from datapizza.tools import tool
from datapizza.tools.mcp_client import MCPClient
from hp2.core.settings import get_settings


# Load settings from environment
settings = get_settings()

# MCP Server Configuration
HACKAPIZZA_BASE_URL = "https://hackapizza.datapizza.tech"
MCP_ENDPOINT = f"{HACKAPIZZA_BASE_URL}/mcp"


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

You have access to MCP tools that let you interact with the game:
- closed_bid: Place bids for ingredients in the auction
- save_menu: Update your restaurant's menu
- create_market_entry: Create buy/sell offers in the P2P market
- execute_transaction: Accept a market offer
- delete_market_entry: Cancel your market offer
- prepare_dish: Start preparing a dish (takes time)
- serve_dish: Serve a prepared dish to a customer
- update_restaurant_is_open: Open or close your restaurant
- send_message: Communicate with other teams

You must make decisions quickly and autonomously to succeed in this competitive environment.""",
    base_url="https://api.regolo.ai/v1",  # Regolo AI base URL
)


async def main():
    """Main entry point for the agent."""
    print("🍕 Hackapizza 2.0 Agent Starting...")
    print(f"Team ID: {settings.hackapizza_team_id}")
    print(f"Using Regolo AI model: gpt-oss-120b")
    print("-" * 50)
    
    # Initialize MCP client to get Hackapizza game tools
    print(f"Connecting to MCP server: {MCP_ENDPOINT}")
    mcp_client = MCPClient(
        url=MCP_ENDPOINT,
        headers={"x-api-key": settings.hackapizza_team_api_key},
        timeout=30
    )
    
    # Fetch available MCP tools from the server
    print("Fetching MCP tools...")
    mcp_tools = mcp_client.list_tools()
    print(f"✓ Loaded {len(mcp_tools)} MCP tools:")
    for tool in mcp_tools:
        print(f"  - {tool.name}: {tool.description}")
    
    # Combine custom tools with MCP tools as a list
    all_tools = [
        analyze_customer_request,
        check_inventory,
        calculate_bid,
        serve_customer,
    ] + mcp_tools  # MCP tools are already Tool objects
    
    # Create the agent with both custom and MCP tools
    agent = Agent(
        name="hackapizza_agent",
        client=regolo_client,
        tools=all_tools  # Pass as list, not dict
    )
    
    print("\n" + "=" * 50)
    print("Agent Ready! Available tools:")
    print("Custom tools: analyze_customer_request, check_inventory, calculate_bid, serve_customer")
    print(f"MCP tools: {', '.join([t.name for t in mcp_tools])}")
    print("=" * 50 + "\n")
    
    # Example: Run the agent with a test scenario
    response = await agent.run(
        "Check the current game state and tell me what phase we're in. "
        "Then analyze what actions we should take."
    )
    
    print("\nAgent Response:")
    print(response)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
