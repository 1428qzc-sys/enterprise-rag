"""Download an exact requirements lock concurrently for Docker builds."""

from __future__ import annotations

import argparse
import hashlib
import platform
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen


EXACT_REQUIREMENT = re.compile(r"^[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+$")
DOWNLOAD_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ResumableArtifact:
    filename: str
    url: str
    sha256: str
    size: int


# Large artifacts are pinned to immutable PyPI file URLs so interrupted cold
# downloads can resume. Versions, filenames, sizes, and digests come from the
# official PyPI release metadata. Unknown architectures fall back to pip.
RESUMABLE_ARTIFACTS: dict[str, dict[str, ResumableArtifact]] = {
    "jieba==0.42.1": {
        "*": ResumableArtifact(
            filename="jieba-0.42.1.tar.gz",
            url=(
                "https://files.pythonhosted.org/packages/c6/cb/"
                "18eeb235f833b726522d7ebed54f2278ce28ba9438e3135ab0278d9792a2/"
                "jieba-0.42.1.tar.gz"
            ),
            sha256="055ca12f62674fafed09427f176506079bc135638a14e23e25be909131928db2",
            size=19_214_172,
        ),
    },
    "numpy==2.4.6": {
        "x86_64": ResumableArtifact(
            filename=(
                "numpy-2.4.6-cp311-cp311-manylinux_2_27_x86_64."
                "manylinux_2_28_x86_64.whl"
            ),
            url=(
                "https://files.pythonhosted.org/packages/02/03/"
                "74fe2a4cb3817d94d86402f2506554130a2f01414e299b5a843e5a8a957f/"
                "numpy-2.4.6-cp311-cp311-manylinux_2_27_x86_64."
                "manylinux_2_28_x86_64.whl"
            ),
            sha256="89cd468399cfd2504718f0ba50e410dca55a170b61a02ad92bb18c8a65186e93",
            size=16_918_164,
        ),
        "aarch64": ResumableArtifact(
            filename=(
                "numpy-2.4.6-cp311-cp311-manylinux_2_27_aarch64."
                "manylinux_2_28_aarch64.whl"
            ),
            url=(
                "https://files.pythonhosted.org/packages/33/a8/"
                "6fa8c1a345a8c85dbb21932c447bee07c30a2c2a3f31e369c0a84b300147/"
                "numpy-2.4.6-cp311-cp311-manylinux_2_27_aarch64."
                "manylinux_2_28_aarch64.whl"
            ),
            sha256="0ab0a9c4ffb1a6d95ef519fe4247dba8eb6b18ad93999f76b7f657039acabd47",
            size=15_966_692,
        ),
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    return parser.parse_args()


def read_requirements(path: Path) -> list[str]:
    requirements = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    invalid = [item for item in requirements if not EXACT_REQUIREMENT.fullmatch(item)]
    if invalid:
        raise ValueError(f"requirements lock contains non-exact entries: {invalid}")
    if len(requirements) != len(set(requirements)):
        raise ValueError("requirements lock contains duplicate entries")
    return requirements


def select_resumable_artifact(
    requirement: str, machine: str | None = None
) -> ResumableArtifact | None:
    artifacts = RESUMABLE_ARTIFACTS.get(requirement)
    if not artifacts:
        return None
    current_machine = (machine or platform.machine()).lower()
    current_machine = {"amd64": "x86_64", "arm64": "aarch64"}.get(
        current_machine, current_machine
    )
    return artifacts.get(current_machine) or artifacts.get("*")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(DOWNLOAD_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_resumable(
    requirement: str,
    artifact: ResumableArtifact,
    destination: Path,
    *,
    open_url: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: int = 12,
    timeout: int = 60,
) -> str:
    final_path = destination / artifact.filename
    partial_path = destination / f"{artifact.filename}.part"
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        if partial_path.exists() and partial_path.stat().st_size > artifact.size:
            partial_path.unlink()
        if partial_path.exists() and partial_path.stat().st_size == artifact.size:
            if file_sha256(partial_path) == artifact.sha256:
                partial_path.replace(final_path)
                return requirement
            partial_path.unlink()

        offset = partial_path.stat().st_size if partial_path.exists() else 0
        headers = {"User-Agent": "enterprise-rag-clean-build/1.0"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = Request(artifact.url, headers=headers)

        try:
            with open_url(request, timeout=timeout) as response:
                status = getattr(response, "status", None)
                if offset and status != 206:
                    offset = 0
                elif offset:
                    content_range = response.headers.get("Content-Range", "")
                    if not content_range.startswith(f"bytes {offset}-"):
                        raise RuntimeError(
                            f"unexpected Content-Range for {requirement}: {content_range}"
                        )

                mode = "ab" if offset else "wb"
                with partial_path.open(mode) as output:
                    while True:
                        chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                        if not chunk:
                            break
                        output.write(chunk)
                        if output.tell() > artifact.size:
                            raise RuntimeError(
                                f"download exceeds pinned size for {requirement}"
                            )

            actual_size = partial_path.stat().st_size
            if actual_size != artifact.size:
                raise RuntimeError(
                    f"incomplete download for {requirement}: "
                    f"{actual_size}/{artifact.size} bytes"
                )
            actual_digest = file_sha256(partial_path)
            if actual_digest != artifact.sha256:
                partial_path.unlink()
                raise RuntimeError(
                    f"SHA-256 mismatch for {requirement}: {actual_digest}"
                )
            partial_path.replace(final_path)
            return requirement
        except Exception as exc:  # noqa: BLE001 - retry network and protocol failures.
            last_error = exc
            if attempt == max_attempts:
                break
            retained = partial_path.stat().st_size if partial_path.exists() else 0
            print(
                f"[locked-download] retry {attempt}/{max_attempts} {requirement} "
                f"retained={retained}/{artifact.size}: {type(exc).__name__}",
                file=sys.stderr,
                flush=True,
            )
            sleep(min(float(attempt), 5.0))

    raise RuntimeError(
        f"failed resumable download {requirement} after {max_attempts} attempts: "
        f"{type(last_error).__name__}: {last_error}"
    )


def download(requirement: str, destination: Path) -> str:
    artifact = select_resumable_artifact(requirement)
    if artifact:
        return download_resumable(requirement, artifact, destination)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--no-deps",
            "--dest",
            str(destination),
            "--retries",
            "10",
            "--timeout",
            "60",
            requirement,
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        output = "\n".join(
            part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
        )
        raise RuntimeError(f"failed to download {requirement}:\n{output[-4000:]}")
    return requirement


def main() -> int:
    args = parse_args()
    if args.workers < 1 or args.workers > 12:
        raise ValueError("workers must be between 1 and 12")

    requirements = read_requirements(args.requirements)
    args.destination.mkdir(parents=True, exist_ok=False)
    failures: list[str] = []
    completed_count = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(download, requirement, args.destination): requirement
            for requirement in requirements
        }
        for future in as_completed(futures):
            requirement = futures[future]
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 - report all worker failures together.
                failures.append(str(exc))
            else:
                completed_count += 1
                print(
                    f"[locked-download] {completed_count}/{len(requirements)} {requirement}",
                    flush=True,
                )

    if failures:
        raise RuntimeError("\n\n".join(failures))
    artifacts = [path for path in args.destination.iterdir() if path.is_file()]
    if len(artifacts) < len(requirements):
        raise RuntimeError(
            f"downloaded {len(artifacts)} artifacts for {len(requirements)} requirements"
        )
    print(f"[locked-download] complete: {len(artifacts)} artifacts", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
