from typing import List
from pydantic import BaseModel

class RecipeSchema(BaseModel):
    name: str
    preparation_time_ms: int
    ingredients: dict[str, int]
    prestige: int

def should_close_restaurant(
    active_kitchen_queue: List[dict], 
    available_recipes: List[RecipeSchema],
    phase_time_remaining_ms: int,
    max_concurrent_dishes: int = 5
) -> bool:
    """
    Determines if the restaurant should programmatically close to avoid reputation damage
    from unfulfilled orders.
    
    Args:
        active_kitchen_queue: List of dishes currently being prepared (from RestaurantSchema.kitchen).
        available_recipes: Reference list to look up prep times.
        phase_time_remaining_ms: How many milliseconds are left in the Serving Phase.
        max_concurrent_dishes: The physical limit of your kitchen burners/bots.
        
    Returns:
        True if you should trigger update_restaurant_is_open(False), False otherwise.
    """
    # 1. Capacity Check: Are we physically maxed out?
    if len(active_kitchen_queue) >= max_concurrent_dishes:
        return True
        
    # Create a lookup dictionary for prep times
    prep_times: dict[str, int] = {r.name: r.preparation_time_ms for r in available_recipes}
    
    # 2. Time-Boundary Check (Little's Law approximation)
    # Calculate the total serial time required to clear the current queue
    # (Assuming single-threaded kitchen for safety, adjust if kitchen processes in parallel)
    total_processing_time_required = 0
    for order in active_kitchen_queue:
        dish_name = order.get("recipe_name") # Adjust based on actual dict structure
        total_processing_time_required += prep_times.get(dish_name, 0)
        
    # If the time required to clear our current queue is greater than the time
    # left in the phase, we are doomed if we accept another order. Close the doors.
    buffer_ms = 5000 # 5 seconds of safety buffer for network latency
    if (total_processing_time_required + buffer_ms) >= phase_time_remaining_ms:
        return True
        
    return False