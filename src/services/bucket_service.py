"""BucketService — Planner bucket DELETE operations."""

from __future__ import annotations

from .base import BasePlannerService


class BucketService(BasePlannerService):
    """Planner bucket operations."""

    async def _refresh_bucket_etag(self, bucket_id: str) -> str:
        """GET the bucket and return the current @odata.etag value."""
        bucket = await self._client.planner.buckets.by_planner_bucket_id(bucket_id).get()
        etag: str | None = bucket.additional_data.get("@odata.etag") if bucket else None
        if not etag:
            raise ValueError(f"No @odata.etag found on bucket {bucket_id!r}")
        return etag

    async def delete_bucket(self, bucket_id: str, etag: str) -> None:
        """DELETE a bucket.  Retries once with a fresh ETag on 412/409."""
        item = self._client.planner.buckets.by_planner_bucket_id(bucket_id)
        await self._with_retry(
            etag,
            lambda e: item.delete(request_configuration=self._make_config(e)),
            lambda: self._refresh_bucket_etag(bucket_id),
        )
