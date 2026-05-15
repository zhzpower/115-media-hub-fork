import os
import shutil
import time
from typing import Any, Dict, List, Optional, Set

from ..core import STRM_ROOT, normalize_relative_path


STRM_METADATA_EXTENSIONS: Set[str] = {
    ".ass",
    ".bmp",
    ".gif",
    ".idx",
    ".jpeg",
    ".jpg",
    ".json",
    ".nfo",
    ".png",
    ".smi",
    ".srt",
    ".ssa",
    ".sub",
    ".sup",
    ".tbn",
    ".vtt",
    ".webp",
    ".xml",
}
STRM_METADATA_FILENAMES: Set[str] = {".ds_store", "desktop.ini", "thumbs.db"}


def _strm_root(root: Optional[str] = None) -> str:
    return os.path.abspath(str(root or STRM_ROOT))


def _is_inside_root(root_abs: str, path_abs: str) -> bool:
    try:
        return os.path.commonpath([root_abs, path_abs]) == root_abs
    except ValueError:
        return False


def managed_strm_file_path(local_rel_path: str, root: Optional[str] = None) -> str:
    rel_path = normalize_relative_path(str(local_rel_path or ""))
    if not rel_path:
        raise ValueError("STRM 相对路径不能为空")
    root_abs = _strm_root(root)
    target = os.path.abspath(os.path.join(root_abs, rel_path + ".strm"))
    if not _is_inside_root(root_abs, target):
        raise ValueError("STRM 相对路径越界")
    return target


def delete_managed_strm_file(local_rel_path: str, root: Optional[str] = None) -> bool:
    target = managed_strm_file_path(local_rel_path, root=root)
    if not os.path.exists(target):
        return False
    os.remove(target)
    return True


def remove_empty_parent_dirs(start_dir: str, stop_dir: str) -> int:
    removed = 0
    current = os.path.abspath(str(start_dir or ""))
    stop_abs = os.path.abspath(str(stop_dir or ""))
    while current and current != stop_abs and _is_inside_root(stop_abs, current):
        if os.path.isdir(current) and not os.listdir(current):
            os.rmdir(current)
            removed += 1
            current = os.path.dirname(current)
            continue
        break
    return removed


def _file_extension(name: str) -> str:
    base = str(name or "").strip().lower()
    if "." not in base or base.startswith(".") and base.count(".") == 1:
        return ""
    return "." + base.rsplit(".", 1)[-1]


def _format_mtime(ts: float) -> str:
    if ts <= 0:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _empty_summary(path_abs: str, root_abs: str) -> Dict[str, Any]:
    rel_path = normalize_relative_path(os.path.relpath(path_abs, root_abs))
    if rel_path == ".":
        rel_path = ""
    return {
        "path_abs": path_abs,
        "path": rel_path,
        "file_count": 0,
        "metadata_count": 0,
        "unknown_count": 0,
        "strm_count": 0,
        "extensions": set(),
        "unknown_extensions": set(),
        "last_modified_ts": 0.0,
        "children": [],
        "read_error": "",
    }


def _merge_child_summary(parent: Dict[str, Any], child: Dict[str, Any]) -> None:
    parent["file_count"] += int(child.get("file_count", 0) or 0)
    parent["metadata_count"] += int(child.get("metadata_count", 0) or 0)
    parent["unknown_count"] += int(child.get("unknown_count", 0) or 0)
    parent["strm_count"] += int(child.get("strm_count", 0) or 0)
    parent["extensions"].update(child.get("extensions", set()) or set())
    parent["unknown_extensions"].update(child.get("unknown_extensions", set()) or set())
    parent["last_modified_ts"] = max(
        float(parent.get("last_modified_ts", 0.0) or 0.0),
        float(child.get("last_modified_ts", 0.0) or 0.0),
    )


