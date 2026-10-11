"""Request and response bodies for products and revisions.

Request models strip strings and reject unknown fields. The workspace and the creator never come
from these bodies: they are the path id checked against membership and the authenticated user.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from app.models import ProductStatus, RevisionStatus
from app.services.product_service import (
    DESCRIPTION_MAX_LENGTH,
    PRODUCT_CODE_MAX_LENGTH,
    PRODUCT_NAME_MAX_LENGTH,
    REVISION_CODE_MAX_LENGTH,
)

ProductCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=PRODUCT_CODE_MAX_LENGTH),
]
ProductName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=PRODUCT_NAME_MAX_LENGTH),
]
RevisionCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=REVISION_CODE_MAX_LENGTH),
]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=DESCRIPTION_MAX_LENGTH)
]


class _DescribedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: Description | None = None

    @field_validator("description")
    @classmethod
    def _blank_description_is_none(cls, value: str | None) -> str | None:
        """An empty (or whitespace-only) description is stored as no description."""
        return value or None


class ProductCreateRequest(_DescribedRequest):
    product_code: ProductCode
    name: ProductName
    status: ProductStatus = ProductStatus.DRAFT


class RevisionCreateRequest(_DescribedRequest):
    revision_code: RevisionCode
    status: RevisionStatus = RevisionStatus.DRAFT


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    product_code: str
    name: str
    description: str | None
    status: ProductStatus
    created_by_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class RevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    revision_code: str
    description: str | None
    status: RevisionStatus
    created_by_id: uuid.UUID | None
    created_at: datetime


class ProductDetailResponse(ProductResponse):
    """A product with its revisions, newest first."""

    revisions: list[RevisionResponse]
