from evander.resources.service import ResourceService
from .adapter import Adapter


def service(context):
    return ResourceService(context, Adapter())
