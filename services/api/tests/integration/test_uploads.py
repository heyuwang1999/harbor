"""Upload → parse → index → answerable, through the HTTP API.

Ingestion runs inline here (`ingest_eager`), so the test covers the same pipeline the
Celery worker executes without needing a broker.
"""

import io
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from harbor_api.app import create_app
from harbor_api.core.config import Settings
from harbor_api.core.resources import Resources
from harbor_api.models import Tenant

HANDBOOK = """# Acme Kitchen Handbook

## Knife Safety

Every kitchen hand must complete the knife safety induction before their first shift.
Cut-resistant gloves are provided and must be worn when using the mandoline slicer.

## Uniform Allowance

Kitchen staff receive a uniform allowance of HK$1,200 per year, claimed through the
expense system with receipts attached.
"""


@pytest.fixture
def upload_settings(settings: Settings, tmp_path) -> Settings:  # type: ignore[no-untyped-def]
    return settings.model_copy(update={"ingest_eager": True, "storage_root": tmp_path})


@pytest.fixture
async def client(upload_settings: Settings, seeded: None) -> AsyncIterator[AsyncClient]:
    app: FastAPI = create_app(upload_settings)
    # ASGITransport skips the lifespan, so wire the resources the app would have built.
    app.state.resources = Resources.create(upload_settings)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http_client:
            yield http_client
    finally:
        await app.state.resources.close()


UploadFiles = dict[str, tuple[str, io.BytesIO, str]]


def upload_files(content: str = HANDBOOK, filename: str = "acme-handbook.md") -> UploadFiles:
    return {"file": (filename, io.BytesIO(content.encode()), "text/markdown")}


async def test_uploaded_document_becomes_searchable(
    client: AsyncClient, demo_tenant: Tenant
) -> None:
    response = await client.post(
        "/v1/documents",
        files=upload_files(),
        data={"visibility": "internal", "title": "Acme Kitchen Handbook"},
        headers={"x-demo-role": "staff"},
    )
    assert response.status_code == 202
    document_id = response.json()["id"]

    detail = (
        await client.get(f"/v1/documents/{document_id}", headers={"x-demo-role": "staff"})
    ).json()
    assert detail["status"] == "indexed"
    assert detail["error"] is None
    # Short sections merge by design (see chunking tests), so assert coverage, not count.
    assert detail["chunks"]
    assert any("Knife Safety" in chunk["heading_path"] for chunk in detail["chunks"])
    indexed = "\n".join(chunk["text"] for chunk in detail["chunks"])
    assert "mandoline slicer" in indexed
    assert "HK$1,200" in indexed

    search = await client.post(
        "/v1/search",
        json={"query": "mandoline slicer gloves"},
        headers={"x-demo-role": "staff"},
    )
    titles = [candidate["document_title"] for candidate in search.json()["context"]]
    assert "Acme Kitchen Handbook" in titles

    await client.delete(f"/v1/documents/{document_id}", headers={"x-demo-role": "staff"})


async def test_uploaded_document_respects_visibility(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/documents",
        files=upload_files(filename="acme-internal.md"),
        data={"visibility": "internal"},
        headers={"x-demo-role": "staff"},
    )
    document_id = response.json()["id"]

    visitor = await client.post(
        "/v1/search",
        json={"query": "mandoline slicer gloves"},
        headers={"x-demo-role": "visitor"},
    )
    assert all(candidate["visibility"] == "public" for candidate in visitor.json()["candidates"])

    await client.delete(f"/v1/documents/{document_id}", headers={"x-demo-role": "staff"})


async def test_visitors_cannot_upload(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/documents",
        files=upload_files(),
        data={"visibility": "public"},
        headers={"x-demo-role": "visitor"},
    )

    assert response.status_code == 403


async def test_unsupported_file_type_is_rejected_with_a_readable_message(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/v1/documents",
        files={"file": ("payroll.exe", io.BytesIO(b"MZ\x90\x00"), "application/octet-stream")},
        data={"visibility": "internal"},
        headers={"x-demo-role": "staff"},
    )

    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]


async def test_scanned_pdf_fails_with_a_reason_the_uploader_can_act_on(
    client: AsyncClient,
) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)

    response = await client.post(
        "/v1/documents",
        files={"file": ("scan.pdf", io.BytesIO(buffer.getvalue()), "application/pdf")},
        data={"visibility": "internal"},
        headers={"x-demo-role": "staff"},
    )
    document_id = response.json()["id"]

    detail = (
        await client.get(f"/v1/documents/{document_id}", headers={"x-demo-role": "staff"})
    ).json()
    assert detail["status"] == "failed"
    assert "scanned PDF" in detail["error"]

    await client.delete(f"/v1/documents/{document_id}", headers={"x-demo-role": "staff"})


async def test_restricted_upload_requires_groups(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/documents",
        files=upload_files(),
        data={"visibility": "restricted"},
        headers={"x-demo-role": "management"},
    )

    assert response.status_code == 400
    assert "acl_groups" in response.json()["detail"]
