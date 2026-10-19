# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

import pytest

jobs = pytest.importorskip("ringconvert.jobs")
from ringconvert.core.naming import Result  # noqa: E402


def run_sync(job):
    """Run a Job's work loop without threads or a main loop."""
    return job._run_work()


def test_work_job_collects_results_and_failures(tmp_path):
    good, bad = tmp_path / "a.png", tmp_path / "b.png"

    def work(path: Path, _progress) -> Result:
        if path == bad:
            raise ValueError("broken")
        return Result(outputs=[path.with_name("a Cropped.png")])

    seen = []
    job = jobs.Job([good, bad], "crop", seen.append, None, work)
    job._progress = seen.append  # skip GLib.idle_add
    result, failures = run_sync(job)
    assert [p.name for p in result.outputs] == ["a Cropped.png"]
    assert failures[0][0] == bad and str(failures[0][1]) == "broken"
    assert seen == [0.5, 1.0]


def test_tool_notification_titles(tmp_path):
    job = jobs.Job([tmp_path / "a.png"], "crop", None, None, lambda p, _progress: Result())
    note = jobs.build_notification(job, Result(outputs=[tmp_path / "a Cropped.png"]), [])
    assert note is not None
    assert jobs.TOOL_TITLES["crop"][0] == "Cropped"
