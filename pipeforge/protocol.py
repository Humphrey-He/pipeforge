"""AssetFactory V2 统一协议库（双端共用）— 规范 V1.0 / schema 2.0

Local 工作台与 Colab 生产工厂共同遵守的接口契约实现。
覆盖：ID 校验 / 类别字典 / 目录边界 / SHA-256 / READY-PUBLISHED 读写 /
Schema 校验 / 状态机转换表 / 阶段依赖与失效传播。

依赖：jsonschema（可选——缺失时降级为内置自校验，行为一致）。
零第三方强依赖：Local 与 Colab 均可直接 import 使用。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROTOCOL_VERSION = "2.0"
SPEC_DOC_VERSION = "V1.0"

# ---------------------------------------------------------------- 常量
CATEGORIES = ("PROP", "FURN", "CHAR", "ENV", "DECOR", "VFX", "UI")
TASK_STATUSES = ("PENDING", "RUNNING", "RETRY_WAIT", "REVIEW", "COMPLETED", "FAILED", "CANCELLED")
TERMINAL_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")
STAGE_STATUSES = ("PENDING", "RUNNING", "COMPLETED", "RETRY_WAIT", "FAILED", "SKIPPED")
STAGES = ("inference", "validate_raw", "clean", "validate_clean", "uv", "bake", "render", "quality", "manifest", "publish")
QA_STATUSES = ("PASS", "WARNING", "REVIEW", "FAIL")

ERROR_CODES = (
    "INPUT_INVALID", "SCHEMA_INVALID", "INPUT_HASH_MISMATCH", "NETWORK_TIMEOUT",
    "GPU_OOM", "MODEL_LOAD_FAILED", "BLENDER_PROCESS_FAILED", "OUTPUT_INVALID",
    "QUALITY_REJECTED", "PUBLISH_FAILED", "DRIVE_SYNC_FAILED",
)

_RE_ASSET_ID = re.compile(r"^(PROP|FURN|CHAR|ENV|DECOR|VFX|UI)_[A-Z0-9]+(?:_[A-Z0-9]+)*_[0-9]{3,}$")
_RE_TASK_ID = re.compile(r"^TASK_[0-9]{8}_[0-9]{3,}$")
_RE_SESSION_ID = re.compile(r"^SESSION_[0-9]{8}_[0-9A-Z]{3,}$")
_RE_VERSION = re.compile(r"^v[0-9]{3,}$")
_RE_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_RE_ISO_Z = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

ALLOWED_TRANSITIONS = {
    "PENDING": {"RUNNING", "CANCELLED"},
    "RUNNING": {"RETRY_WAIT", "REVIEW", "COMPLETED", "FAILED", "CANCELLED"},
    "RETRY_WAIT": {"RUNNING", "CANCELLED"},
    "REVIEW": {"RUNNING", "FAILED", "CANCELLED"},  # 人工确认后可重入指定阶段
    "COMPLETED": set(),
    "FAILED": set(),
    "CANCELLED": set(),
}

# ---------------------------------------------------------------- ID 校验

def valid_asset_id(aid: str) -> bool:
    return bool(_RE_ASSET_ID.match(aid or "")) and aid.split("_")[0] in CATEGORIES


def valid_task_id(tid: str) -> bool:
    return bool(_RE_TASK_ID.match(tid or ""))


def valid_session_id(sid: str) -> bool:
    return bool(_RE_SESSION_ID.match(sid or ""))


def valid_asset_version(v: str) -> bool:
    return bool(_RE_VERSION.match(v or ""))


def new_task_id(seq: int) -> str:
    """TASK_{YYYYMMDD}_{SEQUENCE:03d}"""
    return "TASK_%s_%03d" % (time.strftime("%Y%m%d"), seq)


def new_session_id() -> str:
    return "SESSION_%s_%s" % (time.strftime("%Y%m%d"), uuid.uuid4().hex[:6].upper())


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

# ---------------------------------------------------------------- Hash

def sha256_file(path: os.PathLike | str) -> str:
    """返回不带前缀的 64 位十六进制 SHA-256（与 Schema pattern 一致）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def config_hash(cfg: dict) -> str:
    """阶段配置指纹（排序序列化），用于检查点比对。"""
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

# ---------------------------------------------------------------- 目录边界

