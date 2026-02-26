"""
Country registry for the Nordic Bankruptcy Monitor.

Plugins register themselves at import time. The pipeline queries this
registry to find active countries based on the COUNTRIES env var.
"""

import logging
import os
from typing import Dict, List

from countries.protocol import CountryPlugin

logger = logging.getLogger(__name__)

COUNTRY_REGISTRY: Dict[str, CountryPlugin] = {}


def register_country(plugin: CountryPlugin) -> None:
    """Register a country plugin. Called at module import time."""
    COUNTRY_REGISTRY[plugin.code] = plugin
    logger.debug(f"Registered country plugin: {plugin.code} ({plugin.name})")


def get_country(code: str) -> CountryPlugin:
    """Get a country plugin by ISO code. Raises KeyError if not registered."""
    return COUNTRY_REGISTRY[code]


def get_active_countries() -> List[CountryPlugin]:
    """Return plugins for countries configured via COUNTRIES env var.

    COUNTRIES=se,no  → [SwedenPlugin, NorwayPlugin]
    COUNTRIES=no     → [NorwayPlugin]
    Default: 'se'    → [SwedenPlugin]
    """
    raw = os.environ.get("COUNTRIES", "se")
    codes = [c.strip().lower() for c in raw.split(",") if c.strip()]
    plugins = []
    for code in codes:
        if code in COUNTRY_REGISTRY:
            plugins.append(COUNTRY_REGISTRY[code])
        else:
            logger.warning(f"Country '{code}' not registered, skipping")
    return plugins
