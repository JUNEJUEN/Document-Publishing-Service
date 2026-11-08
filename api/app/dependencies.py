"""Hands shared objects from app.state to routes."""
from fastapi import Request

from .clients import SourceClient
from .config import Settings
from .runner import Runner
from .store import JobStore


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_source(request: Request) -> SourceClient:
    return request.app.state.source


def get_store(request: Request) -> JobStore:
    return request.app.state.store


def get_runner(request: Request) -> Runner:
    return request.app.state.runner
