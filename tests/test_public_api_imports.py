import importlib


PUBLIC_MODULES = [
    "relayna.contracts",
    "relayna.rabbitmq",
    "relayna.consumer",
    "relayna.status_store",
    "relayna.status_hub",
    "relayna.sse",
    "relayna.history",
    "relayna.fastapi",
    "relayna.observability",
    "relayna.topology",
]


def test_documented_public_modules_import() -> None:
    for module_name in PUBLIC_MODULES:
        importlib.import_module(module_name)
