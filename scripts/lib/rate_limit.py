"""
Rate-limit i backoff helper za HTTP scraping.

Koristi se kao context manager ili direktnim pozivom wait().
Backoff na 429/503: 1 min → 5 min → 15 min → raise BatchPausedException.
Normalni jitter između zahtjeva: 3-6 sekundi.
"""
import logging
import random
import time
from contextlib import contextmanager

import requests

log = logging.getLogger(__name__)

JITTER_MIN = 3.0
JITTER_MAX = 6.0
BACKOFF_SEQUENCE = [60, 300, 900]  # sekunde: 1 min, 5 min, 15 min


class BatchPausedException(Exception):
    """Batch treba stati — iscrpljeni backoff pokušaji."""


class RateLimiter:
    def __init__(self, max_per_run: int = 90):
        self._count = 0
        self._max = max_per_run
        self._backoff_idx = 0

    @property
    def count(self) -> int:
        return self._count

    def wait(self) -> None:
        """Čeka nasumični jitter interval između zahtjeva."""
        delay = random.uniform(JITTER_MIN, JITTER_MAX)
        log.debug("rate_limit: čekam %.1fs (zahtjev %d/%d)", delay, self._count + 1, self._max)
        time.sleep(delay)

    def check_limit(self) -> None:
        """Podiže LimitReached ako smo dosegli max_per_run."""
        if self._count >= self._max:
            raise BatchPausedException(
                f"Dosegnut limit od {self._max} zahtjeva u ovom runu. Restart workflow za nastavak."
            )

    def record_success(self) -> None:
        self._count += 1
        self._backoff_idx = 0  # reset backoff na uspjeh

    def handle_throttle(self, status_code: int) -> None:
        """
        Pozovi kad dobije 429 ili 503.
        Čeka backoff prema sekvenci; ako se iscrpi, diže BatchPausedException.
        """
        if self._backoff_idx >= len(BACKOFF_SEQUENCE):
            raise BatchPausedException(
                f"Iscrpljeni backoff pokušaji nakon {status_code}. Batch pauziran."
            )
        wait_sec = BACKOFF_SEQUENCE[self._backoff_idx]
        log.warning(
            "Primljeno %d — backoff %ds (pokušaj %d/%d)",
            status_code,
            wait_sec,
            self._backoff_idx + 1,
            len(BACKOFF_SEQUENCE),
        )
        time.sleep(wait_sec)
        self._backoff_idx += 1

    def get(self, session: requests.Session, url: str, **kwargs) -> requests.Response:
        """
        Wrapper za session.get koji automatski:
        - provjerava limit
        - čeka jitter prije poziva
        - radi backoff na 429/503
        - bilježi uspjeh
        """
        self.check_limit()
        self.wait()

        while True:
            try:
                resp = session.get(url, **kwargs)
            except requests.RequestException as e:
                log.error("Mrežna greška: %s", e)
                raise

            if resp.status_code in (429, 503):
                self.handle_throttle(resp.status_code)
                continue

            self.record_success()
            return resp


@contextmanager
def rate_limited_session(max_per_run: int = 90):
    """Context manager koji vraća (session, limiter) par."""
    limiter = RateLimiter(max_per_run=max_per_run)
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "hr-HR,hr;q=0.9,en;q=0.8",
    })
    try:
        yield session, limiter
    finally:
        session.close()
