"""Tests for §10 risk/confidence routing + §11 dependency-aware fan-out."""
import pytest

from hermes_cli.worker_backend import (
    classify_task,
    scan_task_dependencies,
    can_parallelize,
)


# --- classify_task (§10) ---

def test_classify_risk_signal_rm_rf():
    c = classify_task("run rm -rf /tmp/cache to clean up")
    assert c["risk"] == "high"
    assert any("rm -rf" in r for r in c["reasons"])


def test_classify_human_lane_production_deploy():
    c = classify_task("deploy the new build to production")
    assert c["lane"] == "human"
    assert c["risk"] == "high"
    assert any("production" in r for r in c["reasons"])
    assert any("deploy" in r for r in c["reasons"])


def test_classify_human_lane_credentials():
    c = classify_task("rotate the api key credentials")
    assert c["lane"] == "human"


def test_classify_deep_lane_refactor():
    c = classify_task("refactor the database migration for scalability")
    assert c["lane"] == "deep"
    assert c["confidence"] >= 0.5


def test_classify_cheap_lane_rename():
    c = classify_task("rename variable x to y in the file")
    assert c["lane"] == "cheap"
    assert c["confidence"] >= 0.5


def test_classify_default_standard():
    c = classify_task("implement the feature described in the ticket")
    assert c["lane"] == "standard"
    assert c["confidence"] == 0.5


# --- scan_task_dependencies (§11) ---

def test_scan_extracts_paths():
    info = scan_task_dependencies("edit auth.py and fix ui.js")
    assert "auth.py" in info["files"]
    assert "ui.js" in info["files"]


def test_scan_quoted_paths():
    info = scan_task_dependencies('update "/tmp/conf/settings.yaml"')
    assert any("settings.yaml" in f for f in info["files"])


def test_scan_no_paths_unknown():
    info = scan_task_dependencies("write a hello world function")
    assert info["files"] == []


# --- can_parallelize (§11) ---

def test_parallel_shared_file_detected():
    """Two tasks referencing the same file must NOT parallelize, even if the
    file does not exist on disk yet (intent to touch it)."""
    res = can_parallelize([
        {"task_text": "edit auth.py"},
        {"task_text": "fix auth.py"},
    ])
    assert res["parallel"] is False
    assert "auth.py" in res["reason"]


def test_parallel_independent_files():
    res = can_parallelize([
        {"task_text": "edit auth.py"},
        {"task_text": "edit ui.py"},
    ])
    assert res["parallel"] is True


def test_parallel_no_files_independent():
    res = can_parallelize([
        {"task_text": "write a function"},
        {"task_text": "write a test"},
    ])
    assert res["parallel"] is True


def test_parallel_three_tasks_one_shared():
    res = can_parallelize([
        {"task_text": "edit auth.py"},
        {"task_text": "edit ui.py"},
        {"task_text": "refactor auth.py"},
    ])
    assert res["parallel"] is False
    assert "auth.py" in res["reason"]
