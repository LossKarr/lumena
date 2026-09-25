"""Reserved IDE tool names; deliberately independent of providers and registries."""


def is_ide_tool_name(name: object) -> bool:
    return isinstance(name, str) and name.strip().lower().startswith("ide_")
