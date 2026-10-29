from pydantic import Field
from evander.resources.models import Settings as BaseSettings, Profile as BaseProfile
from .adapter import Connection, Options


class Profile(BaseProfile):
    options: Options = Field(default_factory=Options)


class Settings(BaseSettings):
    connections: dict[str, Connection] = Field(default_factory=dict)
    profiles: dict[str, Profile] = Field(default_factory=dict)
