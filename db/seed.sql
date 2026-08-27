USE legacy_shop;

INSERT INTO products (id, sku, name, description, price_cents, category, inventory) VALUES
    (1, 'MUG-BLUE', 'Blue Coffee Mug', 'A sturdy blue mug for long debugging sessions.', 1299, 'home', 42),
    (2, 'NOTE-GRID', 'Grid Notebook', 'Hardcover notebook with graph paper.', 899, 'office', 100),
    (3, 'CABLE-USB', 'USB-C Cable', 'One metre braided charging cable.', 1599, 'electronics', 25),
    (4, 'STICKER-OPS', 'Ops Sticker Pack', 'Five durable stickers for laptops and monitors.', 499, 'office', 200)
ON DUPLICATE KEY UPDATE
    name = VALUES(name), description = VALUES(description), price_cents = VALUES(price_cents),
    category = VALUES(category), inventory = VALUES(inventory);

INSERT INTO users (id, username, password, display_name, is_admin) VALUES
    (1, 'alice', 'demo', 'Alice Operator', 0),
    (2, 'admin', 'admin', 'Admin User', 1)
ON DUPLICATE KEY UPDATE
    password = VALUES(password), display_name = VALUES(display_name), is_admin = VALUES(is_admin);

INSERT INTO orders (id, user_id, status, total_cents, created_at) VALUES
    (1001, 1, 'paid', 2198, '2024-01-15 09:30:00'),
    (1002, 1, 'shipped', 1599, '2024-02-20 14:15:00')
ON DUPLICATE KEY UPDATE
    status = VALUES(status), total_cents = VALUES(total_cents), created_at = VALUES(created_at);

INSERT INTO order_items (id, order_id, product_id, quantity, unit_price_cents) VALUES
    (5001, 1001, 1, 1, 1299),
    (5002, 1001, 2, 1, 899),
    (5003, 1002, 3, 1, 1599)
ON DUPLICATE KEY UPDATE
    quantity = VALUES(quantity), unit_price_cents = VALUES(unit_price_cents);
