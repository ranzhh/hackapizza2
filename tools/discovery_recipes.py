from __future__ import annotations

import asyncio
import os
from typing import Dict, List

import aiohttp
from dotenv import load_dotenv

import argparse
import json
import sys
from pathlib import Path

from hp2.core.api import HackapizzaClient
from hp2.core.schema.models import RecipeSchema

# Load environment variables from .env file
load_dotenv()


def get_recipes() -> List[Dict]:
    """Get all the recipes using the api.

    Returns a list of dictionaries, each representing a recipe with its
    name, preparation time, prestige level, and a dict of ingredients
    mapping ingredient name -> quantity required.

    Example entry:
        {
            "name": "Margherita",
            "preparation_time_ms": 5000,
            "prestige": 3,
            "ingredients": {"tomato": 2, "mozzarella": 1}
        }
    """
    return asyncio.run(_async_get_recipes())


async def _async_get_recipes() -> List[Dict]:
    """Async implementation that fetches recipes from the Hackapizza API."""
    team_id = os.environ.get("HACKAPIZZA_TEAM_ID")
    api_key = os.environ.get("HACKAPIZZA_TEAM_API_KEY")
    base_url = os.environ.get("HACKAPIZZA_BASE_URL", "https://hackapizza.datapizza.tech")

    if not team_id:
        raise ValueError("HACKAPIZZA_TEAM_ID is not set in environment / .env file")
    if not api_key:
        raise ValueError("HACKAPIZZA_TEAM_API_KEY is not set in environment / .env file")

    client = HackapizzaClient(
        team_id=int(team_id),
        api_key=api_key,
        base_url=base_url,
        enable_sql_logging=False,
    )

    async with aiohttp.ClientSession(headers=client._headers) as session:
        client._session = session
        recipes: List[RecipeSchema] = await client.get_recipes()

    return [
        {
            "name": recipe.name,
            "ingredients": recipe.ingredients,
        }
        for recipe in recipes
    ]

def get_all(ingredients, recipes):
    available_recipes = []

    for recipe in recipes:
        for ingredient in recipe["ingredients"]:
            if ingredient not in ingredients:
                break
        else:
            available_recipes.append(recipe["name"])

    return available_recipes


def get_weak(ingredients, recipes, match):
    available_recipes = []

    for recipe in recipes:
        count = 0
        for ingredient in recipe["ingredients"]:
            if ingredient in ingredients:
                count += 1

        if count >= match:
            available_recipes.append(recipe['name'])

    return available_recipes


def create_recipe_subset(ingredients: list[str], match = -1):
    """Based on the list of ingredients extract the list of available recipes."""
    recipes = get_recipes()

    if match > 0:
        return get_weak(ingredients, recipes, match)

    return get_all(ingredients, recipes)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Find all recipes that can be made with the given ingredients."
    )
    parser.add_argument(
        "input_file",
        type=Path,
        help=(
            "Path to a JSON file containing the list of available ingredients. "
            'Expected format: {"ingredients": ["ingredient1", "ingredient2", ...]} '
            'or a plain array: ["ingredient1", "ingredient2", ...]'
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("available_recipes.json"),
        help="Path to the output JSON file (default: available_recipes.json)",
    )
# --weak: remove type=bool
    parser.add_argument(
        "--weak",
        type=int,
        default=-1,
        help="Weaker pattern matching that returns all recipes that contain all of the given ingredients",
    )

# --individual: remove type=bool
    parser.add_argument(
        "--individual",
        action="store_true",
        default=False,
    )
    args = parser.parse_args()

    # --- Load ingredients from the JSON file ---
    input_path: Path = args.input_file
    if not input_path.exists():
        print(f"Error: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    try:
        raw = json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON in {input_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    # Accept both {"ingredients": [...]} and a plain list [...]
    if isinstance(raw, dict):
        ingredients_input: list[str] = raw.get("ingredients", [])
    elif isinstance(raw, list):
        ingredients_input = raw
    else:
        print(
            "Error: JSON must be either an object with an 'ingredients' key or a plain list.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not isinstance(ingredients_input, list) or not all(
        isinstance(i, str) for i in ingredients_input
    ):
        print("Error: 'ingredients' must be a list of strings.", file=sys.stderr)
        sys.exit(1)

    # --- Run and write results to JSON ---
    result = create_recipe_subset(ingredients_input, args.individual, args.weak)

    output_path: Path = args.output
    output_path.write_text(
        json.dumps({"recipes": result}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Available recipes ({len(result)}) written to {output_path}:")

