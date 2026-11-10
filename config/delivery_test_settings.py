"""Isolated demo data, optional REAL sends only to explicitly listed test recipients."""
import os
from .demo_settings import *  # noqa: F403

DOCUMENT_DELIVERY_SIMULATE = os.getenv("LRSTOCK_DELIVERY_SIMULATE", "true").lower() != "false"
