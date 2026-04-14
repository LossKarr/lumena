# Backend Patterns — Référence Lumena

> Ce document est chargé quand l'utilisateur demande un site fullstack avec API, authentification, ou base de données. Il définit les patterns backend standardisés.

---

## 1. Architecture API REST

### 1.1 Structure de fichiers

```
api/
├── index.php          # Point d'entrée unique (front controller)
├── config.php         # Variables d'environnement & connexion DB
├── middleware/
│   ├── auth.php       # Vérification JWT
│   ├── cors.php       # Headers CORS
│   └── rate_limit.php # Limitation de requêtes
├── controllers/
│   ├── AuthController.php
│   ├── UserController.php
│   └── ...
├── models/
│   └── Database.php   # Wrapper PDO
└── utils/
    ├── jwt.php        # Génération/validation JWT
    └── validator.php  # Validation des entrées
```

### 1.2 Front Controller (index.php)

```php
<?php
header('Content-Type: application/json; charset=utf-8');

// CORS
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type, Authorization');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(204);
    exit;
}

// Routing
$route = $_GET['route'] ?? '';
$method = $_SERVER['REQUEST_METHOD'];
$input = json_decode(file_get_contents('php://input'), true) ?? [];

try {
    // Dispatch vers le bon controller
    switch ($route) {
        case 'login':    require 'controllers/AuthController.php'; break;
        case 'users':    require 'controllers/UserController.php'; break;
        default:
            http_response_code(404);
            echo json_encode(['error' => 'Route not found']);
    }
} catch (Exception $e) {
    http_response_code(500);
    echo json_encode(['error' => 'Internal server error']);
    error_log($e->getMessage());
}
```

### 1.3 Réponses standardisées

Toujours retourner du JSON avec cette structure :

```json
// Succès
{ "success": true, "data": { ... } }
{ "success": true, "data": [...], "pagination": { "page": 1, "total": 42 } }

// Erreur
{ "success": false, "error": "Message lisible par l'utilisateur" }
```

Codes HTTP à utiliser :
| Code | Usage                          |
|------|--------------------------------|
| 200  | Succès (GET, PUT)              |
| 201  | Ressource créée (POST)         |
| 204  | Succès sans contenu (DELETE)   |
| 400  | Données invalides              |
| 401  | Non authentifié                |
| 403  | Authentifié mais pas autorisé  |
| 404  | Ressource introuvable          |
| 429  | Rate limit dépassé             |
| 500  | Erreur serveur                 |

---

## 2. Authentification JWT

### 2.1 Flow

```
1. POST /api?route=login  {email, password}
2. Serveur vérifie bcrypt → retourne JWT (Header.Payload.Signature)
3. Client stocke le token dans localStorage
4. Requêtes suivantes : Authorization: Bearer <token>
5. Serveur vérifie la signature + expiration
```

### 2.2 Payload JWT

```json
{
  "user_id": 1,
  "email": "admin@example.com",
  "role": "admin",
  "iat": 1708876800,
  "exp": 1708963200
}
```

### 2.3 Implémentation simplifiée (sans bibliothèque)

```php
function generateToken($payload, $secret) {
    $header = base64url_encode(json_encode(['alg' => 'HS256', 'typ' => 'JWT']));
    $payload['iat'] = time();
    $payload['exp'] = time() + 86400; // 24h
    $payloadEncoded = base64url_encode(json_encode($payload));
    $signature = base64url_encode(
        hash_hmac('sha256', "$header.$payloadEncoded", $secret, true)
    );
    return "$header.$payloadEncoded.$signature";
}

function verifyToken($token, $secret) {
    $parts = explode('.', $token);
    if (count($parts) !== 3) return false;
    
    [$header, $payload, $signature] = $parts;
    $expectedSig = base64url_encode(
        hash_hmac('sha256', "$header.$payload", $secret, true)
    );
    
    if (!hash_equals($expectedSig, $signature)) return false;
    
    $data = json_decode(base64url_decode($payload), true);
    if ($data['exp'] < time()) return false;
    
    return $data;
}

function base64url_encode($data) {
    return rtrim(strtr(base64_encode($data), '+/', '-_'), '=');
}

function base64url_decode($data) {
    return base64_decode(strtr($data, '-_', '+/'));
}
```

### 2.4 Protection des mots de passe

```php
// Stockage
$hash = password_hash($password, PASSWORD_BCRYPT, ['cost' => 12]);

// Vérification
if (password_verify($inputPassword, $storedHash)) {
    // Authentifié
}
```

**Règles strictes :**
- Jamais stocker un mot de passe en clair
- Jamais MD5 ou SHA1 pour les mots de passe
- Toujours `password_hash()` + `password_verify()`
- Cost ≥ 10 (12 recommandé)

---

## 3. Base de données

### 3.1 Wrapper PDO

```php
class Database {
    private static $instance = null;
    private $pdo;
    
    private function __construct() {
        $dsn = 'mysql:host=' . DB_HOST . ';dbname=' . DB_NAME . ';charset=utf8mb4';
        $this->pdo = new PDO($dsn, DB_USER, DB_PASS, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES => false
        ]);
    }
    
    public static function getInstance() {
        if (self::$instance === null) {
            self::$instance = new self();
        }
        return self::$instance;
    }
    
    public function query($sql, $params = []) {
        $stmt = $this->pdo->prepare($sql);
        $stmt->execute($params);
        return $stmt;
    }
    
    public function fetchAll($sql, $params = []) {
        return $this->query($sql, $params)->fetchAll();
    }
    
    public function fetch($sql, $params = []) {
        return $this->query($sql, $params)->fetch();
    }
    
    public function lastInsertId() {
        return $this->pdo->lastInsertId();
    }
}
```

