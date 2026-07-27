"""Validator registry use-cases.

Keeps HTTP routers free of store/adapter mapping logic.
"""

from __future__ import annotations

from memanchor.contracts.adapter import MemAnchorAdapter
from memanchor.models import (
    RegistryListResponse,
    RegistryRegisterRequest,
    RegistryRegisterResponse,
    ValidatorEntry,
)
from memanchor.ports.stores import FactStore


async def register_validator(fact_store: FactStore, req: RegistryRegisterRequest) -> RegistryRegisterResponse:
    await fact_store.register_validator(
        req.entity, req.attribute, req.validator_tool, req.adapter, req.observed_inputs
    )
    return RegistryRegisterResponse(
        status="ok", entity=req.entity, attribute=req.attribute, validator_tool=req.validator_tool
    )


async def list_validators(fact_store: FactStore, adapter: MemAnchorAdapter) -> RegistryListResponse:
    rows = await fact_store.list_validators()
    return RegistryListResponse(
        validators=[
            ValidatorEntry(
                entity=row["entity"],
                attribute=row["attribute"],
                validator_tool=row["validator_tool"],
                adapter=row["adapter"],
                observed_inputs=row["observed_inputs"],
                paired_at=row["paired_at"],
                callable_registered=adapter.get_validator(row["validator_tool"]) is not None,
            )
            for row in rows
        ]
    )
