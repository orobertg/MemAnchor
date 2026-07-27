from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class RegistryRegisterRequest(BaseModel):
    entity: str
    attribute: str
    validator_tool: str
    adapter: str
    observed_inputs: dict = {}


class RegistryRegisterResponse(BaseModel):
    status: Literal["ok"]
    entity: str
    attribute: str
    validator_tool: str


class ValidatorEntry(BaseModel):
    entity: str
    attribute: str
    validator_tool: str
    adapter: str
    observed_inputs: dict
    paired_at: str
    callable_registered: bool


class RegistryListResponse(BaseModel):
    validators: list[ValidatorEntry]
