
import asyncio
import os
import re

import sys
import time
from pathlib import Path
from loguru import logger

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core import LumenaCore
from src.voice.stt import get_stt
from src.voice.tts import get_tts

# Durée pendant laquelle on reste en mode "conversation active"
# (pas besoin de redire "Lumena", on parle directement)
CONVERSATION_TIMEOUT = float(os.getenv("LUMENA_VOICE_CONV_TIMEOUT", "45"))

# Mots qui déclenchent un arrêt immédiat du TTS, sans aucune réponse vocale
_STOP_WORDS = frozenset([
    "tais-toi", "tais toi", "taistoi",
    "silence",
    "stop",
    "arrête", "arrete", "arrête de parler", "arrete de parler",
    "mute", "muet",
    "ne parle pas", "ne parle plus",
    "coupe la voix", "coupes la voix",
    "chut",
])


class VoiceAssistant:
    def __init__(self, core: LumenaCore):
        self.core = core
        self.stt = get_stt()
        self.tts = get_tts()
        self.is_running = False
        self.wake_words = [
            "lumena", "luména", "lumi", "lumy", "lumina",
            "lumière", "on l'aime", "hey lumena", "ok lumena",
        ]
        # Timestamp du dernier échange vocal réussi
        self._last_exchange: float = 0.0

    # ── Helpers ──────────────────────────────────────────

    @property
    def _in_conversation(self) -> bool:
        """True si on est dans un échange récent — plus besoin du wake word."""
        return (time.time() - self._last_exchange) < CONVERSATION_TIMEOUT

    def _touch_conversation(self):
        """Marque l'instant du dernier échange."""
        self._last_exchange = time.time()

    async def _speak_and_listen(self, text: str) -> str | None:
        """Fait parler Lumena tout en captant la parole en parallèle (barge-in vrai).

        listen_barge_in() mesure dynamiquement le fond (TTS inclus) puis détecte
        la voix humaine au-dessus. Pas de race condition : capture démarre avant
        que l'utilisateur commence à parler.
        """
        speak_task = asyncio.create_task(self.core.speak(text, wait=True))

        # Attendre que le TTS démarre pour que listen_barge_in mesure le vrai fond
        await asyncio.sleep(0.3)

        barge_task = asyncio.create_task(self.stt.listen_barge_in(timeout=30.0))

        try:
            done, pending = await asyncio.wait(
                [speak_task, barge_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Annuler ce qui reste
            for t in pending:
                if t is barge_task:
                    self.stt.stop_barge_in()
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass  # tâche annulée normalement

            # Barge-in a fini en premier → l'utilisateur a parlé
            if barge_task in done and not barge_task.cancelled():
                try:
                    command = barge_task.result()
                except Exception:
                    command = ""  # barge-in résultat non disponible
                if command:
                    logger.info(f"🔇 Barge-in capturé, coupure TTS: '{command}'")
                    self.tts.stop_speaking()
                    speak_task.cancel()
                    try:
                        await speak_task
                    except (asyncio.CancelledError, Exception):
                        pass  # TTS annulée pour barge-in
                    return command.strip()
        except asyncio.CancelledError:
            self.stt.stop_barge_in()
            for t in [speak_task, barge_task]:
                t.cancel()

        # TTS a fini normalement, pas de barge-in
        try:
            await speak_task
        except (asyncio.CancelledError, Exception):
            pass  # TTS terminée normalement
        return None

    async def _handle_command(self, command: str):
        """Route vers la voie rapide (conversation) ou complète (outils), selon la commande."""
        # Détection prioritaire des mots-stop : coupe immédiatement le TTS, sans réponse
        cmd_lower = command.lower().strip()
        if any(sw in cmd_lower for sw in _STOP_WORDS):
            if self.tts:
                self.tts.stop_speaking()
            self.core.set_global_mute(True)
            logger.info(f"🔇 Mot-stop détecté ('{command}') — TTS coupé, mute global activé")
            return

        logger.info(f"🗣️ → Lumena: '{command}'")
        self._touch_conversation()

        was_auto_speak = self.core.auto_speak
        self.core.set_auto_speak(False)
        barge = None

        try:
            if not self._needs_tools(command):
                # ── Voie rapide : question/conversation pure — 1 seul appel LLM ──
                logger.debug("⚡ Voie rapide (pas d'outils)")
                result = await self.core.chat(command, source_channel="voice")
                if result:
                    spoken = self._clean_for_speech(result)
                    if len(spoken) > 700:
                        spoken = spoken[:700].rsplit(" ", 1)[0] + "..."
                    barge = await self._speak_and_listen(spoken)
            else:
                # ── Voie complète : ReAct + outils ──
                ack = self._make_acknowledgment(command)
                _ack_interrupted = False
                if ack:
                    # L'ack est maintenant interruptible : barge-in possible pendant l'annonce
                    ack_barge = await self._speak_and_listen(ack)
                    if ack_barge:
                        barge = ack_barge   # l'utilisateur a donné une nouvelle commande
                        _ack_interrupted = True

                if not _ack_interrupted:
                    _last_spoken_tool: list = [None]
                    _captured_loop = asyncio.get_running_loop()

                    def _voice_step(tool_name: str, args: dict):
                        msg = self._tool_name_to_speech(tool_name, args)
                        if not msg or msg == _last_spoken_tool[0]:
                            return
                        _last_spoken_tool[0] = msg
                        try:
                            _captured_loop.call_soon_threadsafe(
                                lambda m=msg: asyncio.ensure_future(self.core.speak(m, wait=False))
                            )
                        except Exception:
                            pass  # speak callback best-effort

                    result = await self.core.think_and_act(
                        command, source_channel="voice", step_callback=_voice_step
                    )
                    if result:
                        spoken = await self._summarize_for_speech(result)
                        logger.info(f"🔊 Résumé vocal ({len(spoken)}/{len(result)} chars): {spoken[:80]}...")
                        barge = await self._speak_and_listen(spoken)
        finally:
            self.core.set_auto_speak(was_auto_speak)

        if barge:
            await self._handle_command(barge)

    # ── Annonce préalable ────────────────────────────────

    def _make_acknowledgment(self, command: str) -> str:
        """Génère une courte phrase naturelle annonçant ce que Lumena va faire."""
        cmd = command.lower().strip()

        # Retirer formules de politesse et mots-clés vocaux
        for filler in [
            "lumena", "luména", "s'il te plaît", "sil te plaît", "sil te plait",
            "s'il vous plaît", "stp", "svp", "ok", "hey", "tu peux", "je peux",
            "peux-tu", "pourrais-tu", "pourrais tu", "est-ce que tu peux",
            "est ce que tu peux", "dis-moi", "dis moi", "je voudrais que tu",
            "je voudrais", "je veux", "je souhaite", "j'aimerais", "j'aimerais que tu",
        ]:
            cmd = cmd.replace(filler, " ")
        cmd = re.sub(r"\s+", " ", cmd).strip()

        # Recherche / info
        if re.search(r"\b(recherche|cherche|trouve|trouver|info|infos|qui est|c'est quoi|kezako)\b", cmd):
            m = re.search(
                r"(?:recherche|cherche|trouver?|info(?:s)?(?:\s+sur)?)\s+(?:sur\s+|les?\s+|des?\s+|du\s+)?(.+?)(?:\s+pour moi)?$",
                cmd,
            )
            topic = m.group(1).strip()[:50] if m else cmd[:50]
            return f"Je recherche {topic}, un instant..."

        # Création / écriture / génération
        if re.search(r"\b(crée|créer|écris|écrire|génère|générer|rédige|rédiger|rédaction|génération|crée-moi|fais-moi)\b", cmd):
            return "Je prépare ça, un instant..."

        # Analyse / résumé / explication
        if re.search(r"\b(analyse|analyser|explique|expliquer|résume|résumer|comprends|comprendre)\b", cmd):
            return "J'analyse ça, donne-moi un moment..."

        # Envoi / communication
        if re.search(r"\b(envoie|envoyer|mail|email|message|sms|telegram|whatsapp|discord)\b", cmd):
            return "Je prépare l'envoi..."

        # Code / script
        if re.search(r"\b(code|programme|script|développe|développer|debug|corrige|corriger)\b", cmd):
            return "Je code ça tout de suite..."

        # Ouverture / lancement
        if re.search(r"\b(ouvre|ferme|lance|démarre|arrête|installe|installe)\b", cmd):
            return "Je m'en occupe..."

        # Météo / heure / date
        if re.search(r"\b(météo|meteo|temps|température|heure|date|aujourd'hui)\b", cmd):
            return "Je vérifie ça..."

        # Calcul / conversion
        if re.search(r"\b(calcule|combien|convertis|converti|\d+)\b", cmd):
            return "Je calcule ça..."

        # Conversation simple (bonjour, comment tu vas...)
        if re.search(r"\b(bonjour|salut|comment|vas|vais|bonsoir|merci|super|bien|parfait)\b", cmd):
            return ""  # Pas d'annonce pour les échanges simples

        return "Bien reçu, je m'en occupe..."

    # ── Annonces d'étapes ────────────────────────────────

    @staticmethod
    def _tool_name_to_speech(tool_name: str, args: dict) -> str:
        """Génère une phrase naturelle et variée pour annoncer l'outil en cours."""
        import random
        t = tool_name.lower()
        q = str(args.get("query", args.get("search", args.get("prompt", "")))).strip()[:50]
        url = str(args.get("url", "")).strip()
        f = str(args.get("path", args.get("file_path", args.get("filename", "")))).strip()
        f = f.split("/")[-1].split("\\")[-1][:40] if f else ""
        domain = url.split("/")[2].replace("www.", "") if url.count("/") >= 2 else url[:30]

        if t in ("web_search_brave", "web_search", "browser_search_google",
                 "browser_search_duckduckgo", "ddg_search"):
            if q:
                return random.choice([
                    f"Je cherche {q}...",
                    f"Je regarde ce qu'il y a sur {q}...",
                    f"Je lance une recherche sur {q}...",
                    f"Laisse-moi chercher {q}...",
                ])
            return random.choice([
                "Je fais une recherche...",
                "Je cherche ça sur le web...",
                "Je regarde sur internet...",
            ])

        if t in ("browser_navigate",):
            if domain:
                return random.choice([
                    f"J'ouvre {domain}...",
                    f"Je vais sur {domain}...",
                    f"Je consulte {domain}...",
                ])
            return random.choice(["J'ouvre la page...", "Je navigue..."])

        if t in ("browser_get_content", "browser_extract_text"):
            return random.choice([
                "Je lis ce qu'il y a sur la page...",
                "Je parcours la page...",
                "J'extrait les informations...",
            ])

        if t in ("browser_scroll",):
            return None

        if t in ("mail_send", "send_email"):
            return random.choice([
                "J'envoie le message...",
                "J'expédie ça...",
                "Je vous envoie ça maintenant...",
            ])

        if t in ("mail_list_messages", "mail_search"):
            return random.choice([
                "Je regarde votre boîte mail...",
                "Je consulte vos emails...",
                "Je jette un œil à votre messagerie...",
            ])

        if t in ("write_file", "create_file", "edit_file"):
            if f:
                return random.choice([
                    f"J'écris {f}...",
                    f"Je prépare {f}...",
                    f"Je rédige {f}...",
                ])
            return random.choice(["J'écris le fichier...", "Je prépare ça...", "Je rédige..."])

        if t in ("read_file", "get_file_content"):
            if f:
                return random.choice([
                    f"Je lis {f}...",
                    f"Je consulte {f}...",
                    f"J'ouvre {f}...",
                ])
            return random.choice(["Je lis le fichier...", "Je consulte..."])

        if t in ("execute_code", "run_command", "run_python", "dev_run_fix"):
            return random.choice([
                "J'exécute le code...",
                "Je lance ça...",
                "Je fais tourner le programme...",
                "J'exécute...",
            ])

        if t in ("memory_search", "memory_recall"):
            if q:
                return random.choice([
                    f"Je cherche dans ma mémoire si je sais quelque chose sur {q}...",
                    f"Je fouille dans mes souvenirs...",
                    f"Voyons ce que je sais sur {q}...",
                ])
            return random.choice([
                "Je fouille dans ma mémoire...",
                "Je cherche dans mes souvenirs...",
            ])

        if t in ("memory_save",):
            return None

        if t.startswith("discord_"):
            return random.choice([
                "Je consulte Discord...",
                "Je regarde Discord...",
                "Je jette un œil sur Discord...",
            ])

        if t.startswith("telegram_"):
            return random.choice([
                "Je consulte Telegram...",
                "Je regarde les messages Telegram...",
            ])

        if t.startswith("send_whatsapp_"):
            return random.choice([
                "J'envoie ça sur WhatsApp...",
                "Je prépare le message WhatsApp...",
            ])

        if t in ("get_file_list", "list_files", "list_directory"):
            return random.choice([
                "Je regarde ce qu'il y a comme fichiers...",
                "Je liste les fichiers...",
            ])

        if t in ("calculator", "python_eval"):
            return random.choice([
                "Je calcule...",
                "Je fais le calcul...",
            ])

        if t in ("parallel_tools",):
            return None  # Les sous-outils annoncent déjà

        return None

    @staticmethod
    def _needs_tools(command: str) -> bool:
        """Retourne True si la commande nécessite la boucle ReAct (outils/services externes).
        False = voie rapide via core.chat() — 1 seul appel LLM, ~1-2s."""
        cmd = command.lower()
        TOOL_KEYWORDS = [
            # Messagerie
            "mail", "email", "courriel", "envoie", "envoyer",
            # Web / recherche externe
            "cherche", "recherche", "trouve", "trouver",
            "actualit", "news", "météo", "meteo", "quel temps",
            # Fichiers / création d'artefacts
            "fichier", "télécharge", "télécharger",
            # Données temps réel
            "bourse", "prix du", "cours du",
            "calendrier", "agenda", "rappel",
            # Services
            "discord", "telegram", "whatsapp", "github", "screenshot", "capture d'écran",
            # Navigation web
            "ouvre le site", "va sur le site", "browser",
        ]
        return any(kw in cmd for kw in TOOL_KEYWORDS)

    # ── Résumé vocal ─────────────────────────────────────

    async def _summarize_for_speech(self, text: str) -> str:
        """Adapte intelligemment la réponse pour la voix.

        - Texte déjà court (≤ 500 chars nettoyés) → lu intégralement
        - Texte long → résumé oral généré par le LLM (2-4 phrases, tout le sens, rien de coupé)
        Le message original n'est jamais modifié.
        """
        if not text:
            return ""

        clean = self._clean_for_speech(text)

        # Court → lire entièrement
        if len(clean) <= 500:
            return clean

        # Long → demander au LLM de faire un vrai résumé oral
        try:
            prompt = (
                "Tu es Lumena. Tu viens de produire cette réponse écrite :\n\n"
                f"{text[:3000]}\n\n"
                "Fais-en un résumé oral naturel pour être dit à haute voix. "
                "Règles :\n"
                "- Garde TOUTES les informations importantes (chiffres, noms, prix, dates)\n"
                "- 2 à 4 phrases maximum, langage parlé naturel\n"
                "- Pas de markdown, pas de listes, pas de puces\n"
                "- Si la réponse contient plusieurs éléments, fais une synthèse fluide\n"
                "- Commence directement par le contenu, pas par 'Voici' ou 'En résumé'\n"
                "- Utilise le vouvoiement : 'vous', 'votre', 'vos' pour t'adresser à l'utilisateur. JAMAIS 'tu', 'ton', 'ta', 'tes'.\n"
                "Résumé oral :"
            )
            summary = await self.core.chat(prompt, source_channel="voice_internal")
            summary = self._clean_for_speech(summary or "")
            if summary and len(summary) > 10:
                return summary
        except Exception:
            pass  # clean_for_speech fallback

        # Fallback : premières phrases jusqu'à 600 chars
        sentences = re.split(r'(?<=[.!?])\s+', clean)
        out = ""
        for s in sentences:
            s = s.strip()
            if not s or len(s) < 5:
                continue
            if len(out) + len(s) + 1 > 600:
                break
            out = (out + " " + s).strip() if out else s
        return out or clean[:600].rsplit(" ", 1)[0] + "..."

    @staticmethod
    def _clean_for_speech(text: str) -> str:
        """Nettoie le markdown et les artefacts pour une lecture vocale propre."""
        clean = re.sub(r'```[\s\S]*?```', '', text)
        clean = re.sub(r'#{1,6}\s+', '', clean)
        clean = re.sub(r'\*\*(.+?)\*\*', r'\1', clean)
        clean = re.sub(r'\*(.+?)\*', r'\1', clean)
        clean = re.sub(r'`(.+?)`', r'\1', clean)
        clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean)  # liens md
        clean = re.sub(r'[🌟🔊✅⚠️🚀💡🎭🔥⭐📊📈📉🔧💾📝🗂️🎙️🔔🔇💬⏳❌❓]', '', clean)
        clean = re.sub(r'^\s*[-*•]\s+', '', clean, flags=re.MULTILINE)
        clean = re.sub(r'\n{2,}', '. ', clean)
        clean = re.sub(r'\n', ' ', clean)
        clean = re.sub(r'\s{2,}', ' ', clean).strip()
        return clean

    # ── Boucle principale ────────────────────────────────

    async def start(self):
        self.is_running = True
        logger.info("🎙️ Assistant Vocal démarré. Dites 'Lumena' pour m'activer.")

        while self.is_running:
            try:
                if self._in_conversation:
                    # ── Mode conversation active ──
                    # Attendre que le TTS finisse + délai anti-écho
                    while self.tts and self.tts.is_speaking:
                        await asyncio.sleep(0.1)
                    await asyncio.sleep(0.8)  # laisser l'écho TTS s'atténuer (0.8s)

                    logger.debug("💬 Mode conversation — écoute directe...")
                    command = await self.stt.listen_once(timeout=10.0)  # 10s max phrase (était CONVERSATION_TIMEOUT=45s)

                    # Premier listen vide = probable bruit/écho TTS → réessayer une fois
                    if not command or len(command.strip()) <= 1:
                        logger.debug("💬 listen vide, second essai (8s)...")
                        await asyncio.sleep(0.15)
                        command = await self.stt.listen_once(timeout=8.0)

                    if command and len(command.strip()) > 1:
                        clean = command.strip()
                        # Vérifier si l'utilisateur a dit le wake word → restart conversation timer
                        for ww in self.wake_words:
                            clean = clean.replace(ww, "").strip()
                        if clean:
                            await self._handle_command(clean)
                        else:
                            # Juste "Lumena" sans rien → "Oui ?"
                            barge = await self._speak_and_listen("Oui ?")
                            if barge:
                                await self._handle_command(barge)
                    else:
                        # Silence génuine → fin de conversation
                        logger.info("💬 Conversation terminée (silence)")
                        self._last_exchange = 0.0
                else:
                    # ── Mode veille — attente du wake word ──
                    logger.debug("⏳ En attente du mot-clé 'Lumena'...")
                    result = await self.stt.detect_wake_word(
                        wake_words=self.wake_words,
                        max_listen_seconds=3600,
                    )

                    if result is None:
                        continue

                    logger.info("🔔 Mot-clé détecté !")
                    self._touch_conversation()

                    if result.strip():
                        # Phrase complète : "Lumena ouvre le projet"
                        await self._handle_command(result.strip())
                    else:
                        # Mot-clé seul → "Oui ?" + barge-in possible
                        barge = await self._speak_and_listen("Oui ?")
                        if barge:
                            await self._handle_command(barge)
                        else:
                            # Pas de barge-in, écouter la commande
                            command = await self.stt.listen_once(timeout=7.0)
                            if command and len(command.strip()) > 1:
                                clean = command.lower()
                                for ww in self.wake_words:
                                    clean = clean.replace(ww, "")
                                clean = clean.strip()
                                if clean:
                                    await self._handle_command(clean)
                                else:
                                    logger.info("❓ Mot-clé répété sans commande.")
                            else:
                                logger.info("🔇 Aucune commande entendue après le mot-clé.")

            except Exception as e:
                import traceback
                logger.error(f"❌ Erreur boucle vocale: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(2)

    def stop(self):
        self.is_running = False
        self.stt.stop_listening()


async def main():
    core = LumenaCore()
    await core.initialize()
    core.auto_speak = True
    assistant = VoiceAssistant(core)
    await assistant.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Arrêt de l'assistant vocal.")
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
