def get_ingredient_frequencies(recipes: list[dict]):
    ingredients_counts = {}

    for recipe in recipes:
        for ingredient in recipe["ingredients"]:
            if ingredient not in ingredients_counts:
                ingredients_counts[ingredient] = 0

            ingredients_counts[ingredient] += 1

    return ingredients_counts

