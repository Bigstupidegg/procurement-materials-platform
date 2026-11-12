"""Retrieve only previously deployed Pages artifacts; never execute their contents."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from package_release import (COMMIT, PUBLICATION, read_json, require, seal_publication,
                             tree_hash, validate_event, verify_release)

REPOSITORY = "Bigstupidegg/procurement-materials-platform"
REPOSITORY_ID = 1315749314
WORKFLOW = ".github/workflows/deploy-pages.yml"
MAX_BYTES = 900 * 1024 * 1024


def api(path: str) -> dict:
    result = subprocess.run(
        ["gh", "api", "--hostname", "github.com", "--method", "GET",
         f"repos/{REPOSITORY}/{path}"], capture_output=True, text=True, timeout=60)
    require(result.returncode == 0, "cannot read trusted Pages history")
    value = json.loads(result.stdout)
    require(isinstance(value, dict), "invalid Pages API response")
    return value


def ancestor(root: Path, old: str, current: str) -> None:
    require(isinstance(old, str) and bool(COMMIT.fullmatch(old)), "invalid artifact source")
    require(subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", old,
                            current], capture_output=True).returncode == 0,
            "artifact source is not main ancestry")


def trusted_run(run: dict, workflow_id: int) -> bool:
    return (run.get("workflow_id") == workflow_id and run.get("path") == WORKFLOW
            and run.get("head_repository", {}).get("id") == REPOSITORY_ID
            and run.get("head_branch") == "main"
            and run.get("event") in ("push", "workflow_run", "workflow_dispatch")
            and run.get("status") == "completed" and run.get("conclusion") == "success")


def deployed_at(run_id: int) -> datetime | None:
    steps = [step for job in api(f"actions/runs/{run_id}/jobs?filter=latest&per_page=100")["jobs"]
             for step in job.get("steps", [])]
    uploaded = any(s.get("name") == "Upload validated Pages artifact"
                   and s.get("conclusion") == "success" for s in steps)
    deployed = [s for s in steps if s.get("name") == "Deploy GitHub Pages"
                and s.get("conclusion") == "success" and s.get("completed_at")]
    require(len(deployed) <= 1, "ambiguous Pages deployment")
    if not uploaded or not deployed:
        return None
    return datetime.fromisoformat(deployed[0]["completed_at"].replace("Z", "+00:00"))


def artifact_for(run: dict) -> dict:
    artifacts = api(f"actions/runs/{run['id']}/artifacts?per_page=100")
    require(artifacts.get("total_count", 0) <= 100, "ambiguous artifact pagination")
    matches = [a for a in artifacts["artifacts"] if a.get("name") == "github-pages"]
    require(len(matches) == 1 and matches[0].get("expired") is False,
            "last deployed Pages artifact missing or expired; history cannot be reset")
    item = matches[0]
    require(type(item.get("id")) is int and item["id"] > 0
            and item.get("workflow_run", {}).get("head_sha") == run["head_sha"],
            "artifact does not belong to selected source")
    return {"runId": run["id"], "artifactId": item["id"], "runHead": run["head_sha"]}


def select_history(root: Path, source: str, current_id: int, rollback_id: int = 0) -> dict:
    current = api(f"actions/runs/{current_id}")
    require(current.get("repository", {}).get("id") == REPOSITORY_ID
            and current.get("path") == WORKFLOW and current.get("head_branch") == "main"
            and type(current.get("workflow_id")) is int, "not the trusted Pages publisher")
    if rollback_id:
        require(os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
                and rollback_id != current_id, "rollback requires explicit manual dispatch")
    candidates = []
    for page in range(1, 11):
        result = api(f"actions/workflows/{current['workflow_id']}/runs"
                     f"?branch=main&status=success&per_page=100&page={page}")
        runs = result["workflow_runs"]
        for run in runs:
            if run.get("id") == current_id or not trusted_run(run, current["workflow_id"]):
                continue
            ancestor(root, run["head_sha"], source)
            # Pre-migration workflows cannot have versioned releases to preserve.
            workflow = subprocess.run(["git", "-C", str(root), "show",
                                       f"{run['head_sha']}:{WORKFLOW}"],
                                      capture_output=True, text=True)
            require(workflow.returncode == 0, "missing historical workflow source")
            if "Upload validated Pages artifact" not in workflow.stdout:
                continue
            completed = deployed_at(run["id"])
            if completed is not None:
                candidates.append((completed, run))
        if len(runs) < 100:
            break
    else:
        raise RuntimeError("Pages history exceeds reviewable API window; refusing bootstrap")
    latest = max(candidates, key=lambda pair: pair[0])[1] if candidates else None
    target = None
    if rollback_id:
        target = api(f"actions/runs/{rollback_id}")
        require(trusted_run(target, current["workflow_id"])
                and deployed_at(rollback_id) is not None, "untrusted rollback run")
        ancestor(root, target["head_sha"], source)
        require(latest is not None, "rollback requires published version history")
    return {"latest": artifact_for(latest) if latest else None,
            "target": artifact_for(target) if target else None}


def unpack(download: Path, output: Path, root: Path, source: str) -> dict:
    files = list(download.iterdir())
    require(len(files) == 1 and files[0].is_file()
            and files[0].name in ("artifact.tar", "artifact.tar.gz"), "unexpected Pages archive")
    require(not output.exists(), "artifact destination already exists")
    with tarfile.open(files[0], "r:*") as archive:
        members = archive.getmembers()
        require(len(members) <= 200000 and sum(m.size for m in members) <= MAX_BYTES,
                "Pages archive exceeds supported size")
        names = set()
        for item in members:
            path = PurePosixPath(item.name)
            require(not path.is_absolute() and ".." not in path.parts
                    and "\\" not in item.name and (item.isfile() or item.isdir()),
                    "unsafe Pages archive member")
            normalized = str(path)
            require(normalized not in names, "duplicate Pages archive member")
            names.add(normalized)
        output.mkdir(parents=True)
        archive.extractall(output, members=members, filter="data")
    publication = read_json(output / PUBLICATION)
    verify_release(output, publication["releaseId"])
    ancestor(root, publication["sourceCommit"], source)
    return publication


def recover(latest: Path, target: Path, output: Path, run_id: int) -> dict:
    latest_meta, target_meta = read_json(latest / PUBLICATION), read_json(target / PUBLICATION)
    verify_release(latest, latest_meta["releaseId"])
    verify_release(target, target_meta["releaseId"])
    require(not output.exists(), "recovery output already exists")
    for name in ("assets", "data", "config"):
        require(tree_hash(latest / name) == tree_hash(target / name),
                "rollback legacy root does not match")
    shutil.copytree(latest, output)
    for directory in (target / "releases").iterdir():
        destination = output / "releases" / directory.name
        if destination.exists():
            require(tree_hash(destination) == tree_hash(directory), "immutable release collision")
        else:
            shutil.copytree(directory, destination)
    shutil.copy2(target / "index.html", output / "index.html")
    publication = seal_publication(output, target_meta["releaseId"], "rollback", run_id)
    require(sum(p.stat().st_size for p in output.rglob("*") if p.is_file()) <= MAX_BYTES,
            "recovered Pages site exceeds supported size")
    verify_release(output, publication["releaseId"])
    return publication


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("select", "prepare"))
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--selection", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    require(subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                    text=True).strip() == args.source_commit, "main is not pinned")
    validate_event(root, os.environ["GITHUB_EVENT_NAME"],
                   read_json(Path(os.environ["GITHUB_EVENT_PATH"])), args.source_commit)
    temporary = Path(os.environ["RUNNER_TEMP"])
    if args.command == "select":
        rollback = os.environ.get("ROLLBACK_RUN_ID", "").strip()
        require(not rollback or bool(re.fullmatch(r"[1-9][0-9]*", rollback)), "invalid rollback run ID")
        result = select_history(root, args.source_commit, int(os.environ["GITHUB_RUN_ID"]),
                                int(rollback or 0))
        args.selection.write_text(json.dumps(result), encoding="utf-8")
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            for key in ("latest", "target"):
                for field in ("runId", "artifactId"):
                    print(f"{key}_{field}={(result[key] or {}).get(field, '')}", file=output)
    else:
        result = read_json(args.selection)
        for key in ("latest", "target"):
            if result[key] is not None:
                unpack(temporary / f"download-{key}", temporary / f"site-{key}",
                       root, args.source_commit)
        if result["target"] is not None:
            recover(temporary / "site-latest", temporary / "site-target",
                    root / "_release_site", result["target"]["runId"])


if __name__ == "__main__":
    main()