def _scan_dir(path_abs: str, root_abs: str) -> Dict[str, Any]:
    summary = _empty_summary(path_abs, root_abs)
    try:
        entries = list(os.scandir(path_abs))
    except OSError as exc:
        summary["unknown_count"] += 1
        summary["unknown_extensions"].add("读取失败")
        summary["read_error"] = str(exc)
        return summary

    for entry in entries:
        try:
            stat_result = entry.stat(follow_symlinks=False)
            summary["last_modified_ts"] = max(summary["last_modified_ts"], float(stat_result.st_mtime or 0.0))
        except OSError:
            stat_result = None
        try:
            if entry.is_dir(follow_symlinks=False):
                child = _scan_dir(entry.path, root_abs)
                summary["children"].append(child)
                _merge_child_summary(summary, child)
                continue
            if not entry.is_file(follow_symlinks=False):
                summary["file_count"] += 1
                summary["unknown_count"] += 1
                summary["unknown_extensions"].add("特殊文件")
                continue
        except OSError:
            summary["file_count"] += 1
            summary["unknown_count"] += 1
            summary["unknown_extensions"].add("读取失败")
            continue

        name_key = entry.name.strip().lower()
        ext = _file_extension(entry.name)
        if name_key in STRM_METADATA_FILENAMES:
            summary["file_count"] += 1
            summary["metadata_count"] += 1
            summary["extensions"].add(name_key)
        elif ext == ".strm":
            summary["file_count"] += 1
            summary["strm_count"] += 1
            summary["extensions"].add(ext)
        elif ext in STRM_METADATA_EXTENSIONS:
            summary["file_count"] += 1
            summary["metadata_count"] += 1
            summary["extensions"].add(ext)
        else:
            summary["file_count"] += 1
            summary["unknown_count"] += 1
            summary["unknown_extensions"].add(ext or "无扩展名")

        if stat_result is not None:
            summary["last_modified_ts"] = max(summary["last_modified_ts"], float(stat_result.st_mtime or 0.0))

    return summary


def _is_orphan_metadata_candidate(summary: Dict[str, Any]) -> bool:
    return (
        bool(summary.get("path"))
        and int(summary.get("file_count", 0) or 0) > 0
        and int(summary.get("metadata_count", 0) or 0) > 0
        and int(summary.get("strm_count", 0) or 0) == 0
        and int(summary.get("unknown_count", 0) or 0) == 0
    )


def _is_empty_dir_candidate(summary: Dict[str, Any]) -> bool:
    return (
        bool(summary.get("path"))
        and int(summary.get("file_count", 0) or 0) == 0
        and int(summary.get("metadata_count", 0) or 0) == 0
        and int(summary.get("strm_count", 0) or 0) == 0
        and int(summary.get("unknown_count", 0) or 0) == 0
    )


def _is_manual_check_summary(summary: Dict[str, Any]) -> bool:
    return (
        bool(summary.get("path"))
        and int(summary.get("file_count", 0) or 0) > 0
        and int(summary.get("strm_count", 0) or 0) == 0
        and int(summary.get("unknown_count", 0) or 0) > 0
    )


def _serialize_summary(summary: Dict[str, Any], reason: str = "", kind: str = "") -> Dict[str, Any]:
    last_modified_ts = float(summary.get("last_modified_ts", 0.0) or 0.0)
    unknown_extensions = sorted(str(item) for item in (summary.get("unknown_extensions", set()) or set()) if str(item))
    payload = {
        "path": str(summary.get("path", "") or ""),
        "file_count": int(summary.get("file_count", 0) or 0),
        "metadata_count": int(summary.get("metadata_count", 0) or 0),
        "unknown_count": int(summary.get("unknown_count", 0) or 0),
        "strm_count": int(summary.get("strm_count", 0) or 0),
        "extensions": sorted(str(item) for item in (summary.get("extensions", set()) or set()) if str(item)),
        "unknown_extensions": unknown_extensions,
        "last_modified": _format_mtime(last_modified_ts),
        "last_modified_ts": last_modified_ts,
    }
    if reason:
        payload["reason"] = reason
    if kind:
        payload["kind"] = kind
    if summary.get("read_error"):
        payload["reason"] = str(summary.get("read_error") or reason)
    return payload


def _collect_candidates(summary: Dict[str, Any], output: List[Dict[str, Any]]) -> None:
    if _is_orphan_metadata_candidate(summary):
        output.append(_serialize_summary(summary, kind="metadata"))
        return
    for child in summary.get("children", []) or []:
        _collect_candidates(child, output)


