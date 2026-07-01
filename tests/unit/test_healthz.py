import pytest

from dropbox.main import create_app


@pytest.mark.asyncio
async def test_healthz() -> None:
    """App creates and /healthz returns 200."""
    from httpx import ASGITransport, AsyncClient

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