### 3.2 Schema conventions

| Règle                        | Exemple                          |
|------------------------------|----------------------------------|
| Tables en snake_case         | `activity_log`, `user_sessions`  |
| Clé primaire toujours `id`   | `INT AUTO_INCREMENT PRIMARY KEY` |
| Foreign keys nommées         | `FK_sessions_user_id`           |
| Index sur les colonnes filtrées | `INDEX idx_users_email (email)` |
| `created_at` + `updated_at`  | `TIMESTAMP DEFAULT CURRENT_TIMESTAMP` |
| Charset UTF-8                | `CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci` |

### 3.3 Requêtes préparées

**TOUJOURS** utiliser des requêtes préparées :

```php
// ✅ Correct
$user = $db->fetch("SELECT * FROM users WHERE email = ?", [$email]);

// ❌ JAMAIS
$user = $db->fetch("SELECT * FROM users WHERE email = '$email'");
```

---

## 4. CORS (Cross-Origin Resource Sharing)

### 4.1 Headers requis

```php
// En développement (permissif)
header('Access-Control-Allow-Origin: *');

// En production (restrictif)
$allowed = ['https://monsite.com', 'https://admin.monsite.com'];
$origin = $_SERVER['HTTP_ORIGIN'] ?? '';
if (in_array($origin, $allowed)) {
    header("Access-Control-Allow-Origin: $origin");
}
```

### 4.2 Preflight (OPTIONS)

```php
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    header('Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type, Authorization');
    header('Access-Control-Max-Age: 86400');
    http_response_code(204);
    exit;
}
```

---

## 5. Rate Limiting

### 5.1 Avec fichier (sans Redis)

```php
function checkRateLimit($identifier, $maxRequests = 60, $windowSeconds = 60) {
    $file = sys_get_temp_dir() . '/rate_' . md5($identifier) . '.json';
    
    $data = file_exists($file) ? json_decode(file_get_contents($file), true) : [];
    $now = time();
    
    // Nettoyer les entrées expirées
    $data = array_filter($data, function($t) use ($now, $windowSeconds) {
        return ($now - $t) < $windowSeconds;
    });
    
    if (count($data) >= $maxRequests) {
        header('Retry-After: ' . $windowSeconds);
        http_response_code(429);
        echo json_encode(['error' => 'Too many requests']);
        exit;
    }
    
    $data[] = $now;
    file_put_contents($file, json_encode(array_values($data)));
}
```

### 5.2 Limites recommandées

| Endpoint       | Limite          |
|----------------|-----------------|
| Login          | 5/minute        |
| Register       | 3/minute        |
| API générale   | 60/minute       |
| Upload         | 10/minute       |

---

## 6. Validation des entrées

### 6.1 Fonctions de validation

```php
function validateEmail($email) {
    return filter_var($email, FILTER_VALIDATE_EMAIL) !== false;
}

function validatePassword($password) {
    return strlen($password) >= 8 
        && preg_match('/[A-Z]/', $password) 
        && preg_match('/[0-9]/', $password);
}

function sanitize($input) {
    if (is_array($input)) {
        return array_map('sanitize', $input);
    }
    return htmlspecialchars(trim($input), ENT_QUOTES, 'UTF-8');
}
```

### 6.2 Règles

- **Toujours** valider côté serveur (même si le client valide aussi)
- **Toujours** `htmlspecialchars()` avant d'afficher des données utilisateur
- **Jamais** faire confiance aux données de `$_GET`, `$_POST`, `$_COOKIE`
- Utiliser `filter_var()` pour les emails, URLs, entiers

---

## 7. Gestion des fichiers uploadés

```php
function handleUpload($file, $allowedTypes, $maxSize = 5242880) {
    if ($file['error'] !== UPLOAD_ERR_OK) {
        throw new Exception('Upload failed');
    }
    if ($file['size'] > $maxSize) {
        throw new Exception('File too large (max ' . ($maxSize / 1048576) . 'MB)');
    }
    
    $finfo = finfo_open(FILEINFO_MIME_TYPE);
    $mime = finfo_file($finfo, $file['tmp_name']);
    finfo_close($finfo);
    
    if (!in_array($mime, $allowedTypes)) {
        throw new Exception('Invalid file type');
    }
    
    $ext = pathinfo($file['name'], PATHINFO_EXTENSION);
    $safeName = bin2hex(random_bytes(16)) . '.' . $ext;
    $destination = UPLOAD_DIR . '/' . $safeName;
    
    move_uploaded_file($file['tmp_name'], $destination);
    return $safeName;
}
```

---

## 8. Config d'environnement

```php
// config.php — NE JAMAIS commit ce fichier
define('DB_HOST', 'localhost');
define('DB_NAME', 'project_db');
define('DB_USER', 'root');
define('DB_PASS', '');
define('JWT_SECRET', 'change-this-to-a-random-64-char-string');
define('UPLOAD_DIR', __DIR__ . '/../uploads');
define('BASE_URL', 'http://localhost:8000');
```

**Règles :**
- Ne jamais hardcoder les credentials dans le code source
- `config.php` dans le `.gitignore`
- Fournir un `config.example.php` avec des valeurs placeholder
