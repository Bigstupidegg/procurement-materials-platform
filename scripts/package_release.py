"""Validate and package one commit-pinned, versioned Pages release.

This module never collects market data or changes tracked repository files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

IDS = ("zinc", "copper", "aluminium", "nickel", "iron_ore", "crude_oil", "natural_gas")
OFFSETS = {"oneMonthPercent": 1, "threeMonthPercent": 3,
           "sixMonthPercent": 6, "twelveMonthPercent": 12}
SHA = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class ReleaseBlocked(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseBlocked(message)


def read_json(path: Path) -> dict:
    require(path.is_file(), f"missing {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"invalid {path.name}")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def month_number(value: object) -> int:
    require(isinstance(value, str) and bool(MONTH.fullmatch(value)), "invalid month")
    return int(value[:4]) * 12 + int(value[5:]) - 1


def finite_number(value: object) -> float:
    require(type(value) in (int, float) and math.isfinite(value), "invalid percentage or price")
    return float(value)


def validate_sources(root: Path) -> dict[str, str]:
    data, config, assets = root / "data", root / "config", root / "assets"
    wb = read_json(data / "world-bank.json")
    signals = read_json(data / "signals.json")
    status = read_json(data / "status.json")
    comparison = read_json(data / "comparison.json")
    materials = json.loads((config / "materials.json").read_text(encoding="utf-8"))
    require(isinstance(materials, list) and [m.get("id") for m in materials] == list(IDS),
            "material configuration mismatch")
    require(wb.get("source") == "WORLD_BANK_PINK_SHEET" and wb.get("isRealData") is True,
            "untrusted World Bank source")
    require(comparison.get("primarySource") == "WORLD_BANK_PINK_SHEET"
            and comparison.get("isRealData") is True, "untrusted comparison source")
    require(signals.get("primarySource") == "WORLD_BANK_PINK_SHEET"
            and signals.get("isRealData") is True
            and signals.get("signalPolicy", {}).get("automaticSupplierPriceDecision") is False,
            "untrusted signal policy")
    require(status.get("dataMode") == "WORLD_BANK_PRIMARY"
            and status.get("isStale") is False
            and status.get("worldBank", {}).get("status") == "SUCCESS",
            "failed or stale source status")
    dataset = wb.get("dataset") or {}
    source_sha = (dataset.get("download") or {}).get("sha256")
    source_updated = dataset.get("workbookUpdatedOn")
    latest = dataset.get("latestPeriod")
    require(isinstance(source_sha, str) and bool(SHA.fullmatch(source_sha.lower())),
            "missing World Bank source hash")
    require(isinstance(source_updated, str)
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", source_updated) is not None,
            "missing source update date")
    require(date.fromisoformat(source_updated) <= date.today(), "future source update date")
    require(month_number(latest) <= date.today().year * 12 + date.today().month - 1,
            "future source month")
    require(signals.get("latestPeriod") == status["worldBank"].get("latestPeriod") == latest,
            "latest source month mismatch")
    require(signals.get("sourceSha256") == status["worldBank"].get("sourceSha256") == source_sha,
            "source hash mismatch")
    require(signals.get("sourceUpdatedOn") == status["worldBank"].get("sourceUpdatedOn") == source_updated,
            "source update date mismatch")
    require(set((wb.get("series") or {})) == set(IDS)
            and set((signals.get("materials") or {})) == set(IDS),
            "material IDs mismatch")
    latest_index = month_number(latest)
    for material in materials:
        mid = material["id"]
        series = wb["series"][mid]
        signal = signals["materials"][mid]
        obs = series.get("observations")
        require(isinstance(obs, list) and len(obs) >= 13, f"{mid}: insufficient observations")
        periods = [item.get("period") for item in obs]
        indexes = [month_number(period) for period in periods]
        require(all(b == a + 1 for a, b in zip(indexes, indexes[1:])),
                f"{mid}: missing, duplicate or unordered month")
        require(indexes[-1] == latest_index and series.get("latestPeriod") == latest
                and signal.get("latestPeriod") == latest and signal.get("id") == mid,
                f"{mid}: latest month mismatch")
        require(series.get("currency") == signal.get("currency") == material.get("currency") == "USD"
                and series.get("displayUnit") == signal.get("displayUnit") == material.get("unit")
                and isinstance(series.get("sourceUnit"), str)
                and series["sourceUnit"] == signal.get("sourceUnit") and bool(series["sourceUnit"]),
                f"{mid}: currency or unit mismatch")
        current = finite_number(obs[-1].get("value"))
        signal_latest = finite_number(signal.get("latestValue"))
        require(current > 0 and signal_latest > 0, f"{mid}: invalid latest price")
        # build_signals.round_number publishes latestValue rounded to six decimals.
        require(signal_latest == round(current, 6), f"{mid}: latest price mismatch")
        for key, offset in OFFSETS.items():
            base = signal.get("changeBasePeriods", {}).get(key)
            require(month_number(base) == latest_index - offset,
                    f"{mid}: wrong {key} anchor")
            actual = finite_number(signal.get("changes", {}).get(key))
            anchor = finite_number(obs[-1-offset].get("value"))
            require(anchor > 0 and current > 0, f"{mid}: invalid source price")
            expected = round((current - anchor) / anchor * 100, 6)
            require(abs(actual - expected) <= 0.0000011, f"{mid}: {key} formula mismatch")
        require(signal.get("negotiationSignal", {}).get("triggerWindow")
                in ("3M", "3M+6M", "6M", "6M+12M_AVERAGE", "NONE"),
                f"{mid}: missing trigger window")
    frontend = assets / "trend-signals.js"
    source = frontend.read_text(encoding="utf-8")
    require(all(token in source for token in
                ("sourceSha256", "sourceUpdatedOn", "changeBasePeriods", "manualRateEdited")),
            "frontend cannot enforce signal contract")
    paths = {
        "worldBank": data / "world-bank.json",
        "comparison": data / "comparison.json",
        "signals": data / "signals.json",
        "status": data / "status.json",
        "materials": config / "materials.json",
        "signalRules": config / "signal-rules.json",
        "builder": root / "scripts" / "build_signals.py",
        "frontend": frontend,
        "supplier": assets / "supplier-rationality.js",
        "siteBuilder": root / "scripts" / "prepare_site.py",
    }
    return {name: digest(path) for name, path in paths.items()}


def tree_hash(root: Path, exclude: set[str] | None = None) -> str:
    exclude = exclude or set()
    lines = []
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "symlink in release artifact")
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            if rel not in exclude:
                lines.append(rel + " " + digest(path))
    return hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()


def package_release(root: Path, site: Path, output: Path, source_commit: str,
                    legacy_site: Path) -> dict:
    require(bool(COMMIT.fullmatch(source_commit)), "invalid source commit")
    hashes = validate_sources(root)
    require(site.is_dir() and (site / "index.html").is_file(), "missing prepared site")
    for name in ("signals.json", "status.json", "world-bank.json", "comparison.json"):
        require(digest(site / "data" / name) == digest(root / "data" / name),
                f"prepared {name} changed")
    for name in ("trend-signals.js", "supplier-rationality.js"):
        require(digest(site / "assets" / name) == digest(root / "assets" / name),
                f"prepared {name} changed")
    fingerprint = hashlib.sha256(
        json.dumps({"commit": source_commit, "hashes": hashes}, sort_keys=True).encode()
    ).hexdigest()[:20]
    require(not output.exists(), "release output already exists")
    require(legacy_site.is_dir() and (legacy_site / "index.html").is_file(),
            "missing trusted legacy site")
    legacy_signals = read_json(legacy_site / "data/signals.json")
    legacy_frontend = (legacy_site / "assets/trend-signals.js").read_text(encoding="utf-8")
    require(not legacy_signals.get("sourceSha256") and "sourceSha256" not in legacy_frontend,
            "legacy root is not the accepted old contract")
    shutil.copytree(legacy_site / "assets", output / "assets")
    shutil.copytree(legacy_site / "data", output / "data")
    require((legacy_site / "config/release.json").is_file(),
            "missing trusted legacy release metadata")
    shutil.copytree(legacy_site / "config", output / "config")
    legacy_hashes = {"assets": tree_hash(output / "assets"),
                     "data": tree_hash(output / "data"),
                     "config": tree_hash(output / "config")}
    release = output / "releases" / fingerprint
    shutil.copytree(site, release)
    (release / "config").mkdir(exist_ok=True)
    shutil.copy2(root / "config" / "release.json", release / "config" / "release.json")
    index = output / "index.html"
    index.write_text(
        '<!doctype html><html lang="zh-Hant"><meta charset="utf-8">'
        '<meta name="robots" content="noindex">'
        f'<meta http-equiv="refresh" content="0;url=./releases/{fingerprint}/index.html">'
        '<title>載入已驗證版本</title>'
        f'<a href="./releases/{fingerprint}/index.html">開啟已驗證版本</a>'
        f'<script>location.replace("./releases/{fingerprint}/index.html"+location.search+location.hash)</script>'
        '</html>', encoding="utf-8"
    )
    manifest = {
        "schemaVersion": 1, "releaseId": fingerprint, "sourceCommit": source_commit,
        "projectBasePath": "/procurement-materials-platform/",
        "legacyRootCommit": "d55d156556368570ac4a5b290b5d9762019a61ca",
        "legacyRootHashes": legacy_hashes,
        "hashes": hashes,
        "artifactTreeSha256": tree_hash(output),
    }
    (release / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    verify_release(output, fingerprint)
    return manifest


def verify_release(output: Path, release_id: str) -> None:
    manifest = read_json(output / "releases" / release_id / "release-manifest.json")
    require(manifest.get("releaseId") == release_id and bool(COMMIT.fullmatch(
        str(manifest.get("sourceCommit", "")))), "release manifest identity mismatch")
    require(bool(SHA.fullmatch(str(manifest.get("artifactTreeSha256", "")))),
            "missing artifact hash")
    require(tree_hash(output, {f"releases/{release_id}/release-manifest.json"})
            == manifest["artifactTreeSha256"], "artifact hash mismatch")
    root_index = (output / "index.html").read_text(encoding="utf-8")
    require(f"./releases/{release_id}/index.html" in root_index,
            "wrong project-site release path")
    require(manifest.get("legacyRootCommit") ==
            "d55d156556368570ac4a5b290b5d9762019a61ca",
            "untrusted legacy root commit")
    for name in ("assets", "data", "config"):
        require(tree_hash(output / name) == manifest["legacyRootHashes"][name],
                f"legacy {name} hash mismatch")
    require(not read_json(output / "data/signals.json").get("sourceSha256"),
            "legacy root data unexpectedly changed")
    require("sourceSha256" not in
            (output / "assets/trend-signals.js").read_text(encoding="utf-8"),
            "legacy root frontend unexpectedly changed")
    release = output / "releases" / release_id
    for name, key in (("data/signals.json", "signals"), ("data/status.json", "status"),
                      ("assets/trend-signals.js", "frontend"),
                      ("assets/supplier-rationality.js", "supplier")):
        require(digest(release / name) == manifest["hashes"][key],
                f"release {name} hash mismatch")
    html = (release / "index.html").read_text(encoding="utf-8")
    require("./assets/trend-signals.js" in html and "./assets/app-core.js" in html,
            "versioned HTML lacks frontend")
    require("./data/signals.json" in (release / "assets/trend-signals.js").read_text(encoding="utf-8"),
            "frontend does not request version-relative signal JSON")


APPROVED_UPSTREAM = {
    "Update Procurement Trend Signals": ".github/workflows/update-signals.yml",
    "Update World Bank Data": ".github/workflows/update-world-bank.yml",
    "Update FRED Comparison Data": ".github/workflows/update-fred.yml",
}


def validate_event(root: Path, event_name: str, payload: dict, source_commit: str) -> None:
    repository = payload.get("repository") or {}
    require(repository.get("id") == 1315749314
            and repository.get("full_name") == "Bigstupidegg/procurement-materials-platform",
            "untrusted event repository")
    if event_name == "workflow_run":
        run = payload.get("workflow_run") or {}
        head_repo = run.get("head_repository") or {}
        require(run.get("name") in APPROVED_UPSTREAM
                and run.get("path") == APPROVED_UPSTREAM.get(run.get("name"))
                and run.get("conclusion") == "success"
                and run.get("head_branch") == "main"
                and head_repo.get("id") == 1315749314
                and run.get("event") in ("push", "schedule", "workflow_dispatch"),
                "failed or untrusted upstream workflow")
        upstream = run.get("head_sha")
    elif event_name == "push":
        require(payload.get("ref") == "refs/heads/main", "push is not main")
        upstream = payload.get("after")
    elif event_name == "workflow_dispatch":
        require(os.environ.get("GITHUB_REF") == "refs/heads/main",
                "manual dispatch is not on main")
        upstream = source_commit
    else:
        raise ReleaseBlocked("unsupported publication event")
    require(isinstance(upstream, str) and bool(COMMIT.fullmatch(upstream)),
            "invalid upstream commit")
    require(subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor",
                            upstream, source_commit], capture_output=True).returncode == 0,
            "stale or unrelated upstream commit")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--legacy-site", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if os.environ.get("GITHUB_ACTIONS") == "true":
        event_path = Path(os.environ["GITHUB_EVENT_PATH"])
        validate_event(root, os.environ["GITHUB_EVENT_NAME"],
                       read_json(event_path), args.source_commit)
    require(subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                    text=True).strip() == args.source_commit,
            "checkout is not pinned source commit")
    require(subprocess.run(["git", "-C", str(root), "diff", "--quiet"]).returncode == 0
            and subprocess.run(["git", "-C", str(root), "diff", "--cached", "--quiet"]).returncode == 0,
            "tracked source changed during build")
    manifest = package_release(root, root / "_site", root / "_release_site",
                               args.source_commit, args.legacy_site.resolve())
    print(json.dumps({"releaseId": manifest["releaseId"],
                      "sourceCommit": manifest["sourceCommit"],
                      "artifactTreeSha256": manifest["artifactTreeSha256"]}, sort_keys=True))


if __name__ == "__main__":
    main()
