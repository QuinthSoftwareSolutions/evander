from evander.resources.worker import main as worker_main
from .adapter import Adapter
from pathlib import Path
from .docker import Brokers


def main():
    worker_main(
        "rabbitmq-addon",
        lambda settings: Adapter(
            docker=Brokers(Path(settings.runtime_dir) / "brokers", settings.allowed_images)
        ),
        11993,
    )
