from typing import Dict, List, Tuple
from pydantic import BaseModel

class RecipeSchema(BaseModel):
    name: str
    preparation_time_ms: int
    ingredients: Dict[str, int]
    prestige: int

def allocate_bids(
    balance: float,
    target_recipes: List[RecipeSchema],
    estimated_clearing_prices: Dict[str, float]
) -> List[Dict[str, any]]:
    """
    Calculates exactly how many of each ingredient to bid on, and at what price,
    without exceeding the restaurant's balance.
    
    Args:
        balance: Current restaurant cash.
        target_recipes: A prioritized list of recipes we WANT to cook this turn.
        estimated_clearing_prices: The predicted winning bid for each ingredient (from EMA).
        
    Returns:
        A list of bid dictionaries ready to be sent to the server.
    """
    allocated_bids = {}
    remaining_balance = balance
    
    # Safety margin: Don't spend exactly 100% of balance, keep 5% for emergencies
    spending_cap = remaining_balance * 0.95 
    
    for recipe in target_recipes:
        # Calculate total estimated cost to acquire all ingredients for ONE portion of this recipe
        cost_for_one_portion = 0.0
        for ing, qty in recipe.ingredients.items():
            est_price = estimated_clearing_prices.get(ing, 1.0) # Default to 1.0 if unknown
            cost_for_one_portion += (est_price * qty)
            
        # If we can't afford even one portion, skip this recipe
        if cost_for_one_portion > spending_cap:
            continue
            
        # Figure out how many whole portions we can afford with remaining budget
        affordable_portions = int(spending_cap // cost_for_one_portion)
        
        # Add the required ingredients to our bidding cart
        for ing, qty in recipe.ingredients.items():
            total_qty_needed = qty * affordable_portions
            est_price = estimated_clearing_prices.get(ing, 1.0)
            
            if ing in allocated_bids:
                allocated_bids[ing]["quantity"] += total_qty_needed
            else:
                allocated_bids[ing] = {
                    "ingredient": ing,
                    "quantity": total_qty_needed,
                    # We bid exactly 1% above our estimated clearing price to edge out ties
                    "price": round(est_price * 1.01, 2) 
                }
                
        # Deduct the projected cost from our spending cap
        projected_cost = cost_for_one_portion * affordable_portions
        spending_cap -= projected_cost
        
    # Format for the API payload
    return [
        {"ingredient": data["ingredient"], "quantity": data["quantity"], "price": data["price"]}
        for data in allocated_bids.values()
    ]