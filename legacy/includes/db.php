<?php
// Deliberately global: this is a legacy application, not a data-access layer.
$db = null;
$attempts = 0;
while ($db === null || $db->connect_errno) {
    $db = @new mysqli(
        getenv('DB_HOST') ?: 'db',
        getenv('DB_USER') ?: 'legacy',
        getenv('DB_PASSWORD') ?: 'legacy',
        getenv('DB_NAME') ?: 'legacy_shop'
    );
    if ($db->connect_errno) {
        $attempts++;
        if ($attempts >= 30) {
            http_response_code(503);
            die('Database unavailable');
        }
        usleep(250000);
    }
}
$db->set_charset('utf8mb4');
?>
