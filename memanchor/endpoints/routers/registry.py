"""Validator registry routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from memanchor.application.registry_service import list_validators, register_validator
from memanchor.models import RegistryListResponse, RegistryRegisterRequest, RegistryRegisterResponse

router = APIRouter(tags=["registry"])


@router.post("/registry/register", response_model=RegistryRegisterResponse)
async def registry_register(req: RegistryRegisterRequest, request: Request):
    return await register_validator(request.app.state.sqlite_store, req)


@router.get("/registry", response_model=RegistryListResponse)
async def registry_list(request: Request):
    return await list_validators(request.app.state.sqlite_store, request.app.state.adapter)
