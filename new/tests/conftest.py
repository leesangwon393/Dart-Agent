from __future__ import annotations

import pytest

from app.core.config import DEFAULT_CONFIG
from app.company.repository import CompanyMasterRepository
from app.query.entity_resolver import EntityResolver
from app.routing.evidence_router import EvidenceRouter
from app.routing.task_router import TaskRouter


@pytest.fixture(scope="session")
def repository() -> CompanyMasterRepository:
    return CompanyMasterRepository(DEFAULT_CONFIG.universe_csv_path)


@pytest.fixture(scope="session")
def entity_resolver(repository) -> EntityResolver:
    return EntityResolver(repository)


@pytest.fixture(scope="session")
def task_router() -> TaskRouter:
    return TaskRouter()


@pytest.fixture(scope="session")
def evidence_router() -> EvidenceRouter:
    return EvidenceRouter()
