"""Suite pack packaging and source resolution tests (change add-suite-packaging-and-hygiene, tasks 1.x / 2.x / 5.2).

Covers: manifest build/verify roundtrip (dir, .tar.gz, .zip), tamper rejection
naming the file, missing manifest rejection, nested archive layout rejection,
pack-asset reference escape rejection, unified source resolution (single file /
directory / archive / file:// git URL offline clone), git-missing and
clone-failure error text, temp-dir cleanup, and repo-vs-package starter pack
equality (drift guard).
"""

import json
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

from agent_eval.core.packaging import (
    MANIFEST_NAME,
    SUITE_FILE_NAME,
    PackError,
    build_manifest,
    extract_archive,
    resolve_pack_asset,
    resolve_source,
    resolved_source,
    verify_pack,
    write_manifest,
)

SUITE_YAML = """
name: pack-suite
description: suite for pack tests
version: 1.2.3
tasks:
  - id: t1
    prompt: hello
    graders:
      - type: code
        name: code_based
        config:
          checks:
            - type: contains
              value: "Mock response"
              target: transcript
"""


def _make_pack(tmp_path: Path, *, with_asset: bool = True) -> Path:
    root = tmp_path / "mypack"
    root.mkdir()
    (root / SUITE_FILE_NAME).write_text(SUITE_YAML, encoding="utf-8")
    if with_asset:
        assets = root / "assets"
        assets.mkdir()
        (assets / "data.txt").write_text("asset-bytes\n", encoding="utf-8")
    write_manifest(root, pack_name="mypack")
    return root


def _tar_pack(tmp_path: Path, root: Path, name="pack.tar.gz", subdir: str | None = None) -> Path:
    archive = tmp_path / name
    with tarfile.open(archive, "w:gz") as tf:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                arcname = f"{subdir}/{p.relative_to(root).as_posix()}" if subdir else p.relative_to(root).as_posix()
                tf.add(p, arcname=arcname)
    return archive


def _zip_pack(tmp_path: Path, root: Path, name="pack.zip") -> Path:
    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w") as zf:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(root).as_posix())
    return archive


# ─── Manifest 构建/校验 (task 1.1) ───────────────────────────────────────────


