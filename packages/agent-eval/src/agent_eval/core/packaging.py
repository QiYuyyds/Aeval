"""
Suite pack packaging and source resolution (spec: suite-distribution).

A **pack** is a distributable, verifiable unit: ``suite.yaml`` + its referenced
local assets + ``manifest.json`` (pack name, suite semver, sha256 of every
file). Any hash mismatch → refuse to load and name the file; missing/invalid
manifest → refuse with the reason. Directories and ``.tar.gz`` / ``.zip``
archives are both pack forms (stdlib ``tarfile`` / ``zipfile``, zero new
dependencies); a single ``suite.yaml`` file path bypasses pack verification
entirely (behavior identical to before packs existed).

Asset references inside a pack MUST resolve relative to the pack root via
:func:`resolve_pack_asset` — absolute paths or ``..`` escapes are rejected and
the offending reference is named (packs stay portable; nothing reaches outside
the pack root). Pack loading hands out a :class:`ResolvedSource` whose
``resolve_asset`` enforces exactly this boundary.

Source resolution (:func:`resolve_source`) is the single entry point shared by
``eval-suite run`` and ``eval-suite validate``:

- contains ``://`` (scheme ∈ http/https/ssh/git/file) → git URL: shallow clone
  into a temp directory, then locate ``suite.yaml`` or a pack root inside the
  clone (``file://`` URLs make the whole path offline-testable);
- otherwise a local path: single file (status quo), ``.tar.gz`` / ``.zip``
  (extract + verify), or directory (pack verification — a directory is a pack
  form and must carry a manifest).

Temp directories are owned by the returned :class:`ResolvedSource`; callers
must ``cleanup()`` (or use :func:`resolved_source`) after the suite has run.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

MANIFEST_NAME = "manifest.json"
SUITE_FILE_NAME = "suite.yaml"

# D2: 含 scheme 的来源按 git URL 处理 (其余一律按本地路径)
GIT_URL_SCHEMES = frozenset({"http", "https", "ssh", "git", "file"})

GIT_CLONE_TIMEOUT_SECONDS = 300

# wheel 内置 pack 的解析根 (agent_eval/packs/<name>; 与仓库 packs/<name> 同源,
# CI 测试钉住两者一致)
BUILTIN_PACKS_PACKAGE = "agent_eval.packs"


class PackError(Exception):
    """Pack 完整性/布局/来源解析失败 (错误信息点名文件或引用并给出原因)。"""


# ─── Manifest 构建 ───────────────────────────────────────────────────────────


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _suite_version_of(pack_root: Path) -> str:
    """读 suite.yaml 的 semver (缺省与 EvalSuite 模型一致为 1.0.0)。"""
    suite_file = pack_root / SUITE_FILE_NAME
    try:
        data = yaml.safe_load(suite_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise PackError(f"{SUITE_FILE_NAME} 无法解析: {e}") from e
    if not isinstance(data, dict):
        raise PackError(f"{SUITE_FILE_NAME} 顶层必须是映射 (got {type(data).__name__})")
    version = data.get("version", "1.0.0")
    if not isinstance(version, str) or not version:
        raise PackError(f"{SUITE_FILE_NAME} 的 version 必须是非空字符串 (got {version!r})")
    return version


def _pack_file_relpaths(pack_root: Path) -> list[Path]:
    """包内全部文件相对路径 (排序稳定; manifest 自身与 .git 元数据不在清单里)。"""
    files = [
        p.relative_to(pack_root)
        for p in sorted(pack_root.rglob("*"))
        if p.is_file() and p.name != MANIFEST_NAME and ".git" not in p.parts
    ]
    return files


def build_manifest(pack_root: Path, pack_name: str) -> dict[str, Any]:
    """构建 manifest: 包名 + suite semver + 逐文件 sha256 (路径用 posix 分隔)。"""
    root = Path(pack_root)
    if not (root / SUITE_FILE_NAME).is_file():
        raise PackError(f"Pack 根缺少 {SUITE_FILE_NAME}: {root}")
    return {
        "pack_name": pack_name,
        "suite_version": _suite_version_of(root),
        "files": {
            rel.as_posix(): sha256_of(root / rel) for rel in _pack_file_relpaths(root)
        },
    }


def write_manifest(pack_root: Path, pack_name: str) -> dict[str, Any]:
    """构建并把 manifest.json 写入 pack 根 (打包/再分发的唯一入口)。"""
    manifest = build_manifest(pack_root, pack_name)
    (Path(pack_root) / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


# ─── Manifest 校验 ───────────────────────────────────────────────────────────


def verify_pack(pack_root: Path) -> dict[str, Any]:
    """校验 pack 完整性, 返回通过校验的 manifest。

    拒绝路径 (均给原因并尽可能点名文件):
    - manifest 缺失 / JSON 非法 / 缺必需字段;
    - 清单列出的文件缺失或哈希不符 (点名文件);
    - 磁盘上存在清单未覆盖的文件 (点名文件);
    - manifest.suite_version 与 suite.yaml 实际版本不一致。
    """
    root = Path(pack_root)
    manifest_path = root / MANIFEST_NAME
    if not (root / SUITE_FILE_NAME).is_file():
        raise PackError(
            f"Pack 根缺少 {SUITE_FILE_NAME}: {root} "
            f"(顶层内容: {_layout_listing(root)})"
        )
    if not manifest_path.is_file():
        raise PackError(
            f"Pack 缺少 {MANIFEST_NAME} (pack root: {root}): 以 pack 形态分发的目录或"
            f"压缩包必须带 manifest (pack_name / suite_version / 逐文件 sha256)。"
            f"只想跑单个套件请直接指向 {SUITE_FILE_NAME} 文件"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise PackError(f"{MANIFEST_NAME} 非法 (无法解析): {e}") from e
    if not isinstance(manifest, dict):
        raise PackError(f"{MANIFEST_NAME} 非法: 顶层必须是 JSON 对象")
    files = manifest.get("files")
    missing = [
        key
        for key in ("pack_name", "suite_version")
        if not isinstance(manifest.get(key), str)
    ]
    if missing or not isinstance(files, dict):
        raise PackError(
            f"{MANIFEST_NAME} 非法: 缺少或类型错误的字段: "
            f"{missing + ([] if isinstance(files, dict) else ['files'])} "
            "(需要 pack_name / suite_version / files: {相对路径: sha256})"
        )

    listed: dict[str, str] = files
    for rel, expected in listed.items():
        if not isinstance(expected, str):
            raise PackError(f"{MANIFEST_NAME} 非法: 文件 {rel!r} 的哈希不是字符串")
        target = root / rel
        if not target.is_file():
            raise PackError(
                f"完整性校验失败: 清单中的文件在 pack 内不存在: '{rel}'"
            )
        actual = sha256_of(target)
        if actual != expected.lower():
            raise PackError(
                f"完整性校验失败: 文件 '{rel}' 校验和不符 "
                f"(manifest: {expected}, 实际: {actual}); 该文件在分发后被改动或损坏"
            )

    # 反向覆盖: 磁盘上存在但清单未列出的文件同样拒绝 (清单必须覆盖全部文件,
    # 否则换掉一个未列文件就能绕过校验)
    listed_posix = set(listed)
    for rel in _pack_file_relpaths(root):
        if rel.as_posix() not in listed_posix:
            raise PackError(
                f"完整性校验失败: 文件 '{rel.as_posix()}' 不在 {MANIFEST_NAME} 的清单里 "
                "(manifest 必须覆盖包内全部文件)"
            )

    actual_version = _suite_version_of(root)
    if manifest["suite_version"] != actual_version:
        raise PackError(
            f"{MANIFEST_NAME} 的 suite_version ({manifest['suite_version']}) 与 "
            f"{SUITE_FILE_NAME} 的实际版本 ({actual_version}) 不一致"
        )
    return manifest


def _layout_listing(root: Path) -> str:
    entries = sorted(p.name + ("/" if p.is_dir() else "") for p in root.iterdir())
    return ", ".join(entries) if entries else "(空目录)"


# ─── Pack 内资产引用 ─────────────────────────────────────────────────────────


def resolve_pack_asset(pack_root: Path, reference: str) -> Path:
    """把 pack 内资产引用解析到 pack 根下的绝对路径。

    资产引用一律相对 pack 根; 绝对路径或 ``..`` 逃逸即拒绝并点名该引用 ——
    绝对路径不可移植 (往往指向打包者本机), 越界引用则是 pack 完整性模型的洞。
    """
    root = Path(pack_root).resolve()
    ref = str(reference)
    candidate = Path(ref)
    if candidate.is_absolute():
        raise PackError(
            f"资产引用不是 pack 内相对路径: '{ref}' "
            "(pack 资产必须以相对 pack 根的路径引用, 保证 pack 可移植)"
        )
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PackError(
            f"资产引用解析后逃出 pack 根: '{ref}' -> {resolved} "
            f"(pack root: {root}); 该引用越界, 拒绝加载"
        ) from None
    return resolved


# ─── 压缩包 ──────────────────────────────────────────────────────────────────

_ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".zip")


def is_archive(path: Path) -> bool:
    return path.name.lower().endswith(_ARCHIVE_SUFFIXES)


def _assert_safe_members(names: list[str], dest: Path) -> None:
    for name in names:
        target = (dest / name).resolve()
        if not str(target).startswith(str(dest.resolve())):
            raise PackError(f"压缩包含不安全路径成员: '{name}' (拒绝解包)")


def extract_archive(archive_path: Path) -> tuple[Path, tempfile.TemporaryDirectory]:
    """解包到临时目录并检查顶层布局 (suite.yaml 与 manifest.json 必须在顶层)。

    嵌套 pack 根 (压缩包里先套一层目录) 刻意不支持: 简单、可预测; 错误信息
    给出实际顶层布局, 指导重新打包。
    """
    suffix = archive_path.name.lower()
    tmp = tempfile.TemporaryDirectory(prefix="aeval-pack-")
    dest = Path(tmp.name)
    try:
        if suffix.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as zf:
                _assert_safe_members(zf.namelist(), dest)
                zf.extractall(dest)
        else:
            with tarfile.open(archive_path, "r:*") as tf:
                _assert_safe_members(tf.getnames(), dest)
                try:
                    tf.extractall(dest, filter="data")
                except TypeError:  # Python < 3.11.4 无 filter 参数
                    tf.extractall(dest)

        if not (dest / SUITE_FILE_NAME).is_file() or not (dest / MANIFEST_NAME).is_file():
            listing = ", ".join(sorted(p.name + ("/" if p.is_dir() else "") for p in dest.iterdir()))
            raise PackError(
                f"压缩包顶层布局不符合 pack 结构: 顶层必须直接包含 "
                f"{SUITE_FILE_NAME} 与 {MANIFEST_NAME} (不支持嵌套 pack 根)。"
                f"实际顶层内容: {listing or '(空)'}"
            )
        return dest, tmp
    except Exception:
        tmp.cleanup()
        raise


# ─── 来源解析 ────────────────────────────────────────────────────────────────


@dataclass
class ResolvedSource:
    """解析后的套件来源 (pack 形态已在解析期完成完整性校验)。"""

    suite_path: Path
    pack_root: Path | None = None
    manifest: dict[str, Any] | None = None
    _tmp: tempfile.TemporaryDirectory | None = field(default=None, repr=False)

    @property
    def pack_name(self) -> str | None:
        return str(self.manifest["pack_name"]) if self.manifest else None

    def resolve_asset(self, reference: str) -> Path:
        """解析 pack 内资产引用 (单文件形态无 pack 边界, 原样相对 cwd)。"""
        if self.pack_root is None:
            return Path(reference)
        return resolve_pack_asset(self.pack_root, reference)

    def cleanup(self) -> None:
        """释放来源占用的临时目录 (git clone / 压缩包解包)。"""
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None


@contextmanager
def resolved_source(source: str) -> Iterator[ResolvedSource]:
    """``resolve_source`` 的上下文管理形态: 用完即清临时目录。"""
    resolved = resolve_source(source)
    try:
        yield resolved
    finally:
        resolved.cleanup()


def resolve_source(source: str) -> ResolvedSource:
    """统一来源解析 (run 与 validate 共用同一实现, spec: suite-distribution)。

    - 含 ``://`` (http/https/ssh/git/file) → git URL: 浅 clone 到临时目录后
      定位 ``suite.yaml`` (带 manifest 则按 pack 校验);
    - 本地单文件 → 现状 (不做 pack 校验);
    - 本地 ``.tar.gz`` / ``.zip`` → 解包 + pack 校验;
    - 本地目录 → pack 校验 (目录即 pack 形态, 必须带 manifest)。
    """
    source = str(source).strip()
    if _is_git_url(source):
        return _resolve_git_url(source)
    return _resolve_local(source)


def _is_git_url(source: str) -> bool:
    if "://" not in source:
        return False
    scheme = source.split("://", 1)[0].lower()
    return scheme in GIT_URL_SCHEMES


def _finalize_pack_root(
    pack_root: Path, tmp: tempfile.TemporaryDirectory | None = None
) -> ResolvedSource:
    """对 pack 根做完整性校验并定型来源 (suite.yaml 直指文件路径不经过这里)。"""
    manifest = verify_pack(pack_root)
    return ResolvedSource(
        suite_path=pack_root / SUITE_FILE_NAME,
        pack_root=pack_root,
        manifest=manifest,
        _tmp=tmp,
    )


def _resolve_local(source: str) -> ResolvedSource:
    path = Path(source)
    if not path.exists():
        raise PackError(f"来源不存在: {path}")
    if path.is_file():
        if is_archive(path):
            extracted, tmp = extract_archive(path)
            return _finalize_pack_root(extracted, tmp)
        # 单文件 YAML: 与引入 pack 之前完全同一条路径
        return ResolvedSource(suite_path=path)
    # 目录 = pack 形态 (D1: 统一语义, 免得「有没有清单」出现两套目录行为)
    return _finalize_pack_root(path)


def _locate_suite_root(clone_dir: Path, url: str) -> Path:
    """在 clone 输出里定位 suite.yaml 所在目录 (顶层优先, 其余唯一定位)。"""
    if (clone_dir / SUITE_FILE_NAME).is_file():
        return clone_dir
    matches = sorted(
        p.parent for p in clone_dir.rglob(SUITE_FILE_NAME) if ".git" not in p.parts
    )
    if not matches:
        raise PackError(
            f"git clone 成功但在仓库中找不到 {SUITE_FILE_NAME} (URL: {url}): "
            "该来源不是可运行的评测套件/pack"
        )
    if len(matches) > 1:
        candidates = ", ".join(str(m) for m in matches)
        raise PackError(
            f"仓库中存在多个 {SUITE_FILE_NAME} (URL: {url}): {candidates}; "
            "请在仓库根放置唯一套件或使用带 manifest 的 pack 根"
        )
    return matches[0]


def _resolve_git_url(url: str) -> ResolvedSource:
    git = shutil.which("git")
    command = f"git clone --depth 1 {url} <临时目录>"
    if git is None:
        raise PackError(
            "该来源形态 (git URL) 需要 git, 但 PATH 中找不到 git 可执行文件。\n"
            f"  已尝试命令: {command}\n"
            "请安装 git, 或改用本地文件/压缩包来源"
        )
    tmp = tempfile.TemporaryDirectory(prefix="aeval-clone-")
    clone_dir = Path(tmp.name)
    try:
        proc = subprocess.run(
            [git, "clone", "--depth", "1", url, str(clone_dir)],
            capture_output=True,
            text=True,
            timeout=GIT_CLONE_TIMEOUT_SECONDS,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise PackError(f"git clone 失败 (URL: {url}): {detail or 'git 无输出'}")
        suite_root = _locate_suite_root(clone_dir, url)
        if (suite_root / MANIFEST_NAME).is_file():
            manifest = verify_pack(suite_root)
            return ResolvedSource(
                suite_path=suite_root / SUITE_FILE_NAME,
                pack_root=suite_root,
                manifest=manifest,
                _tmp=tmp,
            )
        # clone 里只有 suite.yaml (无 manifest): 按单文件套件加载
        return ResolvedSource(suite_path=suite_root / SUITE_FILE_NAME, _tmp=tmp)
    except Exception:
        tmp.cleanup()
        raise


def builtin_pack_dir(name: str) -> Path:
    """wheel 内置 pack 的目录 (agent_eval/packs/<name>, 随发行包分发)。"""
    import agent_eval.packs as packs_pkg

    pack_dir = Path(packs_pkg.__file__).resolve().parent / name
    if not (pack_dir / SUITE_FILE_NAME).is_file():
        raise PackError(f"内置 pack 不存在或缺少 {SUITE_FILE_NAME}: {name}")
    return pack_dir
