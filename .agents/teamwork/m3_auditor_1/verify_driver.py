import sys, os
sys.path.insert(0, os.path.abspath("."))

import asyncio
import httpx
from app.providers.openai_driver import OpenAiDriver

async def test_stream():
    def stream_handler(request: httpx.Request):
        sse_body = (
            b'data: {"choices": [{"delta": {"reasoning_content": "Thinking process..."}}]}\n\n'
            b'data: {"choices": [{"delta": {"content": "Streamed token response"}}]}\n\n'
            b'data: [DONE]\n\n'
        )
        return httpx.Response(200, content=sse_body)

    transport = httpx.MockTransport(stream_handler)
    driver = OpenAiDriver(provider="lmstudio", base_url="http://localhost:1234/v1")
    orig_client = httpx.AsyncClient
    deltas = []
    reasoning_deltas = []

    try:
        def custom_client(**kwargs):
            kwargs["transport"] = transport
            return orig_client(**kwargs)
        httpx.AsyncClient = custom_client

        res = await driver.stream(
            [{"role": "user", "content": "Test stream"}],
            on_delta=lambda d: deltas.append(d),
            on_reasoning_delta=lambda rd: reasoning_deltas.append(rd)
        )
        assert res == "Streamed token response", f"Got {res}"
        assert deltas == ["Streamed token response"]
        assert reasoning_deltas == ["Thinking process..."]
        assert driver._offline_detected is False
        print("[PASS 3] OpenAiDriver.stream genuinely parses live SSE deltas and reasoning tokens")
    finally:
        httpx.AsyncClient = orig_client

    driver_offline = OpenAiDriver(provider="lmstudio", base_url="http://127.0.0.1:19876/v1", timeout_ms=2000)
    fallback_deltas = []
    res_off = await driver_offline.stream(
        [{"role": "user", "content": "Section 1 Executive Assessment"}],
        on_delta=lambda d: fallback_deltas.append(d)
    )
    assert driver_offline._offline_detected is True
    assert len(res_off) > 0
    assert len(fallback_deltas) > 0
    print("[PASS 4] OpenAiDriver.stream catches connection failure and delegates to MockDriver stream")

if __name__ == "__main__":
    asyncio.run(test_stream())
