"""优秀穿搭库存储层。

负责在 plugin_data 下持久化优秀穿搭设计、概率配置、使用计数器与提示词。
供 webui 工作流写入、运行时风格研究概率性注入读取。

存储结构（curated_outfits.json）:
{
  "outfits": {
    "法式浪漫风": [
      {"name": "薰衣草晨曦", "description": "...", "created_at": "...", "iterations": 0, "use_count": 0, "tier": "normal"}
    ]
  },
  "probabilities": {"法式浪漫风": 0.5, "cosplay": 1.0},
  "prompts": {"designer": "...", "reviewer": "..."}
}

tier 字段取值：
- "starred": 标星收藏款（绝对正确，仅措辞可调，外观不得调整；内置子款式默认为此档）
- "normal": 经典款（允许简单微调，大体不能改）
"""

from __future__ import annotations

import asyncio
import datetime
import json
from pathlib import Path
from typing import Any

from astrbot.api import logger

# 默认概率：未显式配置时使用
DEFAULT_PROBABILITY = 0.5
# cosplay 风格默认概率（极为特殊的子品类，总是注入）
COSPLAY_DEFAULT_PROBABILITY = 1.0
COSPLAY_STYLE_KEY = "cosplay"


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


class CuratedStore:
    """优秀穿搭库的读写与查询。所有写操作通过 asyncio.Lock 串行化。"""

    def __init__(self, data_dir: Path):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self._data_dir / "curated_outfits.json"
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] = {
            "outfits": {},
            "probabilities": {},
            "prompts": {"designer": "", "reviewer": ""},
        }
        self.load()
        # 首次启动：数据文件不存在时，预填内置子款式作为初始优秀库
        if not self._file_path.exists() or not self._data["outfits"]:
            self._seed_builtin_variants()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def load(self) -> None:
        if not self._file_path.exists():
            return
        try:
            raw = self._file_path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            if not isinstance(data, dict):
                data = {}
            outfits = data.get("outfits")
            if not isinstance(outfits, dict):
                outfits = {}
            probabilities = data.get("probabilities")
            if not isinstance(probabilities, dict):
                probabilities = {}
            prompts = data.get("prompts")
            if not isinstance(prompts, dict):
                prompts = {}
            self._data = {
                "outfits": {str(k): list(v) for k, v in outfits.items() if isinstance(v, list)},
                "probabilities": {str(k): float(v) for k, v in probabilities.items() if isinstance(v, (int, float))},
                "prompts": {
                    "designer": str(prompts.get("designer") or ""),
                    "reviewer": str(prompts.get("reviewer") or ""),
                },
            }
            # 规范化条目字段
            for style, items in self._data["outfits"].items():
                normalized = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    if not item.get("name") or not item.get("description"):
                        continue
                    # tier 字段迁移：显式值优先；缺省时 iterations=-1（内置预填）视为 starred，其余视为 normal
                    tier_value = str(item.get("tier") or "").strip().lower()
                    if tier_value not in ("starred", "normal"):
                        tier_value = "starred" if int(item.get("iterations") or 0) == -1 else "normal"
                    normalized.append({
                        "name": str(item["name"]).strip(),
                        "description": str(item["description"]).strip(),
                        "created_at": str(item.get("created_at") or ""),
                        "iterations": int(item.get("iterations") or 0),
                        "use_count": int(item.get("use_count") or 0),
                        "tier": tier_value,
                    })
                self._data["outfits"][style] = normalized
        except Exception as e:
            logger.warning(f"[dayflow-优秀库] 读取失败: {e}")
            self._data = {"outfits": {}, "probabilities": {}, "prompts": {"designer": "", "reviewer": ""}}

    def _save_locked(self) -> None:
        try:
            self._file_path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[dayflow-优秀库] 保存失败: {e}")

    def _seed_builtin_variants(self) -> None:
        """首次启动时预填内置子款式（STYLE_SUB_VARIANTS）作为初始优秀库。

        - 把中华风洛丽塔、甜系洛丽塔、cosplay 三组内置子款式全部写入
        - 标记为 builtin（use_count 初始为 0，iterations 为 -1 表示内置）
        - 设置默认概率：cosplay=1.0，其他=0.5
        - 持久化到 curated_outfits.json
        """
        try:
            from .constants import STYLE_SUB_VARIANTS  # 延迟导入避免循环依赖
        except Exception as e:
            logger.warning(f"[dayflow-优秀库] 预填内置子款式失败：无法导入 STYLE_SUB_VARIANTS: {e}")
            return
        seeded_count = 0
        for style_name, variants in STYLE_SUB_VARIANTS.items():
            if not isinstance(variants, list):
                continue
            existing_names = {i.get("name") for i in self._data["outfits"].get(style_name, [])}
            items = self._data["outfits"].setdefault(style_name, [])
            for v in variants:
                if not isinstance(v, dict):
                    continue
                name = str(v.get("name") or "").strip()
                desc = str(v.get("description") or "").strip()
                if not name or not desc or name in existing_names:
                    continue
                items.append({
                    "name": name,
                    "description": desc,
                    "created_at": _now_iso(),
                    "iterations": -1,  # -1 表示内置预填
                    "use_count": 0,
                    "tier": "starred",  # 内置子款式默认为标星收藏款（绝对正确，仅措辞可调）
                })
                seeded_count += 1
            # 设置默认概率
            if style_name == COSPLAY_STYLE_KEY:
                self._data["probabilities"].setdefault(style_name, COSPLAY_DEFAULT_PROBABILITY)
            else:
                self._data["probabilities"].setdefault(style_name, DEFAULT_PROBABILITY)
        if seeded_count > 0:
            self._save_locked()
            logger.info(f"[dayflow-优秀库] 首次启动预填内置子款式: {seeded_count} 套")

    async def save(self) -> None:
        async with self._lock:
            self._save_locked()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get_outfits(self, style_name: str) -> list[dict[str, Any]]:
        style = str(style_name or "").strip()
        if not style:
            return []
        return [dict(item) for item in self._data["outfits"].get(style, [])]

    def get_all_styles(self) -> list[str]:
        return sorted(self._data["outfits"].keys())

    def has_style(self, style_name: str) -> bool:
        style = str(style_name or "").strip()
        if not style:
            return False
        items = self._data["outfits"].get(style, [])
        return len(items) > 0

    def get_probability(self, style_name: str) -> float:
        style = str(style_name or "").strip()
        if not style:
            return 0.0
        if style in self._data["probabilities"]:
            return float(self._data["probabilities"][style])
        # 未配置时使用默认值
        if style == COSPLAY_STYLE_KEY:
            return COSPLAY_DEFAULT_PROBABILITY
        return DEFAULT_PROBABILITY

    def get_all_probabilities(self) -> dict[str, float]:
        result = {}
        for style in self._data["outfits"].keys():
            result[style] = self.get_probability(style)
        # 也包含已显式配置但可能无条目的风格
        for style, prob in self._data["probabilities"].items():
            if style not in result:
                result[style] = float(prob)
        return result

    def get_use_counts(self, style_name: str) -> dict[str, int]:
        result = {}
        for item in self.get_outfits(style_name):
            result[item["name"]] = int(item.get("use_count") or 0)
        return result

    def get_prompts(self) -> dict[str, str]:
        return {
            "designer": str(self._data["prompts"].get("designer") or ""),
            "reviewer": str(self._data["prompts"].get("reviewer") or ""),
        }

    def get_overview(self) -> list[dict[str, Any]]:
        """获取所有风格的概览信息（供 webui 概览 tab）。"""
        result = []
        for style in sorted(self._data["outfits"].keys()):
            items = self._data["outfits"][style]
            use_counts = [int(i.get("use_count") or 0) for i in items]
            result.append({
                "style": style,
                "count": len(items),
                "probability": self.get_probability(style),
                "avg_use_count": round(sum(use_counts) / len(use_counts), 2) if use_counts else 0,
                "min_use_count": min(use_counts) if use_counts else 0,
                "max_use_count": max(use_counts) if use_counts else 0,
            })
        return result

    # ------------------------------------------------------------------
    # 运行时注入查询
    # ------------------------------------------------------------------
    def select_for_injection(
        self,
        style_name: str,
        count: int = 2,
        exclude_names: list[str] | None = None,
    ) -> list[dict[str, Any]] | None:
        """供运行时风格研究调用：按 use_count 升序选 count 个条目。

        - 优先选 use_count 最低的（确保每个设计都得到展示）
        - exclude_names 中的条目会被排除（防重复，与 _sub_variants_usage 配合）
        - 不足 count 个时返回实际数量；无可用条目返回 None
        """
        style = str(style_name or "").strip()
        if not style:
            return None
        items = self._data["outfits"].get(style, [])
        if not items:
            return None
        exclude_set = set(str(n or "").strip() for n in (exclude_names or []))
        available = [i for i in items if i.get("name") not in exclude_set]
        if not available:
            # 全部被排除时回退使用全部条目
            available = list(items)
        available.sort(key=lambda x: (int(x.get("use_count") or 0), str(x.get("name") or "")))
        selected = available[: max(1, int(count))]
        return [dict(item) for item in selected]

    # ------------------------------------------------------------------
    # 写操作（均加锁）
    # ------------------------------------------------------------------
    async def add_outfit(self, style_name: str, name: str, description: str, iterations: int = 0, tier: str = "normal") -> tuple[bool, str]:
        style = str(style_name or "").strip()
        name = str(name or "").strip()
        description = str(description or "").strip()
        if not style or not name or not description:
            return False, "风格名、款式名、描述均不能为空"
        tier_value = str(tier or "").strip().lower()
        if tier_value not in ("starred", "normal"):
            tier_value = "normal"
        async with self._lock:
            items = self._data["outfits"].setdefault(style, [])
            if any(i.get("name") == name for i in items):
                return False, f"风格「{style}」下已存在同名设计「{name}」"
            items.append({
                "name": name,
                "description": description,
                "created_at": _now_iso(),
                "iterations": int(iterations),
                "use_count": 0,
                "tier": tier_value,
            })
            self._save_locked()
            logger.info(f"[dayflow-优秀库] 入库: style={style}, name={name}, iterations={iterations}, tier={tier_value}")
            return True, "已入库"

    async def update_outfit(
        self,
        style_name: str,
        old_name: str,
        new_name: str | None = None,
        new_description: str | None = None,
    ) -> tuple[bool, str]:
        style = str(style_name or "").strip()
        old_name = str(old_name or "").strip()
        if not style or not old_name:
            return False, "风格名与原款式名不能为空"
        async with self._lock:
            items = self._data["outfits"].get(style, [])
            target_idx = -1
            for idx, item in enumerate(items):
                if item.get("name") == old_name:
                    target_idx = idx
                    break
            if target_idx < 0:
                return False, f"未找到风格「{style}」下的「{old_name}」"
            new_name = str(new_name or "").strip() if new_name is not None else None
            new_description = str(new_description or "").strip() if new_description is not None else None
            if new_name and new_name != old_name:
                if any(i.get("name") == new_name for i in items if i.get("name") != old_name):
                    return False, f"风格「{style}」下已存在同名设计「{new_name}」"
            if new_name:
                items[target_idx]["name"] = new_name
            if new_description:
                items[target_idx]["description"] = new_description
            self._save_locked()
            logger.info(f"[dayflow-优秀库] 编辑: style={style}, old={old_name}, new={new_name or old_name}")
            return True, "已更新"

    async def delete_outfit(self, style_name: str, name: str) -> tuple[bool, str]:
        style = str(style_name or "").strip()
        name = str(name or "").strip()
        if not style or not name:
            return False, "风格名与款式名不能为空"
        async with self._lock:
            items = self._data["outfits"].get(style, [])
            new_items = [i for i in items if i.get("name") != name]
            if len(new_items) == len(items):
                return False, f"未找到风格「{style}」下的「{name}」"
            self._data["outfits"][style] = new_items
            if not new_items:
                self._data["outfits"].pop(style, None)
            self._save_locked()
            logger.info(f"[dayflow-优秀库] 删除: style={style}, name={name}")
            return True, "已删除"

    async def set_probability(self, style_name: str, probability: float) -> tuple[bool, str]:
        style = str(style_name or "").strip()
        if not style:
            return False, "风格名不能为空"
        try:
            prob = float(probability)
        except Exception:
            return False, "概率必须是数字"
        if prob < 0 or prob > 1:
            return False, "概率必须在 0~1 之间"
        async with self._lock:
            self._data["probabilities"][style] = prob
            self._save_locked()
            logger.info(f"[dayflow-优秀库] 概率设置: style={style}, prob={prob}")
            return True, "已更新"

    async def set_use_count(self, style_name: str, name: str, count: int) -> tuple[bool, str]:
        style = str(style_name or "").strip()
        name = str(name or "").strip()
        if not style or not name:
            return False, "风格名与款式名不能为空"
        try:
            count = max(0, int(count))
        except Exception:
            return False, "计数必须是非负整数"
        async with self._lock:
            items = self._data["outfits"].get(style, [])
            for item in items:
                if item.get("name") == name:
                    item["use_count"] = count
                    self._save_locked()
                    logger.info(f"[dayflow-优秀库] 计数调整: style={style}, name={name}, count={count}")
                    return True, "已更新"
            return False, f"未找到风格「{style}」下的「{name}」"

    async def set_tier(self, style_name: str, name: str, tier: str) -> tuple[bool, str]:
        """切换条目分级。tier: starred=标星收藏款（绝对正确，仅措辞可调），normal=经典款（允许简单微调）。"""
        style = str(style_name or "").strip()
        name = str(name or "").strip()
        if not style or not name:
            return False, "风格名与款式名不能为空"
        tier_value = str(tier or "").strip().lower()
        if tier_value not in ("starred", "normal"):
            return False, "分级必须为 starred 或 normal"
        async with self._lock:
            items = self._data["outfits"].get(style, [])
            for item in items:
                if item.get("name") == name:
                    old_tier = item.get("tier")
                    if old_tier == tier_value:
                        return True, "分级未变更"
                    item["tier"] = tier_value
                    self._save_locked()
                    logger.info(f"[dayflow-优秀库] 分级切换: style={style}, name={name}, {old_tier}->{tier_value}")
                    return True, "已更新"
            return False, f"未找到风格「{style}」下的「{name}」"

    async def increment_use_counts(self, style_name: str, names: list[str]) -> None:
        """运行时注入后调用：增加被注入条目的 use_count。"""
        style = str(style_name or "").strip()
        if not style or not names:
            return
        name_set = set(str(n or "").strip() for n in names if n)
        async with self._lock:
            items = self._data["outfits"].get(style, [])
            changed = False
            for item in items:
                if item.get("name") in name_set:
                    item["use_count"] = int(item.get("use_count") or 0) + 1
                    changed = True
            if changed:
                self._save_locked()
                logger.debug(f"[dayflow-优秀库] 使用计数+1: style={style}, names={list(name_set)}")

    async def set_prompts(self, designer: str | None = None, reviewer: str | None = None) -> tuple[bool, str]:
        async with self._lock:
            if designer is not None:
                self._data["prompts"]["designer"] = str(designer)
            if reviewer is not None:
                self._data["prompts"]["reviewer"] = str(reviewer)
            self._save_locked()
            logger.info("[dayflow-优秀库] 提示词已更新")
            return True, "已更新"

    # ------------------------------------------------------------------
    # 导入/导出
    # ------------------------------------------------------------------
    def export_all(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._data, ensure_ascii=False))

    async def import_data(self, data: dict[str, Any], mode: str = "merge") -> dict[str, Any]:
        """导入数据。mode: merge=同名跳过追加, overwrite=同名覆盖。

        返回 {success, added, skipped, overwritten}
        """
        if not isinstance(data, dict):
            return {"success": False, "error": "数据格式错误：顶层不是对象"}
        outfits_in = data.get("outfits")
        if not isinstance(outfits_in, dict):
            return {"success": False, "error": "数据格式错误：缺少 outfits 字段"}
        added = 0
        skipped = 0
        overwritten = 0
        async with self._lock:
            for style, items in outfits_in.items():
                if not isinstance(items, list):
                    continue
                style = str(style).strip()
                if not style:
                    continue
                current = self._data["outfits"].setdefault(style, [])
                current_map = {i.get("name"): i for i in current}
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip()
                    desc = str(item.get("description") or "").strip()
                    if not name or not desc:
                        continue
                    new_entry = {
                        "name": name,
                        "description": desc,
                        "created_at": str(item.get("created_at") or _now_iso()),
                        "iterations": int(item.get("iterations") or 0),
                        "use_count": int(item.get("use_count") or 0),
                        "tier": (str(item.get("tier") or "").strip().lower() or
                                 ("starred" if int(item.get("iterations") or 0) == -1 else "normal")),
                    }
                    if name in current_map:
                        if mode == "overwrite":
                            # 替换
                            for idx, i in enumerate(current):
                                if i.get("name") == name:
                                    current[idx] = new_entry
                                    break
                            overwritten += 1
                        else:
                            skipped += 1
                    else:
                        current.append(new_entry)
                        current_map[name] = new_entry
                        added += 1
            # 同时合并概率与提示词
            probs_in = data.get("probabilities")
            if isinstance(probs_in, dict):
                for k, v in probs_in.items():
                    try:
                        self._data["probabilities"][str(k).strip()] = float(v)
                    except Exception:
                        pass
            prompts_in = data.get("prompts")
            if isinstance(prompts_in, dict):
                if prompts_in.get("designer"):
                    self._data["prompts"]["designer"] = str(prompts_in["designer"])
                if prompts_in.get("reviewer"):
                    self._data["prompts"]["reviewer"] = str(prompts_in["reviewer"])
            self._save_locked()
        logger.info(f"[dayflow-优秀库] 导入完成: added={added}, skipped={skipped}, overwritten={overwritten}")
        return {"success": True, "added": added, "skipped": skipped, "overwritten": overwritten}
