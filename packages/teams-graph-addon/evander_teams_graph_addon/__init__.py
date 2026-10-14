"""Optional Microsoft integration. Registration performs no provider operations."""


def register_addon():
    from .registration import register_addon as register

    return register()
