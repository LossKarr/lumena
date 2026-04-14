<?php
/**
 * {{PROJECT_TITLE}} — API REST
 * Point d'entrée principal
 */

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type, Authorization');

// Handle preflight
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(200);
    exit;
}

// Config
require_once __DIR__ . '/config.php';

// Router simple
$uri = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
$method = $_SERVER['REQUEST_METHOD'];

// Routes
switch (true) {
    case $uri === '/api/auth/login' && $method === 'POST':
        handleLogin();
        break;
    case $uri === '/api/auth/register' && $method === 'POST':
        handleRegister();
        break;
    case $uri === '/api/users' && $method === 'GET':
        handleGetUsers();
        break;
    case preg_match('/^\/api\/users\/(\d+)$/', $uri, $m) && $method === 'GET':
        handleGetUser((int)$m[1]);
        break;
    case preg_match('/^\/api\/users\/(\d+)$/', $uri, $m) && $method === 'PUT':
        handleUpdateUser((int)$m[1]);
        break;
    case preg_match('/^\/api\/users\/(\d+)$/', $uri, $m) && $method === 'DELETE':
        handleDeleteUser((int)$m[1]);
        break;
    default:
        jsonResponse(['success' => false, 'message' => 'Endpoint non trouvé'], 404);
}

// === HANDLERS ===

function handleLogin() {
    $data = json_decode(file_get_contents('php://input'), true);
    $email = filter_var($data['email'] ?? '', FILTER_SANITIZE_EMAIL);
    $password = $data['password'] ?? '';

    if (!$email || !$password) {
        jsonResponse(['success' => false, 'message' => 'Email et mot de passe requis'], 400);
    }

    // Simulation — remplacer par BDD réelle
    if ($email === 'admin@example.com' && $password === 'admin123') {
        $token = base64_encode(json_encode(['user_id' => 1, 'email' => $email, 'exp' => time() + 86400]));
        jsonResponse([
            'success' => true,
            'data' => [
                'token' => $token,
                'user' => ['id' => 1, 'name' => 'Administrateur', 'email' => $email, 'role' => 'admin']
            ]
        ]);
    } else {
        jsonResponse(['success' => false, 'message' => 'Identifiants invalides'], 401);
    }
}

function handleRegister() {
    $data = json_decode(file_get_contents('php://input'), true);
    $name = htmlspecialchars($data['name'] ?? '', ENT_QUOTES);
    $email = filter_var($data['email'] ?? '', FILTER_SANITIZE_EMAIL);
    $password = $data['password'] ?? '';

    if (!$name || !$email || strlen($password) < 8) {
        jsonResponse(['success' => false, 'message' => 'Tous les champs sont requis (mot de passe min 8 chars)'], 400);
    }

    $hashedPassword = password_hash($password, PASSWORD_BCRYPT);

    jsonResponse([
        'success' => true,
        'data' => ['id' => rand(100, 999), 'name' => $name, 'email' => $email],
        'message' => 'Compte créé avec succès'
    ], 201);
}

function handleGetUsers() {
    jsonResponse([
        'success' => true,
        'data' => [
            ['id' => 1, 'name' => 'Marie Dupont', 'email' => 'marie@example.com', 'role' => 'admin', 'status' => 'active'],
            ['id' => 2, 'name' => 'Jean Martin', 'email' => 'jean@example.com', 'role' => 'editor', 'status' => 'active'],
            ['id' => 3, 'name' => 'Sophie Bernard', 'email' => 'sophie@example.com', 'role' => 'user', 'status' => 'pending'],
            ['id' => 4, 'name' => 'Pierre Leroy', 'email' => 'pierre@example.com', 'role' => 'user', 'status' => 'active'],
            ['id' => 5, 'name' => 'Claire Moreau', 'email' => 'claire@example.com', 'role' => 'editor', 'status' => 'suspended'],
        ]
    ]);
}

function handleGetUser(int $id) {
    jsonResponse(['success' => true, 'data' => ['id' => $id, 'name' => 'Utilisateur ' . $id, 'email' => "user{$id}@example.com"]]);
}

function handleUpdateUser(int $id) {
    $data = json_decode(file_get_contents('php://input'), true);
    jsonResponse(['success' => true, 'data' => array_merge(['id' => $id], $data ?? []), 'message' => 'Utilisateur mis à jour']);
}

function handleDeleteUser(int $id) {
    jsonResponse(['success' => true, 'message' => "Utilisateur #{$id} supprimé"]);
}

function jsonResponse(array $data, int $code = 200) {
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
    exit;
}
