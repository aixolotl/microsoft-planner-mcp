"""PlanService — Planner plan DELETE operations."""

from __future__ import annotations

from .base import BasePlannerService


class PlanService(BasePlannerService):
    """Planner plan operations."""

    async def _refresh_plan_etag(self, plan_id: str) -> str:
        """GET the plan and return the current @odata.etag value."""
        plan = await self._client.planner.plans.by_planner_plan_id(plan_id).get()
        etag: str | None = plan.additional_data.get("@odata.etag") if plan else None
        if not etag:
            raise ValueError(f"No @odata.etag found on plan {plan_id!r}")
        return etag

    async def delete_plan(self, plan_id: str, etag: str) -> None:
        """DELETE a plan.  Retries once with a fresh ETag on 412/409."""
        item = self._client.planner.plans.by_planner_plan_id(plan_id)
        await self._with_retry(
            etag,
            lambda e: item.delete(request_configuration=self._make_config(e)),
            lambda: self._refresh_plan_etag(plan_id),
        )