class TestManifest:
    def test_build_manifest_lists_every_file_with_sha256(self, tmp_path):
        root = _make_pack(tmp_path)
        manifest = build_manifest(root, pack_name="mypack")
        assert manifest["pack_name"] == "mypack"
        assert manifest["suite_version"] == "1.2.3"
        assert set(manifest["files"]) == {f"{SUITE_FILE_NAME}", "assets/data.txt"}
        assert all(len(v) == 64 for v in manifest["files"].values())

    def test_verify_pack_roundtrip_accepts_intact_pack(self, tmp_path):
        root = _make_pack(tmp_path)
        manifest = verify_pack(root)
        assert manifest["pack_name"] == "mypack"

    def test_tampered_file_rejected_and_named(self, tmp_path):
        root = _make_pack(tmp_path)
        (root / "assets" / "data.txt").write_text("tampered\n", encoding="utf-8")
        with pytest.raises(PackError) as excinfo:
            verify_pack(root)
        message = str(excinfo.value)
        assert "assets/data.txt" in message
        assert "完整性校验失败" in message

    def test_tampered_suite_yaml_rejected(self, tmp_path):
        root = _make_pack(tmp_path)
        (root / SUITE_FILE_NAME).write_text(SUITE_YAML + "# changed\n", encoding="utf-8")
        with pytest.raises(PackError, match=SUITE_FILE_NAME):
            verify_pack(root)

    def test_missing_manifest_rejected_with_reason(self, tmp_path):
        root = _make_pack(tmp_path)
        (root / MANIFEST_NAME).unlink()
        with pytest.raises(PackError, match="缺少 manifest.json"):
            verify_pack(root)
        # 同一目录直指 suite.yaml 仍按单文件加载 (不经过 pack 校验)
        resolved = resolve_source(str(root / SUITE_FILE_NAME))
        assert resolved.pack_root is None

    def test_invalid_manifest_json_rejected(self, tmp_path):
        root = _make_pack(tmp_path)
        (root / MANIFEST_NAME).write_text("{not json", encoding="utf-8")
        with pytest.raises(PackError, match="非法"):
            verify_pack(root)

    def test_manifest_missing_required_field_rejected(self, tmp_path):
        root = _make_pack(tmp_path)
        manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
        del manifest["pack_name"]
        (root / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(PackError, match="pack_name"):
            verify_pack(root)

    def test_unlisted_extra_file_rejected(self, tmp_path):
        root = _make_pack(tmp_path)
        (root / "sneaky.txt").write_text("unlisted", encoding="utf-8")
        with pytest.raises(PackError, match="sneaky.txt"):
            verify_pack(root)

    def test_directory_without_suite_yaml_rejected(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(PackError, match=SUITE_FILE_NAME):
            resolve_source(str(empty))


# ─── 三种形态往返 (task 1.1 / 1.3) ───────────────────────────────────────────


class TestArchiveRoundtrip:
    def test_tar_gz_roundtrip_resolves_identical_suite(self, tmp_path):
        root = _make_pack(tmp_path)
        from agent_eval.core.suite import load_suite

        direct = load_suite(root / SUITE_FILE_NAME)
        archive = _tar_pack(tmp_path, root)
        with resolved_source(str(archive)) as resolved:
            assert resolved.pack_name == "mypack"
            from_archive = load_suite(resolved.suite_path)
        assert from_archive.model_dump() == direct.model_dump()

    def test_zip_roundtrip_resolves_identical_suite(self, tmp_path):
        root = _make_pack(tmp_path)
        from agent_eval.core.suite import load_suite

        direct = load_suite(root / SUITE_FILE_NAME)
        archive = _zip_pack(tmp_path, root)
        with resolved_source(str(archive)) as resolved:
            from_archive = load_suite(resolved.suite_path)
        assert from_archive.model_dump() == direct.model_dump()

    def test_nested_pack_root_rejected_with_actual_layout(self, tmp_path):
        root = _make_pack(tmp_path)
        archive = _tar_pack(tmp_path, root, name="nested.tar.gz", subdir="mypack")
        with pytest.raises(PackError) as excinfo:
            resolve_source(str(archive))
        message = str(excinfo.value)
        assert "顶层布局" in message
        assert "mypack/" in message  # 给出实际布局

    def test_zip_nested_pack_root_rejected(self, tmp_path):
        root = _make_pack(tmp_path)
        archive = tmp_path / "nested.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    zf.write(p, f"inner/{p.relative_to(root).as_posix()}")
        with pytest.raises(PackError, match="顶层布局"):
            resolve_source(str(archive))

    def test_tamper_inside_archive_rejected_and_named(self, tmp_path):
        root = _make_pack(tmp_path)
        (root / "assets" / "data.txt").write_text("changed\n", encoding="utf-8")
        archive = _tar_pack(tmp_path, root)
        with pytest.raises(PackError) as excinfo:
            resolve_source(str(archive))
        assert "assets/data.txt" in str(excinfo.value)

    def test_unsupported_archive_suffix_treated_as_single_file(self, tmp_path):
        # .tar (无 gz) 不在 pack 压缩包形态内: 走单文件路径, 由 load_suite 报 YAML 错
        archive = tmp_path / "pack.tar"
        archive.write_bytes(b"not a suite")
        resolved = resolve_source(str(archive))
        assert resolved.pack_root is None
        assert resolved.suite_path == archive

    def test_extract_archive_rejects_unsafe_members(self, tmp_path):
        archive = tmp_path / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../escape.txt", "nope")
        with pytest.raises(PackError, match="不安全路径"):
            extract_archive(archive)


# ─── Pack 内资产引用 (task 1.2) ──────────────────────────────────────────────


class TestPackAssetReferences:
    def test_relative_reference_resolves_within_pack_root(self, tmp_path):
        root = _make_pack(tmp_path)
        target = resolve_pack_asset(root, "assets/data.txt")
        assert target == (root / "assets" / "data.txt").resolve()

    def test_escape_via_dotdot_rejected_and_named(self, tmp_path):
        root = _make_pack(tmp_path)
        with pytest.raises(PackError) as excinfo:
            resolve_pack_asset(root, "../outside.txt")
        assert "../outside.txt" in str(excinfo.value)
        assert "逃出 pack 根" in str(excinfo.value)

    def test_absolute_reference_rejected_and_named(self, tmp_path):
        root = _make_pack(tmp_path)
        with pytest.raises(PackError) as excinfo:
            resolve_pack_asset(root, str(tmp_path / "outside.txt"))
        assert "outside.txt" in str(excinfo.value)

    def test_resolved_source_enforces_pack_boundary(self, tmp_path):
        root = _make_pack(tmp_path)
        with resolved_source(str(root)) as source:
            assert source.resolve_asset("assets/data.txt").is_file()
            with pytest.raises(PackError, match="../outside"):
                source.resolve_asset("../outside.txt")


# ─── 来源解析 (task 2.1 / 2.2 / 2.3) ─────────────────────────────────────────


class TestSourceResolution:
    def test_single_file_bypasses_pack_verification(self, tmp_path):
        # 无 manifest 的目录里的 suite.yaml 直指文件: 现状路径原样保留
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / SUITE_FILE_NAME).write_text(SUITE_YAML, encoding="utf-8")
        resolved = resolve_source(str(plain / SUITE_FILE_NAME))
        assert resolved.pack_root is None
        assert resolved.manifest is None

    def test_missing_source_rejected(self, tmp_path):
        with pytest.raises(PackError, match="来源不存在"):
            resolve_source(str(tmp_path / "nope.yaml"))

    def test_git_url_schemes_recognized(self, tmp_path, monkeypatch):
        # 只验证 scheme 路由进 git 分支 (git 缺失报错即证明没走本地分支)
        monkeypatch.setattr(
            "agent_eval.core.packaging.shutil.which", lambda _: None
        )
        for url in (
            "https://example.com/org/suite.git",
            "http://example.com/org/suite.git",
            "git://example.com/org/suite",
            "ssh://git@example.com/org/suite",
            "file:///tmp/somewhere",
        ):
            with pytest.raises(PackError, match="需要 git"):
                resolve_source(url)

    def test_git_missing_gives_clear_error_without_traceback_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "agent_eval.core.packaging.shutil.which", lambda _: None
        )
        with pytest.raises(PackError) as excinfo:
            resolve_source("https://example.com/org/suite.git")
        message = str(excinfo.value)
        assert "该来源形态 (git URL) 需要 git" in message
        assert "git clone --depth 1 https://example.com/org/suite.git" in message

    def test_git_clone_failure_reports_url(self, tmp_path):
        # file:// 指向不存在的路径: git 在 PATH 里, clone 必然失败
        with pytest.raises(PackError) as excinfo:
            resolve_source((tmp_path / "missing-repo").as_uri())
        message = str(excinfo.value)
        assert "git clone 失败" in message
        assert (tmp_path / "missing-repo").as_uri() in message

    def test_git_clone_without_suite_reports_url(self, tmp_path):
        repo = tmp_path / "empty-repo"
        repo.mkdir()
        (repo / "README.md").write_text("no suite here", encoding="utf-8")
        _git(repo, ["init"])
        _git(repo, ["add", "."])
        _git(repo, ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"])
        with pytest.raises(PackError) as excinfo:
            resolve_source(repo.as_uri())
        assert "找不到 suite.yaml" in str(excinfo.value)
        assert repo.as_uri() in str(excinfo.value)

    def test_file_url_clone_then_load_offline(self, tmp_path):
        """file:// git URL 的离线 clone→加载 e2e (task 2.3)."""
        from agent_eval.core.suite import load_suite

        repo = tmp_path / "suite-repo"
        repo.mkdir()
        (repo / SUITE_FILE_NAME).write_text(SUITE_YAML, encoding="utf-8")
        _git(repo, ["init"])
        _git(repo, ["add", "."])
        _git(repo, ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"])

        with resolved_source(repo.as_uri()) as resolved:
            suite = load_suite(resolved.suite_path)
            assert suite.name == "pack-suite"
            assert resolved.pack_root is None  # 无 manifest 的 clone 按单文件套件加载
            cloned_dir = resolved.suite_path.parent
            assert "aeval-clone-" in str(cloned_dir)  # clone 发生在临时区
        assert not cloned_dir.exists()  # 临时目录用后即清

    def test_file_url_clone_of_pack_root_verifies_manifest(self, tmp_path):
        from agent_eval.core.suite import load_suite

        repo = tmp_path / "pack-repo"
        repo.mkdir()
        (repo / SUITE_FILE_NAME).write_text(SUITE_YAML, encoding="utf-8")
        write_manifest(repo, pack_name="repopack")
        _git(repo, ["init"])
        _git(repo, ["add", "."])
        _git(repo, ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"])

        with resolved_source(repo.as_uri()) as resolved:
            assert resolved.pack_name == "repopack"
            load_suite(resolved.suite_path)

    def test_temp_dir_cleanup_for_archive_source(self, tmp_path):
        root = _make_pack(tmp_path)
        archive = _tar_pack(tmp_path, root)
        resolved = resolve_source(str(archive))
        extracted_dir = resolved.suite_path.parent
        assert extracted_dir.exists()
        resolved.cleanup()
        assert not extracted_dir.exists()


def _git(cwd: Path, args: list[str]) -> None:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# ─── 内置 starter pack 同源 (task 5.2) ───────────────────────────────────────


class TestStarterPackParity:
    def test_repo_and_package_copies_are_identical(self):
        import agent_eval.packs as packs_pkg

        repo_pack = Path(__file__).resolve().parents[3] / "packs" / "starter"
        package_pack = Path(packs_pkg.__file__).resolve().parent / "starter"
        assert (repo_pack / SUITE_FILE_NAME).is_file(), "仓库 packs/starter/ 缺失"
        assert (package_pack / SUITE_FILE_NAME).is_file(), "包内 starter pack 缺失"

        repo_files = {p.relative_to(repo_pack).as_posix() for p in repo_pack.rglob("*") if p.is_file()}
        package_files = {
            p.relative_to(package_pack).as_posix() for p in package_pack.rglob("*") if p.is_file()
        }
        assert repo_files == package_files, f"两侧文件清单不同: {repo_files ^ package_files}"
        for rel in sorted(repo_files):
            assert (repo_pack / rel).read_bytes() == (package_pack / rel).read_bytes(), (
                f"仓库与包内的 {rel} 内容漂移 (两份拷贝必须逐字节一致)"
            )

    def test_builtin_pack_passes_verification_and_loads(self):
        from agent_eval.core.packaging import builtin_pack_dir

        pack_dir = builtin_pack_dir("starter")
        manifest = verify_pack(pack_dir)
        assert manifest["pack_name"] == "starter"
        with resolved_source(str(pack_dir)) as resolved:
            from agent_eval.core.suite import load_suite

            suite = load_suite(resolved.suite_path)
        assert suite.name == "aeval-starter"
        assert yaml.safe_load((pack_dir / SUITE_FILE_NAME).read_text(encoding="utf-8"))["version"] == "1.0.0"


# ─── CLI e2e: 来源四形态 (task 7.2) ──────────────────────────────────────────


class TestCliSourceForms:
    """单文件形态由 test_cli.py 既有用例覆盖; 这里补 pack 压缩包与 file:// git URL。"""

    def test_run_tar_gz_source_completes(self, tmp_path):
        from typer.testing import CliRunner

        from agent_eval._cli_app import app

        root = _make_pack(tmp_path)
        archive = _tar_pack(tmp_path, root, name="suite-pack.tar.gz")
        result = CliRunner().invoke(
            app, ["run", str(archive), "--db", str(tmp_path / "aeval.db")]
        )
        assert result.exit_code == 0, result.output
        assert "Source: pack 'mypack'" in result.output
        assert "Results Summary" in result.output

    def test_run_file_url_git_source_completes(self, tmp_path):
        import subprocess as sp

        from typer.testing import CliRunner

        from agent_eval._cli_app import app

        repo = tmp_path / "suite-repo"
        repo.mkdir()
        (repo / SUITE_FILE_NAME).write_text(SUITE_YAML, encoding="utf-8")
        for args in (["init"], ["add", "."]):
            sp.run(["git", *args], cwd=repo, capture_output=True, check=True)
        sp.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"],
            cwd=repo, capture_output=True, check=True,
        )
        result = CliRunner().invoke(
            app, ["run", repo.as_uri(), "--db", str(tmp_path / "aeval.db")]
        )
        assert result.exit_code == 0, result.output
        assert "Results Summary" in result.output
        assert "Failures" not in result.output
