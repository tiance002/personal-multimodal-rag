from pathlib import Path
import hashlib
import subprocess

import pytest

from eval_center.runtime import committed_source_provenance
from eval_center.verification import ExperimentInvalidError


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    ).stdout


def commit(repo: Path, message: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c",
         "user.email=test@example.invalid", "commit", "--quiet", "-m", message],
        check=True,
        capture_output=True,
    )


def committed_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "source.py").write_text("value = 1" + chr(10), encoding="utf-8")
    git(repo, "add", "source.py")
    commit(repo, "base")
    (repo / "source.py").write_text("value = 2" + chr(10), encoding="utf-8")
    git(repo, "add", "source.py")
    commit(repo, "feature")
    return repo


def test_committed_source_provenance_binds_commit_tree_and_patch(tmp_path):
    repo = committed_repo(tmp_path)

    identity = committed_source_provenance(repo)
    commit_sha = git(repo, "rev-parse", "HEAD").decode().strip()
    tree_oid = git(repo, "rev-parse", "HEAD^{tree}").decode().strip()
    parent_sha = git(repo, "rev-parse", "HEAD^").decode().strip()
    patch = git(repo, "diff", "--binary", parent_sha, commit_sha)

    assert identity == {
        "git_sha": commit_sha,
        "tree_oid": tree_oid,
        "patch_base_sha": parent_sha,
        "patch_sha256": hashlib.sha256(patch).hexdigest(),
        "working_tree_clean": True,
    }


def test_committed_source_provenance_rejects_modified_or_untracked_files(tmp_path):
    repo = committed_repo(tmp_path)
    (repo / "untracked.txt").write_text("not committed" + chr(10), encoding="utf-8")

    with pytest.raises(ExperimentInvalidError, match="uncommitted_execution_code"):
        committed_source_provenance(repo)
