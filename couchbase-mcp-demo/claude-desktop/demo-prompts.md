# Couchbase MCP Demo Commands

**Skip the Couchbase learning curve.** Manage and query data through chat instead of code. What normally takes hours, now takes minutes.

Copy-paste these into Claude Desktop:

## 1. Check Current Infrastructure
```
Show me the buckets, scopes, and collections in my Couchbase cluster, and check the cluster health.
```

## 2. Database Health Check

```
Can you check the health and running services of my Couchbase cluster?
```

## 3. Create a Collection

```
Create a "products", a "users", and a "purchases" collection in the mcp_demo scope of the mcp_demo bucket.
```

*Creates the collections used by the rest of this demo*

## 4. Seed Test Data - Products, Users, and Purchase Log

**IMPORTANT: Data operations only work on the cluster defined in your MCP config. It will not operate on a different cluster.**

```
Create 3 product documents in the products collection with IDs product::1 through product::3. Include name and price fields.

Create 3 user documents in the users collection with IDs user::1 through user::3. Include name and email.

Create a purchase log with 5 documents in the purchases collection showing user purchases. Make some products more popular than others to simulate bestsellers. Store as purchase::1 through purchase::7 with user_id and product_id fields.
```

## 5. Query Current Setup

```
What are the top 3 most purchased products based on the purchase log? Write a SQL++ query that joins purchases to products, groups by product, and shows the product details and total purchase count for each.
```

*Claude writes and runs a SQL++ GROUP BY / ORDER BY / LIMIT query - shows how far a single declarative query goes versus manual iteration*

## 6. Optimization Request

```
That query re-scans every purchase document each time. How should I restructure the data or indexing to make finding the top products faster? Would a secondary index or a materialized "product_popularity" counter document work best? Implement whichever you recommend and backfill it from the existing purchase log.
```

*The LLM suggests either a secondary/composite GSI index for the GROUP BY, or a running-counter document pattern (analogous to a Redis sorted set), explains the tradeoff, and implements it*

## 7. Test Optimized Query

```
Now show me the top 3 most popular products using the optimized approach. Compare this to the previous method.
```

*Demonstrates faster, simpler reads once the right index or counter pattern is in place*
