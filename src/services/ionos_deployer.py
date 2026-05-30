"""
ionos_deployer.py — Service SFTP multi-sites pour IONOS.

Gère la connexion SFTP, l'upload, la suppression et le listing
de fichiers sur des hébergements IONOS. Stocke les credentials
de manière sécurisée dans data/ionos_sites.json (chiffré Fernet).
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import json
import os
import secrets
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

# ── Lazy imports (graceful degradation) ───────────────────────────────────

_paramiko = None


def _get_paramiko():
    global _paramiko
    if _paramiko is None:
        import paramiko
        _paramiko = paramiko
    return _paramiko


# pymysql est optionnel : si absent, la partie BDD se dégrade sans casser le SFTP.
_pymysql = None
_pymysql_tried = False


def _get_pymysql():
    """Import paresseux de pymysql. Retourne None si la dépendance est absente."""
    global _pymysql, _pymysql_tried
    if not _pymysql_tried:
        _pymysql_tried = True
        try:
            import pymysql
            _pymysql = pymysql
        except Exception:
            _pymysql = None
    return _pymysql


# Hôtes BDD IONOS internes (non résolvables/joignables depuis l'extérieur).
_IONOS_INTERNAL_DB_SUFFIXES = (".hosting-data.io", ".db.1and1.com")


class _SSHTunnel:
    """Forward 127.0.0.1:<port libre> -> (remote_host, remote_port) via SSH.

    Sert à atteindre une BDD IONOS interne (`*.hosting-data.io`), non résolvable
    depuis l'extérieur, en passant par l'hôte SFTP/SSH du site (déjà joignable
    et capable de résoudre la BDD en interne). Réutilise paramiko + les creds
    SFTP déjà stockés. Nécessite que le compte IONOS autorise le TCP forwarding.
    """

    def __init__(self, ssh_host, ssh_port, ssh_user, ssh_password,
                 remote_host, remote_port, timeout=5):
        self._args = (ssh_host, int(ssh_port or 22), ssh_user, ssh_password,
                      remote_host, int(remote_port or 3306), timeout)
        self._transport = None
        self._server = None
        self._stop = None
        self.local_port = None

    def __enter__(self):
        import socket
        import threading
        paramiko = _get_paramiko()
        (ssh_host, ssh_port, ssh_user, ssh_password,
         remote_host, remote_port, timeout) = self._args
        self._transport = paramiko.Transport((ssh_host, ssh_port))
        try:
            self._transport.banner_timeout = timeout
        except Exception:
            pass
        self._transport.connect(username=ssh_user, password=ssh_password)
        self._stop = threading.Event()
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self._server.settimeout(1.0)
        self.local_port = self._server.getsockname()[1]
        threading.Thread(
            target=self._serve, args=(remote_host, remote_port), daemon=True,
        ).start()
        return self

    def _serve(self, remote_host, remote_port):
        import socket
        import threading
        while not self._stop.is_set():
            try:
                client, addr = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle, args=(client, addr, remote_host, remote_port),
                daemon=True,
            ).start()

    def _handle(self, client, addr, remote_host, remote_port):
        import select
        try:
            chan = self._transport.open_channel(
                "direct-tcpip", (remote_host, remote_port), addr,
            )
        except Exception:
            try:
                client.close()
            except Exception:
                pass
            return
        try:
            while not self._stop.is_set():
                r, _, _ = select.select([client, chan], [], [], 1.0)
                if client in r:
                    data = client.recv(4096)
                    if not data:
                        break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(4096)
                    if not data:
                        break
                    client.sendall(data)
        except Exception:
            pass
        finally:
            for s in (chan, client):
                try:
                    s.close()
                except Exception:
                    pass

    def __exit__(self, *exc):
        if self._stop:
            self._stop.set()
        for obj in (self._server, self._transport):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass


# ── Fernet helpers ────────────────────────────────────────────────────────

_fernet_cipher = None


def _get_fernet():
    """Return a Fernet instance, creating/loading the key from data/.ionos_key."""
    global _fernet_cipher
    if _fernet_cipher is not None:
        return _fernet_cipher

    from cryptography.fernet import Fernet

    key_path = Path("data/.ionos_key")
    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        raw = key_path.read_bytes().strip()
        if len(raw) < 10:
            raise RuntimeError(
                "data/.ionos_key is corrupted. Delete it and re-add your IONOS sites."
            )
        _fernet_cipher = Fernet(raw)
    else:
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        logger.info("[IONOS] Clé de chiffrement générée → data/.ionos_key")
        _fernet_cipher = Fernet(key)

    return _fernet_cipher


def _encrypt(text: str) -> str:
    return _get_fernet().encrypt(text.encode()).decode()


def _decrypt(token: str) -> str:
    return _get_fernet().decrypt(token.encode()).decode()


def _classify_db_error(raw: str) -> str:
    """Traduit une erreur BDD technique en message utilisateur clair et actionnable.

    Ne renvoie jamais l'erreur brute : seulement un diagnostic compréhensible.
    """
    low = (raw or "").lower()
    if any(m in low for m in (
        "getaddrinfo failed", "errno 11001", "name or service not known",
        "non-existent domain", "nodename nor servname",
        "temporary failure in name resolution",
    )):
        return ("Hôte BDD introuvable côté DNS. Vérifie le nom d'hôte MySQL exact "
                "fourni par IONOS.")
    if any(m in low for m in ("timed out", "timeout", "etimedout")):
        return ("Délai de connexion dépassé : l'hôte BDD ne répond pas "
                "(injoignable ou bloqué par un pare-feu).")
    if any(m in low for m in ("access denied", "1045")):
        return "Accès refusé : vérifie l'utilisateur et le mot de passe de la BDD."
    if any(m in low for m in ("unknown database", "1049")):
        return "Base introuvable : vérifie le nom de la base de données."
    if any(m in low for m in ("connection refused", "10061", "111")):
        return "Connexion refusée par l'hôte : vérifie le host et le port."
    return "Échec de connexion à la BDD. Vérifie l'hôte, le port et les identifiants."


def _redact_db_error(msg: str, *secrets: str) -> str:
    """Rédige défensivement un message d'erreur BDD avant stockage/retour.

    - retire tout secret fourni (mot de passe déchiffré) s'il apparaît ;
    - neutralise les tokens password=... éventuels (defensive, pilotes tiers) ;
    - borne la longueur à 300 caractères.

    Objectif : qu'aucun secret ne puisse fuiter dans `last_check.error` ni dans
    une réponse API, même si une bibliothèque future loggue le mot de passe.
    """
    out = msg or ""
    for s in secrets:
        if s and len(s) >= 3:
            out = out.replace(s, "***")
    # Neutralise un éventuel "password=..." / "passwd=..." dans le texte.
    import re as _re
    out = _re.sub(r"(?i)(pass(?:word|wd)?\s*[=:]\s*)\S+", r"\1***", out)
    return out[:300]


# ── Bridge PHP (Étape 3B : squelette signé/versionné, AUCUNE opération BDD) ──

_BRIDGE_VERSION = "9"
_BRIDGE_HKDF_INFO = "lumena-ionos-bridge-v2"  # info HKDF figé (compat clé), ne PAS suffixer la version
_DB_AFFECTED_MAX_DEFAULT = 50
_DB_AFFECTED_MAX_CAP = 200
# DELETE (Étape 4.4) — plafond plus bas que le write (plus risqué).
_DB_DELETE_MAX_DEFAULT = 25
_DB_DELETE_MAX_CAP = 200
# Snapshot / rollback (Étape 4.3) — bornes.
_SNAPSHOT_MAX_ROWS = 50
_SNAPSHOT_TTL_DAYS = 7
_SNAPSHOT_MAX_PER_SITE = 100
_SNAPSHOT_MAX_FILE_BYTES = 256 * 1024  # blob Fernet au repos
# Sandbox CREATE TABLE (Étape 4.2) — préfixe FIXE, non configurable.
_SANDBOX_PREFIX = "lumena_sandbox_"
_SANDBOX_MAX_COLUMNS = 30
_SANDBOX_MAX_TABLES = 10
# Types whitelistés (DECIMAL exclu en 4.2). VARCHAR exige length 1..255.
_SANDBOX_TYPES = frozenset({"INT", "BIGINT", "VARCHAR", "TEXT", "DATETIME", "DATE", "BOOLEAN", "TINYINT"})
_BRIDGE_TS_WINDOW = 60  # secondes (anti-rejeu + horloge)

# Bridge v2 : HTTPS strict + POST + HMAC(op|body|ts|nonce) + fenêtre ±60s +
# nonce STRICT anti-replay (hors docroot, flock) + op db_ping (connect+ping only,
# creds chiffrés AES-256-GCM fournis par requête, déchiffrés en mémoire).
# v3 : + lecture READ-ONLY structurée (db_tables/db_describe/db_select).
# v4 : + WRITE contrôlé db_write (INSERT/UPDATE seulement).
# v5 : + CREATE TABLE sandbox db_create_table.
# v6 : + SNAPSHOT chiffré avant UPDATE (image-avant capturée DANS la transaction,
# chiffrée AES-GCM, renvoyée jamais en clair ; write REFUSÉ si snapshot impossible).
# v7 : + DELETE contrôlé db_delete (op DISTINCTE, WHERE obligatoire, snapshot op:'delete'
# obligatoire avant suppression → DELETE REFUSÉ si snapshot impossible ; plafond 25/200).
# v8 : + DROP sandbox db_drop_sandbox_table (op DISTINCTE : préfixe lumena_sandbox_ imposé,
# table DOIT être VIDE, sinon refus ; aucun DROP générique, aucun SQL libre).
# v9 : + CLEAR sandbox db_clear_sandbox_table (op DISTINCTE : vide une table lumena_sandbox_*,
# snapshot op:'delete' avant suppression, plafond ; aucun DELETE générique exposé).
# AUCUN ALTER/TRUNCATE/RENAME/SQL libre ; DROP UNIQUEMENT via db_drop_sandbox_table.
# {{SECRET}}/{{VERSION}} remplacés.
_BRIDGE_PHP_TEMPLATE = r"""<?php
// Lumena IONOS DB bridge v{{VERSION}} — read-only + write + create/drop/clear sandbox + snapshot + delete. NE PAS éditer.
declare(strict_types=1);
header('Content-Type: application/json');
$BRIDGE_VERSION = '{{VERSION}}';
$BRIDGE_SECRET  = '{{SECRET}}';
$WINDOW = 60;
$MAX_ROWS = 1000;
$MAX_CELL = 2048;   // octets par cellule
$MAX_OUT  = 8192;   // octets totaux de données

function deny(int $code, string $err) {
    http_response_code($code);
    echo json_encode(['ok' => false, 'error' => $err]);   // jamais de SQL ni de secret
    exit;
}
function valid_ident($s) {
    return is_string($s) && preg_match('/^[A-Za-z0-9_]+$/', $s) === 1;
}
function clamp_limit($n) {
    $n = (int)$n;            // cast strict : jamais une valeur brute
    if ($n < 1) { $n = 1; }
    if ($n > 1000) { $n = 1000; }
    return $n;
}
function bridge_decrypt_creds($body, $op, $ts, $nonce, $secret) {
    if (!is_array($body) || !is_array($body['creds'] ?? null)) { deny(400, 'bad_body'); }
    $key = hash_hkdf('sha256', $secret, 32, '{{HKDF_INFO}}', '');
    $iv  = base64_decode($body['creds']['iv'] ?? '', true);
    $ct  = base64_decode($body['creds']['ct'] ?? '', true);
    $tag = base64_decode($body['creds']['tag'] ?? '', true);
    if ($iv === false || $ct === false || $tag === false) { deny(400, 'bad_enc'); }
    $aad   = $op . '|' . $ts . '|' . $nonce;
    $plain = openssl_decrypt($ct, 'aes-256-gcm', $key, OPENSSL_RAW_DATA, $iv, $tag, $aad);
    if ($plain === false) { deny(401, 'decrypt_failed'); }
    $creds = json_decode($plain, true);
    if (!is_array($creds)) { deny(400, 'bad_creds'); }
    return $creds;
}
function bridge_connect($creds) {
    mysqli_report(MYSQLI_REPORT_OFF);
    return @new mysqli(
        (string)($creds['host'] ?? ''), (string)($creds['user'] ?? ''),
        (string)($creds['password'] ?? ''), (string)($creds['name'] ?? ''),
        (int)($creds['port'] ?? 3306)
    );
}
// bind_param via REFERENCES (compatible toutes versions mysqli, pas de spread).
function bind_params_ref($stmt, $types, $params) {
    $refs = array();
    $refs[] = $types;
    for ($i = 0; $i < count($params); $i++) { $refs[] = &$params[$i]; }
    return call_user_func_array(array($stmt, 'bind_param'), $refs);
}
// Chiffre un texte en AES-256-GCM (format {iv,ct,tag} base64), compat _open_creds Python.
function aes_seal($key, $plaintext, $aad) {
    $iv = random_bytes(12);
    $tag = '';
    $ct = openssl_encrypt($plaintext, 'aes-256-gcm', $key, OPENSSL_RAW_DATA, $iv, $tag, $aad, 16);
    if ($ct === false) { return null; }
    return array('iv'=>base64_encode($iv), 'ct'=>base64_encode($ct), 'tag'=>base64_encode($tag));
}
// Tronque une ligne (cellule max + total max). Retourne [cells, total, trunc, stop].
function clip_row($r, $max_cell, $max_out, $total, $trunc) {
    $cells = array();
    foreach ($r as $cell) {
        if ($cell === null) { $cells[] = null; continue; }
        $s = (string)$cell;
        if (strlen($s) > $max_cell) { $s = substr($s, 0, $max_cell); $trunc = true; }
        $cells[] = $s; $total += strlen($s);
    }
    return array($cells, $total, $trunc, ($total > $max_out));
}
// Récupère les lignes : get_result() si mysqlnd, sinon FALLBACK bind_result/fetch
// (hébergement mutualisé IONOS sans mysqlnd). Retourne [colnames, rows, trunc].
function fetch_rows_bounded($stmt, $max_cell, $max_out) {
    $colnames = array(); $rows = array(); $total = 0; $trunc = false;
    if (function_exists('mysqli_stmt_get_result')) {
        $res = $stmt->get_result();
        if ($res === false) { return array(array(), array(), false); }
        foreach ($res->fetch_fields() as $f) { $colnames[] = $f->name; }
        while ($r = $res->fetch_row()) {
            list($cells, $total, $trunc, $stop) = clip_row($r, $max_cell, $max_out, $total, $trunc);
            $rows[] = $cells;
            if ($stop) { $trunc = true; break; }
        }
        return array($colnames, $rows, $trunc);
    }
    // Fallback sans mysqlnd : metadata + bind_result dynamique.
    $meta = $stmt->result_metadata();
    if ($meta === false) { return array(array(), array(), false); }
    $rowvars = array(); $bind = array();
    foreach ($meta->fetch_fields() as $f) {
        $colnames[] = $f->name; $rowvars[$f->name] = null; $bind[] = &$rowvars[$f->name];
    }
    call_user_func_array(array($stmt, 'bind_result'), $bind);
    while ($stmt->fetch()) {
        $r = array();
        foreach ($colnames as $cn) { $r[] = $rowvars[$cn]; }
        list($cells, $total, $trunc, $stop) = clip_row($r, $max_cell, $max_out, $total, $trunc);
        $rows[] = $cells;
        if ($stop) { $trunc = true; break; }
    }
    return array($colnames, $rows, $trunc);
}

