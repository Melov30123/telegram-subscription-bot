from __future__ import annotations

from datetime import UTC, datetime

from aiohttp import web

from subscription_bot.database import Database


class HealthServer:
    def __init__(self, database: Database, port: int) -> None:
        self.database = database
        self.port = port
        self.started_at = datetime.now(UTC)
        self.runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self.index)
        app.router.add_get("/healthz", self.health)
        app.router.add_get("/readyz", self.ready)
        app.router.add_get("/metrics", self.metrics)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "0.0.0.0", self.port)
        await site.start()

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()
            self.runner = None

    async def index(self, request: web.Request) -> web.Response:
        return web.json_response({"service": "telegram-subscription-bot", "version": "4.0.0"})

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def ready(self, request: web.Request) -> web.Response:
        database_ok = await self.database.health()
        return web.json_response(
            {"status": "ready" if database_ok else "not_ready", "database": database_ok},
            status=200 if database_ok else 503,
        )

    async def metrics(self, request: web.Request) -> web.Response:
        stats = await self.database.get_stats()
        return web.json_response(
            {
                "uptime_seconds": int((datetime.now(UTC) - self.started_at).total_seconds()),
                "users": stats.users,
                "active_subscriptions": stats.active,
                "successful_payments": stats.payments,
                "stars_total": stats.stars_total,
                "guide_purchases": stats.guide_payments,
            }
        )
