from .base import ProtocolPlugin

PLUGIN_REGISTRY: dict[int, type[ProtocolPlugin]] = {}


def register_plugin(proto_num: int):
    """Decorator to register a plugin by its request protocol number."""
    def deco(cls: type[ProtocolPlugin]):
        PLUGIN_REGISTRY[proto_num] = cls
        return cls
    return deco