def _collect_empty_dirs(summary: Dict[str, Any], output: List[Dict[str, Any]]) -> None:
    if _is_orphan_metadata_candidate(summary):
        return
    if _is_empty_dir_candidate(summary):
        output.append(_serialize_summary(summary, kind="empty"))
        return
    for child in summary.get("children", []) or []:
        _collect_empty_dirs(child, output)


def _collect_manual_check(summary: Dict[str, Any], output: List[Dict[str, Any]], parent_blocked: bool = False) -> None:
    is_root = not bool(summary.get("path"))
    blocked = _is_manual_check_summary(summary)
    if blocked and not is_root and not parent_blocked:
        output.append(_serialize_summary(summary, reason="包含未知或非元数据文件，需手动检查", kind="manual_check"))
        return
    for child in summary.get("children", []) or []:
        _collect_manual_check(child, output, parent_blocked=blocked and not is_root)


def preview_orphan_metadata_dirs(root: Optional[str] = None) -> Dict[str, Any]:
    root_abs = _strm_root(root)
    if not os.path.isdir(root_abs):
        return {"ok": True, "root": root_abs, "candidates": [], "empty_dirs": [], "manual_check": []}
    summary = _scan_dir(root_abs, root_abs)
    candidates: List[Dict[str, Any]] = []
    empty_dirs: List[Dict[str, Any]] = []
    manual_check: List[Dict[str, Any]] = []
    _collect_candidates(summary, candidates)
    _collect_empty_dirs(summary, empty_dirs)
    _collect_manual_check(summary, manual_check)
    candidates.sort(key=lambda item: str(item.get("path", "")))
    empty_dirs.sort(key=lambda item: str(item.get("path", "")))
    manual_check.sort(key=lambda item: str(item.get("path", "")))
    return {
        "ok": True,
        "root": root_abs,
        "candidates": candidates,
        "empty_dirs": empty_dirs,
        "manual_check": manual_check,
        "candidate_count": len(candidates),
        "empty_dir_count": len(empty_dirs),
        "manual_check_count": len(manual_check),
    }


def _resolve_cleanup_target(path: str, root_abs: str) -> str:
    rel_path = normalize_relative_path(str(path or ""))
    if not rel_path:
        raise ValueError("清理路径不能为空")
    target = os.path.abspath(os.path.join(root_abs, rel_path))
    if target == root_abs or not _is_inside_root(root_abs, target):
        raise ValueError("清理路径越界")
    return target


def delete_orphan_metadata_dirs(paths: List[str], root: Optional[str] = None) -> Dict[str, Any]:
    root_abs = _strm_root(root)
    deleted: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    unique_paths = []
    seen = set()
    raw_paths = paths if isinstance(paths, list) else []
    for raw_path in raw_paths:
        rel_path = normalize_relative_path(str(raw_path or ""))
        if rel_path and rel_path not in seen:
            seen.add(rel_path)
            unique_paths.append(rel_path)

    for rel_path in sorted(unique_paths, key=lambda value: value.count("/")):
        try:
            target = _resolve_cleanup_target(rel_path, root_abs)
        except ValueError as exc:
            skipped.append({"path": rel_path, "reason": str(exc)})
            continue
        if not os.path.isdir(target):
            skipped.append({"path": rel_path, "reason": "目录不存在"})
            continue
        summary = _scan_dir(target, root_abs)
        is_metadata_candidate = _is_orphan_metadata_candidate(summary)
        is_empty_candidate = _is_empty_dir_candidate(summary)
        if not is_metadata_candidate and not is_empty_candidate:
            skipped.append(
                {
                    "path": rel_path,
                    "reason": "目录状态已变化，未满足只含废弃元数据或空目录且无 STRM 的条件",
                    "detail": _serialize_summary(summary),
                }
            )
            continue
        payload = _serialize_summary(summary, kind="metadata" if is_metadata_candidate else "empty")
        shutil.rmtree(target)
        deleted.append(payload)

    return {"ok": True, "deleted": deleted, "skipped": skipped, "deleted_count": len(deleted), "skipped_count": len(skipped)}
