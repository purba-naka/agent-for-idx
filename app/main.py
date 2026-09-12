from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect

from .config import get_settings
from .idx_client import IDXClient
from .models import Disclosure, IngestPayload
from .pipeline import Pipeline, short_error
from .repository import Repository
from .webhook import WebhookNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)-12s %(message)s",
    datefmt="%H:%M:%S",
)
# APScheduler membanjiri log dengan "Running job"/"executed successfully" tiap
# siklus; hasil poll sudah dilaporkan sendiri oleh poll_idx().
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger("idx")

settings = get_settings()
repository = Repository(settings.database_path)
scheduler = AsyncIOScheduler()

WIB = timezone(timedelta(hours=7))
last_poll: dict[str, str] = {}

webhook_notifier = WebhookNotifier(settings) if settings.webhook_url else None
if webhook_notifier is None:
    logger.warning("WEBHOOK_URL kosong: tidak ada webhook keluar yang aktif")


def _build_graph():
    """Bangun graph agen LLM hanya bila lengkap dikonfigurasi.

    Lazy import + try/except supaya kegagalan (mis. versi langchain tanpa
    create_agent) tidak mematikan aplikasi -- mode webhook-only tetap jalan.
    """
    if not settings.agent_enabled:
        logger.info(
            "Agen LLM/Telegram nonaktif (OPENAI_API_KEY/TELEGRAM_* kosong) -- mode webhook-only"
        )
        return None
    try:
        from .graph import build_graph

        return build_graph(settings)
    except Exception as error:
        logger.warning("Agen LLM dinonaktifkan (build graph gagal: %s)", error)
        return None


graph = _build_graph()
pipeline = Pipeline(settings, repository, graph, webhook_notifier)


def _is_recent(disclosure: Disclosure) -> bool:
    """True bila pengumuman terbit dalam jendela lookback (kandidat notifikasi).

    TglPengumuman IDX naive (WIB). Poll pertama / data lama otomatis menjadi
    baseline tanpa notifikasi supaya tidak spam saat aplikasi baru dinyalakan.
    """
    cutoff = datetime.now(WIB) - timedelta(minutes=settings.idx_lookback_minutes)
    return disclosure.published_at.replace(tzinfo=WIB) >= cutoff



async def poll_idx() -> dict[str, int]:
    """Poll IDX sekali, distribusikan item baru, kembalikan hitungan status."""
    disclosures = await IDXClient(settings).fetch_recent()
    results = [await pipeline.process(item, notify=_is_recent(item)) for item in disclosures]
    counts = {status: results.count(status) for status in set(results)}
    last_poll.update(
        at=datetime.now(WIB).isoformat(),
        fetched=str(len(disclosures)),
        results=json.dumps(counts, ensure_ascii=False),
    )
    # Poll rutin tanpa item baru adalah keadaan normal: turunkan ke DEBUG
    # supaya log hanya berisi kejadian yang berarti.
    noteworthy = {key: value for key, value in counts.items() if key != "duplicate"}
    level = logging.INFO if noteworthy else logging.DEBUG
    logger.log(
        level,
        "poll: %s pengumuman | %s",
        len(disclosures),
        ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "kosong",
    )
    return counts


async def poll_idx_safe() -> None:
    """Bungkus poll_idx agar kegagalan tidak menghentikan scheduler.

    Error jaringan/HTTP dari IDX (503, timeout) bersifat transien dan lazim;
    cukup satu baris ringkas. Traceback penuh hanya untuk error tak terduga.
    """
    try:
        await poll_idx()
    except (OSError, RuntimeError) as error:
        logger.warning("poll gagal: %s", short_error(error))
    except Exception as error:
        if type(error).__module__.startswith("curl_cffi"):
            logger.warning("poll gagal: %s", short_error(error))
        else:
            logger.exception("poll gagal (tak terduga)")


@asynccontextmanager
async def lifespan(_: FastAPI):
    repository.initialize()
    scheduler.add_job(
        poll_idx_safe,
        "interval",
        seconds=settings.idx_poll_seconds,
        id="idx-poll",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    await poll_idx_safe()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="IDX Disclosure Webhook Gateway", lifespan=lifespan)


def require_secret(secret: str | None) -> None:
    if not settings.idx_webhook_secret or secret != settings.idx_webhook_secret:
        raise HTTPException(status_code=401, detail="Webhook secret tidak valid")


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "webhook_out": {"aktif": bool(settings.webhook_url), "format": settings.webhook_format},
        "agent": "enabled" if graph is not None else "disabled",
        "poll_seconds": settings.idx_poll_seconds,
        "last_poll": last_poll,
    }


@app.get("/stats")
async def stats(x_idx_webhook_secret: str | None = Header(default=None)) -> dict[str, int]:
    """Hitungan disclosure per status di database."""
    require_secret(x_idx_webhook_secret)
    return repository.stats()


@app.post("/trigger")
async def trigger(x_idx_webhook_secret: str | None = Header(default=None)) -> dict[str, int]:
    """Jalankan poll IDX sekarang tanpa menunggu jadwal."""
    require_secret(x_idx_webhook_secret)
    return await poll_idx()


@app.post("/webhook/idx")
async def ingest_idx(
    payload: IngestPayload,
    x_idx_webhook_secret: str | None = Header(default=None),
) -> dict[str, int]:
    """Endpoint ingest inbound: dorong disclosure dari luar ke gateway ini."""
    require_secret(x_idx_webhook_secret)
    results = [await pipeline.process(item) for item in payload.disclosures]
    return {status: results.count(status) for status in set(results)}


@app.websocket("/ws/idx")
async def ingest_idx_ws(websocket: WebSocket) -> None:
    if not settings.idx_webhook_secret or websocket.query_params.get("secret") != settings.idx_webhook_secret:
        await websocket.close(code=1008, reason="Webhook secret tidak valid")
        return
    await websocket.accept()
    try:
        while True:
            payload = IngestPayload.model_validate(await websocket.receive_json())
            results = [await pipeline.process(item) for item in payload.disclosures]
            await websocket.send_json({status: results.count(status) for status in set(results)})
    except WebSocketDisconnect:
        return