def safe_join(base: os.PathLike | str, rel: str) -> Path:
    """目录边界校验后的拼接，禁止路径穿越（安全要求第 4 条）。"""
    base_p = Path(base).resolve()
    rel_clean = rel.replace("\\", "/")
    if ":" in rel_clean or rel_clean.startswith("/"):
        raise ValueError("禁止绝对路径/盘符: %r" % rel)
    if ".." in rel_clean.split("/"):
        raise ValueError("禁止路径穿越: %r" % rel)
    target = (base_p / rel_clean).resolve()
    if base_p != target and base_p not in target.parents:
        raise ValueError("路径越界: %r" % rel)
    return target


def validate_relative_path(rel: str, must_be_relative_to: Optional[str] = None) -> bool:
    rel_clean = (rel or "").replace("\\", "/")
    if ":" in rel_clean or rel_clean.startswith("/") or ".." in rel_clean.split("/"):
        return False
    if must_be_relative_to:
        return rel_clean.startswith(must_be_relative_to.rstrip("/") + "/")
    return True

# ---------------------------------------------------------------- 原子写

def atomic_write_json(path: os.PathLike | str, obj: Any) -> None:
    """tmp + 回读校验 + 同目录 rename（Drive 非事务存储的缓解措施）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    data = json.dumps(obj, ensure_ascii=False, indent=2)
    tmp.write_text(data, encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))  # 回读校验
    tmp.replace(p)


def read_json(path: os.PathLike | str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))

# ---------------------------------------------------------------- READY / PUBLISHED

def write_ready(task_dir: os.PathLike | str) -> Path:
    """READY 必须最后写入（3.3 提交协议）。内容含时间戳，非空文件。"""
    p = Path(task_dir) / "READY"
    p.write_text(json.dumps({"status": "READY", "created_at": now_iso()}, ensure_ascii=False), encoding="utf-8")
    return p


def has_ready(task_dir: os.PathLike | str) -> bool:
    return (Path(task_dir) / "READY").exists()


def write_published(version_dir: os.PathLike | str, asset_id: str, asset_version: str,
                    manifest_rel: str, manifest_sha: str) -> Path:
    """PUBLISHED JSON 标记（10.2）。只能在全部校验通过后调用。"""
    payload = {
        "status": "PUBLISHED",
        "asset_id": asset_id,
        "asset_version": asset_version,
        "manifest": manifest_rel,
        "manifest_sha256": manifest_sha,
        "published_at": now_iso(),
    }
    p = Path(version_dir) / "PUBLISHED"
    atomic_write_json(p, payload)
    return p


def read_published(version_dir: os.PathLike | str) -> Optional[dict]:
    p = Path(version_dir) / "PUBLISHED"
    if not p.exists():
        return None
    try:
        d = read_json(p)
        return d if d.get("status") == "PUBLISHED" else None
    except Exception:
        return None

# ---------------------------------------------------------------- 状态机

def can_transition(cur: str, nxt: str) -> bool:
    return nxt in ALLOWED_TRANSITIONS.get(cur, set())


def transition(task: dict, nxt: str) -> dict:
    cur = task["status"]
    if not can_transition(cur, nxt):
        raise ValueError("非法状态转换: %s -> %s" % (cur, nxt))
    task["status"] = nxt
    task["updated_at"] = now_iso()
    return task


def make_error(code: str, stage: str, message: str, retryable: bool, attempt: int) -> dict:
    if code not in ERROR_CODES:
        raise ValueError("未知错误码: %s" % code)
    return {"code": code, "stage": stage, "message": str(message)[:500],
            "retryable": retryable, "attempt": attempt, "timestamp": now_iso()}

# ---------------------------------------------------------------- 失效传播（6.3）

INVALIDATION = {
    "reference": 0,   # 参考图 → inference 及全部下游
    "model": 0,       # 推理模型
    "inference": 0,   # 推理参数
    "clean": 2,       # 清洗 Profile → clean 及下游（STAGES[2]=clean）
    "uv": 4,          # UV 配置（STAGES[4]=uv）
    "bake": 5,        # 烘焙配置（STAGES[5]=bake）
    "render": 6,      # 灯光配置（STAGES[6]=render）
    "qa": 7,          # QA 规则 → quality/manifest/publish（STAGES[7]=quality）
    "manifest": 8,    # Manifest 格式 → manifest/publish（STAGES[8]=manifest）
}


def invalidate_from(what_changed: str) -> List[str]:
    """返回需要重跑的阶段列表（含起点）。"""
    if what_changed not in INVALIDATION:
        raise ValueError("未知失效源: %s（可选: %s）" % (what_changed, ", ".join(INVALIDATION)))
    return list(STAGES[INVALIDATION[what_changed]:])


def stage_valid(task: dict, stage: str, input_path: Optional[Path], processor_version: str,
                output_path: Optional[Path] = None) -> bool:
    """七项检查点校验（6.2）：全过才允许跳过。"""
    if stage not in STAGES:
        raise ValueError("未知阶段: %s" % stage)
    ck = (task.get("stages", {}).get(stage) or {}).get("checkpoint_data")
    if not ck or ck.get("status") != "COMPLETED":
        return False
    if ck.get("processor_version") != processor_version:
        return False
    if input_path is not None:
        if input_path is None or not Path(input_path).exists():
            return False
        if ck.get("input_hash") and sha256_file(input_path) != ck["input_hash"]:
            return False
    if ck.get("config_hash") != config_hash(task.get("config", {}).get(stage, {})):
        return False
    op = ck.get("output_path")
    if not op:
        return False
    if output_path is not None and Path(output_path) != Path(op):
        return False
    if not Path(op).exists():
        return False
    if ck.get("output_hash") and sha256_file(op) != ck["output_hash"]:
        return False
    return True

# ---------------------------------------------------------------- Schema 校验

_TASK_REQUIRED = ("schema_version", "task_id", "asset_id", "project_id", "pipeline", "profile",
                  "priority", "status", "source", "config", "stages", "retry", "outputs", "errors",
                  "created_at", "updated_at")
_MANIFEST_REQUIRED = ("schema_version", "asset_id", "asset_version", "task_id", "project_id",
                      "category", "profile", "pipeline", "geometry", "quality", "delivery",
                      "created_at", "published_at")

try:  # jsonschema 可选
    import jsonschema
    _HAVE_JSONSCHEMA = True
except ImportError:
    _HAVE_JSONSCHEMA = False


def _fallback_task_check(t: dict) -> Tuple[bool, List[str]]:
    errs = []
    for k in _TASK_REQUIRED:
        if k not in t:
            errs.append("缺少必填字段: " + k)
    if not errs:
        if t["schema_version"] != PROTOCOL_VERSION:
            errs.append("schema_version 必须为 2.0")
        if not valid_asset_id(t["asset_id"]):
            errs.append("asset_id 格式非法")
        if not valid_task_id(t["task_id"]):
            errs.append("task_id 格式非法")
        if t["status"] not in TASK_STATUSES:
            errs.append("status 非法: " + str(t["status"]))
        if int(t["priority"]) not in range(1, 10):
            errs.append("priority 应为 1-9")
        refs = t.get("source", {}).get("reference", [])
        if not refs:
            errs.append("source.reference 不能为空")
        for r in refs:
            if not validate_relative_path(r.get("path", "")):
                errs.append("参考图路径非法: " + str(r.get("path")))
            if not _RE_SHA256.match(r.get("sha256", "")):
                errs.append("sha256 格式非法")
        st = t.get("stages", {})
        for s in STAGES:
            if s not in st:
                errs.append("stages 缺少 " + s)
    return (not errs, errs)


def _fallback_manifest_check(m: dict) -> Tuple[bool, List[str]]:
    errs = []
    for k in _MANIFEST_REQUIRED:
        if k not in m:
            errs.append("缺少必填字段: " + k)
    if not errs:
        if not valid_asset_id(m["asset_id"]):
            errs.append("asset_id 格式非法")
        if not valid_asset_version(m["asset_version"]):
            errs.append("asset_version 格式非法（v001）")
        if not valid_task_id(m["task_id"]):
            errs.append("task_id 格式非法")
        if m["quality"]["status"] not in QA_STATUSES:
            errs.append("quality.status 非法")
        if m["delivery"]["status"] not in ("PUBLISHED", "PENDING", "BLOCKED"):
            errs.append("delivery.status 非法")
        for art in m.get("artifacts", []):
            if not validate_relative_path(art.get("path", "")):
                errs.append("artifact 路径非法: " + str(art.get("path")))
    return (not errs, errs)


def validate_task(task: dict, schema_path: Optional[str] = None) -> Tuple[bool, List[str]]:
    """Schema 校验。jsonschema 可用时走 draft-07；否则降级自校验。"""
    if _HAVE_JSONSCHEMA:
        sp = schema_path or str(Path(__file__).parent / "schemas" / "task.schema.json")
        try:
            jsonschema.validate(task, read_json(sp))
            return (True, [])
        except jsonschema.ValidationError as e:
            return (False, [str(e.message)[:300]])
    return _fallback_task_check(task)


def validate_manifest(mani: dict, schema_path: Optional[str] = None) -> Tuple[bool, List[str]]:
    if _HAVE_JSONSCHEMA:
        sp = schema_path or str(Path(__file__).parent / "schemas" / "manifest.schema.json")
        try:
            jsonschema.validate(mani, read_json(sp))
            return (True, [])
        except jsonschema.ValidationError as e:
            return (False, [str(e.message)[:300]])
    return _fallback_manifest_check(mani)


def verify_published_version(version_dir: os.PathLike | str) -> Tuple[bool, List[str]]:
    """本地同步器入口（10.3）：PUBLISHED + Manifest 双验证 + 全文件 Hash 复核。"""
    vd = Path(version_dir)
    errs = []
    pub = read_published(vd)
    if not pub:
        return (False, ["缺少有效 PUBLISHED 标记"])
    mani_rel = pub.get("manifest", "")
    if not mani_rel:
        return (False, ["PUBLISHED 缺少 manifest 字段"])
    mani_path = safe_join(vd, mani_rel)
    if not mani_path.exists():
        return (False, ["Manifest 文件缺失: " + mani_rel])
    mani_sha = sha256_file(mani_path)
    if pub.get("manifest_sha256") and mani_sha != pub["manifest_sha256"]:
        errs.append("Manifest Hash 与 PUBLISHED 记录不一致")
    mani = read_json(mani_path)
    ok, merrs = validate_manifest(mani)
    errs.extend(merrs)
    for art in mani.get("artifacts", []):
        try:
            ap = safe_join(vd, art["path"])
        except ValueError as e:
            errs.append(str(e))
            continue
        if not ap.exists():
            errs.append("产物缺失: " + art["path"])
        elif sha256_file(ap) != art["sha256"]:
            errs.append("Hash 不一致: " + art["path"])
    for mp in mani.get("textures", {}).get("maps", []):
        try:
            tp = safe_join(vd, mp["path"])
        except ValueError as e:
            errs.append(str(e))
            continue
        if not tp.exists():
            errs.append("贴图缺失: " + mp["path"])
        elif sha256_file(tp) != mp["sha256"]:
            errs.append("贴图 Hash 不一致: " + mp["path"])
    return (not errs, errs)


def godot_import_target(category: str, project_id: str, profile: str) -> str:
    """按 category 映射 Godot 导入目录（10.3 第 6 步）。"""
    sub = {"PROP": "props", "FURN": "props", "CHAR": "characters", "ENV": "environment",
           "DECOR": "props", "VFX": "vfx", "UI": "ui"}.get(category, "misc")
    return "assets/models/%s/" % sub


def selftest() -> None:
    """P01/P10 快速自检：示例构造 + ID/状态机/失效传播/边界。"""
    assert valid_asset_id("PROP_WARDROBE_001") and not valid_asset_id("PROP_wardrobe_1")
    assert valid_task_id("TASK_20261122_001") and not valid_task_id("TASK_20261122_1")
    assert can_transition("PENDING", "RUNNING") and not can_transition("COMPLETED", "RUNNING")
    assert invalidate_from("clean")[0] == "clean" and "inference" not in invalidate_from("bake")
    assert invalidate_from("reference") == list(STAGES)
    try:
        safe_join("/base", "../etc")
        raise AssertionError("应当拒绝路径穿越")
    except ValueError:
        pass
    tid = new_task_id(1)
    assert valid_task_id(tid)
    print("protocol.py selftest OK | jsonschema=%s | %s" % (_HAVE_JSONSCHEMA, now_iso()))


if __name__ == "__main__":
    selftest()