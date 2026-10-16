from evander.resources.worker import main as worker_main
from .adapter import Adapter


def main():
    worker_main("sqlserver-addon", lambda settings: Adapter(), 11992)
