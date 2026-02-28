## 🛰️ Hackapizza 2.0: Technical & Strategic Summary

### 1. The Core Objective

The goal is to **maximize the restaurant's final balance**. This is achieved by fulfilling customer orders while managing resources, reputation, and competitive market dynamics.

### 2. The Restaurant State

Every restaurant is defined by four live metrics:

* **Balance:** Your liquid cash (the ultimate score).
* **Inventory:** Current stock of ingredients. **Crucial:** Ingredients expire and are deleted at the end of every turn.
* **Menu:** Your active list of dishes and their set prices.
* **Reputation:** A multiplier/metric that influences whether customers choose your restaurant.

---

### 3. The Game Loop (Phase Mechanics)

A "Turn" represents one day and lasts **5–7 minutes**. Agents must react to the following phases in order:

1. **Speaking Phase:** Negotiation time. Chat with other teams to form alliances or pre-arrange trades. No system ingredients are available yet.
2. **Closed Bid Phase:** The **Blind Auction**.
* Submit bids (Ingredient, Price, Quantity).
* Highest bidders get priority.
* Supply is limited; you may receive 0 or only a fraction of your request.
* Results/Competitor prices are revealed only *after* the phase ends.


3. **Waiting Phase:** Strategy lock-in. Analyze what ingredients you actually won. Reorganize your menu and kitchen based on current stock.
4. **Serving Phase:** The "Live" phase.
* Customers arrive with requests (often ambiguous or in natural language).
* You must match requests to your recipes and check for **intolerances**.
* **Load Management:** You can manually close the restaurant during this phase to prevent reputation damage if you are overwhelmed.


5. **Stopped:** Turn ends. Ingredients expire. Results are calculated.

---

### 4. Customer Archetypes & Preferences

Your menu and pricing strategy dictate which "archetypes" will frequent your establishment:

| Archetype | Time Sensitivity | Budget | Quality Priority | Key to Success |
| --- | --- | --- | --- | --- |
| **Galactic Explorer** | High (Fast) | Low | Low | Simple, cheap, rapid dishes. |
| **Astrobaron** | Very High | High | High | High-status, expensive, elite quality. |
| **Cosmic Sage** | Low (Patient) | High | Very High | Rare ingredients, complex recipes. |
| **Orbital Family** | Low | Medium | Medium | Value-for-money, balanced menu. |

---

### 5. Procurement & The Internal Market

There are two ways to get ingredients:

1. **Federal Auction (Closed Bid):** The primary source. Competitive and risky.
2. **P2P Market:** Buy/Sell directly with other teams.
* Useful for dumping surplus about to expire.
* Useful for sniping ingredients you missed in the auction.
* **Note:** All market offers are wiped at the end of the turn.



---

### 6. Technical Requirements & Evaluation

* **Framework:** Must use `datapizza-ai`.
* **Inference:** Provided by `regolo.ai` (Models: `gpt-oss-120b`, `gpt-oss-20b`, `qwen3-vl-32b`).
* **Communication:** * **SSE (Server-Sent Events):** For real-time updates (game state, customer arrival, prep completion).
* **MCP (Model Context Protocol):** For executing actions (bidding, cooking, serving).


* **The "Golden Run":** On Sunday (10:00 - 12:00), the code is frozen. Your agent must run 100% autonomously without human intervention.

---

### 7. Risk Factors (Penalty Conditions)

* **Wasted Ingredients:** Spending money on stock you don't use is a direct loss (due to expiry).
* **Intolerances:** Serving a dish that conflicts with a customer's biological restrictions leads to **Federal Sanctions** and $0$ payment.
* **Operational Collapse:** Being open with no ingredients or a bad menu destroys reputation.