// 1. HTTPS strict (direct ou derrière proxy IONOS via X-Forwarded-Proto).
$https = (($_SERVER['HTTPS'] ?? '') === 'on')
      || (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https');
if (!$https) { deny(403, 'https_required'); }

// 2. POST + JSON.
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') { deny(403, 'post_required'); }
$req = json_decode(file_get_contents('php://input'), true);
if (!is_array($req)) { deny(400, 'bad_request'); }
$op    = (string)($req['op'] ?? '');
$ts    = (int)($req['ts'] ?? 0);
$nonce = (string)($req['nonce'] ?? '');
$sig   = (string)($req['sig'] ?? '');
$body  = $req['body'] ?? '';

// 3. Fenêtre temporelle.
if (abs(time() - $ts) > $WINDOW) { deny(401, 'stale'); }

// 4. Signature HMAC (constant-time). JSON_UNESCAPED_SLASHES pour matcher
//    json.dumps(separators=(',',':')) côté Python (slashes non échappés).
$payload  = $op . '|' . json_encode($body, JSON_UNESCAPED_SLASHES) . '|' . $ts . '|' . $nonce;
$expected = hash_hmac('sha256', $payload, $BRIDGE_SECRET);
if (!hash_equals($expected, $sig)) { deny(401, 'sig'); }

// 5. Nonce STRICT anti-replay (hors docroot, flock, purge, borné). Fail-safe.
$bridge_id = substr(hash('sha256', $BRIDGE_SECRET), 0, 16);
$nfile = sys_get_temp_dir() . '/lumena_nonce_' . $bridge_id . '.json';
$fp = @fopen($nfile, 'c+');
if ($fp === false) { deny(401, 'nonce_unavailable'); }
flock($fp, LOCK_EX);
$store = json_decode(stream_get_contents($fp), true);
if (!is_array($store)) { $store = []; }
$now = time();
foreach ($store as $n => $t) { if ($now - (int)$t > $WINDOW) { unset($store[$n]); } }
$seen = isset($store[$nonce]);
if (!$seen && count($store) < 5000) { $store[$nonce] = $now; }
ftruncate($fp, 0); rewind($fp); fwrite($fp, json_encode($store));
flock($fp, LOCK_UN); fclose($fp);
if ($seen) { deny(401, 'replay'); }

// 6. Dispatch.
if ($op === 'handshake') {
    echo json_encode(['ok' => true, 'version' => $BRIDGE_VERSION]);
    exit;
}
if ($op === 'db_ping') {
    if (!is_array($body) || !is_array($body['creds'] ?? null)) { deny(400, 'bad_body'); }
    $key = hash_hkdf('sha256', $BRIDGE_SECRET, 32, '{{HKDF_INFO}}', '');
    $iv  = base64_decode($body['creds']['iv'] ?? '', true);
    $ct  = base64_decode($body['creds']['ct'] ?? '', true);
    $tag = base64_decode($body['creds']['tag'] ?? '', true);
    if ($iv === false || $ct === false || $tag === false) { deny(400, 'bad_enc'); }
    $aad   = $op . '|' . $ts . '|' . $nonce;
    $plain = openssl_decrypt($ct, 'aes-256-gcm', $key, OPENSSL_RAW_DATA, $iv, $tag, $aad);
    if ($plain === false) { deny(401, 'decrypt_failed'); }
    $creds = json_decode($plain, true);
    if (!is_array($creds)) { deny(400, 'bad_creds'); }
    $start = microtime(true);
    try {
        mysqli_report(MYSQLI_REPORT_OFF);
        $db = @new mysqli(
            (string)($creds['host'] ?? ''), (string)($creds['user'] ?? ''),
            (string)($creds['password'] ?? ''), (string)($creds['name'] ?? ''),
            (int)($creds['port'] ?? 3306)
        );
        if ($db->connect_errno) { echo json_encode(['ok' => false, 'error' => 'connect']); exit; }
        $db->ping();              // connect + ping uniquement, AUCUNE requête
        $db->close();
        $lat = (int)round((microtime(true) - $start) * 1000);
        echo json_encode(['ok' => true, 'latency_ms' => $lat]);
        exit;
    } catch (Throwable $e) {
        echo json_encode(['ok' => false, 'error' => 'connect']);  // jamais de détail/secret
        exit;
    }
}
// ── Lecture READ-ONLY structurée (v3). SQL construit ici, jamais reçu brut. ──
if ($op === 'db_tables' || $op === 'db_describe' || $op === 'db_select') {
    $creds = bridge_decrypt_creds($body, $op, $ts, $nonce, $BRIDGE_SECRET);
    $db = bridge_connect($creds);
    if ($db->connect_errno) { echo json_encode(['ok' => false, 'error' => 'connect']); exit; }
    try {
        if ($op === 'db_tables') {
            $res = $db->query('SHOW TABLES');
            if ($res === false) { $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
            $tables = []; $trunc = false;
            while ($row = $res->fetch_row()) {        // 1re colonne par index (nom variable selon base)
                if (count($tables) >= $MAX_ROWS) { $trunc = true; break; }
                $tables[] = (string)$row[0];
            }
            $db->close();
            echo json_encode(['ok'=>true,'tables'=>$tables,'truncated'=>$trunc]); exit;
        }
        if ($op === 'db_describe') {
            $table = (string)($body['table'] ?? '');
            if (!valid_ident($table)) { $db->close(); deny(400, 'bad_table'); }
            $res = $db->query('DESCRIBE `' . $table . '`');
            if ($res === false) { $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
            $cols = [];
            while ($r = $res->fetch_assoc()) {
                $cols[] = ['field'=>$r['Field']??'','type'=>$r['Type']??'','null'=>$r['Null']??'',
                           'key'=>$r['Key']??'','default'=>$r['Default']??null,'extra'=>$r['Extra']??''];
            }
            $db->close();
            echo json_encode(['ok'=>true,'columns'=>$cols]); exit;
        }
        // db_select
        $table = (string)($body['table'] ?? '');
        if (!valid_ident($table)) { $db->close(); deny(400, 'bad_table'); }
        $collist = '*';
        $cols = $body['columns'] ?? null;
        if (is_array($cols) && count($cols) > 0) {
            $safe = [];
            foreach ($cols as $c) {
                if (!valid_ident((string)$c)) { $db->close(); deny(400, 'bad_column'); }
                $safe[] = '`' . $c . '`';
            }
            $collist = implode(',', $safe);
        }
        $limit = clamp_limit($body['limit'] ?? 100);   // entier sûr, jamais bind
        $sql = 'SELECT ' . $collist . ' FROM `' . $table . '`';
        $params = []; $types = '';
        $where = $body['where'] ?? null;
        if (is_array($where) && count($where) > 0) {
            $conds = [];
            foreach ($where as $col => $val) {
                if (!valid_ident((string)$col)) { $db->close(); deny(400, 'bad_column'); }
                $conds[] = '`' . $col . '` = ?';        // égalité simple, valeur prepared
                $params[] = (string)$val; $types .= 's';
            }
            $sql .= ' WHERE ' . implode(' AND ', $conds);
        }
        $sql .= ' LIMIT ' . $limit;                     // entier clampé interpolé, jamais brut
        $stmt = $db->prepare($sql);
        if ($stmt === false) { $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        if (count($params) > 0) {
            if (bind_params_ref($stmt, $types, $params) === false) {   // échec de bind → stop propre
                $stmt->close(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit;
            }
        }
        if (!$stmt->execute()) { $stmt->close(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        // get_result() si mysqlnd, sinon fallback bind_result/fetch (IONOS mutualisé).
        list($colnames, $rows, $trunc) = fetch_rows_bounded($stmt, $MAX_CELL, $MAX_OUT);
        $stmt->close(); $db->close();
        echo json_encode(['ok'=>true,'columns'=>$colnames,'rows'=>$rows,'count'=>count($rows),'truncated'=>$trunc]); exit;
    } catch (Throwable $e) {
        echo json_encode(['ok'=>false,'error'=>'db_error']); exit;   // jamais le SQL
    }
}
// ── WRITE contrôlé (v4) : INSERT/UPDATE uniquement. SQL construit ici. ──
// Pas de DELETE/DDL/multi-statements. Transaction + rollback. Snapshot compté
// (jamais renvoyé). UPDATE sans WHERE interdit. affected_rows plafonné.
if ($op === 'db_write') {
    $creds = bridge_decrypt_creds($body, $op, $ts, $nonce, $BRIDGE_SECRET);
    $wop = strtolower((string)($body['wop'] ?? ''));   // 'insert' | 'update'
    if ($wop !== 'insert' && $wop !== 'update') { deny(400, 'bad_op'); }
    $table = (string)($body['table'] ?? '');
    if (!valid_ident($table)) { deny(400, 'bad_table'); }
    $values = $body['values'] ?? null;
    if (!is_array($values) || count($values) === 0) { deny(400, 'bad_values'); }
    foreach ($values as $c => $v) { if (!valid_ident((string)$c)) { deny(400, 'bad_column'); } }
    $where = $body['where'] ?? null;
    if ($wop === 'update') {
        if (!is_array($where) || count($where) === 0) { deny(400, 'missing_where'); }
        foreach ($where as $c => $v) { if (!valid_ident((string)$c)) { deny(400, 'bad_column'); } }
    }
    $cap = (int)($body['affected_max'] ?? 50);
    if ($cap < 1) { $cap = 1; } if ($cap > 200) { $cap = 200; }
    $db = bridge_connect($creds);
    if ($db->connect_errno) { echo json_encode(['ok'=>false,'error'=>'connect']); exit; }
    try {
        $db->begin_transaction();
        $snapshot_count = 0; $snapshot_enc = null;
        if ($wop === 'insert') {
            $cols = array(); $ph = array(); $params = array(); $types = '';
            foreach ($values as $c => $v) { $cols[]='`'.$c.'`'; $ph[]='?'; $params[]=(string)$v; $types.='s'; }
            $sql = 'INSERT INTO `'.$table.'` ('.implode(',', $cols).') VALUES ('.implode(',', $ph).')';
        } else { // update : SNAPSHOT image-avant (v6) DANS la transaction, puis UPDATE.
            $wsql = array(); $wp = array(); $wt = '';
            foreach ($where as $c => $v) { $wsql[]='`'.$c.'`=?'; $wp[]=(string)$v; $wt.='s'; }
            $cst = $db->prepare('SELECT COUNT(*) FROM `'.$table.'` WHERE '.implode(' AND ', $wsql));
            if ($cst === false) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
            if (bind_params_ref($cst, $wt, $wp) === false) { $cst->close(); $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
            $cst->execute(); $cst->bind_result($scnt);
            if ($cst->fetch()) { $snapshot_count = (int)$scnt; }
            $cst->close();
            if ($snapshot_count > $cap) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'too_many_rows','snapshot_count'=>$snapshot_count]); exit; }

            // ── SNAPSHOT obligatoire (v6) : si capture impossible → write REFUSÉ. ──
            // PK obligatoire pour un restore fiable.
            $pk_col = null;
            $pkres = $db->query("SHOW KEYS FROM `".$table."` WHERE Key_name = 'PRIMARY'");
            if ($pkres !== false) { $pr = $pkres->fetch_assoc(); if ($pr) { $pk_col = $pr['Column_name']; } }
            if ($pk_col === null) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'snapshot_no_pk']); exit; }
            // Capture image-avant : SELECT * borné par cap, dans la transaction.
            $sst = $db->prepare('SELECT * FROM `'.$table.'` WHERE '.implode(' AND ', $wsql).' LIMIT '.$cap);
            if ($sst === false) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
            if (bind_params_ref($sst, $wt, $wp) === false) { $sst->close(); $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
            if (!$sst->execute()) { $sst->close(); $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
            list($scols, $srows, $strunc) = fetch_rows_bounded($sst, 65536, 200000);  // cellule 64Ko, total ~200Ko
            $sst->close();
            if ($strunc) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'snapshot_too_large']); exit; }
            $snap_rows = array();
            foreach ($srows as $r) { $obj = array(); foreach ($scols as $i => $cn) { $obj[$cn] = $r[$i]; } $snap_rows[] = $obj; }
            $snap_json = json_encode(array('table'=>$table,'pk_col'=>$pk_col,'op'=>'update','rows'=>$snap_rows), JSON_UNESCAPED_SLASHES);
            if ($snap_json === false || strlen($snap_json) > 200000) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'snapshot_too_large']); exit; }
            $skey = hash_hkdf('sha256', $BRIDGE_SECRET, 32, '{{HKDF_INFO}}', '');
            $snapshot_enc = aes_seal($skey, $snap_json, $op.'|'.$ts.'|'.$nonce);
            if ($snapshot_enc === null) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'snapshot_failed']); exit; }

            $set = array(); $params = array(); $types = '';
            foreach ($values as $c => $v) { $set[]='`'.$c.'`=?'; $params[]=(string)$v; $types.='s'; }
            $sql = 'UPDATE `'.$table.'` SET '.implode(',', $set).' WHERE '.implode(' AND ', $wsql);
            foreach ($where as $c => $v) { $params[]=(string)$v; $types.='s'; }  // params WHERE après SET
        }
        $stmt = $db->prepare($sql);
        if ($stmt === false) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        if (count($params) > 0 && bind_params_ref($stmt, $types, $params) === false) {
            $stmt->close(); $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit;
        }
        if (!$stmt->execute()) { $stmt->close(); $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        $affected = $stmt->affected_rows;
        $stmt->close();
        if ($affected > $cap) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'too_many_rows','affected'=>$affected]); exit; }
        $db->commit(); $db->close();
        $warning = ($wop === 'update' && $affected === 0) ? 'no_rows_modified' : '';
        $resp = array('ok'=>true,'op'=>$wop,'affected'=>$affected,'snapshot_count'=>$snapshot_count,'warning'=>$warning);
        if ($snapshot_enc !== null) { $resp['snapshot_enc'] = $snapshot_enc; }  // jamais en clair
        echo json_encode($resp); exit;
    } catch (Throwable $e) {
        try { $db->rollback(); } catch (Throwable $e2) {}
        $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit;   // jamais le SQL
    }
}
// ── DELETE contrôlé (v7) : op DISTINCTE de db_write. WHERE obligatoire. ──
// SNAPSHOT obligatoire AVANT suppression (op:'delete') → si impossible, DELETE REFUSÉ.
// Plafond plus bas que le write. AUCUN DROP/ALTER/TRUNCATE/RENAME/SQL libre.
if ($op === 'db_delete') {
    $creds = bridge_decrypt_creds($body, $op, $ts, $nonce, $BRIDGE_SECRET);
    $table = (string)($body['table'] ?? '');
    if (!valid_ident($table)) { deny(400, 'bad_table'); }
    $where = $body['where'] ?? null;
    if (!is_array($where) || count($where) === 0) { deny(400, 'missing_where'); }   // jamais de DELETE total
    foreach ($where as $c => $v) { if (!valid_ident((string)$c)) { deny(400, 'bad_column'); } }
    $cap = (int)($body['affected_max'] ?? 25);
    if ($cap < 1) { $cap = 1; } if ($cap > 200) { $cap = 200; }
    $db = bridge_connect($creds);
    if ($db->connect_errno) { echo json_encode(['ok'=>false,'error'=>'connect']); exit; }
    try {
        $db->begin_transaction();
        $wsql = array(); $wp = array(); $wt = '';
        foreach ($where as $c => $v) { $wsql[]='`'.$c.'`=?'; $wp[]=(string)$v; $wt.='s'; }
        $cst = $db->prepare('SELECT COUNT(*) FROM `'.$table.'` WHERE '.implode(' AND ', $wsql));
        if ($cst === false) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        if (bind_params_ref($cst, $wt, $wp) === false) { $cst->close(); $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        $cst->execute(); $cst->bind_result($scnt); $snapshot_count = 0;
        if ($cst->fetch()) { $snapshot_count = (int)$scnt; }
        $cst->close();
        if ($snapshot_count > $cap) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'too_many_rows','snapshot_count'=>$snapshot_count]); exit; }

        // ── SNAPSHOT obligatoire (op:'delete') : si capture impossible → DELETE REFUSÉ. ──
        $pk_col = null;
        $pkres = $db->query("SHOW KEYS FROM `".$table."` WHERE Key_name = 'PRIMARY'");
        if ($pkres !== false) { $pr = $pkres->fetch_assoc(); if ($pr) { $pk_col = $pr['Column_name']; } }
        if ($pk_col === null) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'snapshot_no_pk']); exit; }
        $sst = $db->prepare('SELECT * FROM `'.$table.'` WHERE '.implode(' AND ', $wsql).' LIMIT '.$cap);
        if ($sst === false) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        if (bind_params_ref($sst, $wt, $wp) === false) { $sst->close(); $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        if (!$sst->execute()) { $sst->close(); $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        list($scols, $srows, $strunc) = fetch_rows_bounded($sst, 65536, 200000);  // cellule 64Ko, total ~200Ko
        $sst->close();
        if ($strunc) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'snapshot_too_large']); exit; }
        $snap_rows = array();
        foreach ($srows as $r) { $obj = array(); foreach ($scols as $i => $cn) { $obj[$cn] = $r[$i]; } $snap_rows[] = $obj; }
        $snap_json = json_encode(array('table'=>$table,'pk_col'=>$pk_col,'op'=>'delete','rows'=>$snap_rows), JSON_UNESCAPED_SLASHES);
        if ($snap_json === false || strlen($snap_json) > 200000) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'snapshot_too_large']); exit; }
        $skey = hash_hkdf('sha256', $BRIDGE_SECRET, 32, '{{HKDF_INFO}}', '');
        $snapshot_enc = aes_seal($skey, $snap_json, $op.'|'.$ts.'|'.$nonce);
        if ($snapshot_enc === null) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'snapshot_failed']); exit; }

        $sql = 'DELETE FROM `'.$table.'` WHERE '.implode(' AND ', $wsql);
        $stmt = $db->prepare($sql);
        if ($stmt === false) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        if (bind_params_ref($stmt, $wt, $wp) === false) { $stmt->close(); $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        if (!$stmt->execute()) { $stmt->close(); $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        $affected = $stmt->affected_rows;
        $stmt->close();
        if ($affected > $cap) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'too_many_rows','affected'=>$affected]); exit; }
        $db->commit(); $db->close();
        $resp = array('ok'=>true,'op'=>'delete','affected'=>$affected,'snapshot_count'=>$snapshot_count,'warning'=>($affected===0?'no_rows_deleted':''));
        $resp['snapshot_enc'] = $snapshot_enc;  // jamais en clair
        echo json_encode($resp); exit;
    } catch (Throwable $e) {
        try { $db->rollback(); } catch (Throwable $e2) {}
        $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit;   // jamais le SQL
    }
}
// ── CREATE TABLE sandbox (v5) : préfixe fixe + types whitelistés. ──
// AUCUN DROP/ALTER/TRUNCATE/RENAME/SQL libre. IF NOT EXISTS (no overwrite).
// ENGINE=InnoDB DEFAULT CHARSET=utf8mb4. DDL auto-commit (pas de transaction).
if ($op === 'db_create_table') {
    $creds = bridge_decrypt_creds($body, $op, $ts, $nonce, $BRIDGE_SECRET);
    $name = (string)($body['name'] ?? '');
    // préfixe FIXE imposé + identifiant strict
    if (strpos($name, 'lumena_sandbox_') !== 0 || !preg_match('/^[a-z0-9_]{1,64}$/', $name)) { deny(400, 'bad_prefix'); }
    $columns = $body['columns'] ?? null;
    if (!is_array($columns) || count($columns) === 0 || count($columns) > 30) { deny(400, 'bad_columns'); }
    $allowed_types = array('INT','BIGINT','VARCHAR','TEXT','DATETIME','DATE','BOOLEAN','TINYINT');
    $defs = array('`id` INT AUTO_INCREMENT PRIMARY KEY');  // PK auto, l'utilisateur ne la fournit pas
    $seen = array();
    foreach ($columns as $col) {
        if (!is_array($col)) { deny(400, 'bad_columns'); }
        $cname = (string)($col['name'] ?? '');
        $ctype = strtoupper((string)($col['type'] ?? ''));
        if (!valid_ident($cname) || strtolower($cname) === 'id') { deny(400, 'bad_column'); }
        if (isset($seen[$cname])) { deny(400, 'bad_column'); }
        $seen[$cname] = true;
        if (!in_array($ctype, $allowed_types, true)) { deny(400, 'bad_type'); }
        $sqltype = '';
        if ($ctype === 'VARCHAR') {
            $len = (int)($col['length'] ?? 0);
            if ($len < 1 || $len > 255) { deny(400, 'bad_length'); }
            $sqltype = 'VARCHAR('.$len.')';
        } elseif ($ctype === 'BOOLEAN') {
            $sqltype = 'TINYINT(1)';
        } elseif ($ctype === 'TINYINT') {
            $sqltype = 'TINYINT(1)';
        } else {
            $sqltype = $ctype;  // INT/BIGINT/TEXT/DATETIME/DATE
        }
        $def = '`'.$cname.'` '.$sqltype;
        $def .= (isset($col['nullable']) && $col['nullable'] === false) ? ' NOT NULL' : ' NULL';
        if (array_key_exists('default', $col) && $col['default'] !== null && $col['default'] !== '') {
            $dv = (string)$col['default'];
            // charset borné SANS quote ni backslash → littéral sûr en single-quote
            if (strlen($dv) > 64 || !preg_match('/^[A-Za-z0-9_:.\\- ]+$/', $dv)) { deny(400, 'bad_default'); }
            $def .= " DEFAULT '".$dv."'";
        }
        $defs[] = $def;
    }
    $db = bridge_connect($creds);
    if ($db->connect_errno) { echo json_encode(['ok'=>false,'error'=>'connect']); exit; }
    try {
        // Tables existantes de la base courante (via information_schema, sans LIKE).
        $existing = array();
        $ires = $db->query('SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()');
        if ($ires !== false) {
            while ($row = $ires->fetch_row()) { $existing[] = (string)$row[0]; }
        }
        $sandbox_count = 0; $existed = false;
        foreach ($existing as $tn) {
            if (strpos($tn, 'lumena_sandbox_') === 0) { $sandbox_count++; }
            if ($tn === $name) { $existed = true; }
        }
        if (!$existed && $sandbox_count >= 10) { $db->close(); echo json_encode(['ok'=>false,'error'=>'too_many_sandbox_tables']); exit; }
        $sql = 'CREATE TABLE IF NOT EXISTS `'.$name.'` ('.implode(', ', $defs).') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4';
        if ($db->query($sql) === false) { $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        $db->close();
        echo json_encode(['ok'=>true,'table'=>$name,'created'=>!$existed]); exit;
    } catch (Throwable $e) {
        $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit;   // jamais le SQL
    }
}
// ── DROP sandbox (v8) : op DISTINCTE. DROP UNIQUEMENT sur lumena_sandbox_* VIDE. ──
// Aucun DROP générique, aucun ALTER/TRUNCATE/RENAME, aucun SQL libre.
if ($op === 'db_drop_sandbox_table') {
    $creds = bridge_decrypt_creds($body, $op, $ts, $nonce, $BRIDGE_SECRET);
    $name = (string)($body['name'] ?? '');
    // Préfixe FIXE imposé + identifiant strict (jamais users/sessions/… ni table métier).
    if (strpos($name, 'lumena_sandbox_') !== 0 || !preg_match('/^[a-z0-9_]{1,64}$/', $name)) { deny(400, 'bad_prefix'); }
    $db = bridge_connect($creds);
    if ($db->connect_errno) { echo json_encode(['ok'=>false,'error'=>'connect']); exit; }
    try {
        // La table existe-t-elle dans la base courante ?
        $est = $db->prepare("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = ?");
        if ($est === false) { $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        if (bind_params_ref($est, 's', array($name)) === false) { $est->close(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        $est->execute(); $est->bind_result($texists); $exists = 0;
        if ($est->fetch()) { $exists = (int)$texists; }
        $est->close();
        if ($exists === 0) { $db->close(); echo json_encode(['ok'=>false,'error'=>'not_found']); exit; }
        // La table DOIT être vide (sécurité : pas de perte de données silencieuse).
        $cst = $db->query('SELECT COUNT(*) AS c FROM `'.$name.'`');  // nom whitelisté ci-dessus
        if ($cst === false) { $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        $crow = $cst->fetch_assoc(); $rowcount = $crow ? (int)$crow['c'] : 0;
        if ($rowcount > 0) { $db->close(); echo json_encode(['ok'=>false,'error'=>'table_not_empty','rows'=>$rowcount]); exit; }
        // DROP de la seule table sandbox vide.
        if ($db->query('DROP TABLE `'.$name.'`') === false) { $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        $db->close();
        echo json_encode(['ok'=>true,'op'=>'drop_sandbox','table'=>$name,'dropped'=>true]); exit;
    } catch (Throwable $e) {
        $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit;   // jamais le SQL
    }
}
// ── CLEAR sandbox (v9) : vide une table lumena_sandbox_* (DELETE total contrôlé). ──
// Snapshot op:'delete' AVANT (restaurable), plafond, op DISTINCTE. Aucun DELETE générique.
if ($op === 'db_clear_sandbox_table') {
    $creds = bridge_decrypt_creds($body, $op, $ts, $nonce, $BRIDGE_SECRET);
    $name = (string)($body['name'] ?? '');
    if (strpos($name, 'lumena_sandbox_') !== 0 || !preg_match('/^[a-z0-9_]{1,64}$/', $name)) { deny(400, 'bad_prefix'); }
    $cap = (int)($body['affected_max'] ?? 200);
    if ($cap < 1) { $cap = 1; } if ($cap > 200) { $cap = 200; }
    $db = bridge_connect($creds);
    if ($db->connect_errno) { echo json_encode(['ok'=>false,'error'=>'connect']); exit; }
    try {
        // Existence (base courante).
        $est = $db->prepare("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = ?");
        if ($est === false) { $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        if (bind_params_ref($est, 's', array($name)) === false) { $est->close(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        $est->execute(); $est->bind_result($texists); $exists = 0;
        if ($est->fetch()) { $exists = (int)$texists; }
        $est->close();
        if ($exists === 0) { $db->close(); echo json_encode(['ok'=>false,'error'=>'not_found']); exit; }
        $db->begin_transaction();
        $crow = $db->query('SELECT COUNT(*) AS c FROM `'.$name.'`');
        if ($crow === false) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        $cr = $crow->fetch_assoc(); $rowcount = $cr ? (int)$cr['c'] : 0;
        if ($rowcount === 0) { $db->rollback(); $db->close(); echo json_encode(['ok'=>true,'op'=>'clear_sandbox','table'=>$name,'affected'=>0,'snapshot_count'=>0,'warning'=>'already_empty']); exit; }
        if ($rowcount > $cap) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'too_many_rows','rows'=>$rowcount]); exit; }
        // SNAPSHOT op:'delete' (restaurable par ré-INSERT), comme db_delete.
        $pk_col = null;
        $pkres = $db->query("SHOW KEYS FROM `".$name."` WHERE Key_name = 'PRIMARY'");
        if ($pkres !== false) { $pr = $pkres->fetch_assoc(); if ($pr) { $pk_col = $pr['Column_name']; } }
        if ($pk_col === null) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'snapshot_no_pk']); exit; }
        $sst = $db->prepare('SELECT * FROM `'.$name.'` LIMIT '.$cap);
        if ($sst === false) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        if (!$sst->execute()) { $sst->close(); $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        list($scols, $srows, $strunc) = fetch_rows_bounded($sst, 65536, 200000);
        $sst->close();
        if ($strunc) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'snapshot_too_large']); exit; }
        $snap_rows = array();
        foreach ($srows as $r) { $obj = array(); foreach ($scols as $i => $cn) { $obj[$cn] = $r[$i]; } $snap_rows[] = $obj; }
        $snap_json = json_encode(array('table'=>$name,'pk_col'=>$pk_col,'op'=>'delete','rows'=>$snap_rows), JSON_UNESCAPED_SLASHES);
        if ($snap_json === false || strlen($snap_json) > 200000) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'snapshot_too_large']); exit; }
        $skey = hash_hkdf('sha256', $BRIDGE_SECRET, 32, '{{HKDF_INFO}}', '');
        $snapshot_enc = aes_seal($skey, $snap_json, $op.'|'.$ts.'|'.$nonce);
        if ($snapshot_enc === null) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'snapshot_failed']); exit; }
        // DELETE total de la table sandbox (op dédiée ; jamais de DELETE générique exposé).
        $del = $db->query('DELETE FROM `'.$name.'`');
        if ($del === false) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit; }
        $affected = $db->affected_rows;
        if ($affected > $cap) { $db->rollback(); $db->close(); echo json_encode(['ok'=>false,'error'=>'too_many_rows','affected'=>$affected]); exit; }
        $db->commit(); $db->close();
        echo json_encode(array('ok'=>true,'op'=>'clear_sandbox','table'=>$name,'affected'=>$affected,'snapshot_count'=>$rowcount,'snapshot_enc'=>$snapshot_enc)); exit;
    } catch (Throwable $e) {
        try { $db->rollback(); } catch (Throwable $e2) {}
        $db->close(); echo json_encode(['ok'=>false,'error'=>'db_error']); exit;   // jamais le SQL
    }
}
deny(400, 'unsupported_op');
"""

# Garde anti-listing : tout accès direct au dossier .lumena renvoie 403.
_BRIDGE_INDEX_PHP = "<?php http_response_code(403); exit;\n"


def _render_bridge_php(secret: str, version: str = _BRIDGE_VERSION) -> str:
    """Rend le contenu PHP du bridge (déterministe pour un (secret, version) donné)."""
    return (
        _BRIDGE_PHP_TEMPLATE
        .replace("{{VERSION}}", version)
        .replace("{{HKDF_INFO}}", _BRIDGE_HKDF_INFO)
        .replace("{{SECRET}}", secret)
    )


def _derive_bridge_key(secret: str) -> bytes:
    """Clé AES-256 dérivée du secret bridge (HKDF-SHA256, salt=None, info figé).

    Doit correspondre à `hash_hkdf('sha256', $secret, 32, info, '')` côté PHP.
    """
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.hashes import SHA256
    return HKDF(
        algorithm=SHA256(), length=32, salt=None,
        info=_BRIDGE_HKDF_INFO.encode("utf-8"),
    ).derive(secret.encode("utf-8"))


def _seal_creds(secret: str, creds: dict, op: str, ts: int, nonce: str) -> dict:
    """Chiffre les creds BDD en AES-256-GCM (interop openssl PHP).

    Retourne {iv, ct, tag} en base64. AAD = "op|ts|nonce" (liaison à la requête).
    Le ciphertext est SANS le tag (tag séparé, 16 octets).
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _derive_bridge_key(secret)
    iv = os.urandom(12)
    aad = f"{op}|{ts}|{nonce}".encode("utf-8")
    plaintext = json.dumps(creds, separators=(",", ":")).encode("utf-8")
    ct_tag = AESGCM(key).encrypt(iv, plaintext, aad)
    ct, tag = ct_tag[:-16], ct_tag[-16:]
    return {
        "iv": base64.b64encode(iv).decode(),
        "ct": base64.b64encode(ct).decode(),
        "tag": base64.b64encode(tag).decode(),
    }


