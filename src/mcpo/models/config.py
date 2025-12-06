from __future__ import annotations

from typing import List, Optional, Literal
from pydantic import BaseModel, field_validator, HttpUrl, model_validator


class ServerConfig(BaseModel):
    name: str
    type: Literal["stdio", "sse", "streamable-http"]
    command: Optional[str] = None
    args: Optional[List[str]] = None
    url: Optional[HttpUrl] = None

    @model_validator(mode="after")
    def _validate(self) -> "ServerConfig":
        if self.type == "stdio":
            if not self.command:
                raise ValueError("stdio requires 'command'")
        elif self.type in ("sse", "streamable-http"):
            if not self.url:
                raise ValueError("remote server requires 'url'")
        return self


class AppConfig(BaseModel):
    servers: List[ServerConfig]

    @field_validator("servers")
    @classmethod
    def _unique_names(cls, v: List[ServerConfig]) -> List[ServerConfig]:
        names = [s.name for s in v]
        if len(names) != len(set(names)):
            raise ValueError("Server names must be unique")
        return v
