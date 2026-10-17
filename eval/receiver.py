import asyncio
import time
from aiohttp import web
from typing import Dict, Any, Optional

class WebhookReceiver:
    def __init__(self, host: str = "127.0.0.1", port: int = 9999):
        self.host = host
        self.port = port
        self.webhooks: Dict[str, Dict[str, Any]] = {}
        self.events: Dict[str, asyncio.Event] = {}
        self.app = web.Application()
        self.app.router.add_post("/webhook", self._handle_webhook)
        self.app.router.add_get("/health", self._handle_health)
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        received_at = time.perf_counter()
        received_at_wall = time.time()
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)

        job_id = str(data.get("job_id"))
        self.webhooks[job_id] = {
            "received_at": received_at,
            "received_at_wall": received_at_wall,
            "payload": data
        }

        if job_id in self.events:
            self.events[job_id].set()

        return web.json_response({"status": "received"}, status=200)

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()

    def register_job(self, job_id: str):
        if job_id not in self.events:
            self.events[job_id] = asyncio.Event()

    async def wait_for_job(self, job_id: str, timeout: float = 60.0) -> Optional[Dict[str, Any]]:
        if job_id not in self.events:
            self.events[job_id] = asyncio.Event()

        if job_id in self.webhooks:
            return self.webhooks[job_id]

        try:
            await asyncio.wait_for(self.events[job_id].wait(), timeout=timeout)
            return self.webhooks.get(job_id)
        except asyncio.TimeoutError:
            return None

    def clear(self):
        self.webhooks.clear()
        self.events.clear()