def _open_creds(secret: str, sealed: dict, op: str, ts: int, nonce: str) -> dict:
    """Inverse de _seal_creds (utilisé pour le round-trip de test côté Python)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _derive_bridge_key(secret)
    iv = base64.b64decode(sealed["iv"])
    ct = base64.b64decode(sealed["ct"])
    tag = base64.b64decode(sealed["tag"])
    aad = f"{op}|{ts}|{nonce}".encode("utf-8")
    plaintext = AESGCM(key).decrypt(iv, ct + tag, aad)
    return json.loads(plaintext.decode("utf-8"))


def _bridge_sign(secret: str, op: str, body, ts: int, nonce: str) -> str:
    """HMAC-SHA256 sur 'op|json(body)|ts|nonce' (doit matcher le PHP).

    json.dumps(separators=(',',':'), ensure_ascii=True) ⇔ json_encode(..., JSON_UNESCAPED_SLASHES) :
    pas d'espaces, unicode échappé des deux côtés, slashes non échappés des deux côtés.
    """
    import hmac
    body_json = json.dumps(body, separators=(",", ":"), ensure_ascii=True)
    payload = f"{op}|{body_json}|{ts}|{nonce}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


# Validation read-only côté Lumena (1re des deux validations ; le bridge revalide).
import re as _re_db
_DB_IDENT_RE = _re_db.compile(r"^[A-Za-z0-9_]+$")
_DB_LIMIT_DEFAULT = 100
_DB_LIMIT_MAX = 1000


def _valid_db_identifier(name) -> bool:
    """True si `name` est un identifiant SQL sûr (table/colonne) : [A-Za-z0-9_]+."""
    return isinstance(name, str) and bool(_DB_IDENT_RE.match(name))


def _clamp_db_limit(n) -> int:
    """Clampe strictement la limite dans [1, _DB_LIMIT_MAX]."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = _DB_LIMIT_DEFAULT
    return max(1, min(n, _DB_LIMIT_MAX))


def _render_bridge_index_php() -> str:
    """Rend la garde index.php (403) déposée dans /.lumena/ (remplace .htaccess interdit)."""
    return _BRIDGE_INDEX_PHP


def _bridge_checksum(php_content: str) -> str:
    """Checksum déterministe du contenu déployé."""
    return "sha256:" + hashlib.sha256(php_content.encode("utf-8")).hexdigest()


