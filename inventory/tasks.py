from celery import shared_task

from .product_asset import run_product_asset_bot


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def generate_product_assets(
    self,
    product_id: int,
    force_description: bool = False,
    force_image: bool = False,
    job_id: int | None = None,
) -> dict[str, bool | int | str]:
    return run_product_asset_bot(
        product_id,
        force_description=force_description,
        force_image=force_image,
        job_id=job_id,
    )
