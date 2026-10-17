"""Shared state and FastAPI dependencies."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from a0td.config import AppConfig
from a0td.jobs.manager import JobManager
from a0td.storage.db import Database
from a0td.storage.files import Files


@dataclass(slots=True)
class Services:
    """Everything the routers need; one instance per app (``app.state.services``)."""

    config: AppConfig
    db: Database
    files: Files
    manager: JobManager


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]