def _write_temp_php(content: str) -> str:
    """Écrit le contenu dans un fichier temporaire .php (nom local non interdit)."""
    fd, path = tempfile.mkstemp(suffix=".php", prefix="lumena_bridge_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        raise
    return path


# ── Data models ───────────────────────────────────────────────────────────

# Files that must NEVER be uploaded (security)
_FORBIDDEN_FILES = frozenset({
    ".env", ".htpasswd", ".htaccess",
    "wp-config.php", "web.config",
})

_FORBIDDEN_EXTENSIONS = frozenset({
    ".sql", ".key", ".pem", ".crt", ".pfx",
    ".bak", ".dump", ".sqlite", ".db",
})

_SITES_PATH = Path("data/ionos_sites.json")
_BACKUPS_DIR = Path("data/ionos_backups")
_DB_AUDIT_PATH = Path("data/ionos_db_audit.jsonl")  # audit write append-only (sans valeurs/secret)
_SNAPSHOT_DIR = Path("data/ionos_db_snapshots")     # snapshots chiffrés Fernet (par site)
_SNAPSHOT_INDEX = Path("data/ionos_db_snapshots/index.jsonl")  # métadonnées NON sensibles
# Propositions ReAct INSERT/UPDATE (Étape 4.5A) — proposées par l'agent, exécutées
# par confirmation humaine. Valeurs chiffrées Fernet au repos ; index = métadonnées seules.
_PROPOSAL_DIR = Path("data/ionos_db_proposals")
_PROPOSAL_INDEX = Path("data/ionos_db_proposals/index.jsonl")


@dataclass
class DeployResult:
    success: bool
    uploaded: int = 0
    skipped: int = 0
    errors: List[str] = field(default_factory=list)
    total_bytes: int = 0
    duration_sec: float = 0.0
    dry_run: bool = False


@dataclass
class RemoteFile:
    path: str
    size: int
    is_dir: bool
    modified: Optional[str] = None


# ── Main service ──────────────────────────────────────────────────────────

class IonosDeployer:
    """Service SFTP multi-sites pour IONOS."""

    def __init__(self):
        self._sites: Dict[str, dict] = {}
        self._load_sites()

    # ── Persistence ───────────────────────────────────────────────────

    def _load_sites(self):
        if _SITES_PATH.exists():
            try:
                raw = json.loads(_SITES_PATH.read_text(encoding="utf-8"))
                self._sites = raw.get("sites", {})
            except Exception as e:
                logger.error(f"[IONOS] Erreur lecture {_SITES_PATH}: {e}")
                self._sites = {}

    def _save_sites(self):
        _SITES_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "sites": self._sites,
            "encryption_check": "lumena_ionos_v1",
        }
        tmp = _SITES_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_SITES_PATH)

    # ── Site management ───────────────────────────────────────────────

    def add_site(
        self,
        domain: str,
        host: str,
        user: str,
        password: str,
        port: int = 22,
        root: str = "/",
        label: str = "",
    ) -> dict:
        """Add or update a site configuration. Tests connection first."""
        domain = domain.strip().lower()
        if not domain:
            raise ValueError("Le domaine ne peut pas être vide.")
        if not host or not user or not password:
            raise ValueError("host, user et password sont obligatoires.")

        # Test connection before saving
        self._test_connection_sync(host, user, password, port)

        self._sites[domain] = {
            "label": label or domain,
            "host": host,
            "user": user,
            "password_encrypted": _encrypt(password),
            "port": port,
            "root": root.rstrip("/") or "/",
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "last_deploy": None,
            "deploy_count": 0,
        }
        self._save_sites()
        logger.info(f"[IONOS] Site ajouté: {domain} → {host}")
        return {"status": "ok", "domain": domain, "host": host}

    def remove_site(self, domain: str):
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        del self._sites[domain]
        self._save_sites()
        logger.info(f"[IONOS] Site supprimé: {domain}")

    def list_sites(self) -> List[dict]:
        """Return all sites WITHOUT passwords.

        Sortie consommée par la route PUBLIQUE `GET /api/ionos/sites` :
        on n'expose donc QUE le statut BDD minimal (booléen + ok du dernier
        test), jamais host/user/nom de BDD. Le détail BDD non sensible est
        réservé à `get_site` (route admin).
        """
        result = []
        for domain, info in self._sites.items():
            result.append({
                "domain": domain,
                "label": info.get("label", domain),
                "host": info.get("host", ""),
                "user": info.get("user", ""),
                "port": info.get("port", 22),
                "root": info.get("root", "/"),
                "last_deploy": info.get("last_deploy"),
                "deploy_count": info.get("deploy_count", 0),
                **self._db_status_summary(info, include_config=False),
            })
        return result

    def get_site(self, domain: str) -> Optional[dict]:
        domain = domain.strip().lower()
        info = self._sites.get(domain)
        if not info:
            return None
        return {
            "domain": domain,
            "label": info.get("label", domain),
            "host": info.get("host", ""),
            "user": info.get("user", ""),
            "port": info.get("port", 22),
            "root": info.get("root", "/"),
            "last_deploy": info.get("last_deploy"),
            "deploy_count": info.get("deploy_count", 0),
            **self._db_status_summary(info, include_config=True),
        }

    # ── BDD associée au site (Étape 1 : configuration uniquement) ─────
    # AUCUNE connexion réelle ici. Le test de connexion (pymysql) et la
    # lecture/écriture SQL arrivent dans des étapes ultérieures.

    @staticmethod
    def _db_status_summary(info: dict, *, include_config: bool) -> dict:
        """Statut BDD NON sensible. Ne renvoie JAMAIS de mot de passe.

        include_config=True (get_site / route admin) : host, name, user,
        engine, last_check.
        include_config=False (list_sites / route publique) : uniquement le
        booléen `database_configured` + l'état OK du dernier test.
        """
        db = info.get("database") or {}
        configured = bool(db.get("enabled"))
        summary: Dict[str, Any] = {"database_configured": configured}
        if not configured:
            return summary
        last_check = db.get("last_check") if isinstance(db.get("last_check"), dict) else None
        if include_config:
            summary["database_host"] = db.get("host", "")
            summary["database_name"] = db.get("name", "")
            summary["database_user"] = db.get("user", "")
            summary["database_engine"] = db.get("engine", "")
            summary["database_last_check"] = last_check
        else:
            summary["database_last_check_ok"] = (
                bool(last_check.get("ok")) if last_check else None
            )
        return summary

    def set_site_database(
        self,
        domain: str,
        host: str,
        name: str,
        user: str,
        password: str = "",
        port: int = 3306,
        label: str = "",
        description: str = "",
        engine: str = "mariadb",
        version: str = "",
    ) -> dict:
        """Configure (ou met à jour) la BDD associée à un site IONOS.

        Étape 1 — stockage de configuration UNIQUEMENT : aucune connexion
        réelle n'est établie. Le mot de passe est chiffré (Fernet) et n'est
        jamais renvoyé. Si `password` est vide et qu'une BDD est déjà
        configurée, le mot de passe existant est conservé.
        """
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        if not host or not name or not user:
            raise ValueError("host, name et user sont obligatoires pour la BDD.")

        existing = self._sites[domain].get("database") or {}
        if password:
            password_encrypted = _encrypt(password)
        elif existing.get("password_encrypted"):
            password_encrypted = existing["password_encrypted"]
        else:
            raise ValueError("password est obligatoire pour une nouvelle BDD.")

        self._sites[domain]["database"] = {
            "enabled": True,
            "label": label or existing.get("label", "") or "BDD principale",
            "description": description or existing.get("description", ""),
            "host": host,
            "port": int(port) if port else 3306,
            "name": name,
            "user": user,
            "password_encrypted": password_encrypted,
            "engine": (engine or "mariadb").lower(),
            "version": version or existing.get("version", ""),
            # Jamais initialisé ici : le test de connexion (Étape 2) le remplira.
            "last_check": existing.get("last_check"),
        }
        self._save_sites()
        logger.info(f"[IONOS] BDD configurée pour le site: {domain}")
        return {"status": "ok", "domain": domain, "database_configured": True}

    def get_site_database(self, domain: str, include_secret: bool = False) -> Optional[dict]:
        """Retourne la config BDD d'un site, SANS le mot de passe par défaut.

        `include_secret=True` renvoie en plus le mot de passe déchiffré —
        réservé à un usage interne (connexion/test en Étape 2), jamais exposé
        via API ou UI. Retourne None si aucune BDD n'est configurée.
        """
        domain = domain.strip().lower()
        info = self._sites.get(domain)
        if not info:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = info.get("database")
        if not db or not db.get("enabled"):
            return None
        result = {
            "enabled": True,
            "label": db.get("label", ""),
            "description": db.get("description", ""),
            "host": db.get("host", ""),
            "port": db.get("port", 3306),
            "name": db.get("name", ""),
            "user": db.get("user", ""),
            "engine": db.get("engine", ""),
            "version": db.get("version", ""),
            "last_check": db.get("last_check") if isinstance(db.get("last_check"), dict) else None,
        }
        if include_secret:
            enc = db.get("password_encrypted")
            result["password"] = _decrypt(enc) if enc else ""
        return result

    def clear_site_database(self, domain: str) -> dict:
        """Supprime la config BDD d'un site (laisse le site SFTP intact)."""
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        if "database" in self._sites[domain]:
            del self._sites[domain]["database"]
            self._save_sites()
            logger.info(f"[IONOS] BDD supprimée pour le site: {domain}")
        return {"status": "ok", "domain": domain, "database_configured": False}

    # ── Bridge PHP (Étape 3B : install / remove / status via SFTP) ────
    # Réutilise STRICTEMENT les primitives existantes (upload_files /
    # delete_remote / list_remote). Aucune opération BDD ici (squelette).

    async def install_database_bridge(self, domain: str) -> dict:
        """Déploie le squelette bridge signé/versionné/checksummé via SFTP.

        Pré-requis : une BDD configurée sur le site. Dépose 2 fichiers dans
        /.lumena/ : `db-<hash>.php` (bridge) et `index.php` (garde 403). Stocke
        les métadonnées bridge (secret chiffré Fernet). Aucune connexion BDD.
        Retour NON sensible (jamais le secret en clair).
        """
        domain = domain.strip().lower()
        cfg = self.get_site_database(domain)  # KeyError si site absent
        if cfg is None:
            return {"ok": False, "installed": False,
                    "error": "Aucune BDD configurée pour ce site."}

        secret = secrets.token_hex(32)
        file_hash = secrets.token_hex(8)
        version = _BRIDGE_VERSION
        php = _render_bridge_php(secret, version)
        index_php = _render_bridge_index_php()
        checksum = _bridge_checksum(php)
        # `bridge_path`/`index_path` = chemins WEB (URL), relatifs au docroot.
        # Stockés avec un "/" initial pour construire l'URL publique.
        bridge_path = f"/.lumena/db-{file_hash}.php"
        index_path = "/.lumena/index.php"
        # SFTP : on dépose RELATIVEMENT au `root` du site (sans "/" initial), pour
        # que le fichier atterrisse DANS le docroot configuré (jamais à la racine
        # SFTP absolue). Le nom du dossier docroot est arbitraire — on suit `root`.
        bridge_rel = bridge_path.lstrip("/")
        index_rel = index_path.lstrip("/")

        tmp_files: List[str] = []
        try:
            php_tmp = _write_temp_php(php)
            index_tmp = _write_temp_php(index_php)
            tmp_files = [php_tmp, index_tmp]
            result = await self.upload_files(
                domain,
                [(bridge_rel, Path(php_tmp)), (index_rel, Path(index_tmp))],
            )
        finally:
            for p in tmp_files:
                try:
                    os.remove(p)
                except OSError:
                    pass

        if not result.success or result.uploaded < 2:
            return {"ok": False, "installed": False,
                    "error": "Échec du dépôt du bridge: " + "; ".join(result.errors)}

        self._sites[domain]["database"]["bridge"] = {
            "installed": True,
            "path": bridge_path,
            "index_path": index_path,
            "version": version,
            "checksum": checksum,
            "secret_encrypted": _encrypt(secret),
            "installed_at": dt.datetime.now().isoformat(timespec="seconds"),
            "last_seen_at": None,
            "last_check": None,
        }
        self._save_sites()
        logger.info(f"[IONOS] Bridge BDD installé pour le site: {domain}")
        return {"ok": True, "installed": True, "version": version,
                "path": bridge_path, "checksum": checksum}

    async def remove_database_bridge(self, domain: str) -> dict:
        """Supprime les fichiers du bridge via SFTP et purge la config bridge.

        Le site SFTP et la config BDD restent intacts.
        """
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database") or {}
        bridge = db.get("bridge")
        if not bridge:
            return {"ok": True, "installed": False, "removed": False}

        # Suppression RELATIVE au root (mêmes chemins que l'upload).
        paths = [p.lstrip("/") for p in (bridge.get("path"), bridge.get("index_path")) if p]
        deleted = 0
        try:
            res = await self.delete_remote(domain, paths)
            deleted = res.get("deleted", 0)
        except Exception as e:
            logger.warning(f"[IONOS] Suppression bridge partielle ({domain}): {e}")
        # Purge la config bridge dans tous les cas (on cesse de le suivre).
        db.pop("bridge", None)
        self._save_sites()
        logger.info(f"[IONOS] Bridge BDD retiré pour le site: {domain}")
        return {"ok": True, "installed": False, "removed": True, "deleted": deleted}

    async def get_database_bridge_status(self, domain: str) -> dict:
        """Statut bridge NON sensible (+ détection d'orphelin). Jamais de secret."""
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database") or {}
        bridge = db.get("bridge")

        # Listing /.lumena pour détecter un orphelin (best-effort).
        # listed_ok distingue « listing réussi (même vide) » de « échec listing ».
        names: List[str] = []
        listed_ok = False
        try:
            listed = await self.list_remote(domain, ".lumena")  # relatif au root
            names = [str(getattr(f, "path", "")).rsplit("/", 1)[-1] for f in listed]
            listed_ok = True
        except Exception:
            listed_ok = False
        has_bridge_file = any(n.startswith("db-") and n.endswith(".php") for n in names)

        if not bridge:
            return {
                "installed": False,
                "orphan": "untracked_bridge_file" if (listed_ok and has_bridge_file) else None,
            }

        file_present = None
        if listed_ok:
            file_present = bridge.get("path", "").rsplit("/", 1)[-1] in names
        orphan = "config_without_file" if file_present is False else None
        return {
            "installed": True,
            "version": bridge.get("version"),
            "checksum": bridge.get("checksum"),
            "path": bridge.get("path"),
            "last_check": bridge.get("last_check"),
            "file_present": file_present,
            "orphan": orphan,
        }

    def _bridge_request(self, domain: str, secret: str, bridge_path: str,
                        op: str, body, timeout: int = 10,
                        ts: Optional[int] = None, nonce: Optional[str] = None) -> dict:
        """Requête HTTPS signée vers le bridge. Retourne le JSON parsé (+ _http_status).

        HTTPS STRICT (URL https obligatoire). Signature HMAC + ts + nonce strict.
        `ts`/`nonce` peuvent être fournis pour rester cohérents avec l'AAD d'un
        payload chiffré. Lève en cas d'échec réseau.
        """
        import httpx
        if ts is None:
            ts = int(time.time())
        if nonce is None:
            nonce = secrets.token_hex(16)
        url = f"https://{domain}{bridge_path}"
        if not url.startswith("https://"):
            raise ValueError("Bridge: HTTPS obligatoire")
        sig = _bridge_sign(secret, op, body, ts, nonce)
        payload = {"op": op, "body": body, "ts": ts, "nonce": nonce, "sig": sig}
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
        data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            data = {}
        data["_http_status"] = resp.status_code
        return data

    def _bridge_db_ping(self, domain: str, bridge: dict, cfg: dict,
                        timeout: int) -> Tuple[bool, int, str]:
        """Ping BDD via le bridge (connect+ping only). (ok, latency_ms, error).

        Les creds BDD sont chiffrés (AES-256-GCM) par requête, jamais au repos
        côté bridge. ts/nonce partagés entre l'AAD du scellage et la signature.
        """
        secret = _decrypt(bridge["secret_encrypted"])
        ts = int(time.time())
        nonce = secrets.token_hex(16)
        creds = {
            "host": cfg["host"], "port": int(cfg.get("port", 3306) or 3306),
            "user": cfg["user"], "password": cfg.get("password", ""),
            "name": cfg["name"],
        }
        body = {"creds": _seal_creds(secret, creds, "db_ping", ts, nonce)}
        start = time.perf_counter()
        try:
            data = self._bridge_request(
                domain, secret, bridge["path"], "db_ping", body, timeout,
                ts=ts, nonce=nonce,
            )
            lat = int((time.perf_counter() - start) * 1000)
            if data.get("_http_status") == 200 and data.get("ok"):
                return True, int(data.get("latency_ms", lat)), ""
            return False, lat, str(data.get("error", f"http_{data.get('_http_status')}"))
        except Exception as e:
            return False, int((time.perf_counter() - start) * 1000), str(e)

    # ── Lecture READ-ONLY structurée via bridge (Étape 3D) ────────────
    # Service-only. Lumena valide (1re passe) puis le bridge revalide et
    # construit le SQL. Aucun SQL libre, aucun write. Jamais de secret en sortie.

    def _bridge_read(self, domain: str, op: str, extra_body: Optional[dict] = None,
                     timeout: int = 15) -> dict:
        """Envoie une op de lecture au bridge et renvoie le payload (ou {ok:False,...}).

        Gère : BDD non configurée, bridge non installé, version obsolète
        (upgrade_required), erreurs réseau/HTTP. Jamais de secret/SQL en sortie.
        """
        domain = domain.strip().lower()
        cfg = self.get_site_database(domain, include_secret=True)  # KeyError si site absent
        if cfg is None:
            return {"ok": False, "error": "no_database",
                    "message": "Aucune BDD configurée pour ce site."}
        bridge = (self._sites[domain].get("database") or {}).get("bridge")
        if not bridge or not bridge.get("installed"):
            return {"ok": False, "error": "bridge_not_installed",
                    "message": "Accès BDD sécurisé non installé. Active-le d'abord."}
        if bridge.get("version") != _BRIDGE_VERSION:
            return {"ok": False, "upgrade_required": True, "error": "upgrade_required",
                    "message": "Accès sécurisé à mettre à jour : réinstalle l'accès BDD sécurisé."}

        secret = _decrypt(bridge["secret_encrypted"])
        ts = int(time.time())
        nonce = secrets.token_hex(16)
        creds = {
            "host": cfg["host"], "port": int(cfg.get("port", 3306) or 3306),
            "user": cfg["user"], "password": cfg.get("password", ""),
            "name": cfg["name"],
        }
        body = {"creds": _seal_creds(secret, creds, op, ts, nonce)}
        if extra_body:
            body.update(extra_body)
        try:
            data = self._bridge_request(domain, secret, bridge["path"], op, body,
                                        timeout, ts=ts, nonce=nonce)
        except Exception:
            return {"ok": False, "error": "request_failed",
                    "message": "Connexion au bridge échouée."}
        if data.get("_http_status") != 200 or not data.get("ok"):
            return {"ok": False, "error": str(data.get("error", f"http_{data.get('_http_status')}")),
                    "message": "Lecture BDD refusée par le bridge."}
        data.pop("_http_status", None)
        return data

    def db_list_tables(self, domain: str) -> dict:
        """Liste les tables de la BDD (read-only, via bridge)."""
        return self._bridge_read(domain, "db_tables")

    def db_describe_table(self, domain: str, table: str) -> dict:
        """Décrit les colonnes d'une table (read-only, via bridge)."""
        if not _valid_db_identifier(table):
            return {"ok": False, "error": "bad_table",
                    "message": "Nom de table invalide."}
        return self._bridge_read(domain, "db_describe", {"table": table})

    def db_select(self, domain: str, table: str, columns: Optional[list] = None,
                  where: Optional[dict] = None, limit: int = _DB_LIMIT_DEFAULT) -> dict:
        """SELECT borné read-only (via bridge). Validation Lumena AVANT envoi.

        - table/colonnes : identifiants whitelistés ([A-Za-z0-9_]+) ;
        - where : égalité simple uniquement (clés = colonnes validées) ;
        - limit : clampé [1, 1000] ; le bridge revalide et impose le LIMIT.
        """
        if not _valid_db_identifier(table):
            return {"ok": False, "error": "bad_table", "message": "Nom de table invalide."}
        extra: dict = {"table": table, "limit": _clamp_db_limit(limit)}
        if columns is not None:
            if not isinstance(columns, list) or not all(_valid_db_identifier(c) for c in columns):
                return {"ok": False, "error": "bad_column", "message": "Colonne invalide."}
            extra["columns"] = columns
        if where is not None:
            if not isinstance(where, dict) or not all(_valid_db_identifier(k) for k in where):
                return {"ok": False, "error": "bad_column", "message": "Filtre invalide."}
            extra["where"] = {k: ("" if v is None else str(v)) for k, v in where.items()}
        return self._bridge_read(domain, "db_select", extra)

    # ── WRITE contrôlé (Étape 4.1 : INSERT/UPDATE uniquement, UI-only) ────
    # write_enabled OFF par défaut + allowlist par site. confirm obligatoire.
    # Transaction côté bridge. Snapshot compté, jamais exposé. DELETE interdit.

    def set_site_write_config(self, domain: str, enabled: bool, tables: list) -> dict:
        """Active/désactive l'écriture et fixe l'allowlist des tables writables."""
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database")
        if not isinstance(db, dict):
            raise ValueError("Aucune BDD configurée pour ce site.")
        clean_tables = [t for t in (tables or []) if _valid_db_identifier(t)]
        db["write_enabled"] = bool(enabled)
        db["write_tables"] = clean_tables
        self._save_sites()
        logger.info("[IONOS DB WRITE] config site={} enabled={} tables={}", domain, bool(enabled), len(clean_tables))
        return {"ok": True, "enabled": bool(enabled), "tables": clean_tables}

    def get_site_write_config(self, domain: str) -> dict:
        """Config write non sensible : {enabled, tables}."""
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database") or {}
        return {"enabled": bool(db.get("write_enabled")), "tables": list(db.get("write_tables") or [])}

    def set_site_delete_config(self, domain: str, enabled: bool, tables: list) -> dict:
        """Active/désactive le DELETE et fixe l'allowlist SÉPARÉE (Étape 4.4).

        Indépendant de write_enabled/restore_enabled : activer l'écriture ou la
        restauration n'active jamais le DELETE.
        """
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database")
        if not isinstance(db, dict):
            raise ValueError("Aucune BDD configurée pour ce site.")
        clean_tables = [t for t in (tables or []) if _valid_db_identifier(t)]
        db["delete_enabled"] = bool(enabled)
        db["delete_tables"] = clean_tables
        self._save_sites()
        logger.info("[IONOS DB DELETE] config site={} enabled={} tables={}", domain, bool(enabled), len(clean_tables))
        return {"ok": True, "enabled": bool(enabled), "tables": clean_tables}

    def get_site_delete_config(self, domain: str) -> dict:
        """Config delete non sensible : {enabled, tables}. OFF + vide par défaut."""
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database") or {}
        return {"enabled": bool(db.get("delete_enabled")), "tables": list(db.get("delete_tables") or [])}

    # ── Propositions ReAct INSERT/UPDATE (Étape 4.5A) ─────────────────
    # ReAct PROPOSE, l'humain EXÉCUTE. Le handler ne peut jamais exécuter ni
    # poser confirm=true. Valeurs chiffrées Fernet au repos ; aucune valeur en
    # clair dans index/API/UI/audit. Réutilise les guards write 4.1 à l'exécution.

    def set_site_react_write_config(self, domain: str, enabled: bool) -> dict:
        """Active/désactive la CRÉATION de propositions ReAct write (OFF par défaut).

        Indépendant de write_enabled : ce flag n'autorise QUE la proposition.
        L'exécution exige EN PLUS les guards write 4.1 (write_enabled + allowlist).
        """
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database")
        if not isinstance(db, dict):
            raise ValueError("Aucune BDD configurée pour ce site.")
        db["react_write_enabled"] = bool(enabled)
        self._save_sites()
        logger.info("[IONOS DB REACT] config site={} enabled={}", domain, bool(enabled))
        return {"ok": True, "enabled": bool(enabled)}

    def get_site_react_write_config(self, domain: str) -> dict:
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database") or {}
        return {"enabled": bool(db.get("react_write_enabled"))}

    def set_site_react_delete_config(self, domain: str, enabled: bool) -> dict:
        """Active/désactive la CRÉATION de propositions ReAct DELETE (4.5B, OFF par défaut).

        Cumulatif avec le kill-switch global LUMENA_IONOS_REACT_DELETE_ENABLED et avec
        les guards delete 4.4 (delete_enabled + allowlist) à l'exécution.
        """
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database")
        if not isinstance(db, dict):
            raise ValueError("Aucune BDD configurée pour ce site.")
        db["react_delete_enabled"] = bool(enabled)
        self._save_sites()
        logger.info("[IONOS DB REACT] config DELETE site={} enabled={}", domain, bool(enabled))
        return {"ok": True, "enabled": bool(enabled)}

    def get_site_react_delete_config(self, domain: str) -> dict:
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database") or {}
        return {"enabled": bool(db.get("react_delete_enabled"))}

    @staticmethod
    def _react_delete_killswitch_on() -> bool:
        """Kill-switch GLOBAL : LUMENA_IONOS_REACT_DELETE_ENABLED == '1'. OFF par défaut."""
        return os.getenv("LUMENA_IONOS_REACT_DELETE_ENABLED", "0").strip() == "1"

    # ── DROP sandbox contrôlé (Étape 4.6) ─────────────────────────────
    # DROP UNIQUEMENT sur lumena_sandbox_* VIDE, via proposition + approbation humaine.
    # Flag site sandbox_drop_enabled OFF + kill-switch global. Aucun DROP générique.

    def set_site_sandbox_drop_config(self, domain: str, enabled: bool) -> dict:
        """Active/désactive le DROP de tables sandbox (OFF par défaut).

        Cumulatif avec le kill-switch global LUMENA_IONOS_SANDBOX_DROP_ENABLED.
        """
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database")
        if not isinstance(db, dict):
            raise ValueError("Aucune BDD configurée pour ce site.")
        db["sandbox_drop_enabled"] = bool(enabled)
        self._save_sites()
        logger.info("[IONOS DB SANDBOX DROP] config site={} enabled={}", domain, bool(enabled))
        return {"ok": True, "enabled": bool(enabled)}

    def get_site_sandbox_drop_config(self, domain: str) -> dict:
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database") or {}
        return {"enabled": bool(db.get("sandbox_drop_enabled"))}

    @staticmethod
    def _sandbox_drop_killswitch_on() -> bool:
        """Kill-switch GLOBAL : LUMENA_IONOS_SANDBOX_DROP_ENABLED == '1'. OFF par défaut."""
        return os.getenv("LUMENA_IONOS_SANDBOX_DROP_ENABLED", "0").strip() == "1"

    # ── CLEAR sandbox contrôlé (Étape 4.7) : vider une table sandbox ───
    # Réversible (snapshot op:'delete' avant) → flag site sandbox_clear_enabled
    # suffit (pas de kill-switch global). DELETE total via op dédiée, jamais exposé.

    def set_site_sandbox_clear_config(self, domain: str, enabled: bool) -> dict:
        """Active/désactive le CLEAR (vidage) de tables sandbox (OFF par défaut)."""
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database")
        if not isinstance(db, dict):
            raise ValueError("Aucune BDD configurée pour ce site.")
        db["sandbox_clear_enabled"] = bool(enabled)
        self._save_sites()
        logger.info("[IONOS DB SANDBOX CLEAR] config site={} enabled={}", domain, bool(enabled))
        return {"ok": True, "enabled": bool(enabled)}

    def get_site_sandbox_clear_config(self, domain: str) -> dict:
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database") or {}
        return {"enabled": bool(db.get("sandbox_clear_enabled"))}

    def propose_clear_sandbox(self, domain: str, table: str, source: str = "react") -> dict:
        """Enfile une PROPOSITION de vidage d'une table sandbox (ReAct). N'exécute RIEN.

        Verrous : flag site `sandbox_clear_enabled` + préfixe `lumena_sandbox_` + la
        table doit exister. 0 ligne → 'déjà vide' (aucune proposition). > plafond → refus.
        L'exécution reste humaine (approve → DELETE total contrôlé + snapshot).
        """
        domain = domain.strip().lower()
        table = (table or "").strip()
        if not self._valid_sandbox_name(table):
            return {"ok": False, "error": "bad_prefix",
                    "message": "Seules les tables lumena_sandbox_* peuvent être vidées."}
        if not self.get_site_sandbox_clear_config(domain)["enabled"]:
            return {"ok": False, "error": "sandbox_clear_disabled",
                    "message": "Vidage sandbox désactivé pour ce site."}
        # Existence + comptage (lecture seule).
        try:
            chk = self.db_select(domain, table, limit=_DB_DELETE_MAX_CAP + 1)
        except Exception:
            chk = {"ok": False}
        if not chk.get("ok"):
            return {"ok": False, "error": "not_found", "message": "Table introuvable ou illisible."}
        count = int(chk.get("count") or 0)
        if count == 0:
            return {"ok": True, "status": "already_empty", "proposal_id": None,
                    "op": "clear_sandbox", "table": table, "message": "Table déjà vide — rien à faire."}
        if count > _DB_DELETE_MAX_CAP:
            return {"ok": False, "error": "too_many_rows",
                    "message": f"Trop de lignes (~{count} > {_DB_DELETE_MAX_CAP}) — vidage refusé (mode admin requis)."}

        prop_id = secrets.token_hex(12)
        payload = {"op": "clear_sandbox", "table": table, "values": None, "where": None}
        blob = _encrypt(json.dumps(payload, ensure_ascii=False))
        site_dir = _PROPOSAL_DIR / domain
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / f"{prop_id}.enc").write_text(blob, encoding="utf-8")
        created = dt.datetime.now()
        meta = {
            "id": prop_id, "domain": domain, "op": "clear_sandbox", "table": table,
            "value_keys": [], "where_keys": [], "estimated_count": count,
            "status": "pending", "source": source,
            "created_at": created.isoformat(timespec="seconds"),
        }
        idx = self._proposal_index_read()
        idx.append(meta)
        self._proposal_index_write(idx)
        self._audit_db_write(domain, "propose_clear_sandbox", table, [], [],
                             None, count, True, "", source, snapshot_id=None)
        logger.info("[IONOS DB SANDBOX CLEAR] proposition site={} table={} rows={} id={}",
                    domain, table, count, prop_id)
        return {"ok": True, "proposal_id": prop_id, "op": "clear_sandbox", "table": table,
                "estimated_count": count, "status": "pending"}

    def clear_sandbox_table(self, domain: str, name: str, confirm: bool = False,
                            confirm_table: str = "", source: str = "ui") -> dict:
        """Vide une table sandbox via le bridge (Étape 4.7). DELETE total contrôlé + snapshot.

        Garde-fous : `sandbox_clear_enabled` + `confirm` + `confirm_table == name` +
        préfixe. Snapshot op:'delete' capturé avant (restaurable par ré-INSERT).
        """
        domain = domain.strip().lower()
        name = (name or "").strip()
        if not self._valid_sandbox_name(name):
            return {"ok": False, "error": "bad_prefix",
                    "message": "Seules les tables lumena_sandbox_* peuvent être vidées."}
        if not confirm:
            return {"ok": False, "error": "not_confirmed", "message": "Confirmation explicite requise."}
        if (confirm_table or "").strip() != name:
            return {"ok": False, "error": "confirm_mismatch",
                    "message": "Le nom de table retapé ne correspond pas."}
        if not self.get_site_sandbox_clear_config(domain)["enabled"]:
            return {"ok": False, "error": "sandbox_clear_disabled",
                    "message": "Vidage sandbox désactivé pour ce site."}

        cfg = self.get_site_database(domain, include_secret=True)
        if cfg is None:
            return {"ok": False, "error": "no_database", "message": "Aucune BDD configurée."}
        bridge = (self._sites[domain].get("database") or {}).get("bridge")
        if not bridge or not bridge.get("installed"):
            return {"ok": False, "error": "bridge_not_installed", "message": "Accès BDD sécurisé non installé."}
        if bridge.get("version") != _BRIDGE_VERSION:
            return {"ok": False, "upgrade_required": True, "error": "upgrade_required",
                    "message": "Accès sécurisé à mettre à jour : réinstalle l'accès BDD sécurisé."}

        secret = _decrypt(bridge["secret_encrypted"])
        ts = int(time.time())
        nonce = secrets.token_hex(16)
        creds = {
            "host": cfg["host"], "port": int(cfg.get("port", 3306) or 3306),
            "user": cfg["user"], "password": cfg.get("password", ""), "name": cfg["name"],
        }
        body = {"creds": _seal_creds(secret, creds, "db_clear_sandbox_table", ts, nonce),
                "name": name, "affected_max": _DB_DELETE_MAX_CAP}
        try:
            data = self._bridge_request(domain, secret, bridge["path"], "db_clear_sandbox_table",
                                        body, max(15, 0), ts=ts, nonce=nonce)
        except Exception:
            self._audit_db_write(domain, "clear_sandbox", name, [], [], None, None, False,
                                 "request_failed", source)
            return {"ok": False, "error": "request_failed", "message": "Connexion au bridge échouée."}

        ok = data.get("_http_status") == 200 and data.get("ok") is True
        affected = data.get("affected")
        snapshot_count = data.get("snapshot_count")
        err = "" if ok else str(data.get("error", f"http_{data.get('_http_status')}"))
        snapshot_id = None
        if ok and data.get("snapshot_enc"):
            try:
                snap = _open_creds(secret, data["snapshot_enc"], "db_clear_sandbox_table", ts, nonce)
                snapshot_id = self._store_snapshot(domain, snap)
            except Exception as e:
                logger.warning("[IONOS DB SNAPSHOT] stockage échoué ({}): {}", domain, e)
        self._audit_db_write(domain, "clear_sandbox", name, [], [], affected,
                             snapshot_count, ok, err, source, snapshot_id=snapshot_id)
        if not ok:
            msg = {
                "too_many_rows": "Trop de lignes — vidage annulé (rollback).",
                "snapshot_no_pk": "Snapshot impossible (pas de clé primaire) — vidage refusé.",
                "snapshot_too_large": "Snapshot trop volumineux — vidage refusé.",
                "snapshot_failed": "Capture du snapshot échouée — vidage refusé.",
                "not_found": "Table introuvable.", "bad_prefix": "Table non sandbox.",
            }.get(data.get("error"), "Vidage refusé par le bridge.")
            return {"ok": False, "error": err, "message": msg}
        return {"ok": True, "op": "clear_sandbox", "table": name, "affected": affected,
                "snapshot_count": snapshot_count, "snapshot_id": snapshot_id,
                "warning": data.get("warning", ""), "message": ""}

    def propose_drop_sandbox(self, domain: str, table: str, source: str = "react") -> dict:
        """Enfile une PROPOSITION de DROP d'une table sandbox VIDE (ReAct). N'exécute RIEN.

        Triple verrou à la proposition : kill-switch global + flag site
        `sandbox_drop_enabled` + préfixe `lumena_sandbox_`. La table doit être VIDE
        (vérifié à l'exécution par le bridge). L'exécution reste humaine (approve).
        """
        domain = domain.strip().lower()
        table = (table or "").strip()
        if not self._valid_sandbox_name(table):
            return {"ok": False, "error": "bad_prefix",
                    "message": "Seules les tables lumena_sandbox_* peuvent être supprimées."}
        if not self._sandbox_drop_killswitch_on():
            return {"ok": False, "error": "sandbox_drop_killswitch_off",
                    "message": "DROP sandbox désactivé globalement (kill-switch)."}
        if not self.get_site_sandbox_drop_config(domain)["enabled"]:
            return {"ok": False, "error": "sandbox_drop_disabled",
                    "message": "DROP sandbox désactivé pour ce site."}
        # La table doit être VIDE dès la PROPOSITION (pas seulement à l'exécution) :
        # on ne met pas en file une proposition vouée à être refusée.
        if self._sandbox_table_rowcount(domain, table) > 0:
            return {"ok": False, "error": "table_not_empty",
                    "message": "Table non vide — videz-la d'abord (DROP refusé)."}

        prop_id = secrets.token_hex(12)
        payload = {"op": "drop_sandbox", "table": table, "values": None, "where": None}
        blob = _encrypt(json.dumps(payload, ensure_ascii=False))
        site_dir = _PROPOSAL_DIR / domain
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / f"{prop_id}.enc").write_text(blob, encoding="utf-8")
        created = dt.datetime.now()
        meta = {
            "id": prop_id, "domain": domain, "op": "drop_sandbox", "table": table,
            "value_keys": [], "where_keys": [], "estimated_count": None,
            "status": "pending", "source": source,
            "created_at": created.isoformat(timespec="seconds"),
        }
        idx = self._proposal_index_read()
        idx.append(meta)
        self._proposal_index_write(idx)
        self._audit_db_write(domain, "propose_drop_sandbox", table, [], [],
                             None, None, True, "", source, snapshot_id=None)
        logger.info("[IONOS DB SANDBOX DROP] proposition site={} table={} id={}", domain, table, prop_id)
        return {"ok": True, "proposal_id": prop_id, "op": "drop_sandbox", "table": table,
                "status": "pending"}

    def drop_sandbox_table(self, domain: str, name: str, confirm: bool = False,
                           confirm_table: str = "", source: str = "ui") -> dict:
        """Exécute le DROP d'une table sandbox VIDE via le bridge (Étape 4.6).

        Garde-fous : `sandbox_drop_enabled` + `confirm` + `confirm_table == name` +
        préfixe `lumena_sandbox_`. Le bridge refuse si la table n'est pas vide
        (`table_not_empty`). DROP uniquement via op dédiée, aucun SQL libre.
        """
        domain = domain.strip().lower()
        name = (name or "").strip()
        if not self._valid_sandbox_name(name):
            return {"ok": False, "error": "bad_prefix",
                    "message": "Seules les tables lumena_sandbox_* peuvent être supprimées."}
        if not confirm:
            return {"ok": False, "error": "not_confirmed", "message": "Confirmation explicite requise."}
        if (confirm_table or "").strip() != name:
            return {"ok": False, "error": "confirm_mismatch",
                    "message": "Le nom de table retapé ne correspond pas."}
        if not self.get_site_sandbox_drop_config(domain)["enabled"]:
            return {"ok": False, "error": "sandbox_drop_disabled",
                    "message": "DROP sandbox désactivé pour ce site."}

        cfg = self.get_site_database(domain, include_secret=True)
        if cfg is None:
            return {"ok": False, "error": "no_database", "message": "Aucune BDD configurée."}
        bridge = (self._sites[domain].get("database") or {}).get("bridge")
        if not bridge or not bridge.get("installed"):
            return {"ok": False, "error": "bridge_not_installed", "message": "Accès BDD sécurisé non installé."}
        if bridge.get("version") != _BRIDGE_VERSION:
            return {"ok": False, "upgrade_required": True, "error": "upgrade_required",
                    "message": "Accès sécurisé à mettre à jour : réinstalle l'accès BDD sécurisé."}

        secret = _decrypt(bridge["secret_encrypted"])
        ts = int(time.time())
        nonce = secrets.token_hex(16)
        creds = {
            "host": cfg["host"], "port": int(cfg.get("port", 3306) or 3306),
            "user": cfg["user"], "password": cfg.get("password", ""), "name": cfg["name"],
        }
        body = {"creds": _seal_creds(secret, creds, "db_drop_sandbox_table", ts, nonce), "name": name}
        try:
            data = self._bridge_request(domain, secret, bridge["path"], "db_drop_sandbox_table",
                                        body, max(15, 0), ts=ts, nonce=nonce)
        except Exception:
            self._audit_db_write(domain, "drop_sandbox", name, [], [], None, None, False,
                                 "request_failed", source)
            return {"ok": False, "error": "request_failed", "message": "Connexion au bridge échouée."}

        ok = data.get("_http_status") == 200 and data.get("ok") is True
        err = "" if ok else str(data.get("error", f"http_{data.get('_http_status')}"))
        self._audit_db_write(domain, "drop_sandbox", name, [], [], None, None, ok, err, source)
        if not ok:
            msg = {
                "table_not_empty": "Table non vide — DROP refusé (vide-la d'abord).",
                "bad_prefix": "Seules les tables lumena_sandbox_* sont supprimables.",
                "not_found": "Table introuvable.",
            }.get(data.get("error"), "DROP refusé par le bridge.")
            return {"ok": False, "error": err, "message": msg}
        return {"ok": True, "op": "drop_sandbox", "table": name, "dropped": True, "message": ""}

    def _proposal_index_read(self) -> list:
        if not _PROPOSAL_INDEX.exists():
            return []
        out = []
        for line in _PROPOSAL_INDEX.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
        return out

    def _proposal_index_write(self, entries: list) -> None:
        _PROPOSAL_INDEX.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PROPOSAL_INDEX.with_suffix(".tmp")
        tmp.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + ("\n" if entries else ""),
                       encoding="utf-8")
        tmp.replace(_PROPOSAL_INDEX)

    def _estimate_update_count(self, domain: str, table: str, where: dict):
        """Best-effort : COUNT lecture seule via db_select (borné). None si indisponible."""
        try:
            r = self.db_select(domain, table, where=where, limit=_DB_AFFECTED_MAX_CAP)
            if r.get("ok"):
                return int(r.get("count") or 0)
        except Exception:
            pass
        return None

    def _sandbox_table_rowcount(self, domain: str, table: str, limit: int = 1) -> int:
        """Compte (borné) les lignes d'une table via db_select. 0 si indéterminable.

        Best-effort lecture seule : `limit=1` suffit pour tester la vacuité ;
        `limit` plus élevé pour détecter un dépassement de seuil.
        """
        try:
            r = self.db_select(domain, table, limit=limit)
            if r.get("ok"):
                return int(r.get("count") or 0)
        except Exception:
            pass
        return 0

    def propose_write(self, domain: str, op: str, table: str, values: dict,
                      where: Optional[dict] = None, source: str = "react") -> dict:
        """Enfile une PROPOSITION d'écriture INSERT/UPDATE (ReAct). N'exécute RIEN.

        Refus (aucune mise en file) si :
        - `react_write_enabled=false` ;
        - `write_enabled=false` ou table hors `write_tables` (guards 4.1) ;
        - op invalide / identifiants invalides / UPDATE sans WHERE.
        Valeurs chiffrées Fernet au repos ; l'index ne contient que des métadonnées.
        """
        domain = domain.strip().lower()
        op = (op or "").strip().lower()
        if op not in ("insert", "update"):
            return {"ok": False, "error": "bad_op", "message": "Seuls INSERT et UPDATE sont proposables."}
        if not _valid_db_identifier(table):
            return {"ok": False, "error": "bad_table", "message": "Nom de table invalide."}
        if not isinstance(values, dict) or not values or not all(_valid_db_identifier(k) for k in values):
            return {"ok": False, "error": "bad_values", "message": "Valeurs/colonnes invalides."}
        if op == "update":
            if not isinstance(where, dict) or not where or not all(_valid_db_identifier(k) for k in where):
                return {"ok": False, "error": "missing_where", "message": "UPDATE sans WHERE valide interdit."}

        # Verrou 1 : proposition ReAct autorisée pour ce site.
        if not self.get_site_react_write_config(domain)["enabled"]:
            return {"ok": False, "error": "react_write_disabled",
                    "message": "Propositions ReAct désactivées pour ce site."}
        # Verrou 2 (anticipé) : guards write 4.1 — refus si non satisfaits (pas de mise en file).
        wc = self.get_site_write_config(domain)
        if not wc["enabled"]:
            return {"ok": False, "error": "write_disabled", "message": "Écriture désactivée pour ce site."}
        if table not in wc["tables"]:
            return {"ok": False, "error": "table_not_allowed", "message": "Table non autorisée en écriture."}

        value_keys = list(values.keys())
        where_keys = list(where.keys()) if (op == "update" and where) else []
        estimated_count = self._estimate_update_count(domain, table, where) if op == "update" else None

        prop_id = secrets.token_hex(12)
        payload = {"op": op, "table": table, "values": values,
                   "where": where if op == "update" else None}
        blob = _encrypt(json.dumps(payload, ensure_ascii=False))  # valeurs chiffrées au repos
        site_dir = _PROPOSAL_DIR / domain
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / f"{prop_id}.enc").write_text(blob, encoding="utf-8")
        created = dt.datetime.now()
        meta = {
            "id": prop_id, "domain": domain, "op": op, "table": table,
            "value_keys": value_keys, "where_keys": where_keys,
            "estimated_count": estimated_count, "status": "pending",
            "source": source, "created_at": created.isoformat(timespec="seconds"),
        }
        idx = self._proposal_index_read()
        idx.append(meta)
        self._proposal_index_write(idx)
        # Audit non sensible : proposition (aucune valeur).
        self._audit_db_write(domain, f"propose_{op}", table, where_keys, value_keys,
                             None, None, True, "", source, snapshot_id=None)
        logger.info("[IONOS DB REACT] proposition site={} op={} table={} id={}", domain, op, table, prop_id)
        return {"ok": True, "proposal_id": prop_id, "op": op, "table": table,
                "value_keys": value_keys, "where_keys": where_keys,
                "estimated_count": estimated_count, "status": "pending"}

    def propose_delete(self, domain: str, table: str, where: Optional[dict] = None,
                       source: str = "react") -> dict:
        """Enfile une PROPOSITION de DELETE (ReAct, Étape 4.5B). N'exécute RIEN.

        Triple verrou à la proposition :
        - kill-switch GLOBAL `LUMENA_IONOS_REACT_DELETE_ENABLED == '1'` ;
        - flag site `react_delete_enabled` ON ;
        - guards delete 4.4 : `delete_enabled` + table dans `delete_tables`.
        WHERE non vide obligatoire ; refus si `estimated_count` dépasse le plafond DELETE.
        L'exécution reste humaine (approve → db_delete confirm serveur-side).
        """
        domain = domain.strip().lower()
        if not _valid_db_identifier(table):
            return {"ok": False, "error": "bad_table", "message": "Nom de table invalide."}
        if not isinstance(where, dict) or not where or not all(_valid_db_identifier(k) for k in where):
            return {"ok": False, "error": "missing_where", "message": "DELETE sans WHERE valide interdit."}

        # Verrou 0 : kill-switch global.
        if not self._react_delete_killswitch_on():
            return {"ok": False, "error": "react_delete_killswitch_off",
                    "message": "Propositions DELETE ReAct désactivées globalement (kill-switch)."}
        # Verrou 1 : flag site react_delete_enabled.
        if not self.get_site_react_delete_config(domain)["enabled"]:
            return {"ok": False, "error": "react_delete_disabled",
                    "message": "Propositions DELETE ReAct désactivées pour ce site."}
        # Verrou 2 (anticipé) : guards delete 4.4.
        dc = self.get_site_delete_config(domain)
        if not dc["enabled"]:
            return {"ok": False, "error": "delete_disabled", "message": "Suppression désactivée pour ce site."}
        if table not in dc["tables"]:
            return {"ok": False, "error": "table_not_allowed", "message": "Table non autorisée en suppression."}

        where_keys = list(where.keys())
        estimated_count = self._estimate_update_count(domain, table, where)  # COUNT lecture seule
        if estimated_count is not None and estimated_count > _DB_DELETE_MAX_DEFAULT:
            return {"ok": False, "error": "too_many_rows",
                    "message": f"Trop de lignes ciblées (~{estimated_count} > {_DB_DELETE_MAX_DEFAULT}) — proposition refusée."}

        prop_id = secrets.token_hex(12)
        payload = {"op": "delete", "table": table, "values": None, "where": where}
        blob = _encrypt(json.dumps(payload, ensure_ascii=False))
        site_dir = _PROPOSAL_DIR / domain
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / f"{prop_id}.enc").write_text(blob, encoding="utf-8")
        created = dt.datetime.now()
        meta = {
            "id": prop_id, "domain": domain, "op": "delete", "table": table,
            "value_keys": [], "where_keys": where_keys,
            "estimated_count": estimated_count, "status": "pending",
            "source": source, "created_at": created.isoformat(timespec="seconds"),
        }
        idx = self._proposal_index_read()
        idx.append(meta)
        self._proposal_index_write(idx)
        self._audit_db_write(domain, "propose_delete", table, where_keys, [],
                             None, None, True, "", source, snapshot_id=None)
        logger.info("[IONOS DB REACT] proposition DELETE site={} table={} id={}", domain, table, prop_id)
        return {"ok": True, "proposal_id": prop_id, "op": "delete", "table": table,
                "value_keys": [], "where_keys": where_keys,
                "estimated_count": estimated_count, "status": "pending"}

    def list_pending_actions(self, domain: str) -> dict:
        """Liste les propositions (métadonnées NON sensibles, jamais de valeurs)."""
        domain = domain.strip().lower()
        items = [e for e in self._proposal_index_read()
                 if e.get("domain") == domain and e.get("status") == "pending"]
        items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return {"ok": True, "actions": items}

    def _proposal_set_status(self, domain: str, proposal_id: str, status: str) -> None:
        idx = self._proposal_index_read()
        for e in idx:
            if e.get("domain") == domain and e.get("id") == proposal_id:
                e["status"] = status
                e["resolved_at"] = dt.datetime.now().isoformat(timespec="seconds")
        self._proposal_index_write(idx)
        try:
            p = _PROPOSAL_DIR / domain / f"{proposal_id}.enc"
            if p.exists():
                p.unlink()  # le payload chiffré n'est plus nécessaire après résolution
        except Exception:
            pass

    def reject_pending_action(self, domain: str, proposal_id: str, source: str = "ui") -> dict:
        domain = domain.strip().lower()
        meta = next((e for e in self._proposal_index_read()
                     if e.get("domain") == domain and e.get("id") == proposal_id
                     and e.get("status") == "pending"), None)
        if meta is None:
            return {"ok": False, "error": "not_found", "message": "Proposition introuvable."}
        self._proposal_set_status(domain, proposal_id, "rejected")
        self._audit_db_write(domain, "reject", meta.get("table", ""), meta.get("where_keys", []),
                             meta.get("value_keys", []), None, None, True, "", source)
        return {"ok": True, "status": "rejected"}

    def approve_pending_action(self, domain: str, proposal_id: str, confirm: bool = False,
                               source: str = "ui") -> dict:
        """Exécute une proposition APRÈS confirmation humaine.

        `confirm=true` est posé ici (serveur-side), JAMAIS par le modèle.
        - INSERT/UPDATE → `db_write(..., confirm=True, source="react_approved")`
          (guards 4.1 + snapshot UPDATE 4.3).
        - DELETE (4.5B) → `db_delete(..., confirm=True, confirm_table=table,
          source="react_delete_approved")` (guards 4.4 + snapshot obligatoire avant DELETE).
        Le payload chiffré est lu en mémoire seulement.
        """
        domain = domain.strip().lower()
        if not confirm:
            return {"ok": False, "error": "not_confirmed", "message": "Confirmation explicite requise."}
        meta = next((e for e in self._proposal_index_read()
                     if e.get("domain") == domain and e.get("id") == proposal_id
                     and e.get("status") == "pending"), None)
        if meta is None:
            return {"ok": False, "error": "not_found", "message": "Proposition introuvable."}
        path = _PROPOSAL_DIR / domain / f"{proposal_id}.enc"
        if not path.exists():
            return {"ok": False, "error": "not_found", "message": "Payload de proposition introuvable."}
        try:
            payload = json.loads(_decrypt(path.read_text(encoding="utf-8")))  # plaintext en mémoire
        except Exception:
            return {"ok": False, "error": "decrypt_failed", "message": "Proposition illisible."}

        op = payload.get("op"); table = payload.get("table")
        values = payload.get("values") or {}; where = payload.get("where")
        # Exécution réelle, confirm posé serveur-side (jamais par le modèle).
        if op == "drop_sandbox":
            # DROP sandbox (4.6) → préfixe + flag + confirm_table + table VIDE (bridge).
            r = self.drop_sandbox_table(domain, table, confirm=True,
                                        confirm_table=table, source="react_drop_approved")
        elif op == "clear_sandbox":
            # CLEAR sandbox (4.7) → préfixe + flag + confirm_table + snapshot avant (bridge).
            r = self.clear_sandbox_table(domain, table, confirm=True,
                                         confirm_table=table, source="react_clear_approved")
        elif op == "delete":
            # DELETE (4.5B) → guards 4.4 + confirm_table + snapshot obligatoire avant suppression.
            r = self.db_delete(domain, table, where=where, confirm=True,
                               confirm_table=table, source="react_delete_approved")
        else:
            # INSERT/UPDATE (4.5A) → guards 4.1 + snapshot UPDATE 4.3.
            r = self.db_write(domain, op, table, values, where=where,
                              confirm=True, source="react_approved")
        self._proposal_set_status(domain, proposal_id, "executed" if r.get("ok") else "failed")
        return r

    def _audit_db_write(self, domain: str, op: str, table: str, where_keys: list,
                        value_keys: list, affected, snapshot_count, ok: bool,
                        error: str = "", source: str = "ui", snapshot_id=None) -> None:
        """Audit léger d'une écriture : logger.info + JSONL append-only.

        N'écrit JAMAIS de valeurs, de snapshot ni de secret — uniquement des
        noms de colonnes, des compteurs et un identifiant de snapshot.
        """
        entry = {
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
            "domain": domain, "op": op, "table": table,
            "where_keys": list(where_keys or []), "value_keys": list(value_keys or []),
            "affected": affected, "snapshot_count": snapshot_count,
            "snapshot_id": snapshot_id,
            "ok": bool(ok), "source": source, "error": error or "",
        }
        logger.info("[IONOS DB WRITE] site={} op={} table={} affected={} ok={}",
                    domain, op, table, affected, ok)
        try:
            _DB_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_DB_AUDIT_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("[IONOS DB WRITE] audit JSONL échoué: {}", e)

    def db_write(self, domain: str, op: str, table: str, values: dict,
                 where: Optional[dict] = None, confirm: bool = False,
                 source: str = "ui") -> dict:
        """Écriture contrôlée INSERT/UPDATE via bridge (Étape 4.1).

        Garde-fous Lumena (le bridge revalide + transaction) :
        - `write_enabled` du site ET `table` dans l'allowlist ;
        - `confirm=true` obligatoire ;
        - `op ∈ {insert, update}` (DELETE/DDL interdits) ;
        - identifiants whitelistés ; UPDATE sans WHERE interdit ; WHERE égalité simple.
        Jamais de valeurs/snapshot/secret en sortie.
        """
        domain = domain.strip().lower()
        op = (op or "").strip().lower()
        if op not in ("insert", "update"):
            return {"ok": False, "error": "bad_op", "message": "Seuls INSERT et UPDATE sont permis."}
        if not confirm:
            return {"ok": False, "error": "not_confirmed", "message": "Confirmation explicite requise."}
        if not _valid_db_identifier(table):
            return {"ok": False, "error": "bad_table", "message": "Nom de table invalide."}
        if not isinstance(values, dict) or not values or not all(_valid_db_identifier(k) for k in values):
            return {"ok": False, "error": "bad_values", "message": "Valeurs/colonnes invalides."}
        if op == "update":
            if not isinstance(where, dict) or not where or not all(_valid_db_identifier(k) for k in where):
                return {"ok": False, "error": "missing_where", "message": "UPDATE sans WHERE valide interdit."}

        # Activation + allowlist (KeyError si site absent).
        wc = self.get_site_write_config(domain)
        if not wc["enabled"]:
            return {"ok": False, "error": "write_disabled", "message": "Écriture désactivée pour ce site."}
        if table not in wc["tables"]:
            return {"ok": False, "error": "table_not_allowed", "message": "Table non autorisée en écriture."}

        cfg = self.get_site_database(domain, include_secret=True)
        if cfg is None:
            return {"ok": False, "error": "no_database", "message": "Aucune BDD configurée."}
        bridge = (self._sites[domain].get("database") or {}).get("bridge")
        if not bridge or not bridge.get("installed"):
            return {"ok": False, "error": "bridge_not_installed", "message": "Accès BDD sécurisé non installé."}
        if bridge.get("version") != _BRIDGE_VERSION:
            return {"ok": False, "upgrade_required": True, "error": "upgrade_required",
                    "message": "Accès sécurisé à mettre à jour : réinstalle l'accès BDD sécurisé."}

        secret = _decrypt(bridge["secret_encrypted"])
        ts = int(time.time())
        nonce = secrets.token_hex(16)
        creds = {
            "host": cfg["host"], "port": int(cfg.get("port", 3306) or 3306),
            "user": cfg["user"], "password": cfg.get("password", ""), "name": cfg["name"],
        }
        body = {
            "creds": _seal_creds(secret, creds, "db_write", ts, nonce),
            "wop": op, "table": table,
            "values": {k: ("" if v is None else str(v)) for k, v in values.items()},
            "affected_max": _DB_AFFECTED_MAX_DEFAULT,
        }
        if op == "update":
            body["where"] = {k: ("" if v is None else str(v)) for k, v in where.items()}

        where_keys = list(where.keys()) if (op == "update" and where) else []
        value_keys = list(values.keys())
        try:
            data = self._bridge_request(domain, secret, bridge["path"], "db_write", body,
                                        max(15, 0), ts=ts, nonce=nonce)
        except Exception:
            self._audit_db_write(domain, op, table, where_keys, value_keys, None, None, False,
                                 "request_failed", source)
            return {"ok": False, "error": "request_failed", "message": "Connexion au bridge échouée."}

        ok = data.get("_http_status") == 200 and data.get("ok") is True
        affected = data.get("affected")
        snapshot_count = data.get("snapshot_count")
        err = "" if ok else str(data.get("error", f"http_{data.get('_http_status')}"))
        # Snapshot (UPDATE v6) : déchiffrer en mémoire, re-chiffrer Fernet, stocker.
        snapshot_id = None
        if ok and op == "update" and data.get("snapshot_enc"):
            try:
                snap = _open_creds(secret, data["snapshot_enc"], "db_write", ts, nonce)
                snapshot_id = self._store_snapshot(domain, snap)
            except Exception as e:
                logger.warning("[IONOS DB SNAPSHOT] stockage échoué ({}): {}", domain, e)
        self._audit_db_write(domain, op, table, where_keys, value_keys, affected,
                             snapshot_count, ok, err, source, snapshot_id=snapshot_id)
        if not ok:
            msg = {
                "too_many_rows": "Trop de lignes impactées — opération annulée (rollback).",
                "missing_where": "UPDATE sans WHERE interdit.",
                "bad_op": "Opération non permise.",
                "snapshot_no_pk": "Snapshot impossible (table sans clé primaire) — écriture refusée.",
                "snapshot_too_large": "Snapshot trop volumineux — écriture refusée.",
                "snapshot_failed": "Capture du snapshot échouée — écriture refusée.",
            }.get(data.get("error"), "Écriture refusée par le bridge.")
            return {"ok": False, "error": err, "message": msg,
                    "snapshot_count": snapshot_count}
        return {"ok": True, "op": op, "table": table, "affected": affected,
                "snapshot_count": snapshot_count, "snapshot_id": snapshot_id,
                "warning": data.get("warning", ""), "message": ""}

    # ── DELETE contrôlé (Étape 4.4) ───────────────────────────────────
    # Op DISTINCTE de db_write. WHERE obligatoire ; confirm + confirm_table exact ;
    # flag/allowlist DÉDIÉS ; snapshot op:'delete' obligatoire (sinon DELETE refusé).
    def db_delete(self, domain: str, table: str, where: Optional[dict] = None,
                  confirm: bool = False, confirm_table: str = "",
                  source: str = "ui") -> dict:
        """Suppression contrôlée de lignes via bridge (Étape 4.4).

        Garde-fous Lumena (le bridge revalide + transaction + snapshot) :
        - `delete_enabled` du site ET `table` dans l'allowlist DÉDIÉE `delete_tables` ;
        - `confirm=true` ET `confirm_table` exactement égal au nom de table ;
        - `where` non vide (pas de DELETE total), identifiants whitelistés ;
        - snapshot op:'delete' capturé AVANT suppression → si impossible, DELETE REFUSÉ.
        Jamais de valeurs/snapshot/secret en sortie.
        """
        domain = domain.strip().lower()
        if not _valid_db_identifier(table):
            return {"ok": False, "error": "bad_table", "message": "Nom de table invalide."}
        if not confirm:
            return {"ok": False, "error": "not_confirmed", "message": "Confirmation explicite requise."}
        if (confirm_table or "").strip() != table:
            return {"ok": False, "error": "confirm_mismatch",
                    "message": "Le nom de table retapé ne correspond pas."}
        if not isinstance(where, dict) or not where or not all(_valid_db_identifier(k) for k in where):
            return {"ok": False, "error": "missing_where",
                    "message": "DELETE sans WHERE valide interdit."}

        # Activation + allowlist DÉDIÉE (KeyError si site absent).
        dc = self.get_site_delete_config(domain)
        if not dc["enabled"]:
            return {"ok": False, "error": "delete_disabled", "message": "Suppression désactivée pour ce site."}
        if table not in dc["tables"]:
            return {"ok": False, "error": "table_not_allowed", "message": "Table non autorisée en suppression."}

        cfg = self.get_site_database(domain, include_secret=True)
        if cfg is None:
            return {"ok": False, "error": "no_database", "message": "Aucune BDD configurée."}
        bridge = (self._sites[domain].get("database") or {}).get("bridge")
        if not bridge or not bridge.get("installed"):
            return {"ok": False, "error": "bridge_not_installed", "message": "Accès BDD sécurisé non installé."}
        if bridge.get("version") != _BRIDGE_VERSION:
            return {"ok": False, "upgrade_required": True, "error": "upgrade_required",
                    "message": "Accès sécurisé à mettre à jour : réinstalle l'accès BDD sécurisé."}

        secret = _decrypt(bridge["secret_encrypted"])
        ts = int(time.time())
        nonce = secrets.token_hex(16)
        creds = {
            "host": cfg["host"], "port": int(cfg.get("port", 3306) or 3306),
            "user": cfg["user"], "password": cfg.get("password", ""), "name": cfg["name"],
        }
        body = {
            "creds": _seal_creds(secret, creds, "db_delete", ts, nonce),
            "table": table,
            "where": {k: ("" if v is None else str(v)) for k, v in where.items()},
            "affected_max": _DB_DELETE_MAX_DEFAULT,
        }
        where_keys = list(where.keys())
        try:
            data = self._bridge_request(domain, secret, bridge["path"], "db_delete", body,
                                        max(15, 0), ts=ts, nonce=nonce)
        except Exception:
            self._audit_db_write(domain, "delete", table, where_keys, [], None, None, False,
                                 "request_failed", source)
            return {"ok": False, "error": "request_failed", "message": "Connexion au bridge échouée."}

        ok = data.get("_http_status") == 200 and data.get("ok") is True
        affected = data.get("affected")
        snapshot_count = data.get("snapshot_count")
        err = "" if ok else str(data.get("error", f"http_{data.get('_http_status')}"))
        # Snapshot op:'delete' : déchiffrer en mémoire, re-chiffrer Fernet, stocker.
        snapshot_id = None
        if ok and data.get("snapshot_enc"):
            try:
                snap = _open_creds(secret, data["snapshot_enc"], "db_delete", ts, nonce)
                snapshot_id = self._store_snapshot(domain, snap)
            except Exception as e:
                logger.warning("[IONOS DB SNAPSHOT] stockage échoué ({}): {}", domain, e)
        self._audit_db_write(domain, "delete", table, where_keys, [], affected,
                             snapshot_count, ok, err, source, snapshot_id=snapshot_id)
        if not ok:
            msg = {
                "too_many_rows": "Trop de lignes impactées — opération annulée (rollback).",
                "missing_where": "DELETE sans WHERE interdit.",
                "snapshot_no_pk": "Snapshot impossible (table sans clé primaire) — suppression refusée.",
                "snapshot_too_large": "Snapshot trop volumineux — suppression refusée.",
                "snapshot_failed": "Capture du snapshot échouée — suppression refusée.",
            }.get(data.get("error"), "Suppression refusée par le bridge.")
            return {"ok": False, "error": err, "message": msg,
                    "snapshot_count": snapshot_count}
        return {"ok": True, "op": "delete", "table": table, "affected": affected,
                "snapshot_count": snapshot_count, "snapshot_id": snapshot_id,
                "warning": data.get("warning", ""), "message": ""}

    # ── Snapshot chiffré / rollback (Étape 4.3) ───────────────────────
    # Stockage Fernet au repos ; plaintext transitoire en mémoire seulement.
    # restore_enabled OFF par défaut ; restore = écriture gatée + confirm + audit.

    def set_site_restore_config(self, domain: str, enabled: bool) -> dict:
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database")
        if not isinstance(db, dict):
            raise ValueError("Aucune BDD configurée pour ce site.")
        db["restore_enabled"] = bool(enabled)
        self._save_sites()
        logger.info("[IONOS DB RESTORE] config site={} enabled={}", domain, bool(enabled))
        return {"ok": True, "enabled": bool(enabled)}

    def get_site_restore_config(self, domain: str) -> dict:
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database") or {}
        return {"enabled": bool(db.get("restore_enabled"))}

    def _snapshot_index_read(self) -> list:
        if not _SNAPSHOT_INDEX.exists():
            return []
        out = []
        for line in _SNAPSHOT_INDEX.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
        return out

    def _snapshot_index_write(self, entries: list) -> None:
        _SNAPSHOT_INDEX.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SNAPSHOT_INDEX.with_suffix(".tmp")
        tmp.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + ("\n" if entries else ""),
                       encoding="utf-8")
        tmp.replace(_SNAPSHOT_INDEX)

    def _purge_snapshots(self, domain: str) -> None:
        """Supprime les snapshots expirés (TTL) et l'excédent (> max/site, plus anciens)."""
        now = dt.datetime.now()
        entries = self._snapshot_index_read()
        kept, dropped = [], []
        for e in entries:
            exp = e.get("expires_at")
            expired = False
            if exp:
                try:
                    expired = dt.datetime.fromisoformat(exp) < now
                except Exception:
                    expired = False
            (dropped if expired else kept).append(e)
        # excédent par site (garder les plus récents)
        by_site = {}
        for e in kept:
            by_site.setdefault(e.get("domain"), []).append(e)
        final = []
        for dom, lst in by_site.items():
            lst.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            final.extend(lst[:_SNAPSHOT_MAX_PER_SITE])
            dropped.extend(lst[_SNAPSHOT_MAX_PER_SITE:])
        for e in dropped:
            try:
                p = _SNAPSHOT_DIR / e.get("domain", "") / f"{e.get('id')}.enc"
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        if dropped:
            self._snapshot_index_write(final)

    def _store_snapshot(self, domain: str, snap: dict) -> Optional[str]:
        """Chiffre (Fernet) et stocke un snapshot ; indexe les métadonnées NON sensibles."""
        rows = snap.get("rows") or []
        snap_id = secrets.token_hex(12)
        blob = _encrypt(json.dumps(snap, ensure_ascii=False))  # Fernet au repos
        if len(blob) > _SNAPSHOT_MAX_FILE_BYTES:
            logger.warning("[IONOS DB SNAPSHOT] blob trop volumineux, snapshot non stocké ({})", domain)
            return None
        site_dir = _SNAPSHOT_DIR / domain
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / f"{snap_id}.enc").write_text(blob, encoding="utf-8")
        created = dt.datetime.now()
        meta = {
            "id": snap_id, "domain": domain, "table": snap.get("table", ""),
            "op": snap.get("op", "update"), "row_count": len(rows),
            "columns": list(rows[0].keys()) if rows else [],  # noms seulement
            "created_at": created.isoformat(timespec="seconds"),
            "expires_at": (created + dt.timedelta(days=_SNAPSHOT_TTL_DAYS)).isoformat(timespec="seconds"),
        }
        idx = self._snapshot_index_read()
        idx.append(meta)
        self._snapshot_index_write(idx)
        self._purge_snapshots(domain)
        logger.info("[IONOS DB SNAPSHOT] stocké site={} table={} rows={} id={}",
                    domain, meta["table"], meta["row_count"], snap_id)
        return snap_id

    def list_snapshots(self, domain: str) -> dict:
        """Liste les métadonnées de snapshots (NON sensible, jamais de valeurs)."""
        domain = domain.strip().lower()
        self._purge_snapshots(domain)
        items = [e for e in self._snapshot_index_read() if e.get("domain") == domain]
        items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return {"ok": True, "snapshots": items}

    def delete_snapshot(self, domain: str, snapshot_id: str) -> dict:
        domain = domain.strip().lower()
        idx = self._snapshot_index_read()
        new_idx = [e for e in idx if not (e.get("domain") == domain and e.get("id") == snapshot_id)]
        try:
            p = _SNAPSHOT_DIR / domain / f"{snapshot_id}.enc"
            if p.exists():
                p.unlink()
        except Exception:
            pass
        self._snapshot_index_write(new_idx)
        return {"ok": True, "deleted": len(idx) - len(new_idx) > 0}

    def restore_snapshot(self, domain: str, snapshot_id: str, confirm: bool = False,
                         source: str = "ui") -> dict:
        """Restaure une image-avant via le chemin write contrôlé.

        Branche sur `snap["op"]` :
        - `update` → ré-applique les valeurs-avant par PK (`db_write update`) ;
        - `delete` → ré-INSÈRE la ligne complète (PK comprise) (`db_write insert`).
        Exige `restore_enabled=true` + `confirm=true` + guards write 4.1
        (write_enabled + table allowlistée en write). Jamais automatique. Audit obligatoire.
        """
        domain = domain.strip().lower()
        if not confirm:
            return {"ok": False, "error": "not_confirmed", "message": "Confirmation explicite requise."}
        if not self.get_site_restore_config(domain)["enabled"]:
            return {"ok": False, "error": "restore_disabled", "message": "Restauration désactivée pour ce site."}
        meta = next((e for e in self._snapshot_index_read()
                     if e.get("domain") == domain and e.get("id") == snapshot_id), None)
        if meta is None:
            return {"ok": False, "error": "not_found", "message": "Snapshot introuvable."}
        path = _SNAPSHOT_DIR / domain / f"{snapshot_id}.enc"
        if not path.exists():
            return {"ok": False, "error": "not_found", "message": "Fichier snapshot introuvable."}
        try:
            snap = json.loads(_decrypt(path.read_text(encoding="utf-8")))  # plaintext en mémoire
        except Exception:
            return {"ok": False, "error": "decrypt_failed", "message": "Snapshot illisible."}

        table = snap.get("table", "")
        pk_col = snap.get("pk_col")
        snap_op = (snap.get("op") or "update").lower()
        rows = snap.get("rows") or []
        if not pk_col or not rows:
            return {"ok": False, "error": "bad_snapshot", "message": "Snapshot incomplet."}
        restored, errors = 0, 0
        for row in rows:
            if pk_col not in row:
                errors += 1
                continue
            if snap_op == "delete":
                # Restaurer un DELETE = ré-INSÉRER la ligne complète (PK comprise).
                if not row:
                    continue
                r = self.db_write(domain, "insert", table, dict(row),
                                  confirm=True, source="restore")
            else:
                # Restaurer un UPDATE = ré-appliquer les valeurs-avant par PK.
                values = {k: v for k, v in row.items() if k != pk_col}
                if not values:
                    continue
                r = self.db_write(domain, "update", table, values,
                                  where={pk_col: row[pk_col]}, confirm=True, source="restore")
            if r.get("ok"):
                restored += 1
            else:
                errors += 1
        self._audit_db_write(domain, "restore", table, [pk_col], meta.get("columns", []),
                             restored, len(rows), errors == 0, "" if errors == 0 else "partial",
                             source, snapshot_id=snapshot_id)
        if errors and not restored:
            return {"ok": False, "error": "restore_failed", "message": "Restauration échouée.",
                    "restored": restored}
        return {"ok": True, "restored": restored, "errors": errors,
                "message": "" if not errors else "Restauration partielle.",
                "snapshot_id": snapshot_id}

    # ── CREATE TABLE sandbox contrôlé (Étape 4.2) ─────────────────────
    # Préfixe FIXE lumena_sandbox_ ; types whitelistés ; flag OFF par défaut.
    # Aucun DROP/ALTER/TRUNCATE/RENAME/SQL libre.

    def set_site_sandbox_config(self, domain: str, enabled: bool) -> dict:
        """Active/désactive la création de tables sandbox pour le site."""
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database")
        if not isinstance(db, dict):
            raise ValueError("Aucune BDD configurée pour ce site.")
        db["sandbox_create_enabled"] = bool(enabled)
        self._save_sites()
        logger.info("[IONOS DB SANDBOX] config site={} enabled={}", domain, bool(enabled))
        return {"ok": True, "enabled": bool(enabled)}

    def get_site_sandbox_config(self, domain: str) -> dict:
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        db = self._sites[domain].get("database") or {}
        return {"enabled": bool(db.get("sandbox_create_enabled"))}

    @staticmethod
    def _valid_sandbox_name(name) -> bool:
        return (isinstance(name, str) and name.startswith(_SANDBOX_PREFIX)
                and bool(_re_db.match(r"^[a-z0-9_]{1,64}$", name)))

    @staticmethod
    def _validate_sandbox_columns(columns) -> Optional[str]:
        """Valide le schéma JSON. Retourne un code d'erreur ou None si OK."""
        if not isinstance(columns, list) or not columns or len(columns) > _SANDBOX_MAX_COLUMNS:
            return "bad_columns"
        seen = set()
        for col in columns:
            if not isinstance(col, dict):
                return "bad_columns"
            cname = col.get("name")
            ctype = str(col.get("type", "")).upper()
            if not _valid_db_identifier(cname) or cname.lower() == "id" or cname in seen:
                return "bad_column"
            seen.add(cname)
            if ctype not in _SANDBOX_TYPES:
                return "bad_type"
            if ctype == "VARCHAR":
                try:
                    ln = int(col.get("length", 0))
                except (TypeError, ValueError):
                    return "bad_length"
                if ln < 1 or ln > 255:
                    return "bad_length"
            dv = col.get("default")
            if dv not in (None, ""):
                dv = str(dv)
                if len(dv) > 64 or not _re_db.match(r"^[A-Za-z0-9_:.\- ]+$", dv):
                    return "bad_default"
        return None

    def db_create_sandbox_table(self, domain: str, name: str, columns: list,
                                confirm: bool = False, source: str = "ui") -> dict:
        """Crée une table sandbox (préfixe fixe, types whitelistés) via bridge.

        Garde-fous Lumena (le bridge revalide) : flag activé, préfixe imposé,
        schéma whitelisté, confirmation. Jamais de SQL/secret en sortie.
        """
        domain = domain.strip().lower()
        if not confirm:
            return {"ok": False, "error": "not_confirmed", "message": "Confirmation explicite requise."}
        if not self._valid_sandbox_name(name):
            return {"ok": False, "error": "bad_prefix",
                    "message": f"Le nom doit commencer par '{_SANDBOX_PREFIX}' (a-z0-9_, ≤64)."}
        col_err = self._validate_sandbox_columns(columns)
        if col_err:
            return {"ok": False, "error": col_err, "message": "Schéma de colonnes invalide."}

        if not self.get_site_sandbox_config(domain)["enabled"]:
            return {"ok": False, "error": "sandbox_disabled",
                    "message": "Création de tables sandbox désactivée pour ce site."}
        cfg = self.get_site_database(domain, include_secret=True)
        if cfg is None:
            return {"ok": False, "error": "no_database", "message": "Aucune BDD configurée."}
        bridge = (self._sites[domain].get("database") or {}).get("bridge")
        if not bridge or not bridge.get("installed"):
            return {"ok": False, "error": "bridge_not_installed", "message": "Accès BDD sécurisé non installé."}
        if bridge.get("version") != _BRIDGE_VERSION:
            return {"ok": False, "upgrade_required": True, "error": "upgrade_required",
                    "message": "Accès sécurisé à mettre à jour : réinstalle l'accès BDD sécurisé."}

        secret = _decrypt(bridge["secret_encrypted"])
        ts = int(time.time())
        nonce = secrets.token_hex(16)
        creds = {
            "host": cfg["host"], "port": int(cfg.get("port", 3306) or 3306),
            "user": cfg["user"], "password": cfg.get("password", ""), "name": cfg["name"],
        }
        body = {
            "creds": _seal_creds(secret, creds, "db_create_table", ts, nonce),
            "name": name, "columns": columns,
        }
        col_names = [c.get("name") for c in columns]
        try:
            data = self._bridge_request(domain, secret, bridge["path"], "db_create_table",
                                        body, 20, ts=ts, nonce=nonce)
        except Exception:
            self._audit_db_write(domain, "create_table", name, [], col_names, None, None,
                                 False, "request_failed", source)
            return {"ok": False, "error": "request_failed", "message": "Connexion au bridge échouée."}
        ok = data.get("_http_status") == 200 and data.get("ok") is True
        err = "" if ok else str(data.get("error", f"http_{data.get('_http_status')}"))
        self._audit_db_write(domain, "create_table", name, [], col_names, None, None, ok, err, source)
        if not ok:
            return {"ok": False, "error": err, "message": "Création refusée par le bridge."}
        return {"ok": True, "table": name, "created": bool(data.get("created", True)), "message": ""}

    def _store_db_last_check(self, domain: str, last_check: dict) -> None:
        """Persiste le dernier statut de test BDD (non sensible) sur la fiche site."""
        db = self._sites.get(domain, {}).get("database")
        if isinstance(db, dict):
            db["last_check"] = last_check
            self._save_sites()

    def test_database_connection(self, domain: str) -> dict:
        """Teste la connexion à la BDD du site (Étape 2).

        Connexion réelle via PyMySQL, timeout court, **PING uniquement** — aucune
        lecture ni écriture de données (pas de SELECT/SHOW/DESCRIBE). Le mot de
        passe est déchiffré seulement ici, en mémoire, et n'est JAMAIS renvoyé.
        Met à jour `database.last_check` (statut non sensible) et le retourne.
        """
        domain = domain.strip().lower()
        cfg = self.get_site_database(domain, include_secret=True)  # KeyError si site absent
        checked_at = dt.datetime.now().isoformat(timespec="seconds")
        if cfg is None:
            return {
                "configured": False, "ok": False,
                "error": "Aucune BDD configurée pour ce site.",
                "message": "Aucune BDD configurée pour ce site.",
                "checked_at": checked_at, "latency_ms": 0,
            }

        try:
            timeout = int(os.getenv("LUMENA_IONOS_DB_TIMEOUT", "5"))
        except ValueError:
            timeout = 5

        # ── Voie BRIDGE (prioritaire si installé) — chemin normal IONOS mutualisé.
        bridge = (self._sites[domain].get("database") or {}).get("bridge")
        if bridge and bridge.get("installed"):
            if bridge.get("version") != _BRIDGE_VERSION:
                last_check = {
                    "ok": False, "checked_at": checked_at, "latency_ms": 0,
                    "error": f"bridge version {bridge.get('version')} < {_BRIDGE_VERSION}",
                }
                self._store_db_last_check(domain, last_check)
                return {
                    "configured": True, "via": "bridge", "upgrade_required": True,
                    "message": "Accès sécurisé à mettre à jour : réinstalle l'accès BDD sécurisé.",
                    **last_check,
                }
            ok, latency_ms, raw_err = self._bridge_db_ping(domain, bridge, cfg, max(timeout, 10))
            if ok:
                last_check = {"ok": True, "checked_at": checked_at, "latency_ms": latency_ms, "error": ""}
                user_message = ""
            else:
                last_check = {
                    "ok": False, "checked_at": checked_at, "latency_ms": latency_ms,
                    "error": _redact_db_error(raw_err, cfg.get("password", "")),
                }
                user_message = _classify_db_error(raw_err)
            self._store_db_last_check(domain, last_check)
            return {"configured": True, "via": "bridge", "message": user_message, **last_check}

        pymysql = _get_pymysql()
        if pymysql is None:
            last_check = {
                "ok": False, "checked_at": checked_at, "latency_ms": 0,
                "error": "pymysql non disponible (dépendance BDD non installée)",
            }
            self._store_db_last_check(domain, last_check)
            return {
                "configured": True, "degraded": True,
                "message": "Module BDD (pymysql) indisponible côté serveur.",
                **last_check,
            }

        try:
            timeout = int(os.getenv("LUMENA_IONOS_DB_TIMEOUT", "5"))
        except ValueError:
            timeout = 5

        db_host = cfg["host"]
        db_port = int(cfg.get("port", 3306) or 3306)
        db_user = cfg["user"]
        db_pass = cfg.get("password", "")
        db_name = cfg["name"]
        is_internal = db_host.lower().endswith(_IONOS_INTERNAL_DB_SUFFIXES)
        # Modes : auto (directe puis tunnel si échec), always (tunnel direct), off (directe seule).
        mode = os.getenv("LUMENA_IONOS_DB_TUNNEL", "auto").strip().lower()

        via = "direct"
        ok, latency_ms, raw_err = False, 0, ""

        if mode != "always":
            ok, latency_ms, raw_err = self._db_ping(
                pymysql, db_host, db_port, db_user, db_pass, db_name, timeout,
            )

        # Repli (ou voie directe) par tunnel SSH via l'hôte SFTP du site.
        if not ok and mode != "off":
            via = "ssh_tunnel"
            try:
                sftp = self._get_credentials(domain)
                with _SSHTunnel(sftp["host"], sftp.get("port", 22), sftp["user"],
                                sftp["password"], db_host, db_port, timeout) as tun:
                    ok, latency_ms, raw_err2 = self._db_ping(
                        pymysql, "127.0.0.1", tun.local_port, db_user, db_pass,
                        db_name, timeout,
                    )
                if not ok:
                    raw_err = raw_err2 or raw_err
            except Exception as e:
                raw_err = f"tunnel SSH: {e}" + (f" | direct: {raw_err}" if raw_err else "")

        if ok:
            last_check = {"ok": True, "checked_at": checked_at, "latency_ms": latency_ms, "error": ""}
            user_message = ""
        else:
            last_check = {
                "ok": False, "checked_at": checked_at, "latency_ms": latency_ms,
                "error": _redact_db_error(raw_err, db_pass),
            }
            user_message = self._db_failure_message(raw_err, is_internal, via)

        self._store_db_last_check(domain, last_check)
        return {"configured": True, "via": via, "message": user_message, **last_check}

    @staticmethod
    def _db_ping(pymysql, host, port, user, password, database, timeout):
        """PING de connexion (aucune donnée lue). Retourne (ok, latency_ms, error)."""
        conn = None
        start = time.perf_counter()
        try:
            conn = pymysql.connect(
                host=host, port=int(port or 3306), user=user, password=password,
                database=database, connect_timeout=timeout,
                read_timeout=timeout, write_timeout=timeout,
            )
            conn.ping(reconnect=False)
            return True, int((time.perf_counter() - start) * 1000), ""
        except Exception as e:
            return False, int((time.perf_counter() - start) * 1000), str(e)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    @staticmethod
    def _db_failure_message(raw_err: str, is_internal: bool, via: str) -> str:
        """Message utilisateur clair (jamais l'erreur brute)."""
        if is_internal:
            return (
                "Base IONOS interne (webspace) injoignable directement : ces BDD "
                "ne sont pas exposées hors de l'infra IONOS. "
                + ("Le tunnel SSH a aussi échoué — vérifie que l'accès SSH/forwarding "
                   "est activé sur le site IONOS, ou gère la BDD via phpMyAdmin / "
                   "active l'accès externe dans le panel."
                   if via == "ssh_tunnel" else
                   "Active le tunnel SSH (LUMENA_IONOS_DB_TUNNEL) ou l'accès externe IONOS.")
            )
        return _classify_db_error(raw_err)

    def _get_credentials(self, domain: str) -> dict:
        """Get site config with decrypted password."""
        domain = domain.strip().lower()
        info = self._sites.get(domain)
        if not info:
            raise KeyError(f"Site '{domain}' non trouvé. Utilisez ionos_add_site d'abord.")
        return {
            "host": info["host"],
            "user": info["user"],
            "password": _decrypt(info["password_encrypted"]),
            "port": info.get("port", 22),
            "root": info.get("root", "/"),
        }

    # ── SFTP connection ───────────────────────────────────────────────

    def _connect_sftp(self, host: str, user: str, password: str, port: int = 22):
        """Create an SFTP connection via paramiko.

        Uses SSHClient instead of raw Transport to support keyboard-interactive
        auth (required by IONOS and many shared hosting providers).
        """
        paramiko = _get_paramiko()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=host,
                port=port,
                username=user,
                password=password,
                look_for_keys=False,
                allow_agent=False,
                timeout=15,
            )
        except Exception as exc:
            if "Bad authentication type" in str(exc):
                allowed = getattr(exc, "allowed_types", [])
                if allowed == [""] or allowed == []:
                    raise ConnectionError(
                        f"Authentification SFTP refusée par {host}. "
                        "Le serveur rejette le mot de passe. "
                        "Vérifie dans le panneau IONOS que : "
                        "1) l'accès SFTP/SSH est activé, "
                        "2) le mot de passe SFTP est bien défini (il peut différer du mot de passe principal), "
                        "3) le nom d'utilisateur est correct."
                    ) from exc
            raise
        sftp = client.open_sftp()
        # Attach client to transport so we can close it later
        transport = client.get_transport()
        transport._ssh_client = client  # prevent GC + allow cleanup
        return sftp, transport

    def _test_connection_sync(self, host: str, user: str, password: str, port: int = 22):
        """Test SFTP connectivity (raises on failure)."""
        sftp = None
        transport = None
        try:
            sftp, transport = self._connect_sftp(host, user, password, port)
            sftp.listdir(".")
            logger.info(f"[IONOS] Connexion SFTP OK → {host}:{port}")
        finally:
            if sftp:
                sftp.close()
            self._close_transport(transport)

    @staticmethod
    def _close_transport(transport):
        """Close transport and its underlying SSHClient if any."""
        if not transport:
            return
        client = getattr(transport, "_ssh_client", None)
        try:
            transport.close()
        except Exception:
            pass
        if client:
            try:
                client.close()
            except Exception:
                pass

    # ── Path security ─────────────────────────────────────────────────

    @staticmethod
    def _is_forbidden(filepath: Path) -> bool:
        """Check if a file should never be uploaded."""
        name = filepath.name.lower()
        if name in _FORBIDDEN_FILES:
            return True
        if filepath.suffix.lower() in _FORBIDDEN_EXTENSIONS:
            return True
        return False

    @staticmethod
    def _validate_remote_path(path: str, root: str) -> str:
        """Validate and resolve a remote path (prevent traversal)."""
        # Normalize
        clean = path.replace("\\", "/")
        # Block traversal
        if ".." in clean.split("/"):
            raise ValueError(f"Path traversal interdit: {path}")
        # Resolve relative to root
        if not clean.startswith("/"):
            clean = root.rstrip("/") + "/" + clean
        return clean

    # ── Deploy ────────────────────────────────────────────────────────

    async def deploy(
        self,
        domain: str,
        local_dir: Path,
        *,
        dry_run: bool = False,
    ) -> DeployResult:
        """Deploy a local directory to an IONOS site via SFTP."""
        return await asyncio.to_thread(
            self._deploy_sync, domain, local_dir, dry_run=dry_run
        )

    def _deploy_sync(
        self,
        domain: str,
        local_dir: Path,
        *,
        dry_run: bool = False,
    ) -> DeployResult:
        creds = self._get_credentials(domain)
        local_dir = Path(local_dir)

        if not local_dir.is_dir():
            return DeployResult(
                success=False,
                errors=[f"Dossier local introuvable: {local_dir}"],
            )

        # Collect files
        files_to_upload: List[Tuple[Path, str]] = []
        total_size = 0
        skipped = 0

        for f in sorted(local_dir.rglob("*")):
            if not f.is_file():
                continue
            if self._is_forbidden(f):
                skipped += 1
                logger.debug(f"[IONOS] Skip interdit: {f.name}")
                continue
            rel = f.relative_to(local_dir).as_posix()
            remote = self._validate_remote_path(rel, creds["root"])
            files_to_upload.append((f, remote))
            total_size += f.stat().st_size

        # Size guard
        max_mb = int(os.getenv("LUMENA_IONOS_MAX_UPLOAD_MB", "100"))
        if total_size > max_mb * 1024 * 1024:
            return DeployResult(
                success=False,
                errors=[
                    f"Taille totale {total_size / 1048576:.1f} Mo dépasse la limite de {max_mb} Mo."
                ],
            )

        if dry_run:
            return DeployResult(
                success=True,
                uploaded=len(files_to_upload),
                skipped=skipped,
                total_bytes=total_size,
                dry_run=True,
            )

        # Backup before deploy (if enabled)
        do_backup = os.getenv("LUMENA_IONOS_BACKUP_BEFORE_DEPLOY", "1") == "1"
        if do_backup:
            try:
                self._backup_remote_sync(domain, creds)
            except Exception as e:
                logger.warning(f"[IONOS] Backup échoué (deploy continue): {e}")

        # Upload
        start = time.monotonic()
        sftp = None
        transport = None
        errors: List[str] = []
        uploaded = 0

        try:
            sftp, transport = self._connect_sftp(
                creds["host"], creds["user"], creds["password"], creds["port"]
            )

            for local_path, remote_path in files_to_upload:
                try:
                    # Ensure remote directory exists
                    remote_dir = "/".join(remote_path.split("/")[:-1])
                    self._mkdir_p(sftp, remote_dir)
                    sftp.put(str(local_path), remote_path)
                    uploaded += 1
                except Exception as e:
                    errors.append(f"{remote_path}: {e}")
                    logger.error(f"[IONOS] Upload échoué {remote_path}: {e}")

        except Exception as e:
            errors.append(f"Connexion SFTP: {e}")
        finally:
            if sftp:
                sftp.close()
            self._close_transport(transport)

        duration = time.monotonic() - start

        # Update site stats
        site = self._sites.get(domain.strip().lower())
        if site:
            site["last_deploy"] = dt.datetime.now().isoformat(timespec="seconds")
            site["deploy_count"] = site.get("deploy_count", 0) + 1
            self._save_sites()

        return DeployResult(
            success=len(errors) == 0,
            uploaded=uploaded,
            skipped=skipped,
            errors=errors,
            total_bytes=total_size,
            duration_sec=round(duration, 2),
        )

    # ── Upload specific files ─────────────────────────────────────────

    async def upload_files(
        self,
        domain: str,
        files: List[Tuple[str, Path]],
    ) -> DeployResult:
        """Upload specific files (remote_path, local_path) pairs."""
        return await asyncio.to_thread(self._upload_files_sync, domain, files)

    def _upload_files_sync(
        self,
        domain: str,
        files: List[Tuple[str, Path]],
    ) -> DeployResult:
        creds = self._get_credentials(domain)
        start = time.monotonic()
        sftp = None
        transport = None
        errors: List[str] = []
        uploaded = 0
        total_bytes = 0

        try:
            sftp, transport = self._connect_sftp(
                creds["host"], creds["user"], creds["password"], creds["port"]
            )

            for remote_rel, local_path in files:
                local_path = Path(local_path)
                if not local_path.is_file():
                    errors.append(f"Fichier local introuvable: {local_path}")
                    continue
                if self._is_forbidden(local_path):
                    errors.append(f"Fichier interdit: {local_path.name}")
                    continue

                remote = self._validate_remote_path(remote_rel, creds["root"])
                try:
                    remote_dir = "/".join(remote.split("/")[:-1])
                    self._mkdir_p(sftp, remote_dir)
                    sftp.put(str(local_path), remote)
                    uploaded += 1
                    total_bytes += local_path.stat().st_size
                except Exception as e:
                    errors.append(f"{remote}: {e}")

        except Exception as e:
            errors.append(f"Connexion SFTP: {e}")
        finally:
            if sftp:
                sftp.close()
            self._close_transport(transport)

        return DeployResult(
            success=len(errors) == 0,
            uploaded=uploaded,
            errors=errors,
            total_bytes=total_bytes,
            duration_sec=round(time.monotonic() - start, 2),
        )

    # ── List remote ───────────────────────────────────────────────────

    async def list_remote(
        self, domain: str, path: str = "/"
    ) -> List[RemoteFile]:
        return await asyncio.to_thread(self._list_remote_sync, domain, path)

    def _list_remote_sync(self, domain: str, path: str = "/") -> List[RemoteFile]:
        creds = self._get_credentials(domain)
        remote_path = self._validate_remote_path(path, creds["root"])

        sftp = None
        transport = None
        results: List[RemoteFile] = []

        try:
            sftp, transport = self._connect_sftp(
                creds["host"], creds["user"], creds["password"], creds["port"]
            )

            for entry in sftp.listdir_attr(remote_path):
                is_dir = stat.S_ISDIR(entry.st_mode) if entry.st_mode else False
                mtime = None
                if entry.st_mtime:
                    mtime = dt.datetime.fromtimestamp(entry.st_mtime).isoformat(
                        timespec="seconds"
                    )
                results.append(RemoteFile(
                    path=f"{remote_path.rstrip('/')}/{entry.filename}",
                    size=entry.st_size or 0,
                    is_dir=is_dir,
                    modified=mtime,
                ))

        except Exception as e:
            logger.error(f"[IONOS] List remote échoué: {e}")
            raise
        finally:
            if sftp:
                sftp.close()
            self._close_transport(transport)

        return results

    # ── Delete remote ─────────────────────────────────────────────────

    async def delete_remote(
        self, domain: str, paths: List[str]
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self._delete_remote_sync, domain, paths)

    def _delete_remote_sync(
        self, domain: str, paths: List[str]
    ) -> Dict[str, Any]:
        creds = self._get_credentials(domain)
        sftp = None
        transport = None
        deleted = 0
        errors: List[str] = []

        try:
            sftp, transport = self._connect_sftp(
                creds["host"], creds["user"], creds["password"], creds["port"]
            )

            for p in paths:
                remote = self._validate_remote_path(p, creds["root"])
                try:
                    sftp.remove(remote)
                    deleted += 1
                except Exception as e:
                    errors.append(f"{remote}: {e}")

        except Exception as e:
            errors.append(f"Connexion SFTP: {e}")
        finally:
            if sftp:
                sftp.close()
            self._close_transport(transport)

        return {
            "success": len(errors) == 0,
            "deleted": deleted,
            "errors": errors,
        }

    # ── Test connection ───────────────────────────────────────────────

    async def test_connection(self, domain: str) -> bool:
        creds = self._get_credentials(domain)
        try:
            await asyncio.to_thread(
                self._test_connection_sync,
                creds["host"], creds["user"], creds["password"], creds["port"],
            )
            return True
        except Exception:
            return False

    # ── Backup ────────────────────────────────────────────────────────

    def _backup_remote_sync(self, domain: str, creds: dict):
        """Download existing remote files to local backup dir."""
        sftp = None
        transport = None
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = _BACKUPS_DIR / domain / ts
        backup_dir.mkdir(parents=True, exist_ok=True)

        try:
            sftp, transport = self._connect_sftp(
                creds["host"], creds["user"], creds["password"], creds["port"]
            )
            self._download_dir(sftp, creds["root"] or "/", backup_dir)
            logger.info(f"[IONOS] Backup → {backup_dir}")
        finally:
            if sftp:
                sftp.close()
            self._close_transport(transport)

    def _download_dir(self, sftp, remote_dir: str, local_dir: Path):
        """Recursively download a remote directory."""
        try:
            entries = sftp.listdir_attr(remote_dir)
        except Exception:
            return

        for entry in entries:
            remote_path = f"{remote_dir.rstrip('/')}/{entry.filename}"
            local_path = local_dir / entry.filename

            if stat.S_ISDIR(entry.st_mode) if entry.st_mode else False:
                local_path.mkdir(exist_ok=True)
                self._download_dir(sftp, remote_path, local_path)
            else:
                try:
                    sftp.get(remote_path, str(local_path))
                except Exception as e:
                    logger.debug(f"[IONOS] Backup skip {remote_path}: {e}")

    # ── SFTP helpers ──────────────────────────────────────────────────

    @staticmethod
    def _mkdir_p(sftp, remote_dir: str):
        """Recursively create remote directories (like mkdir -p)."""
        if not remote_dir or remote_dir == "/":
            return
        dirs = remote_dir.split("/")
        current = ""
        for d in dirs:
            if not d:
                current = "/"
                continue
            current = f"{current}/{d}" if current != "/" else f"/{d}"
            try:
                sftp.stat(current)
            except FileNotFoundError:
                sftp.mkdir(current)


# ── Singleton partagé (processus) ─────────────────────────────────────────
# Un SEUL IonosDeployer pour tout le process : sinon l'état en mémoire (_sites,
# write_enabled/allowlist…) diverge entre le déployeur des handlers ReAct et celui
# des routes web. Bug observé : propose_write (handler) active write_enabled + save
# disque, mais l'instance web garde un _sites périmé → approve renvoie write_disabled.
_shared_deployer: Optional["IonosDeployer"] = None


def get_shared_deployer() -> "IonosDeployer":
    """Retourne l'unique instance IonosDeployer du process (création paresseuse)."""
    global _shared_deployer
    if _shared_deployer is None:
        _shared_deployer = IonosDeployer()
    return _shared_deployer
