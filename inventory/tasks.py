from celery import shared_task

from .product_asset import run_product_asset_bot


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def generate_product_assets(
    self,
    product_id: int,
    force_description: bool = False,
    force_image: bool = False,
    force_techsheet: bool = False,
    force_pdf: bool = False,
    force_videos: bool = False,
    force_blog: bool = False,
    assets: list[str] | None = None,
    job_id: int | None = None,
) -> dict[str, bool | int | str]:
    return run_product_asset_bot(
        product_id,
        assets=assets,
        force_description=force_description,
        force_image=force_image,
        force_techsheet=force_techsheet,
        force_pdf=force_pdf,
        force_videos=force_videos,
        force_blog=force_blog,
        job_id=job_id,
    )
