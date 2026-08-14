from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _close(logger):
    for handler in list(logging.getLogger().handlers):
        if getattr(handler, "_telepiplex_handler_kind", ""):
            logging.getLogger().removeHandler(handler)
            handler.close()


def test_each_start_creates_one_new_folder_with_human_and_machine_logs(tmp_path):
    from app.utils.logger import Logger

    first = Logger(
        config_root=tmp_path,
        session_id="ABC123",
        now=datetime(2026, 8, 14, 15, 15, 30, tzinfo=timezone.utc),
    )
    first.info(
        "第一次启动",
        event_name="runtime.started",
        diagnostic_fields={"status": "ready", "input": {"mode": "polling"}},
    )
    first_session = first.session
    _close(first.logger)

    second = Logger(
        config_root=tmp_path,
        session_id="DEF456",
        now=datetime(2026, 8, 14, 15, 16, 30, tzinfo=timezone.utc),
    )
    second.info("第二次启动", event_name="runtime.started")
    second_session = second.session
    _close(second.logger)

    assert first_session.directory != second_session.directory
    assert first_session.directory.parent == tmp_path / "logs"
    assert second_session.directory.parent == tmp_path / "logs"
    assert not (tmp_path / "logs" / "sessions").exists()
    assert first_session.human_path.parent == first_session.machine_path.parent
    assert first_session.human_path.name == "telepiplex.human.log"
    assert first_session.machine_path.name == "telepiplex.machine.jsonl"
    assert "第一次启动" in first_session.human_path.read_text(encoding="utf-8")
    assert "第二次启动" not in first_session.human_path.read_text(encoding="utf-8")
    first_event = json.loads(first_session.machine_path.read_text(encoding="utf-8"))
    first_human = first_session.human_path.read_text(encoding="utf-8")
    assert first_event["identity"]["session_id"] == "ABC123"
    assert first_event["event"]["name"] == "runtime.started"
    assert first_event["event_id"] not in first_human


def test_session_retention_removes_whole_old_folders_by_age_and_start_count(tmp_path):
    from app.utils.logger import prune_log_sessions

    sessions_root = tmp_path / "logs"
    sessions_root.mkdir(parents=True)
    now = datetime(2026, 8, 14, 16, 0, tzinfo=timezone.utc)
    unrelated = sessions_root / "exports"
    unrelated.mkdir()
    unrelated_timestamp = (now - timedelta(days=90)).timestamp()
    os.utime(unrelated, (unrelated_timestamp, unrelated_timestamp))
    old = sessions_root / "20260701T000000+0000-OLD"
    old.mkdir()
    (old / "telepiplex.human.log").write_text("old", encoding="utf-8")
    old_timestamp = (now - timedelta(days=31)).timestamp()
    os.utime(old, (old_timestamp, old_timestamp))

    recent = []
    for index in range(31):
        directory = sessions_root / f"20260814T15{index:02d}00+0000-RECENT{index:02d}"
        directory.mkdir()
        timestamp = (now - timedelta(minutes=31 - index)).timestamp()
        os.utime(directory, (timestamp, timestamp))
        recent.append(directory)

    removed = prune_log_sessions(sessions_root, now=now, keep_sessions=30, keep_days=30)

    remaining = sorted(path.name for path in sessions_root.iterdir())
    assert old.name not in remaining
    assert recent[0].name not in remaining
    assert "exports" in remaining
    assert len(remaining) == 31
    assert set(removed) == {old, recent[0]}
    assert not old.exists()
    assert not recent[0].exists()


def test_level_reconfiguration_reuses_the_current_startup_folder(tmp_path):
    from app.utils.logger import Logger, configure_root_logger

    wrapper = Logger(config_root=tmp_path, session_id="LEVEL1")
    directory = wrapper.session.directory
    configure_root_logger(level=logging.DEBUG, session=wrapper.session)
    logging.getLogger("telepiplex.test").debug(
        "调试级别已经启用",
        extra={"event_name": "logging.level.changed"},
    )
    _close(wrapper.logger)

    session_dirs = list((tmp_path / "logs").iterdir())
    assert session_dirs == [directory]
    assert "调试级别已经启用" in wrapper.session.human_path.read_text(encoding="utf-8")


def test_init_creates_the_host_session_directly_under_config_logs(tmp_path, monkeypatch):
    import app.init as init

    original_logger = init.logger
    monkeypatch.setattr(init, "CONFIG", str(tmp_path))
    monkeypatch.setattr(init, "bot_config", {"log_level": "info"})
    try:
        init.create_logger()
        session = init.logger.session
        _close(init.logger.logger)
    finally:
        init.logger = original_logger

    assert session.directory.parent == tmp_path / "logs"
    assert "日志系统启动完成" in session.human_path.read_text(encoding="utf-8")
    event = json.loads(session.machine_path.read_text(encoding="utf-8"))
    assert event["event"]["name"] == "diagnostics.session.started"
    assert event["facts"]["output"]["session_directory"] == str(session.directory)


def test_existing_key_value_logs_become_typed_machine_facts_without_rewriting_callers(tmp_path):
    from app.utils.logger import Logger

    wrapper = Logger(config_root=tmp_path, session_id="LEGACY")
    wrapper.info(
        "search_source_completed source=tvdb stage=source_resolution "
        "status=matched count=38 duration_ms=621"
    )
    _close(wrapper.logger)

    event = json.loads(wrapper.session.machine_path.read_text(encoding="utf-8"))
    assert event["event"]["name"] == "search_source_completed"
    assert event["event"]["stage"] == "source_resolution"
    assert event["event"]["status"] == "matched"
    assert event["event"]["duration_ms"] == 621
    assert event["facts"]["legacy_fields"] == {"source": "tvdb", "count": 38}
    human = wrapper.session.human_path.read_text(encoding="utf-8")
    assert "来源：tvdb" in human
    assert "count：38" in human
