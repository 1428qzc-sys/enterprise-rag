from pathlib import PurePosixPath

import pytest

from scripts.backup_restore_drill import relative_upload_path


@pytest.mark.parametrize(
    ("stored_path", "expected"),
    [
        ("data/uploads/tenant/document.md", "uploads/tenant/document.md"),
        ("./data/uploads/tenant/document.md", "uploads/tenant/document.md"),
        ("/app/data/uploads/tenant/document.md", "uploads/tenant/document.md"),
    ],
)
def test_relative_upload_path_accepts_runtime_forms(
    stored_path: str, expected: str
) -> None:
    assert relative_upload_path(stored_path) == PurePosixPath(expected)


@pytest.mark.parametrize(
    "stored_path",
    [
        "uploads/tenant/document.md",
        "data/cache/document.md",
        "/tmp/uploads/document.md",
        "data/uploads/../secret.txt",
        "/app/data/uploads/../../secret.txt",
    ],
)
def test_relative_upload_path_rejects_paths_outside_uploads(stored_path: str) -> None:
    with pytest.raises(RuntimeError):
        relative_upload_path(stored_path)
