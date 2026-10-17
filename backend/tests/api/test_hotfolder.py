"""Watched folder (CLI-02): polling, stability check, presets, .done / .error."""

import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from a0td.api.app import build_services, create_app
from a0td.automation.hotfolder import HotFolder
from a0td.config import AppConfig


def _wait(predicate: Callable[[], bool], timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError("timed out waiting for the hot folder")


@pytest.fixture
def hot_client(make_config: Callable[..., AppConfig], fake_engine: None) -> Iterator[TestClient]:
    cfg = make_config(hotfolder=True, hotfolder_interval_s=0.2)
    with TestClient(create_app(cfg)) as c:
        yield c


def _data(client: TestClient) -> Path:
    return Path(client.app.state.services.config.data_dir)  # type: ignore[attr-defined]


def test_file_in_preset_folder_is_rendered_and_archived(
    hot_client: TestClient, fixtures_dir: Path
) -> None:
    data = _data(hot_client)
    folder = data / "inbox" / "builtin-real-a4"
    _wait(lambda: (data / "inbox").is_dir())
    folder.mkdir()
    (folder / "plan.pdf").write_bytes((fixtures_dir / "a0_vector_grid.pdf").read_bytes())
    outbox = data / "outbox" / "builtin-real-a4"
    _wait(lambda: (folder / ".done" / "plan.pdf").is_file())
    assert (outbox / "tiles ñ.pdf").read_bytes().startswith(b"%PDF")
    log = (folder / ".done" / "plan.pdf.log").read_text(encoding="utf-8")
    assert "status: done" in log
    assert "output: builtin-real-a4" in log
    assert not (folder / "plan.pdf").exists()


def test_file_in_inbox_root_uses_default_settings(
    hot_client: TestClient, fixtures_dir: Path
) -> None:
    data = _data(hot_client)
    _wait(lambda: (data / "inbox").is_dir())
    (data / "inbox" / "root.pdf").write_bytes((fixtures_dir / "a0_vector_grid.pdf").read_bytes())
    _wait(lambda: (data / "inbox" / ".done" / "root.pdf").is_file())
    assert (data / "outbox" / "tiles ñ.pdf").is_file()


def test_unknown_preset_and_corrupt_files_go_to_error(
    hot_client: TestClient, fixtures_dir: Path
) -> None:
    data = _data(hot_client)
    _wait(lambda: (data / "inbox").is_dir())
    nope = data / "inbox" / "no-such-preset"
    nope.mkdir()
    (nope / "a.pdf").write_bytes((fixtures_dir / "a0_vector_grid.pdf").read_bytes())
    (data / "inbox" / "junk.pdf").write_bytes(b"this is not a pdf at all" * 20)
    _wait(lambda: (nope / ".error" / "a.pdf.log").is_file())
    _wait(lambda: (data / "inbox" / ".error" / "junk.pdf.log").is_file())
    assert "UNKNOWN_PRESET" in (nope / ".error" / "a.pdf.log").read_text(encoding="utf-8")
    assert "status: error" in (data / "inbox" / ".error" / "junk.pdf.log").read_text("utf-8")
    assert (nope / ".error" / "a.pdf").is_file()
    assert not (data / "outbox" / "no-such-preset").exists()


def test_preset_can_be_named_case_insensitively(hot_client: TestClient, fixtures_dir: Path) -> None:
    created = hot_client.post(
        "/api/v1/presets", json={"name": "Mi Preset", "settings": {"target": {"paper": "A3"}}}
    )
    assert created.status_code in (200, 201), created.text
    data = _data(hot_client)
    _wait(lambda: (data / "inbox").is_dir())
    folder = data / "inbox" / "mi preset"
    folder.mkdir()
    (folder / "x.pdf").write_bytes((fixtures_dir / "a0_vector_grid.pdf").read_bytes())
    _wait(lambda: (folder / ".done" / "x.pdf").is_file())


def test_name_collisions_in_outbox_and_done_are_resolved(
    hot_client: TestClient, fixtures_dir: Path
) -> None:
    data = _data(hot_client)
    _wait(lambda: (data / "inbox").is_dir())
    src = (fixtures_dir / "a0_vector_grid.pdf").read_bytes()
    for n in range(2):
        (data / "inbox" / "same.pdf").write_bytes(src)
        _wait(lambda n=n: len(list((data / "inbox" / ".done").glob("same*.pdf"))) == n + 1)
    assert len(list((data / "outbox").glob("tiles*.pdf"))) == 2


def test_files_still_being_written_are_skipped(
    make_config: Callable[..., AppConfig], tmp_path: Path
) -> None:
    cfg = make_config(hotfolder=True)
    hot = HotFolder(build_services(cfg), interval_s=1)
    inbox = hot.inbox / "preset"
    inbox.mkdir(parents=True)
    f = inbox / "big.pdf"
    f.write_bytes(b"%PDF-1")
    assert hot._stable_files() == []  # first sight
    f.write_bytes(b"%PDF-1 more data")  # still growing
    assert hot._stable_files() == []
    assert [c.path for c in hot._stable_files()] == [f]  # unchanged across two polls
    # hidden files, symlinks and the .done / .error folders are never picked up
    (inbox / ".hidden.pdf").write_bytes(b"x")
    (inbox / ".done").mkdir()
    (inbox / ".done" / "old.pdf").write_bytes(b"x")
    hot._busy.add(f)
    assert hot._stable_files() == []
    hot._busy.clear()
    assert [c.path for c in hot._stable_files()] == [f]


def test_empty_files_wait_until_they_have_content(
    make_config: Callable[..., AppConfig],
) -> None:
    hot = HotFolder(build_services(make_config(hotfolder=True)), interval_s=1)
    hot.inbox.mkdir(parents=True)
    f = hot.inbox / "empty.pdf"
    f.write_bytes(b"")
    assert hot._stable_files() == []
    assert hot._stable_files() == []
    f.write_bytes(b"%PDF")
    assert hot._stable_files() == []
    assert len(hot._stable_files()) == 1


def test_failed_move_is_not_reprocessed_and_gives_up(
    make_config: Callable[..., AppConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    from a0td.automation import hotfolder as hf

    hot = HotFolder(build_services(make_config(hotfolder=True)), interval_s=1)
    hot.inbox.mkdir(parents=True)
    f = hot.inbox / "stuck.pdf"
    f.write_bytes(b"%PDF-1 data")
    cand = hf.Candidate(f, hot.inbox, None)
    moves: list[str] = []

    def failing_move(src: str, dst: str) -> None:
        moves.append(src)
        raise OSError("read-only")

    monkeypatch.setattr(hf.shutil, "move", failing_move)
    hot._finish(cand, None, ("X", "boom"))
    assert f in hot._stuck
    assert len(moves) == 1
    for _ in range(6):  # polls: the file is never offered again, the move is retried a few times
        assert hot._stable_files() == []
    assert len(moves) == hf.MAX_MOVE_ATTEMPTS
    assert hot._stuck[f].gave_up
    f.unlink()
    hot._stable_files()
    assert f not in hot._stuck


def test_stuck_move_recovers_when_the_folder_becomes_writable(
    make_config: Callable[..., AppConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    from a0td.automation import hotfolder as hf

    hot = HotFolder(build_services(make_config(hotfolder=True)), interval_s=1)
    hot.inbox.mkdir(parents=True)
    f = hot.inbox / "late.pdf"
    f.write_bytes(b"%PDF-1 data")
    cand = hf.Candidate(f, hot.inbox, None)
    real = hf.shutil.move
    monkeypatch.setattr(hf.shutil, "move", lambda *a: (_ for _ in ()).throw(OSError("busy")))
    hot._finish(cand, None, None)
    monkeypatch.setattr(hf.shutil, "move", real)
    assert hot._stable_files() == []
    assert (hot.inbox / ".done" / "late.pdf").is_file()
    assert not f.exists()
    assert f not in hot._stuck


def test_symlink_swapped_in_after_the_check_is_not_followed(
    make_config: Callable[..., AppConfig], tmp_path: Path
) -> None:
    import os

    from a0td.automation.hotfolder import HotFolderError

    hot = HotFolder(build_services(make_config(hotfolder=True)), interval_s=1)
    hot.inbox.mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    link = hot.inbox / "x.pdf"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(HotFolderError):
        hot._import(link)


def test_file_replaced_between_stat_and_open_is_rejected(
    make_config: Callable[..., AppConfig],
) -> None:
    from a0td.automation.hotfolder import HotFolderError, _open_regular

    hot = HotFolder(build_services(make_config(hotfolder=True)), interval_s=1)
    hot.inbox.mkdir(parents=True)
    f = hot.inbox / "a.pdf"
    f.write_bytes(b"first")
    before = f.stat()
    f.write_bytes(b"second, longer")
    with pytest.raises(HotFolderError, match="changed"):
        _open_regular(f, before)
