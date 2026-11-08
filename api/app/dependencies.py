"""Hands shared objects from app.state to routes."""
from fastapi import Request

from .clients import SourceClient
from .config import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_source(request: Request) -> SourceClient:
    return request.app.state.source
