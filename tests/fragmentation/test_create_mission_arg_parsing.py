"""Parsing tool-aware de create_mission (bug run taskman, 2026-07-01).

Un objectif de mission long/multi-ligne avec guillemets internes non échappés casse
json.loads → l'ancien parse_action_args (tool-AGNOSTIC) tombait sur les fallbacks
path/content/file_match (déclenchés par « storage.py » dans le texte) → create_mission
recevait {'path','content'} au lieu de {'objective'} → mission JAMAIS créée (échec ×4).
Ces tests verrouillent la récupération du paramètre-texte primaire + les garde-fous.
"""
from src.reasoning.response_parser import parse_action_args


# Le cas EXACT du run : objectif riche (guillemets internes, .py, newlines) → JSON cassé.
_BROKEN = (
    '{"objective": "Construis un projet Python taskman dans workspace/taskman/.\n'
    'Fichiers : storage.py, core.py, cli.py, test_core.py.\n'
    'Vérifie avec python -c "import taskman.core, taskman.cli" puis pytest.", '
    '"deadline": "15 minutes"}'
)


def test_create_mission_broken_json_recovers_objective():
    args = parse_action_args(_BROKEN, tool_name="create_mission")
    # Le fix : on récupère objective, PAS path/content.
    assert "path" not in args, "fallback fichier ne doit plus s'appliquer à create_mission"
    assert "content" not in args
    assert "objective" in args
    assert args["objective"].startswith("Construis un projet Python taskman")
    assert "storage.py" in args["objective"]
    assert 'import taskman.core' in args["objective"], "guillemets internes préservés"
    assert args.get("deadline") == "15 minutes"


def test_create_mission_valid_json_unchanged():
    # JSON bien formé → json.loads direct, inchangé (jamais la voie de récupération).
    args = parse_action_args(
        '{"objective": "guide simple", "deadline": "5 minutes"}',
        tool_name="create_mission",
    )
    assert args == {"objective": "guide simple", "deadline": "5 minutes"}


def test_create_mission_deadline_optional():
    args = parse_action_args(
        '{"objective": "Fais X avec des "quotes" internes\net des sauts"}',
        tool_name="create_mission",
    )
    assert args.get("objective", "").startswith("Fais X")
    assert "path" not in args and "content" not in args


def test_create_mission_last_resort_bare_text():
    # Pas de clé "objective" explicite, JSON cassé → dernier recours : tout devient objective
    # (UNIQUEMENT pour cet outil, garde-fou reviewer).
    args = parse_action_args('Construis taskman avec storage.py et core.py', tool_name="create_mission")
    assert args.get("objective", "").startswith("Construis taskman")
    assert "path" not in args and "content" not in args


def test_write_file_unaffected_by_tool_aware():
    # NON-RÉGRESSION : write_file garde le comportement content/path (fix pollution intact).
    args = parse_action_args(
        '{"path": "workspace/x.md", "content": "# T\nAvec "guillemets"\nFin", "force_rewrite": true}',
        tool_name="write_file",
    )
    assert args["path"] == "workspace/x.md" or "x.md" in str(args.get("path", ""))
    assert args["content"].startswith("# T")
    assert not args["content"].startswith('"')


def test_no_tool_name_keeps_legacy_behavior():
    # Rétro-compat : sans tool_name, un write_file-like reste parsé comme avant.
    args = parse_action_args('{"path": "a.md", "content": "hello"}')
    assert args == {"path": "a.md", "content": "hello"}
