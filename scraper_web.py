from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

# Usar Cloudflare Worker si está configurado
WORKER_URL = os.environ.get("CLOUDFLARE_WORKER_URL", "").strip().rstrip("/")
if WORKER_URL:
    URL = f"{WORKER_URL}/"
else:
    URL = "https://loteka.com.do/"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"


class ClienteScraper:
    def __init__(self, cache_path: Path | None = None) -> None:
        self.cache_path = cache_path
        self.cache: dict[str, Any] = self._leer_cache()
        # No usar proxies si estamos usando Cloudflare Worker
        if WORKER_URL:
            self.proxies = []
        else:
            self.proxies: list[str] = [
                item.strip()
                for item in os.environ.get("SCRAPER_PROXY_URLS", "").split(",")
                if item.strip()
            ]
            if not self.proxies:
                unico = os.environ.get("SCRAPER_PROXY_URL", "").strip()
                if unico:
                    self.proxies = [unico]
        self.indice_proxy = int(self.cache.get("proxy_index", 0) or 0)

    def _leer_cache(self) -> dict[str, Any]:
        if not self.cache_path or not self.cache_path.exists():
            return {}
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _guardar_cache(self) -> None:
        if not self.cache_path:
            return
        self.cache["proxy_index"] = self.indice_proxy
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def proxy_actual(self) -> str | None:
        if not self.proxies:
            return None
        return self.proxies[self.indice_proxy % len(self.proxies)]

    def siguiente_proxy(self) -> None:
        if not self.proxies:
            return
        self.indice_proxy = (self.indice_proxy + 1) % len(self.proxies)
        self._guardar_cache()

    def _abrir(self, proxy: str | None):
        handlers = [ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
        return build_opener(*handlers)

    def descargar(self, forzar_completo: bool = False) -> tuple[str | None, bool]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-DO,es;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        }
        if not forzar_completo:
            if self.cache.get("etag"):
                headers["If-None-Match"] = str(self.cache["etag"])
            if self.cache.get("last_modified"):
                headers["If-Modified-Since"] = str(self.cache["last_modified"])

        request = Request(URL, headers=headers)
        opener = self._abrir(self.proxy_actual())
        try:
            with opener.open(request, timeout=60) as response:
                html = response.read().decode("utf-8", errors="replace")
                self.cache["etag"] = response.headers.get("ETag")
                self.cache["last_modified"] = response.headers.get("Last-Modified")
                self.cache["html"] = html
                self.cache["actualizado"] = time_stamp()
                self._guardar_cache()
                return html, False
        except HTTPError as error:
            if error.code == 304:
                return self.cache.get("html"), True
            if error.code == 403:
                try:
                    headers_min = {"User-Agent": USER_AGENT}
                    request = Request(URL, headers=headers_min)
                    with opener.open(request, timeout=60) as response:
                        html = response.read().decode("utf-8", errors="replace")
                        self.cache["etag"] = response.headers.get("ETag")
                        self.cache["last_modified"] = response.headers.get("Last-Modified")
                        self.cache["html"] = html
                        self.cache["actualizado"] = time_stamp()
                        self._guardar_cache()
                        return html, False
                except HTTPError as e2:
                    if e2.code == 304:
                        return self.cache.get("html"), True
                    raise
            raise


def time_stamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def intervalo_jitter(minimo: int, maximo: int) -> int:
    if maximo <= minimo:
        return max(5, minimo)
    return random.randint(minimo, maximo)
