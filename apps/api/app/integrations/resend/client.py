"""Resend (https://resend.com) transactional email client.

Docs: https://resend.com/docs/api-reference/emails/send-email

Thin async wrapper over POST https://api.resend.com/emails with exponential
backoff on 429 / 5xx. Kept minimal on purpose -- we do not need the full
Resend surface (templates, webhooks, domains) for the boletins use case.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.integrations.base import IntegrationClient

logger = logging.getLogger(__name__)

RESEND_BASE_URL = "https://api.resend.com"


class ResendError(RuntimeError):
    """Raised when Resend returns a non-2xx response we cannot recover from."""


class ResendClient(IntegrationClient):
    name = "resend"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = RESEND_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not api_key:
            raise ValueError("RESEND_API_KEY is required to construct ResendClient")
        self._api_key = api_key
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def health_check(self) -> bool:
        # Resend has no cheap health endpoint; assume OK if an API key is set.
        return True

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, max=8),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def send_email(
        self,
        *,
        to: list[str],
        subject: str,
        html: str,
        from_: str,
        reply_to: str | None = None,
    ) -> dict[str, Any]:
        """Send an email. Returns the Resend response body (includes message id).

        Raises:
            ResendError: on non-2xx after retries.
        """
        if not to:
            raise ValueError("`to` must contain at least one recipient")

        payload: dict[str, Any] = {
            "from": from_,
            "to": to,
            "subject": subject,
            "html": html,
        }
        if reply_to:
            payload["reply_to"] = reply_to

        resp = await self._client.post("/emails", json=payload)
        if resp.status_code >= 400:
            # 429 and 5xx are retried by tenacity (as HTTPError via raise_for_status).
            # 4xx other than 429 bubble up as ResendError so the caller knows the
            # payload was rejected (bad sender, bad recipient, etc.).
            if resp.status_code == 429 or resp.status_code >= 500:
                resp.raise_for_status()
            try:
                body = resp.json()
            except ValueError:
                body = {"raw": resp.text}
            raise ResendError(
                f"Resend rejected request ({resp.status_code}): {body}"
            )
        return resp.json()

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()
