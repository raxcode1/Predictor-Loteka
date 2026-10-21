"""Cliente HTTP del scraper: proxy rotatorio, petición condicional y caché.

Tres mecanismos que reducen la huella del scraping:

1. **Petición condicional** (`ETag` / `If-Modified-Since`): si la página no
   ha cambiado, el servidor responde `304` sin cuerpo. Es exactamente lo que
   hace un navegador al recargar, y el sitio no sirve ni sirve el HTML.
2. **Proxy rotatorio**: el tráfico puede salir por una IP residencial
   (`SCRAPER_PROXY_URLS`, lista separada por comas) en lugar de la IP del
   datacenter. Si una IP acaba bloqueada, la siguiente del conjunto sigue
   funcionando.
3. **Jitter**: el intervalo entre peticiones se sortea dentro de una ventana,
   de modo que nunca hay cadencia fija.

Nada de esto aumenta el volumen de peticiones: el objetivo es que el patrón
sea indistinguible del de un visitante puntual.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

URL = "https://loteka.com.do/"
USER_AGENT = (
    "Mozilla/5.0 (compatible; RuletaExpressCSV/1.0; +https://loteka.com.do/)"
)


class ClienteScraper:
    """Descarga la página pública de Loteka de forma condicionada y con proxy."""

    def __init__(self, cache_path: Path | None = None) -> None:
        self.cache_path = cache_path
        self.cache: dict[str, Any] = self._leer_cache()
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

    # ------------------------------------------------------------------
    def _leer_cache(self) -> dict[str, Any]:
        if not self.cache_path or not self.cache_path.exists():
            return {}
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _guardar_cache(self) -> None:
        if not self.cache_path:
            return
        self.cache["proxy_index"] = self.indice_proxy
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(self.cache, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    # ------------------------------------------------------------------
    def proxy_actual(self) -> str | None:
        if not self.proxies:
            return None
        return self.proxies[self.indice_proxy % len(self.proxies)]

    def siguiente_proxy(self) -> None:
        """Avanza al siguiente proxy del conjunto (rotación al fallar)."""
        if not self.proxies:
            return
        self.indice_proxy = (self.indice_proxy + 1) % len(self.proxies)
        self._guardar_cache()

    def _abrir(self, proxy: str | None):
        handlers = (
            [ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
        )
        return build_opener(*handlers)

    # ------------------------------------------------------------------
    def descargar(self) -> tuple[str | None, bool]:
        """Descarga la página.

        Devuelve ``(html, no_modificado)``. Si el servidor responde `304`, se
        reutiliza el HTML de la última descarga.
        """
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}
        if self.cache.get("etag"):
            headers["If-None-Match"] = str(self.cache["etag"])
        if self.cache.get("last_modified"):
            headers["If-Modified-Since"] = str(self.cache["last_modified"])

        request = Request(URL, headers=headers)
        opener = self._abrir(self.proxy_actual())
        try:
            with opener.open(request, timeout=30) as response:
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
            raise


def time_stamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def intervalo_jitter(minimo: int, maximo: int) -> int:
    """Sortea un intervalo dentro de la ventana configurada."""
    if maximo <= minimo:
        return max(5, minimo)
    return random.randint(minimo, maximo)
