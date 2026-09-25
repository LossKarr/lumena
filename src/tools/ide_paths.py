"""LOT L5-3c-1 - forme comparable d'un dossier annonce par l'IDE.

`canonical_workspace` vivait dans `reasoning/ide_mission_scope.py` (CONN-5A), ou
seul le rail de mission en avait besoin. L5-3c en a besoin **aussi** dans le pont
(`tools/ide_bridge.py`) et dans le lanceur (`tools/ide_launcher.py`), pour designer
l'instance ouverte sur un dossier donne.

Faire importer `reasoning` par `tools` serait une INVERSION de dependance : la
couche basse dependrait de la couche haute. La fonction descend donc ici, ou elle
est a sa place - normaliser un chemin de systeme de fichiers n'est pas du
raisonnement - et `ide_mission_scope` la reexporte. Les 14 appelants existants,
tests compris, restent valides sans modification.

Pas dans `ide_protocol.py` : ce module se declare codec pur (« no sockets, ReAct
ownership or tool authorization ») et `resolve()` touche le disque.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional


def canonical_workspace(path: Any) -> Optional[str]:
    """Forme comparable d'un dossier absolu : liens resolus, casse normalisee.

    Le chemin annonce par l'IDE n'est valide cote Lumena que comme texte : il ne
    doit jamais etre compare brut. Relatif, vide ou avec NUL : aucune forme.
    """
    if type(path) is not str or not path.strip() or "\x00" in path:
        return None
    candidate = Path(path.strip())
    if not candidate.is_absolute():
        return None
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None
    return os.path.normcase(str(resolved))
