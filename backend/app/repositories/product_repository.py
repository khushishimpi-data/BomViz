"""Queries on `products` and `product_revisions`.

Products are tenant-owned through `workspace_id`; every product query filters by it, or reaches
the product through the caller's membership of its workspace. Revisions have no `workspace_id`
(deviation A11) and are only ever queried for a product that was resolved that way.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, ProductRevision, ProductStatus, UserRole, WorkspaceMember

LIKE_ESCAPE = "\\"


@dataclass(frozen=True, slots=True)
class ProductAccess:
    """A product together with the role the current user holds in its workspace."""

    product: Product
    role: UserRole


def escape_like(value: str) -> str:
    """Escape LIKE wildcards so `value` matches literally (used with `ESCAPE '\\'`)."""
    return (
        value.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", f"{LIKE_ESCAPE}%")
        .replace("_", f"{LIKE_ESCAPE}_")
    )


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_access(self, product_id: uuid.UUID, user_id: uuid.UUID) -> ProductAccess | None:
        """The product and `user_id`'s role in its workspace, in one scoped query.

        Returns None both when the product does not exist and when the user is not a member of
        its workspace, so callers cannot tell the two apart.
        """
        result = await self._session.execute(
            select(Product, WorkspaceMember.role)
            .join(
                WorkspaceMember,
                and_(
                    WorkspaceMember.workspace_id == Product.workspace_id,
                    WorkspaceMember.user_id == user_id,
                ),
            )
            .where(Product.id == product_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        product, role = row
        return ProductAccess(product=product, role=role)

    async def add_product(self, product: Product) -> Product:
        """Insert `product` inside a savepoint; a duplicate code raises `IntegrityError`.

        Only the savepoint is rolled back on failure, so the caller's transaction stays usable.
        """
        async with self._session.begin_nested():
            self._session.add(product)
        return product

    async def add_revision(self, revision: ProductRevision) -> ProductRevision:
        """Insert `revision` inside a savepoint; a duplicate code raises `IntegrityError`."""
        async with self._session.begin_nested():
            self._session.add(revision)
        return revision

    async def list_products(
        self,
        workspace_id: uuid.UUID,
        *,
        q: str | None,
        status: ProductStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Product], int]:
        """Products of `workspace_id` ordered by code, and the total matching the filters.

        `q` matches a case-insensitive substring of the code or name, taken literally.
        """
        conditions: list[ColumnElement[bool]] = [Product.workspace_id == workspace_id]
        if q:
            pattern = f"%{escape_like(q)}%"
            conditions.append(
                or_(
                    Product.product_code.ilike(pattern, escape=LIKE_ESCAPE),
                    Product.name.ilike(pattern, escape=LIKE_ESCAPE),
                )
            )
        if status is not None:
            conditions.append(Product.status == status)

        total = await self._session.scalar(
            select(func.count()).select_from(Product).where(*conditions)
        )
        result = await self._session.scalars(
            select(Product)
            .where(*conditions)
            .order_by(Product.product_code, Product.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.all()), total or 0

    async def list_revisions(self, product_id: uuid.UUID) -> list[ProductRevision]:
        """Revisions of `product_id`, newest first."""
        result = await self._session.scalars(
            select(ProductRevision)
            .where(ProductRevision.product_id == product_id)
            .order_by(ProductRevision.created_at.desc(), ProductRevision.id.desc())
        )
        return list(result.all())
