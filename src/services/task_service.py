"""TaskService — Planner task and task-details PATCH/DELETE operations."""

from __future__ import annotations

from msgraph.generated.models.planner_task import PlannerTask
from msgraph.generated.models.planner_task_details import PlannerTaskDetails

from .base import BasePlannerService


class TaskService(BasePlannerService):
    """Planner task and task-details operations."""

    async def _refresh_task_etag(self, task_id: str) -> str:
        """GET the task and return the current @odata.etag value."""
        task = await self._client.planner.tasks.by_planner_task_id(task_id).get()
        etag: str | None = task.additional_data.get("@odata.etag") if task else None
        if not etag:
            raise ValueError(f"No @odata.etag found on task {task_id!r}")
        return etag

    async def _refresh_details_etag(self, task_id: str) -> str:
        """GET the task details and return the current @odata.etag value."""
        details = await self._client.planner.tasks.by_planner_task_id(task_id).details.get()
        etag: str | None = details.additional_data.get("@odata.etag") if details else None
        if not etag:
            raise ValueError(f"No @odata.etag found on task details {task_id!r}")
        return etag

    async def patch_task(self, task_id: str, body: PlannerTask, etag: str) -> PlannerTask | dict | None:
        """PATCH a task.  Retries once with a fresh ETag on 412/409."""
        item = self._client.planner.tasks.by_planner_task_id(task_id)
        result = await self._with_retry(
            etag,
            lambda e: item.patch(body, request_configuration=self._make_config(e, prefer_representation=True)),
            lambda: self._refresh_task_etag(task_id),
        )
        return self.serialize_graph_object(result) if self._serialize else result

    async def delete_task(self, task_id: str, etag: str) -> None:
        """DELETE a task.  Retries once with a fresh ETag on 412/409."""
        item = self._client.planner.tasks.by_planner_task_id(task_id)
        await self._with_retry(
            etag,
            lambda e: item.delete(request_configuration=self._make_config(e)),
            lambda: self._refresh_task_etag(task_id),
        )

    async def patch_task_details(self, task_id: str, body: PlannerTaskDetails, etag: str) -> PlannerTaskDetails | dict | None:
        """PATCH task details.  Retries once with a fresh ETag on 412/409."""
        item = self._client.planner.tasks.by_planner_task_id(task_id).details
        result = await self._with_retry(
            etag,
            lambda e: item.patch(body, request_configuration=self._make_config(e, prefer_representation=True)),
            lambda: self._refresh_details_etag(task_id),
        )
        return serialize_graph_object(result) if self._serialize else result
