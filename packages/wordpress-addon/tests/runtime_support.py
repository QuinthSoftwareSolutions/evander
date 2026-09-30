"""Create this add-on's test runtime through Evander's configuration APIs."""

from evander.config import GatewayConfig
from evander.directory_config import (
    DIRECTORY_CONFIG_DOMAIN_FIELDS,
    DirectoryConfigStore,
    get_domain_base_config,
)
from evander.directories import DirectoryStore
from evander.host_config import HostConfig, save_host_config


def write_runtime_config(base_dir, **overrides):
    config = GatewayConfig(runtime_dir=base_dir / "runtime", **overrides)
    save_host_config(HostConfig.from_gateway_config(config))
    directories = DirectoryStore(config.directories_store_path)
    directories.ensure_root()
    store = DirectoryConfigStore(directories, runtime_dir=config.runtime_dir)
    store.ensure_root()
    for domain, fields in DIRECTORY_CONFIG_DOMAIN_FIELDS.items():
        if not set(overrides).isdisjoint(fields):
            store.write("root", domain=domain, payload=get_domain_base_config(config, domain))
    return config.runtime_dir
