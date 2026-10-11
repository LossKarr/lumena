"""Authenticated one-click installation state for the local Qwen3-TTS runtime."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any, Callable, Mapping

from src.runtime.component_manifest import ComponentManifest
from src.runtime.component_service import (
    ComponentServiceError,
    install_release_components,
    load_component_trusted_keys,
)
from src.runtime.component_store import ComponentStore
from src.utils.file_lock import ProcessFileLock
from src.utils.paths import ROOT_DIR, data_dir_for_root
from src.utils.persistence import atomic_write_json, safe_read_json
from src.version import __version__

from .qwen_online_installer import install_qwen_from_recipe
from .qwen_recipe import QwenInstallRecipe, load_qwen_install_recipe


logger = logging.getLogger(__name__)
_ACTIVE_STATES = {"preparing", "downloading", "installing", "verifying"}
_FINAL_COMPONENTS = {"qwen-model", "qwen-tokenizer", "lumena-voice"}
_UNPUBLISHED_MINIMUM_DISK_BYTES = 12 * 1024 * 1024 * 1024
_INSTALL_HEADROOM_BYTES = 512 * 1024 * 1024


def _safe_message(value: object, default: str = "Installation Qwen3-TTS impossible") -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return (text or default)[:240]


class QwenInstallService:
    """Run the long signed install outside the API request and expose bounded state."""

    def __init__(
        self, *, root: Path | None = None, data_dir: Path | None = None,
        manifest_path: Path | None = None, trusted_keys_path: Path | None = None,
        recipe_path: Path | None = None,
        release_version: str = __version__,
        installer: Callable[..., dict[str, Any]] = install_release_components,
        online_installer: Callable[..., dict[str, Any]] = install_qwen_from_recipe,
        finalizer: Callable[[Path], dict[str, Any]] | None = None,
        canary: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        app_root = Path(root or ROOT_DIR).resolve()
        resolved_data_dir = Path(data_dir or data_dir_for_root(app_root)).resolve()
        configured_component_root = os.getenv("LUMENA_COMPONENT_ROOT", "").strip()
        self.app_root = app_root
        self.root = Path(configured_component_root).expanduser().resolve() \
            if configured_component_root else (
                app_root if root is not None else resolved_data_dir / "components"
            )
        self.data_dir = resolved_data_dir
        self.manifest_path = Path(
            manifest_path
            or os.getenv("LUMENA_COMPONENT_MANIFEST_PATH", "").strip()
            or app_root / "installer" / "component-manifest.json"
        ).resolve()
        self.trusted_keys_path = Path(
            trusted_keys_path
            or os.getenv("LUMENA_COMPONENT_TRUSTED_KEYS_PATH", "").strip()
            or app_root / "installer" / "component-trusted-keys.json"
        ).resolve()
        self.recipe_path = Path(
            recipe_path
            or os.getenv("LUMENA_QWEN_RECIPE_PATH", "").strip()
            or app_root / "config" / "components" / "qwen3_tts_windows_x64.json"
        ).resolve()
        self.release_version = str(release_version)
        self.installer = installer
        self.online_installer = online_installer
        self._finalizer = finalizer
        self._canary = canary
        self.state_path = self.data_dir / "voice" / "qwen-install-state.json"
        self.lock_path = self.root / ".qwen-install.lock"
        self._thread: threading.Thread | None = None
        self._guard = threading.RLock()
        self._downloads: dict[str, tuple[int, int]] = {}
        self._download_totals: dict[str, int] = {}
        self._last_progress_percent = 0
        previous = safe_read_json(self.state_path, default={})
        if isinstance(previous, Mapping) and previous.get("state") in _ACTIVE_STATES:
            recovery_probe = ProcessFileLock(self.lock_path, "lumena-qwen-install-recovery")
            if recovery_probe.acquire():
                recovery_probe.release()
                self._write_state(
                    state="failed", progress_percent=0,
                    message="Installation interrompue. Vous pouvez la reprendre.",
                    error_code="interrupted", retryable=True,
                )

    def _write_state(self, **changes: Any) -> dict[str, Any]:
        with self._guard:
            current = safe_read_json(self.state_path, default={})
            if not isinstance(current, Mapping) or current.get("schema_version") != 1:
                current = {}
            value = {
                "schema_version": 1,
                "state": "idle",
                "progress_percent": 0,
                "message": "Qwen3-TTS peut être installé localement.",
                "error_code": None,
                "retryable": True,
                "started_at": None,
                "finished_at": None,
                **dict(current),
                **changes,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            if current.get("state") in _ACTIVE_STATES and value.get("state") in _ACTIVE_STATES:
                value["progress_percent"] = max(
                    int(current.get("progress_percent", 0)),
                    int(value.get("progress_percent", 0)),
                )
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.state_path, value)
            return value

    def _verified_manifest(self) -> ComponentManifest:
        if not self.manifest_path.is_file() or not self.trusted_keys_path.is_file():
            raise ComponentServiceError("release_assets_missing")
        keys = load_component_trusted_keys(self.trusted_keys_path)
        manifest = ComponentManifest.from_json(
            self.manifest_path.read_bytes(), trusted_keys=keys,
        )
        if manifest.release_version != self.release_version:
            raise ComponentServiceError("release_version_mismatch")
        ids = {item.component_id for item in manifest.components}
        if not _FINAL_COMPONENTS.issubset(ids) or not any(
            item.startswith("qwen-runtime-shard-") for item in ids
        ):
            raise ComponentServiceError("release_components_incomplete")
        return manifest

    def _verified_recipe(self) -> QwenInstallRecipe:
        try:
            recipe = load_qwen_install_recipe(self.recipe_path)
            recipe.assert_host_compatible()
            return recipe
        except Exception as exc:
            code = str(exc)
            if code not in {"unsupported_platform", "unsupported_python"}:
                code = "installation_recipe_invalid"
            raise ComponentServiceError(code) from exc

    def _installation_source(self) -> tuple[str, ComponentManifest | QwenInstallRecipe]:
        manifest_exists = self.manifest_path.is_file()
        keys_exist = self.trusted_keys_path.is_file()
        if manifest_exists or keys_exist:
            if not (manifest_exists and keys_exist):
                raise ComponentServiceError("release_assets_incomplete")
            return "signed_release", self._verified_manifest()
        return "public_recipe", self._verified_recipe()

    def _installed(self) -> bool:
        store = ComponentStore(self.root)
        components_ready = all(store.active(component_id) is not None for component_id in (
            "qwen-runtime", "qwen-model",
        ))
        if not components_ready:
            return False
        from .qwen_runtime import resolve_qwen_paths

        paths = resolve_qwen_paths(root=self.root, store=store)
        python = paths.get("python")
        model_dir = paths.get("model_dir")
        if not isinstance(python, Path) or not python.is_file() \
                or not isinstance(model_dir, Path) \
                or not (model_dir / "config.json").is_file() \
                or not (model_dir / "model.safetensors").is_file() \
                or not (model_dir / "speech_tokenizer" / "model.safetensors").is_file():
            return False
        if store.active("lumena-voice") is not None:
            return True
        from .qwen_identity import local_qwen_identity_ready
        return local_qwen_identity_ready(self.data_dir / "voice")

    def status(self) -> dict[str, Any]:
        with self._guard:
            raw = safe_read_json(self.state_path, default={})
            state = dict(raw) if isinstance(raw, Mapping) and raw.get("schema_version") == 1 else {}
            installed = self._installed()
            try:
                source, specification = self._installation_source()
                available = True
                if isinstance(specification, ComponentManifest):
                    required_bytes = sum(item.size for item in specification.components)
                    required_disk_bytes = sum(
                        item.size + item.unpacked_size for item in specification.components
                    ) + _INSTALL_HEADROOM_BYTES
                else:
                    required_bytes = specification.required_download_bytes
                    required_disk_bytes = specification.required_disk_bytes
                unavailable_reason = None
            except Exception as exc:
                available = False
                source = None
                required_bytes = None
                required_disk_bytes = _UNPUBLISHED_MINIMUM_DISK_BYTES
                code = str(exc)
                unavailable_reason = (
                    code if code in {"unsupported_platform", "unsupported_python"}
                    else "installation_recipe_invalid"
                )
            free_disk_bytes = shutil.disk_usage(self.root).free
            disk_ok = free_disk_bytes >= required_disk_bytes
            blockers: list[str] = []
            if not available:
                blockers.append(str(unavailable_reason or "release_invalid"))
            if not disk_ok:
                blockers.append("insufficient_disk_space")
            if installed and state.get("state") not in _ACTIVE_STATES:
                ready_message = (
                    state.get("message")
                    if state.get("state") == "ready" and state.get("message")
                    else "Qwen3-TTS est installé."
                )
                state.update({
                    "state": "ready", "progress_percent": 100,
                    "message": ready_message, "error_code": None,
                })
            elif not installed and not available and not state.get("message"):
                state["message"] = (
                    "Cette machine n'est pas compatible avec le moteur Qwen3-TTS local."
                    if unavailable_reason in {"unsupported_platform", "unsupported_python"}
                    else "La recette d'installation Qwen3-TTS est absente ou invalide."
                )
            return {
                "schema_version": 1,
                "state": state.get("state", "ready" if installed else "idle"),
                "available": available,
                "installed": installed,
                "progress_percent": max(0, min(100, int(state.get("progress_percent", 0)))),
                "message": _safe_message(state.get("message"), "Qwen3-TTS non installé"),
                "error_code": state.get("error_code"),
                "retryable": bool(state.get("retryable", True)),
                "required_download_bytes": required_bytes,
                "required_disk_bytes": required_disk_bytes,
                "free_disk_bytes": free_disk_bytes,
                "disk_ok": disk_ok,
                "blockers": blockers,
                "unavailable_reason": unavailable_reason,
                "install_source": source,
                "updated_at": state.get("updated_at"),
            }

    def start(self) -> dict[str, Any]:
        with self._guard:
            if self._thread is not None and self._thread.is_alive():
                return self.status()
            if self._installed():
                previous = safe_read_json(self.state_path, default={})
                previous_message = (
                    str(previous.get("message", ""))
                    if isinstance(previous, Mapping) and previous.get("state") == "ready"
                    else ""
                )
                self._write_state(
                    state="ready", progress_percent=100,
                    message=previous_message or "Qwen3-TTS est installé.",
                    error_code=None, retryable=False,
                )
                return self.status()
            source, specification = self._installation_source()
            if isinstance(specification, ComponentManifest):
                required = sum(
                    item.size + item.unpacked_size for item in specification.components
                ) + _INSTALL_HEADROOM_BYTES
            else:
                required = specification.required_disk_bytes
            if shutil.disk_usage(self.root).free < required:
                raise ComponentServiceError("insufficient_disk_space")
            install_lock = ProcessFileLock(self.lock_path, "lumena-qwen-install")
            if not install_lock.acquire():
                raise ComponentServiceError("another_installation_running")
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._downloads.clear()
            self._last_progress_percent = 0
            if isinstance(specification, ComponentManifest):
                self._download_totals = {
                    item.component_id: int(item.size) for item in specification.components
                }
            else:
                self._download_totals = {
                    f"model:{item.path}": int(item.size)
                    for item in specification.model_files
                }
            self._write_state(
                state="preparing", progress_percent=1,
                message="Préparation de l'installation sécurisée…",
                error_code=None, retryable=True, started_at=now, finished_at=None,
            )
            try:
                self._thread = threading.Thread(
                    target=self._run, args=(source, specification, install_lock),
                    name="qwen-component-install", daemon=True,
                )
                self._thread.start()
            except Exception:
                install_lock.release()
                raise
            return self.status()

    def _on_event(self, event: str) -> None:
        if event == "online.runtime.prepare":
            label, progress, state = "Création du moteur Qwen isolé…", 5, "installing"
        elif event == "online.runtime.packages":
            label, progress, state = "Installation vérifiée du moteur local…", 10, "installing"
        elif event == "online.model.download":
            label, progress, state = "Téléchargement du modèle officiel Qwen…", 20, "downloading"
        elif event == "online.activate":
            label, progress, state = "Activation atomique des composants…", 84, "installing"
        elif event.startswith("component.download:"):
            label, progress = "Téléchargement des composants…", 4
            state = "downloading"
        elif event.startswith("component.verify:"):
            label, progress = "Vérification des signatures et empreintes…", 76
            state = "verifying"
        else:
            label, progress = "Installation atomique des composants…", 82
            state = "installing"
        self._write_state(state=state, progress_percent=progress, message=label)

    def _on_bytes(self, component_id: str, written: int, total: int) -> None:
        with self._guard:
            self._downloads[str(component_id)] = (
                max(0, min(int(written), int(total))), max(1, int(total)),
            )
            downloaded = sum(item[0] for item in self._downloads.values())
            declared = sum(self._download_totals.values()) or sum(
                item[1] for item in self._downloads.values()
            )
            percent = 4 + int(68 * downloaded / max(1, declared))
            percent = min(72, percent)
            if percent <= self._last_progress_percent:
                return
            self._last_progress_percent = percent
        self._write_state(
            state="downloading", progress_percent=percent,
            message="Téléchargement sécurisé en cours…",
        )

    def _run(
        self, source: str, specification: ComponentManifest | QwenInstallRecipe,
        lock: ProcessFileLock,
    ) -> None:
        try:
            if source == "signed_release":
                self.installer(
                    root=self.root, manifest_path=self.manifest_path,
                    trusted_keys_path=self.trusted_keys_path,
                    expected_release=self.release_version,
                    progress=self._on_event, byte_progress=self._on_bytes,
                )
            else:
                if not isinstance(specification, QwenInstallRecipe):
                    raise ComponentServiceError("installation_recipe_invalid")
                self.online_installer(
                    root=self.root, recipe=specification,
                    progress=self._on_event, byte_progress=self._on_bytes,
                )
            self._write_state(
                state="verifying", progress_percent=90,
                message="Préparation de l'identité vocale locale…",
            )
            if self._finalizer is not None:
                finalizer = self._finalizer
            elif source == "signed_release":
                from .component_install import activate_installed_voice_pack

                def finalizer(root: Path) -> dict[str, Any]:
                    return activate_installed_voice_pack(root=root)
            else:
                from .qwen_identity import ensure_local_qwen_identity

                def finalizer(_root: Path) -> dict[str, Any]:
                    return ensure_local_qwen_identity(
                        app_root=self.app_root, voice_dir=self.data_dir / "voice",
                    )
            finalizer(self.root)
            self._write_state(
                state="verifying", progress_percent=95,
                message="Test audio local réel…",
            )
            if self._canary is None:
                from .component_install import run_qwen_install_canary
                canary = run_qwen_install_canary
            else:
                canary = self._canary
            result = canary()
            qualified = bool(result.get("qualified"))
            self._write_state(
                state="ready", progress_percent=100,
                message=(
                    "Qwen3-TTS est installé et prêt."
                    if qualified else
                    "Qwen3-TTS est installé ; Piper reste prioritaire sur ce PC."
                ),
                error_code=None, retryable=False,
                finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
        except Exception as exc:
            logger.exception("Qwen3-TTS one-click installation failed")
            known_codes = {
                "release_assets_missing", "release_version_mismatch",
                "release_components_incomplete", "insufficient_disk_space",
                "release_assets_incomplete", "another_installation_running",
                "unsupported_platform", "unsupported_python",
                "installation_recipe_invalid",
                "python_runtime_missing", "venv_creation_failed",
                "runtime_install_timeout", "runtime_process_failed",
                "runtime_command_failed", "runtime_probe_failed",
                "huggingface_client_missing", "model_download_failed",
                "model_file_size_mismatch", "model_checksum_mismatch",
                "model_path_escaped",
                "bundled_piper_unavailable", "piper_reference_generation_failed",
                "piper_reference_audio_invalid", "local_identity_verification_failed",
            }
            code = str(exc) if str(exc) in known_codes else "installation_failed"
            messages = {
                "insufficient_disk_space": "Espace disque insuffisant pour Qwen3-TTS.",
                "another_installation_running": "Une autre installation est déjà en cours.",
                "release_version_mismatch": "Le paquet Qwen ne correspond pas à cette version de Lumena.",
                "unsupported_platform": "Qwen3-TTS n'est pas compatible avec cette plateforme.",
                "unsupported_python": "Qwen3-TTS demande le runtime Python 3.12 de Lumena.",
                "venv_creation_failed": "Le moteur Python isolé n'a pas pu être créé.",
                "runtime_install_timeout": "L'installation du moteur local a expiré ; elle peut être reprise.",
                "runtime_command_failed": "Les dépendances du moteur local n'ont pas pu être installées.",
                "runtime_probe_failed": "Le moteur Qwen installé n'a pas réussi son contrôle d'intégrité.",
                "model_download_failed": "Le téléchargement du modèle officiel a échoué ; il peut être repris.",
                "model_file_size_mismatch": "Un fichier du modèle Qwen est incomplet.",
                "model_checksum_mismatch": "L'empreinte du modèle Qwen est invalide.",
                "bundled_piper_unavailable": "La voix locale Piper requise est indisponible.",
                "piper_reference_generation_failed": "La référence vocale locale n'a pas pu être créée.",
                "piper_reference_audio_invalid": "La référence vocale locale créée est invalide.",
            }
            self._write_state(
                state="failed", progress_percent=0,
                message=messages.get(code, "Installation impossible. Les fichiers existants sont conservés."),
                error_code=code, retryable=True,
                finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
        finally:
            lock.release()


_SERVICE: QwenInstallService | None = None
_SERVICE_LOCK = threading.Lock()


def get_qwen_install_service() -> QwenInstallService:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = QwenInstallService()
        return _SERVICE
