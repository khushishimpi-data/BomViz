"""Products and their revisions: creation with unique codes, listing, detail, audits.

The workspace (for list/create) or the product (for detail/revisions) has already been resolved
through the caller's membership by the route dependency, so nothing here trusts a client id.
Every creation is audited in the same transaction.
"""

from dataclasses import dataclass
from http import HTTPStatus

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models import Product, ProductRevision, ProductStatus, RevisionStatus, User
from app.repositories.product_repository import ProductAccess, ProductRepository
from app.repositories.workspace_repository import WorkspaceAccess
from app.services.audit_service import AuditAction, AuditService

PRODUCT_CODE_MAX_LENGTH = 64
PRODUCT_NAME_MAX_LENGTH = 200
DESCRIPTION_MAX_LENGTH = 2000
REVISION_CODE_MAX_LENGTH = 32
SEARCH_MAX_LENGTH = 200

_PRODUCT_CODE_CONSTRAINT = "uq_products_workspace_id_product_code"
_REVISION_CODE_CONSTRAINT = "uq_product_revisions_product_id_revision_code"


class ProductNotFoundError(AppError):
    """Nonexistent product, or one in a workspace the caller is not a member of."""

    status_code = HTTPStatus.NOT_FOUND
    code = "NOT_FOUND"

    def __init__(self) -> None:
        super().__init__("Product not found.")


class ProductCodeTakenError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "PRODUCT_CODE_TAKEN"

    def __init__(self) -> None:
        super().__init__("A product with that code already exists in this workspace.")


class RevisionCodeTakenError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "REVISION_CODE_TAKEN"

    def __init__(self) -> None:
        super().__init__("A revision with that code already exists for this product.")


@dataclass(frozen=True, slots=True)
class ProductDetail:
    product: Product
    revisions: list[ProductRevision]


def _is_violation(exc: IntegrityError, constraint: str) -> bool:
    return constraint in str(exc.orig)


class ProductService:
    def __init__(self, session: AsyncSession) -> None:
        self._products = ProductRepository(session)
        self._audit = AuditService(session)

    async def create(
        self,
        access: WorkspaceAccess,
        actor: User,
        *,
        product_code: str,
        name: str,
        description: str | None,
        status: ProductStatus,
    ) -> Product:
        """Create a product in the resolved workspace. Duplicate code -> `ProductCodeTakenError`."""
        workspace_id = access.workspace.id
        product = Product(
            workspace_id=workspace_id,
            product_code=product_code,
            name=name,
            description=description,
            status=status,
            created_by_id=actor.id,
        )
        try:
            await self._products.add_product(product)
        except IntegrityError as exc:
            if _is_violation(exc, _PRODUCT_CODE_CONSTRAINT):
                raise ProductCodeTakenError() from exc
            raise
        await self._audit.record(
            AuditAction.PRODUCT_CREATED,
            entity_type="product",
            entity_id=product.id,
            actor_id=actor.id,
            workspace_id=workspace_id,
            metadata={"product_code": product.product_code},
        )
        return product

    async def list_products(
        self,
        access: WorkspaceAccess,
        *,
        q: str | None,
        status: ProductStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Product], int]:
        return await self._products.list_products(
            access.workspace.id, q=q, status=status, limit=limit, offset=offset
        )

    async def get_detail(self, access: ProductAccess) -> ProductDetail:
        """The resolved product with its revisions, newest first."""
        revisions = await self._products.list_revisions(access.product.id)
        return ProductDetail(product=access.product, revisions=revisions)

    async def create_revision(
        self,
        access: ProductAccess,
        actor: User,
        *,
        revision_code: str,
        description: str | None,
        status: RevisionStatus,
    ) -> ProductRevision:
        """Add a revision to the resolved product. Duplicate -> `RevisionCodeTakenError`."""
        product = access.product
        revision = ProductRevision(
            product_id=product.id,
            revision_code=revision_code,
            description=description,
            status=status,
            created_by_id=actor.id,
        )
        try:
            await self._products.add_revision(revision)
        except IntegrityError as exc:
            if _is_violation(exc, _REVISION_CODE_CONSTRAINT):
                raise RevisionCodeTakenError() from exc
            raise
        await self._audit.record(
            AuditAction.PRODUCT_REVISION_CREATED,
            entity_type="product_revision",
            entity_id=revision.id,
            actor_id=actor.id,
            workspace_id=product.workspace_id,
            metadata={"revision_code": revision.revision_code},
        )
        return revision
