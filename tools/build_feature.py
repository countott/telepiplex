#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from email.parser import Parser
from pathlib import Path

import yaml

for _root in (Path(__file__).resolve().parents[1], Path("/")):
    if (_root / "app/core/plugin_artifact.py").is_file():
        if str(_root) in sys.path:
            sys.path.remove(str(_root))
        sys.path.insert(0, str(_root))
        break

from app.core.plugin_artifact import build_tpx


class FeatureBuildError(RuntimeError):
    pass


_FORBIDDEN_ROOT_IMPORTS = {"app", "init", "telegram"}
_DISTRIBUTION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def _validate_distribution_name(name: str):
    normalized = name.casefold().replace("_", "-").replace(".", "-")
    if normalized.startswith("telepiplex-") and normalized != "telepiplex-plugin-sdk":
        raise FeatureBuildError(
            f"forbidden Feature distribution dependency: {normalized}"
        )


def validate_feature_requirements(source: str):
    for raw in str(source or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if (
            line.startswith("-")
            or "://" in line
            or " @ " in line
            or "/" in line
            or "\\" in line
            or line.casefold().endswith((".whl", ".zip", ".tar.gz", ".tgz"))
        ):
            raise FeatureBuildError("Feature requirements must use named distributions")
        match = _DISTRIBUTION_NAME.match(line)
        if match is None:
            raise FeatureBuildError("Feature requirement has no distribution name")
        _validate_distribution_name(match.group(0))


def _wheel_metadata(path: Path):
    try:
        with zipfile.ZipFile(path) as wheel:
            members = [
                name
                for name in wheel.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(members) != 1:
                raise FeatureBuildError("wheel must contain exactly one METADATA member")
            return Parser().parsestr(wheel.read(members[0]).decode("utf-8"))
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise FeatureBuildError("wheel metadata cannot be read") from exc


def validate_plugin_wheel(path: Path):
    metadata = _wheel_metadata(path)
    for requirement in metadata.get_all("Requires-Dist", []):
        match = _DISTRIBUTION_NAME.match(requirement.strip())
        if match is None:
            raise FeatureBuildError("plugin wheel has an invalid Requires-Dist")
        _validate_distribution_name(match.group(0))


def validate_wheelhouse(path: Path):
    for wheel in sorted(path.glob("*.whl")):
        name = str(_wheel_metadata(wheel).get("Name") or "").strip()
        if not name:
            raise FeatureBuildError("wheel metadata is missing Name")
        _validate_distribution_name(name)


def validate_feature_imports(source_dir: Path):
    source_dir = Path(source_dir)
    own_packages = {
        path.name
        for path in (source_dir / "src").iterdir()
        if path.is_dir() and path.name.startswith("telepiplex_")
    }
    for path in sorted((source_dir / "src").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            raise FeatureBuildError(f"cannot parse Feature source: {path}: {exc}") from exc
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".", 1)[0] in _FORBIDDEN_ROOT_IMPORTS:
                    raise FeatureBuildError(
                        f"forbidden cross-runtime import in {path}: {name}"
                    )
                root = name.split(".", 1)[0]
                if (
                    root.startswith("telepiplex_")
                    and root != "telepiplex_plugin_sdk"
                    and root not in own_packages
                ):
                    raise FeatureBuildError(
                        f"forbidden Feature-to-Feature import in {path}: {name}"
                    )


def build_feature_artifact(
    source_dir: Path,
    output: Path,
    *,
    sdk_source: Path,
    repository: str,
    branch: str,
    commit: str,
    python_executable: str | Path = sys.executable,
) -> Path:
    source_dir = Path(source_dir).resolve()
    output = Path(output).resolve()
    sdk_source = Path(sdk_source).resolve()
    for required in (
        source_dir / "pyproject.toml",
        source_dir / "manifest.yaml",
        source_dir / "config.schema.json",
        source_dir / "config.default.yaml",
        sdk_source / "pyproject.toml",
    ):
        if not required.is_file():
            raise FeatureBuildError(f"required Feature build input is missing: {required}")
    validate_feature_imports(source_dir)

    with tempfile.TemporaryDirectory(prefix="telepiplex-feature-build-") as temp_name:
        temp = Path(temp_name)
        plugin_wheels = temp / "plugin-wheels"
        sdk_wheels = temp / "sdk-wheels"
        package = temp / "package"
        wheelhouse = package / "wheelhouse"
        plugin_wheels.mkdir()
        sdk_wheels.mkdir()
        wheelhouse.mkdir(parents=True)

        _run_wheel(python_executable, source_dir, plugin_wheels)
        _run_wheel(python_executable, sdk_source, sdk_wheels)
        plugin_candidates = sorted(plugin_wheels.glob("*.whl"))
        sdk_candidates = sorted(sdk_wheels.glob("*.whl"))
        if len(plugin_candidates) != 1 or len(sdk_candidates) != 1:
            raise FeatureBuildError("Feature and SDK builds must each produce exactly one wheel")
        validate_plugin_wheel(plugin_candidates[0])
        shutil.copy2(plugin_candidates[0], package / "plugin.whl")
        shutil.copy2(sdk_candidates[0], wheelhouse / sdk_candidates[0].name)

        requirements = source_dir / "requirements-feature.txt"
        requirement_source = requirements.read_text(encoding="utf-8") if requirements.is_file() else ""
        validate_feature_requirements(requirement_source)
        if requirements.is_file() and any(
            line.strip() and not line.lstrip().startswith("#")
            for line in requirement_source.splitlines()
        ):
            _run([
                str(python_executable),
                "-m",
                "pip",
                "wheel",
                "--wheel-dir",
                str(wheelhouse),
                "-r",
                str(requirements),
            ], cwd=source_dir)

        try:
            manifest = yaml.safe_load((source_dir / "manifest.yaml").read_text(encoding="utf-8"))
            manifest["source"] = {
                "repository": str(repository),
                "branch": str(branch),
                "commit": str(commit).lower(),
            }
        except (OSError, TypeError, yaml.YAMLError) as exc:
            raise FeatureBuildError("Feature manifest cannot be updated") from exc
        (package / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )
        shutil.copy2(source_dir / "config.schema.json", package / "config.schema.json")
        shutil.copy2(source_dir / "config.default.yaml", package / "config.default.yaml")
        output.parent.mkdir(parents=True, exist_ok=True)
        validate_wheelhouse(wheelhouse)
        return build_tpx(package, output)


def _run_wheel(python_executable, source: Path, output: Path):
    _run([
        str(python_executable),
        "-m",
        "pip",
        "wheel",
        "--no-deps",
        "--no-build-isolation",
        "--wheel-dir",
        str(output),
        str(source),
    ], cwd=source)


def _run(argv: list[str], *, cwd: Path) -> str:
    result = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout)[-2000:]
        raise FeatureBuildError(f"wheel build failed: {detail}")
    return result.stdout


def _git(source_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_dir), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise FeatureBuildError(f"git metadata unavailable: {(result.stderr or '').strip()}")
    return result.stdout.strip()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build an immutable .tpx directly from a clean Feature source branch."
    )
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--sdk", type=Path, default=Path(__file__).resolve().parents[1] / "sdk")
    args = parser.parse_args(argv)
    source = args.source_dir.resolve()
    if _git(source, "status", "--porcelain", "--untracked-files=all"):
        raise SystemExit("Feature source worktree is dirty; commit before building")
    repository = _git(source, "remote", "get-url", "origin")
    branch = _git(source, "branch", "--show-current")
    commit = _git(source, "rev-parse", "HEAD")
    path = build_feature_artifact(
        source,
        args.output,
        sdk_source=args.sdk,
        repository=repository,
        branch=branch,
        commit=commit,
    )
    print(path)


if __name__ == "__main__":
    main()
