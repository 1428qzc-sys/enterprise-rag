from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "docker" / "download_locked_requirements.py"
SPEC = importlib.util.spec_from_file_location("locked_download_script", SCRIPT_PATH)
assert SPEC and SPEC.loader
locked_download = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = locked_download
SPEC.loader.exec_module(locked_download)


class FakeResponse:
    def __init__(
        self,
        chunks: list[bytes | Exception],
        *,
        status: int,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.chunks = iter(chunks)
        self.status = status
        self.headers = headers or {}

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int) -> bytes:
        value = next(self.chunks, b"")
        if isinstance(value, Exception):
            raise value
        return value


def test_selects_pinned_artifact_for_supported_architectures() -> None:
    amd64 = locked_download.select_resumable_artifact("numpy==2.4.6", "AMD64")
    arm64 = locked_download.select_resumable_artifact("numpy==2.4.6", "arm64")

    assert "x86_64" in amd64.filename
    assert "aarch64" in arm64.filename
    assert locked_download.select_resumable_artifact("numpy==2.4.6", "s390x") is None
    assert locked_download.select_resumable_artifact("jieba==0.42.1", "s390x")


def test_resumes_partial_download_and_verifies_digest(tmp_path: Path) -> None:
    payload = b"resumable artifact"
    digest = hashlib.sha256(payload).hexdigest()
    artifact = locked_download.ResumableArtifact(
        filename="artifact.whl",
        url="https://files.pythonhosted.org/artifact.whl",
        sha256=digest,
        size=len(payload),
    )
    responses = iter(
        [
            FakeResponse([payload[:7], TimeoutError("interrupted")], status=200),
            FakeResponse(
                [payload[7:]],
                status=206,
                headers={"Content-Range": f"bytes 7-{len(payload) - 1}/{len(payload)}"},
            ),
        ]
    )
    requests: list[Any] = []

    def open_url(request: Any, *, timeout: int) -> FakeResponse:
        requests.append(request)
        assert timeout == 60
        return next(responses)

    result = locked_download.download_resumable(
        "artifact==1.0",
        artifact,
        tmp_path,
        open_url=open_url,
        sleep=lambda _seconds: None,
        max_attempts=2,
    )

    assert result == "artifact==1.0"
    assert (tmp_path / artifact.filename).read_bytes() == payload
    assert not (tmp_path / f"{artifact.filename}.part").exists()
    assert requests[1].get_header("Range") == "bytes=7-"


def test_rejects_artifact_with_wrong_digest(tmp_path: Path) -> None:
    payload = b"tampered"
    artifact = locked_download.ResumableArtifact(
        filename="artifact.whl",
        url="https://files.pythonhosted.org/artifact.whl",
        sha256=hashlib.sha256(b"expected").hexdigest(),
        size=len(payload),
    )

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        locked_download.download_resumable(
            "artifact==1.0",
            artifact,
            tmp_path,
            open_url=lambda *_args, **_kwargs: FakeResponse([payload], status=200),
            sleep=lambda _seconds: None,
            max_attempts=1,
        )

    assert not (tmp_path / artifact.filename).exists()
    assert not (tmp_path / f"{artifact.filename}.part").exists()
