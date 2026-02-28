from typing import Dict, List
from pydantic import BaseModel

# Simplified schemas for the algorithm based on your provided structures
class RecipeSchema(BaseModel):
    name: str
    preparation_time_ms: int
    ingredients: Dict[str, int]
    prestige: int
    
class MenuItemSchema(BaseModel):
    name: str
    price: float

def optimize_menu_greedy(
    current_inventory: Dict[str, int], 
    available_recipes: List[RecipeSchema], 
    target_prices: Dict[str, float]
) -> List[MenuItemSchema]:
    """
    Determines the optimal menu given a fixed inventory using a greedy approximation 
    of the Multi-Dimensional Knapsack Problem.
    
    Args:
        current_inventory: What we actually have in stock right now.
        available_recipes: List of all recipes in the game.
        target_prices: A dictionary mapping recipe names to their intended selling price.
        
    Returns:
        A list of MenuItemSchema representing what we should actually sell.
    """
    # 1. Calculate the "Value Density" of each recipe
    # Value density = Expected Revenue / Total Number of Ingredients required
    recipe_values = []
    for recipe in available_recipes:
        total_ingredients_needed = sum(recipe.ingredients.values())
        if total_ingredients_needed == 0:
            continue
            
        expected_revenue = target_prices.get(recipe.name, 0.0)
        value_density = expected_revenue / total_ingredients_needed
        recipe_values.append({
            "recipe": recipe,
            "density": value_density,
            "revenue": expected_revenue
        })
        
    # Sort recipes by highest value density first
    recipe_values.sort(key=lambda x: x["density"], reverse=True)
    
    working_inventory = current_inventory.copy()
    final_menu_items = {}
    
    # 2. Greedily "cook" recipes virtually to see how many we can support
    for item in recipe_values:
        recipe = item["recipe"]
        price = item["revenue"]
        
        # Calculate maximum portions of THIS recipe we can make with current stock
        possible_portions = float('inf')
        for ing_name, required_qty in recipe.ingredients.items():
            stock = working_inventory.get(ing_name, 0)
            if required_qty > 0:
                portions = stock // required_qty
                if portions < possible_portions:
                    possible_portions = portions
                    
        # If we can make at least one, add it to the menu and deduct ingredients
        if possible_portions > 0:
            final_menu_items[recipe.name] = price
            
            # Deduct used ingredients from working inventory
            # We assume we want to put it on the menu if we can make it at least once
            for ing_name, required_qty in recipe.ingredients.items():
                working_inventory[ing_name] -= (required_qty * possible_portions)
                
    # 3. Format output
    menu = [MenuItemSchema(name=name, price=price) for name, price in final_menu_items.items()]
    return menu

# Example Usage:
# my_inv = {"Quantum Flour": 5, "Void Tomato": 2}
# recipes = [RecipeSchema(name="Space Pizza", preparation_time_ms=5000, ingredients={"Quantum Flour": 2, "Void Tomato": 1}, prestige=10)]
# prices = {"Space Pizza": 45.0}
# optimal_menu = optimize_menu_greedy(my_inv, recipes, prices)