"""Optional Azure DevOps integration. Importing the package performs no provider calls."""

def register_addon():
    from .registration import register_addon as register
    return register()
