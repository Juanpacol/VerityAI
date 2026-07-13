"""Knowledge graph layer — Neo4j integration."""

from .client import KGClient
from .ingestion import KGIngestion

__all__ = ["KGClient", "KGIngestion"]
