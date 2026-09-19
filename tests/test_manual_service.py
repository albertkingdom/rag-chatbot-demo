"""Unit tests for the basic manual-upload safety boundary."""

import asyncio
from io import BytesIO

import pytest
from starlette.datastructures import Headers, UploadFile

from src import manual_service


def _upload(name: str, content_type: str, body: bytes) -> UploadFile:
    return UploadFile(
        filename=name,
        file=BytesIO(body),
        headers=Headers({"content-type": content_type}),
    )


def test_save_manual_uses_server_generated_name_and_strips_path(tmp_path, monkeypatch):
    monkeypatch.setattr(manual_service, "DATA_SOURCE_DIR", str(tmp_path))
    upload = _upload("../../manual.csv", "text/csv", b"question,answer\nq,a\n")

    saved = asyncio.run(manual_service.save_manual(upload))

    assert saved.parent == tmp_path
    assert saved.name.endswith("-manual.csv")
    assert saved.read_bytes() == b"question,answer\nq,a\n"
    assert not list(tmp_path.glob(".*.upload"))


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("manual.exe", "application/octet-stream"),
        ("manual.pdf", "text/plain"),
        ("manual.xlsx", "application/zip"),
    ],
)
def test_save_manual_rejects_disallowed_or_mismatched_type(
    tmp_path, monkeypatch, filename, content_type
):
    monkeypatch.setattr(manual_service, "DATA_SOURCE_DIR", str(tmp_path))

    with pytest.raises(manual_service.ManualUploadError):
        asyncio.run(manual_service.save_manual(_upload(filename, content_type, b"data")))

    assert not list(tmp_path.iterdir())


def test_oversized_upload_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(manual_service, "DATA_SOURCE_DIR", str(tmp_path))
    monkeypatch.setattr(manual_service, "MAX_UPLOAD_BYTES", 3)

    with pytest.raises(manual_service.ManualTooLargeError):
        asyncio.run(
            manual_service.save_manual(_upload("manual.csv", "text/csv", b"four"))
        )

    assert not list(tmp_path.iterdir())
