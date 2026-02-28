# hackapizza — MCP Reference

| | |
|---|---|
| **Server** | `hackapizza` v1.0.0 |
| **Protocol** | `2025-06-18` |
| **Endpoint** | `https://hackapizza.datapizza.tech/mcp` |
| **Generated** | `20260228T145434Z` |

## Tools

> 9 tool(s) available.

### `closed_bid`

Place a closed bid for a dish

```python
def closed_bid(bids) -> Any:
  """Place a closed bid for a dish
  Args:
    bids (list): –
  """
```

### `save_menu`

Save the menu

```python
def save_menu(items) -> Any:
  """Save the menu
  Args:
    items (list): –
  """
```

### `create_market_entry`

Create a buy or sell request into the market. The 'side' field can be either 'BUY' or 'SELL', the 'ingredient_name' field is the name of the ingredient, the 'quantity' is the quantity of the ingredient and the 'price' is the total price of the transaction

```python
def create_market_entry(side, ingredient_name, quantity, price) -> Any:
  """Create a buy or sell request into the market. The 'side' field can be either 'BUY' or 'SELL', the 'ingredient_name' field is the name of the ingredient, the 'quantity' is the quantity of the ingredient and the 'price' is the total price of the transaction
  Args:
    side (Literal['SELL', 'BUY']): –
    ingredient_name (str): [minLength=1]
    quantity (int): [>0]
    price (int): [>=0]
  """
```

### `execute_transaction`

execute the transaction in the market

```python
def execute_transaction(market_entry_id) -> Any:
  """execute the transaction in the market
  Args:
    market_entry_id (float): –
  """
```

### `delete_market_entry`

Delete a market entry by id. Only the restaurant that created the entry can delete it.

```python
def delete_market_entry(market_entry_id) -> Any:
  """Delete a market entry by id. Only the restaurant that created the entry can delete it.
  Args:
    market_entry_id (float): –
  """
```

### `prepare_dish`

Prepare a dish

```python
def prepare_dish(dish_name) -> Any:
  """Prepare a dish
  Args:
    dish_name (str): –
  """
```

### `serve_dish`

Serve a dish to a customer

```python
def serve_dish(dish_name, client_id) -> Any:
  """Serve a dish to a customer
  Args:
    dish_name (str): –
    client_id (str): –
  """
```

### `update_restaurant_is_open`

Open or close the restaurant for business. Pass is_open=true to open, is_open=false to close.

```python
def update_restaurant_is_open(is_open) -> Any:
  """Open or close the restaurant for business. Pass is_open=true to open, is_open=false to close.
  Args:
    is_open (bool): true to open the restaurant, false to close it
  """
```

### `send_message`

Send a message to another restaurant. The recipient will receive a real-time notification.

```python
def send_message(recipient_id, text) -> Any:
  """Send a message to another restaurant. The recipient will receive a real-time notification.
  Args:
    recipient_id (int): The ID of the recipient restaurant [>0]
    text (str): The message text to send [minLength=1, maxLength=1000]
  """
```
