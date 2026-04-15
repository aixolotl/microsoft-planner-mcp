from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class PlansGetQueryParameters:
    """Query parameters for GET /me/planner/plans.

    Avoids importing the deprecated PlansRequestBuilderGetQueryParameters and
    PlansRequestBuilderGetRequestConfiguration classes from the SDK, both of which
    emit DeprecationWarning at import time.
    """

    select: Optional[list[str]] = None

    def get_query_parameter(self, original_name: str) -> str:
        if original_name == "select":
            return "%24select"
        return original_name
