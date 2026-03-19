from __future__ import annotations

from multiprocessing import Process
from pathlib import Path
from time import sleep

from findmejobs.utils.locking import FileLock


def _acquire_lock_and_touch(lock_path: Path, marker_path: Path) -> None:
    with FileLock(lock_path):
        marker_path.write_text("locked", encoding="utf-8")


def test_file_lock_blocks_overlapping_processes(tmp_path: Path) -> None:
    lock_path = tmp_path / "pipeline.lock"
    marker_path = tmp_path / "marker.txt"

    with FileLock(lock_path):
        worker = Process(target=_acquire_lock_and_touch, args=(lock_path, marker_path))
        worker.start()
        sleep(0.2)
        assert not marker_path.exists()

    worker.join(timeout=2)
    assert worker.exitcode == 0
    assert marker_path.read_text(encoding="utf-8") == "locked"
