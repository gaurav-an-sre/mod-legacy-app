<?php
session_start();
require __DIR__ . '/includes/db.php';

function json_response($value, $status = 200) {
    http_response_code($status);
    header('Content-Type: application/json');
    echo json_encode($value, JSON_UNESCAPED_SLASHES);
    exit;
}

function request_json() {
    $raw = file_get_contents('php://input');
    $value = json_decode($raw ?: '{}', true);
    return is_array($value) ? $value : [];
}

function money($cents) {
    return number_format($cents / 100, 2, '.', '');
}

function product_row($row) {
    return [
        'id' => (int) $row['id'],
        'sku' => $row['sku'],
        'name' => $row['name'],
        'description' => $row['description'],
        'price' => money((int) $row['price_cents']),
        'category' => $row['category'],
        'inventory' => (int) $row['inventory'],
    ];
}

function page($title, $body) {
    echo '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">';
    echo '<title>' . htmlspecialchars($title) . '</title><style>
    body{font-family:Arial,sans-serif;background:#f1eee8;color:#29251f;margin:0}
    header{background:#40372d;color:#fff;padding:18px 8%;display:flex;gap:24px;align-items:center}
    header a{color:#fff;text-decoration:none} main{max-width:960px;margin:32px auto;padding:0 20px}
    .card{background:#fff;border:1px solid #d7d0c5;padding:18px;margin:12px 0;box-shadow:2px 2px #d7d0c5}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px}
    .price{font-size:1.25rem;font-weight:bold;color:#8d422b} button{background:#8d422b;color:#fff;border:0;padding:9px 14px}
    input,select{padding:8px;border:1px solid #aaa} table{border-collapse:collapse;width:100%}td,th{padding:9px;border-bottom:1px solid #ddd;text-align:left}
    .muted{color:#71695f}
    </style></head><body><header><strong>Old Mill Shop</strong>
    <a href="/">Catalog</a><a href="/cart.php">Cart</a><a href="/login.php">Login</a><a href="/admin.php">Admin</a>
    </header><main>' . $body . '</main></body></html>';
}

$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);

if ($path === '/api/catalog/products') {
    $q = trim($_GET['q'] ?? '');
    $page_number = max(1, (int) ($_GET['page'] ?? 1));
    $per_page = min(20, max(1, (int) ($_GET['per_page'] ?? 20)));
    $offset = ($page_number - 1) * $per_page;
    $safe_q = $db->real_escape_string($q);
    // Query is intentionally inlined like the original application.
    $sql = "SELECT * FROM products WHERE name LIKE '%$safe_q%' OR category LIKE '%$safe_q%' ORDER BY id LIMIT $per_page OFFSET $offset";
    $rows = [];
    $result = $db->query($sql);
    while ($row = $result->fetch_assoc()) $rows[] = product_row($row);
    json_response(['page' => $page_number, 'per_page' => $per_page, 'products' => $rows]);
}

if ($path === '/api/catalog/product') {
    if (!isset($_GET['id']) || !ctype_digit((string) $_GET['id'])) json_response(['error' => 'id is required'], 400);
    $id = (int) $_GET['id'];
    $result = $db->query("SELECT * FROM products WHERE id = $id");
    $row = $result ? $result->fetch_assoc() : null;
    if (!$row) json_response(['error' => 'product not found'], 404);
    json_response(product_row($row));
}

if ($path === '/api/orders/cart') {
    $cart = $_SESSION['cart'] ?? [];
    $items = [];
    $total = 0;
    foreach ($cart as $product_id => $quantity) {
        $result = $db->query("SELECT * FROM products WHERE id = " . (int) $product_id);
        if ($row = $result->fetch_assoc()) {
            $line_total = (int) $row['price_cents'] * (int) $quantity;
            $total += $line_total;
            $items[] = ['product' => product_row($row), 'quantity' => (int) $quantity, 'line_total' => money($line_total)];
        }
    }
    json_response(['items' => $items, 'total' => money($total)]);
}

if ($path === '/api/orders/checkout' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $input = request_json();
    $product_id = (int) ($input['product_id'] ?? 0);
    $quantity = max(1, min(10, (int) ($input['quantity'] ?? 1)));
    $result = $db->query("SELECT * FROM products WHERE id = $product_id");
    $row = $result ? $result->fetch_assoc() : null;
    if (!$row) json_response(['error' => 'product not found'], 404);
    $order_id = 2000 + count($_SESSION['checkout_ids'] ?? []);
    $_SESSION['checkout_ids'][] = $order_id;
    $_SESSION['cart'] = [];
    $total = (int) $row['price_cents'] * $quantity;
    json_response(['order_id' => $order_id, 'status' => 'accepted', 'total' => money($total)], 201);
}

if ($path === '/api/users/login' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $input = request_json();
    $username = $db->real_escape_string((string) ($input['username'] ?? ''));
    $password = $db->real_escape_string((string) ($input['password'] ?? ''));
    $result = $db->query("SELECT * FROM users WHERE username = '$username' AND password = '$password'");
    $user = $result ? $result->fetch_assoc() : null;
    if (!$user) json_response(['error' => 'invalid credentials'], 401);
    $_SESSION['user_id'] = (int) $user['id'];
    json_response(['ok' => true, 'user' => ['id' => (int) $user['id'], 'username' => $user['username'], 'display_name' => $user['display_name']]]);
}

if ($path === '/api/reports/top-products') {
    $rows = [];
    $result = $db->query("SELECT p.id, p.name, SUM(oi.quantity) AS units FROM order_items oi JOIN products p ON p.id = oi.product_id GROUP BY p.id, p.name ORDER BY units DESC, p.id LIMIT 10");
    while ($row = $result->fetch_assoc()) $rows[] = ['id' => (int) $row['id'], 'name' => $row['name'], 'units' => (int) $row['units']];
    json_response(['products' => $rows]);
}

if ($path === '/' || $path === '/index.php') {
    $q = trim($_GET['q'] ?? '');
    $safe_q = $db->real_escape_string($q);
    $result = $db->query("SELECT * FROM products WHERE name LIKE '%$safe_q%' OR category LIKE '%$safe_q%' ORDER BY id");
    $body = '<h1>Catalog</h1><p class="muted">A very ordinary shop from a very old codebase.</p><form><input name="q" value="' . htmlspecialchars($q) . '" placeholder="Search products"><button>Search</button></form><div class="grid">';
    while ($row = $result->fetch_assoc()) {
        $body .= '<article class="card"><h2><a href="/product.php?id=' . $row['id'] . '">' . htmlspecialchars($row['name']) . '</a></h2><p>' . htmlspecialchars($row['description']) . '</p><p class="price">$' . money($row['price_cents']) . '</p><p class="muted">' . htmlspecialchars($row['category']) . '</p></article>';
    }
    $body .= '</div>';
    page('Old Mill Shop', $body);
    exit;
}

if ($path === '/product.php') {
    $id = (int) ($_GET['id'] ?? 0);
    $result = $db->query("SELECT * FROM products WHERE id = $id");
    $row = $result ? $result->fetch_assoc() : null;
    if (!$row) { http_response_code(404); page('Not found', '<h1>Product not found</h1>'); exit; }
    $body = '<div class="card"><h1>' . htmlspecialchars($row['name']) . '</h1><p>' . htmlspecialchars($row['description']) . '</p><p class="price">$' . money($row['price_cents']) . '</p><p>Stock: ' . (int) $row['inventory'] . '</p><form method="post" action="/cart.php"><input type="hidden" name="product_id" value="' . $id . '"><label>Quantity <input name="quantity" type="number" min="1" max="10" value="1"></label> <button>Add to cart</button></form></div>';
    page($row['name'], $body);
    exit;
}

if ($path === '/cart.php') {
    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        $id = (int) ($_POST['product_id'] ?? 0);
        $_SESSION['cart'][$id] = max(1, min(10, (int) ($_POST['quantity'] ?? 1)));
    }
    $body = '<h1>Your cart</h1><div class="card"><p>Cart items are stored in this PHP session.</p><p><a href="/checkout.php">Proceed to checkout</a></p></div>';
    page('Cart', $body);
    exit;
}

if ($path === '/checkout.php') {
    $body = '<h1>Checkout</h1><div class="card"><p>This legacy checkout accepts the cart without a payment gateway.</p><form method="post"><label>Confirm name <input name="name" required></label> <button>Place order</button></form></div>';
    if ($_SERVER['REQUEST_METHOD'] === 'POST') $body .= '<div class="card"><strong>Order accepted.</strong> Thank you, ' . htmlspecialchars($_POST['name'] ?? 'shopper') . '.</div>';
    page('Checkout', $body);
    exit;
}

if ($path === '/login.php') {
    $body = '<h1>Login</h1><div class="card"><form method="post"><p><input name="username" placeholder="username"></p><p><input name="password" type="password" placeholder="password"></p><button>Login</button></form></div>';
    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        $username = $db->real_escape_string($_POST['username'] ?? '');
        $password = $db->real_escape_string($_POST['password'] ?? '');
        $result = $db->query("SELECT * FROM users WHERE username = '$username' AND password = '$password'");
        if ($user = $result->fetch_assoc()) { $_SESSION['user_id'] = $user['id']; $body .= '<div class="card">Welcome back, ' . htmlspecialchars($user['display_name']) . '.</div>'; }
        else $body .= '<div class="card">Invalid credentials.</div>';
    }
    page('Login', $body);
    exit;
}

if ($path === '/admin.php') {
    $result = $db->query("SELECT p.name, SUM(oi.quantity) AS units FROM order_items oi JOIN products p ON p.id = oi.product_id GROUP BY p.id, p.name ORDER BY units DESC");
    $body = '<h1>Admin report</h1><table><tr><th>Product</th><th>Units sold</th></tr>';
    while ($row = $result->fetch_assoc()) $body .= '<tr><td>' . htmlspecialchars($row['name']) . '</td><td>' . (int) $row['units'] . '</td></tr>';
    $body .= '</table>';
    page('Admin report', $body);
    exit;
}

http_response_code(404);
page('Not found', '<h1>Not found</h1>');
?>
