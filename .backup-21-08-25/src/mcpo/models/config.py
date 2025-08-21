from __future__ import annotations
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, AnyHttpUrl, model_validator

ServerType = Literal["stdio", "sse", "streamable-http"]

class ServerConfig(BaseModel):
    name: str = Field(min_length=1)
    type: ServerType
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    url: Optional[AnyHttpUrl] = None
    headers: Optional[dict] = None
    disabled: bool = False

    @model_validator(mode="after")
    def validate_mode(self):
        if self.type == "stdio" and not self.command:
            raise ValueError("'command' is required for stdio server")
        if self.type in ("sse", "streamable-http") and not self.url:
            raise ValueError("'url' is required for remote server types")
        return self

class AppConfig(BaseModel):
    servers: List[ServerConfig]

    @model_validator(mode="after")
    def check_unique(self):
        names = [s.name for s in self.servers]
        if len(names) != len(set(names)):
            raise ValueError("server names must be unique")
        return self
