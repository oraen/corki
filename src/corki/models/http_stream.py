"""Own HTTP attempts and their responses before handing off to an SSE reader."""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx

from corki.models.backoff import backoff
from corki.models.http_errors import http_error

wait_http_retry = asyncio.sleep


@asynccontextmanager
async def model_http_stream(
    client,
    url,
    *,
    headers,
    payload,
    max_retries,
    base_seconds,
    encode_json=None,
):
    request_body = {"json": payload}
    if encode_json is not None:
        request_body = {"content": encode_json(payload).encode("utf-8")}
        headers = httpx.Headers(headers)
        headers.setdefault("Content-Type", "application/json")
    response = None
    for attempt in range(max_retries + 1):
        try:
            request = client.build_request(
                "POST",
                url,
                headers=headers,
                **request_body,
            )
            response = await client.send(request, stream=True)
            if response.is_success:
                break
            try:
                body = (await response.aread()).decode("utf-8", errors="replace")
            except Exception:
                # The reference transport keeps the status even if reading its
                # diagnostic body fails. In particular, a 429 must not become
                # a retryable network error just because its body is truncated.
                # HTTPX also closes a fully read response inside aread(), so a
                # diagnostic read may fail with an ordinary close exception.
                body = ""
                logging.getLogger(__name__).warning(
                    "Model HTTP error response body unavailable", exc_info=True
                )
            finally:
                try:
                    await response.aclose()
                except Exception:
                    # Preserve both the known HTTP status and any cancellation
                    # unwinding the body read; cleanup is not a new connection attempt.
                    logging.getLogger(__name__).warning(
                        "Model HTTP error response cleanup failed", exc_info=True
                    )
            if not 500 <= response.status_code < 600 or attempt == max_retries:
                raise http_error(response, body)
        except httpx.TransportError:
            if response is not None:
                await response.aclose()
            if attempt == max_retries:
                raise
        delay = backoff(base_seconds, attempt)
        logging.getLogger(__name__).debug(
            "Retrying model HTTP request",
            extra={"retry_attempt": attempt + 1, "retry_delay": delay, "retry_layer": "http"},
        )
        await wait_http_retry(delay)
        response = None
    # Keep the yielded reader outside the retry catch: body failures belong to
    # sampling, whose next request must be rebuilt from the updated history.
    primary_error: BaseException | None = None
    try:
        yield response
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            await response.aclose()
        except Exception:
            if primary_error is None:
                raise
            logging.getLogger(__name__).warning(
                "Model HTTP response cleanup failed while unwinding", exc_info=True
            )
