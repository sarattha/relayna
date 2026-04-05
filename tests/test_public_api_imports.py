import importlib

PUBLIC_MODULES = [
    "relayna.contracts",
    "relayna.rabbitmq",
    "relayna.consumer",
    "relayna.status",
    "relayna.api",
    "relayna.workflow",
    "relayna.observability",
    "relayna.topology",
    "relayna.mcp",
    "relayna.studio",
    "relayna.dlq",
]


def test_documented_public_modules_import() -> None:
    for module_name in PUBLIC_MODULES:
        importlib.import_module(module_name)
