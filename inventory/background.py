from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from queue import Queue
from typing import Iterable

from django.utils import timezone

from .models import ProductAssetJob, ProductAssetJobLog
from .product_asset import run_product_asset_bot

logger = logging.getLogger(__name__)


@dataclass
class _ProductAssetJobEntry:
    job_id: int
    product_ids: list[int]
    assets: list[str]
    force_description: bool
    force_image: bool
    force_techsheet: bool
    force_pdf: bool
    force_videos: bool
    force_blog: bool


class ProductAssetJobWorker(threading.Thread):
    def __init__(self) -> None:
        super().__init__(name="ProductAssetJobWorker", daemon=True)
        self._queue: Queue[_ProductAssetJobEntry] = Queue()
        self.start()

    def enqueue(self, entry: _ProductAssetJobEntry) -> None:
        self._queue.put(entry)

    def run(self) -> None:
        while True:
            entry = self._queue.get()
            try:
                self._process(entry)
            except Exception:
                logger.exception("Erreur lors du traitement du job %s", entry.job_id)
                self._mark_job_failed(entry.job_id, "Erreur interne inattendue.")
            finally:
                self._queue.task_done()

    def _process(self, entry: _ProductAssetJobEntry) -> None:
        if not entry.product_ids:
            return
        for product_id in entry.product_ids:
            run_product_asset_bot(
                product_id,
                assets=entry.assets,
                force_description=entry.force_description,
                force_image=entry.force_image,
                force_techsheet=entry.force_techsheet,
                force_pdf=entry.force_pdf,
                force_videos=entry.force_videos,
                force_blog=entry.force_blog,
                job_id=entry.job_id,
            )

    def _mark_job_failed(self, job_id: int, message: str) -> None:
        job = ProductAssetJob.objects.filter(pk=job_id).first()
        if not job:
            return
        ProductAssetJobLog.objects.create(job=job, message=message, level=ProductAssetJobLog.Level.ERROR)
        job.status = ProductAssetJob.Status.FAILED
        job.finished_at = timezone.now()
        job.last_message = message
        job.save(update_fields=["status", "finished_at", "last_message", "updated_at"])


_worker: ProductAssetJobWorker | None = None
_worker_lock = threading.Lock()


def get_product_asset_worker() -> ProductAssetJobWorker:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = ProductAssetJobWorker()
    return _worker


def enqueue_product_asset_job(
    job_id: int,
    product_ids: Iterable[int],
    assets: list[str],
    force_description: bool = False,
    force_image: bool = False,
    force_techsheet: bool = False,
    force_pdf: bool = False,
    force_videos: bool = False,
    force_blog: bool = False,
) -> None:
    if not job_id or not product_ids:
        return
    entry = _ProductAssetJobEntry(
        job_id=job_id,
        product_ids=list(product_ids),
        assets=assets,
        force_description=force_description,
        force_image=force_image,
        force_techsheet=force_techsheet,
        force_pdf=force_pdf,
        force_videos=force_videos,
        force_blog=force_blog,
    )
    get_product_asset_worker().enqueue(entry)
