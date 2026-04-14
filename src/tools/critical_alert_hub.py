"""
🚨 LUMENA - Critical Alert Hub

Envoi d'alertes critiques par SMS et appel via Twilio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
import base64
import json
import os
import threading
from ..utils.persistence import atomic_write_json, safe_read_json
import urllib.parse
import urllib.request
from loguru import logger


@dataclass
class AlertConfig:
    enabled: bool
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_from_number: str
    default_to_number: str
    sms_enabled: bool
    call_enabled: bool
    cooldown_sec: int


class CriticalAlertHub:
    """Hub d'alerte critique (SMS + Appel)."""

    _ALLOWED_SEVERITIES = {"info", "medium", "high", "critical"}
    _CALL_ALLOWED = {"high", "critical"}

    def __init__(self, data_root: Path):
        self.data_root = Path(data_root)
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.log_file = self.data_root / "critical_alerts.log"
        self.state_file = self.data_root / "critical_alerts_state.json"
        self._lock = threading.Lock()

    @staticmethod
    def _env_flag(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _normalize_phone(number: str) -> str:
        value = str(number or "").strip()
        if not value:
            return ""
        if value.startswith("+"):
            return value
        digits = "".join(ch for ch in value if ch.isdigit())
        if not digits:
            return ""
        return f"+{digits}"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _load_config(self) -> AlertConfig:
        return AlertConfig(
            enabled=self._env_flag("LUMENA_CRITICAL_ALERTS_ENABLED", False),
            twilio_account_sid=str(os.getenv("TWILIO_ACCOUNT_SID", "")).strip(),
            twilio_auth_token=str(os.getenv("TWILIO_AUTH_TOKEN", "")).strip(),
            twilio_from_number=self._normalize_phone(os.getenv("TWILIO_FROM_NUMBER", "")),
            default_to_number=self._normalize_phone(os.getenv("LUMENA_ALERT_TO_NUMBER", "")),
            sms_enabled=self._env_flag("LUMENA_CRITICAL_SMS_ENABLED", True),
            call_enabled=self._env_flag("LUMENA_CRITICAL_CALL_ENABLED", True),
            cooldown_sec=max(0, int(str(os.getenv("LUMENA_CRITICAL_ALERT_COOLDOWN_SEC", "300")).strip() or "300")),
        )

    def _append_log(self, payload: Dict[str, Any]) -> None:
        try:
            line = json.dumps(payload, ensure_ascii=False)
            with self.log_file.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception as exc:
            logger.debug("Alert log write skipped: {}", exc)

    def _load_state(self) -> Dict[str, Any]:
        return safe_read_json(self.state_file, default={})

    def _save_state(self, state: Dict[str, Any]) -> None:
        try:
            atomic_write_json(self.state_file, state)
        except Exception as exc:
            logger.debug("Alert state write skipped: {}", exc)

    def _check_cooldown(self, target: str, channel: str, cooldown_sec: int) -> Optional[str]:
        if cooldown_sec <= 0:
            return None

        state = self._load_state()
        key = f"{channel}:{target}"
        last_raw = str(state.get(key, "")).strip()
        if last_raw:
            try:
                last = datetime.fromisoformat(last_raw)
                wait_until = last + timedelta(seconds=cooldown_sec)
                now = datetime.now()
                if now < wait_until:
                    remaining = int((wait_until - now).total_seconds())
                    return f"Cooldown actif ({remaining}s restantes)"
            except Exception as e:
                logger.debug(f"Parse cooldown: {e}")
        return None

    def _touch_cooldown(self, target: str, channel: str) -> None:
        with self._lock:
            state = self._load_state()
            state[f"{channel}:{target}"] = self._now_iso()
            self._save_state(state)

    def _validate_ready(self, cfg: AlertConfig) -> Optional[str]:
        if not cfg.enabled:
            return "Alertes critiques désactivées (LUMENA_CRITICAL_ALERTS_ENABLED=0)."
        if not cfg.twilio_account_sid or not cfg.twilio_auth_token:
            return "Twilio non configuré (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN manquants)."
        if not cfg.twilio_from_number:
            return "TWILIO_FROM_NUMBER manquant."
        return None

    @staticmethod
    def _auth_header(account_sid: str, auth_token: str) -> str:
        token = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    @staticmethod
    def _post_form(url: str, form_data: Dict[str, str], auth_header: str) -> Dict[str, Any]:
        data = urllib.parse.urlencode(form_data).encode("utf-8")
        req = urllib.request.Request(url=url, data=data, method="POST")
        req.add_header("Authorization", auth_header)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            payload = ""
            try:
                payload = exc.read().decode("utf-8", errors="replace")
            except Exception:
                payload = ""

            twilio_code = ""
            twilio_msg = ""
            if payload:
                try:
                    parsed = json.loads(payload)
                    twilio_code = str(parsed.get("code", "") or "").strip()
                    twilio_msg = str(parsed.get("message", "") or "").strip()
                except Exception:
                    twilio_msg = payload[:300]

            detail = f"HTTP {exc.code}"
            if twilio_code:
                detail += f" / Twilio {twilio_code}"
            if twilio_msg:
                detail += f": {twilio_msg}"
            raise RuntimeError(detail) from exc

    @staticmethod
    def _sanitize_severity(severity: str) -> str:
        value = str(severity or "high").strip().lower()
        if value not in CriticalAlertHub._ALLOWED_SEVERITIES:
            return "high"
        return value

    def send_critical_sms(self, message: str, to_number: str = "", severity: str = "high") -> Dict[str, Any]:
        cfg = self._load_config()
        severity = self._sanitize_severity(severity)
        target = self._normalize_phone(to_number) or cfg.default_to_number

        if not cfg.sms_enabled:
            return {"success": False, "error": "SMS critique désactivé (LUMENA_CRITICAL_SMS_ENABLED=0)."}
        if severity not in {"high", "critical"}:
            return {"success": False, "error": "Severité insuffisante pour SMS (utiliser high/critical)."}
        if not target:
            return {"success": False, "error": "Numéro destinataire manquant (LUMENA_ALERT_TO_NUMBER)."}

        ready_error = self._validate_ready(cfg)
        if ready_error:
            return {"success": False, "error": ready_error}

        cooldown_error = self._check_cooldown(target, "sms", cfg.cooldown_sec)
        if cooldown_error:
            return {"success": False, "error": cooldown_error}

        body = f"[LUMENA {severity.upper()}] {str(message or '').strip()}"
        body = body[:1500]

        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{cfg.twilio_account_sid}/Messages.json"
            payload = {
                "From": cfg.twilio_from_number,
                "To": target,
                "Body": body,
            }
            data = self._post_form(url, payload, self._auth_header(cfg.twilio_account_sid, cfg.twilio_auth_token))
            sid = str(data.get("sid", "")).strip()

            self._touch_cooldown(target, "sms")
            self._append_log({
                "ts": self._now_iso(),
                "channel": "sms",
                "severity": severity,
                "to": target,
                "sid": sid,
                "ok": True,
            })
            return {"success": True, "channel": "sms", "to": target, "sid": sid, "severity": severity}
        except Exception as exc:
            self._append_log({
                "ts": self._now_iso(),
                "channel": "sms",
                "severity": severity,
                "to": target,
                "ok": False,
                "error": str(exc),
            })
            return {"success": False, "error": f"Twilio SMS erreur: {exc}"}

    @staticmethod
    def _build_twimlet_url(message: str) -> str:
        text = str(message or "").strip()[:400]
        encoded = urllib.parse.urlencode({"Message[0]": text})
        return f"https://twimlets.com/message?{encoded}"

    def place_critical_call(self, message: str, to_number: str = "", severity: str = "critical") -> Dict[str, Any]:
        cfg = self._load_config()
        severity = self._sanitize_severity(severity)
        target = self._normalize_phone(to_number) or cfg.default_to_number

        if not cfg.call_enabled:
            return {"success": False, "error": "Appel critique désactivé (LUMENA_CRITICAL_CALL_ENABLED=0)."}
        if severity not in self._CALL_ALLOWED:
            return {"success": False, "error": "Severité insuffisante pour appel (utiliser high/critical)."}
        if not target:
            return {"success": False, "error": "Numéro destinataire manquant (LUMENA_ALERT_TO_NUMBER)."}

        ready_error = self._validate_ready(cfg)
        if ready_error:
            return {"success": False, "error": ready_error}

        cooldown_error = self._check_cooldown(target, "call", cfg.cooldown_sec)
        if cooldown_error:
            return {"success": False, "error": cooldown_error}

        spoken = f"Alerte Lumena {severity}. {str(message or '').strip()}"
        twiml_url = str(os.getenv("LUMENA_CRITICAL_CALL_TWIML_URL", "")).strip() or self._build_twimlet_url(spoken)

        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{cfg.twilio_account_sid}/Calls.json"
            payload = {
                "From": cfg.twilio_from_number,
                "To": target,
                "Url": twiml_url,
            }
            data = self._post_form(url, payload, self._auth_header(cfg.twilio_account_sid, cfg.twilio_auth_token))
            sid = str(data.get("sid", "")).strip()

            self._touch_cooldown(target, "call")
            self._append_log({
                "ts": self._now_iso(),
                "channel": "call",
                "severity": severity,
                "to": target,
                "sid": sid,
                "ok": True,
            })
            return {"success": True, "channel": "call", "to": target, "sid": sid, "severity": severity}
        except Exception as exc:
            self._append_log({
                "ts": self._now_iso(),
                "channel": "call",
                "severity": severity,
                "to": target,
                "ok": False,
                "error": str(exc),
            })
            return {"success": False, "error": f"Twilio Call erreur: {exc}"}

    def notify_critical(
        self,
        message: str,
        to_number: str = "",
        severity: str = "critical",
        prefer: str = "auto",
    ) -> Dict[str, Any]:
        severity = self._sanitize_severity(severity)
        pref = str(prefer or "auto").strip().lower()
        if pref not in {"auto", "sms", "call", "both"}:
            pref = "auto"

        if pref == "sms":
            return self.send_critical_sms(message=message, to_number=to_number, severity=severity)
        if pref == "call":
            return self.place_critical_call(message=message, to_number=to_number, severity=severity)

        if pref == "both":
            sms = self.send_critical_sms(message=message, to_number=to_number, severity=severity)
            call = self.place_critical_call(message=message, to_number=to_number, severity=severity)
            return {
                "success": bool(sms.get("success") or call.get("success")),
                "sms": sms,
                "call": call,
            }

        # auto
        if severity == "critical":
            call = self.place_critical_call(message=message, to_number=to_number, severity=severity)
            if call.get("success"):
                return {"success": True, "primary": "call", "call": call}
            sms = self.send_critical_sms(message=message, to_number=to_number, severity=severity)
            return {"success": bool(sms.get("success")), "primary": "sms", "call": call, "sms": sms}

        sms = self.send_critical_sms(message=message, to_number=to_number, severity=severity)
        return {"success": bool(sms.get("success")), "primary": "sms", "sms": sms}
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
