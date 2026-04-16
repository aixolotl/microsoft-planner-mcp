"""GroupService — Microsoft 365 group operations."""

from __future__ import annotations

from .base import BasePlannerService


class GroupService(BasePlannerService):
    """Microsoft 365 group operations.

    No group-specific mutations yet — this class exists so list_my_groups can
    call paginate() through a consistently-named service, matching the pattern
    used by BucketService, PlanService, and TaskService.
    """
