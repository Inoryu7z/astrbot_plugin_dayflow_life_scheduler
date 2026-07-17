"""优秀穿搭库 WebUI 后端 API。

注册到 AstrBot 主 webui 的 Plugin Pages 机制（非独立 FastAPI 服务器）。
路由前缀：`/{PLUGIN_NAME}/page`

四个 Tab 对应的端点：
- 设计 tab：/styles, /design, /review
- 优秀库 tab：/outfits, /outfits/add, /outfits/update, /outfits/delete,
              /outfits/use_count, /probability, /export, /import
- 提示词 tab：/prompts
- 概览 tab：/overview

响应格式（AstrBot 标准）：
- 成功：{"status": "ok", "data": X}
- 失败：{"status": "error", "message": M}

所有 handler 均为 async（参考 astrbot_plugin_lm_patch 的做法），即便仅读取 query 参数。
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from .constants import DEFAULT_OUTFIT_STYLES, STYLE_SUB_VARIANTS
from .curated_store import COSPLAY_STYLE_KEY, CuratedStore
from .designer import OutfitDesigner

PLUGIN_NAME = "astrbot_plugin_dayflow_life_scheduler"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"


class PluginPageApi:
    """优秀穿搭库 WebUI 的后端 API。"""

    def __init__(
        self,
        context: Any,
        curated_store: CuratedStore,
        outfit_designer: OutfitDesigner,
        service: Any,
    ) -> None:
        self.context = context
        self.curated_store = curated_store
        self.outfit_designer = outfit_designer
        self.service = service

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------
    def register(self) -> None:
        register = self.context.register_web_api
        routes = [
            # 设计 tab
            (f"{PAGE_API_PREFIX}/styles", self.list_styles, ["GET"], "dayflow 优秀库：可选风格列表"),
            (f"{PAGE_API_PREFIX}/design", self.design_outfit, ["POST"], "dayflow 优秀库：设计师生成穿搭"),
            (f"{PAGE_API_PREFIX}/review", self.review_outfit, ["POST"], "dayflow 优秀库：审核师迭代修改"),
            # 优秀库 tab
            (f"{PAGE_API_PREFIX}/outfits", self.list_outfits, ["GET"], "dayflow 优秀库：列出条目"),
            (f"{PAGE_API_PREFIX}/outfits/add", self.add_outfit, ["POST"], "dayflow 优秀库：添加条目"),
            (f"{PAGE_API_PREFIX}/outfits/update", self.update_outfit, ["POST"], "dayflow 优秀库：编辑条目"),
            (f"{PAGE_API_PREFIX}/outfits/delete", self.delete_outfit, ["POST"], "dayflow 优秀库：删除条目"),
            (f"{PAGE_API_PREFIX}/outfits/use_count", self.set_use_count, ["POST"], "dayflow 优秀库：调整使用计数"),
            (f"{PAGE_API_PREFIX}/probability", self.get_or_set_probability, ["GET", "POST"], "dayflow 优秀库：概率配置"),
            (f"{PAGE_API_PREFIX}/export", self.export_data, ["GET"], "dayflow 优秀库：导出全部数据"),
            (f"{PAGE_API_PREFIX}/import", self.import_data, ["POST"], "dayflow 优秀库：导入数据"),
            # 提示词 tab
            (f"{PAGE_API_PREFIX}/prompts", self.get_or_set_prompts, ["GET", "POST"], "dayflow 优秀库：设计师/审核师提示词"),
            # 概览 tab
            (f"{PAGE_API_PREFIX}/overview", self.get_overview, ["GET"], "dayflow 优秀库：风格概览"),
        ]
        for route, handler, methods, desc in routes:
            register(route, handler, methods, desc)
        logger.info(f"[dayflow-优秀库] 已注册 {len(routes)} 个 Web API 路由")

    # ------------------------------------------------------------------
    # 响应与请求工具
    # ------------------------------------------------------------------
    @staticmethod
    def _ok(data: Any) -> dict:
        return {"status": "ok", "data": data}

    @staticmethod
    def _err(message: str) -> dict:
        return {"status": "error", "message": str(message or "操作失败")}

    @staticmethod
    async def _body() -> dict:
        """读取 JSON body。空 body 返回 {}。"""
        from astrbot.api.web import request
        try:
            data = await request.json(default={})
        except Exception:
            data = {}
        return data or {}

    @staticmethod
    def _query(key: str, default: str = "") -> str:
        from astrbot.api.web import request
        try:
            return (request.query.get(key, default) or default).strip()
        except Exception:
            return default

    @staticmethod
    def _method() -> str:
        from astrbot.api.web import request
        try:
            return str(request.method or "GET").upper()
        except Exception:
            return "GET"

    # ------------------------------------------------------------------
    # 设计 tab
    # ------------------------------------------------------------------
    async def list_styles(self):
        """聚合所有风格来源：
        - DEFAULT_OUTFIT_STYLES（全局默认池）
        - 所有人格的 pool.outfit_styles（包含 A 人格的"女仆自定义风"等私有风格）
        - STYLE_SUB_VARIANTS 的键（已有内置子款式的风格）
        - curated_store 中已有的风格（用户已入库的）

        用户在前端还可手动输入新风格名（不在此接口返回，但 design/review 端点接受任意风格名）。
        """
        styles: dict[str, set[str]] = {}

        def add(style_name: str, source: str):
            name = str(style_name or "").strip()
            if not name:
                return
            styles.setdefault(name, set()).add(source)

        # 1. 全局默认池
        for s in DEFAULT_OUTFIT_STYLES:
            add(s, "default_pool")

        # 2. 所有人格的 pool.outfit_styles
        try:
            for persona in self.service.cfg.personas():
                for s in persona.get("pool", {}).get("outfit_styles", []) or []:
                    add(s, "persona_pool")
        except Exception as e:
            logger.warning(f"[dayflow-优秀库] 聚合人格风格池失败: {e}")

        # 3. 已有内置子款式
        for s in STYLE_SUB_VARIANTS.keys():
            add(s, "builtin_variants")

        # 4. curated_store 已有
        for s in self.curated_store.get_all_styles():
            add(s, "curated")

        result = [
            {
                "name": name,
                "sources": sorted(sources),
                "has_curated": "curated" in sources,
                "has_builtin_variants": "builtin_variants" in sources,
                "curated_count": len(self.curated_store.get_outfits(name)),
                "probability": self.curated_store.get_probability(name),
            }
            for name, sources in sorted(styles.items())
        ]
        return self._ok({"styles": result, "total": len(result)})

    async def design_outfit(self):
        data = await self._body()
        style_name = str(data.get("style_name") or "").strip()
        user_input = str(data.get("user_input") or "").strip() or None
        if not style_name:
            return self._err("style_name 不能为空")
        try:
            result = await self.outfit_designer.design(
                style_name=style_name,
                user_input=user_input,
            )
        except Exception as e:
            logger.warning(f"[dayflow-优秀库] 设计调用异常: {e}")
            return self._err(f"设计调用异常：{e}")
        if not result.get("success"):
            return self._err(result.get("error") or "设计师未产出有效结果")
        return self._ok({
            "name": result.get("name", ""),
            "description": result.get("description", ""),
            "style_name": style_name,
            "note": "设计已生成，可点击「通过」入库或「迭代」交给审核师修改",
        })

    async def review_outfit(self):
        data = await self._body()
        style_name = str(data.get("style_name") or "").strip()
        original_name = str(data.get("original_name") or "").strip()
        original_description = str(data.get("original_description") or "").strip()
        user_feedback = str(data.get("user_feedback") or "").strip()
        if not style_name or not original_name or not original_description:
            return self._err("style_name/original_name/original_description 均不能为空")
        if not user_feedback:
            return self._err("迭代必须提供 user_feedback（用户修改意见）")
        try:
            result = await self.outfit_designer.review(
                style_name=style_name,
                original_name=original_name,
                original_description=original_description,
                user_feedback=user_feedback,
            )
        except Exception as e:
            logger.warning(f"[dayflow-优秀库] 审核调用异常: {e}")
            return self._err(f"审核调用异常：{e}")
        if not result.get("success"):
            return self._err(result.get("error") or "审核师未产出有效结果")
        return self._ok({
            "name": result.get("name", ""),
            "description": result.get("description", ""),
            "critique": result.get("critique", ""),
            "style_name": style_name,
            "original_name": original_name,
            "note": "审核师已产出修改版本，可继续迭代或通过入库",
        })

    # ------------------------------------------------------------------
    # 优秀库 tab
    # ------------------------------------------------------------------
    async def list_outfits(self):
        style = self._query("style")
        if style:
            items = self.curated_store.get_outfits(style)
            return self._ok({
                "style": style,
                "probability": self.curated_store.get_probability(style),
                "items": items,
                "count": len(items),
            })
        # 无 style 参数时返回全部
        all_styles = self.curated_store.get_all_styles()
        result = []
        for s in all_styles:
            items = self.curated_store.get_outfits(s)
            result.append({
                "style": s,
                "probability": self.curated_store.get_probability(s),
                "items": items,
                "count": len(items),
            })
        return self._ok({"styles": result, "total": len(all_styles)})

    async def add_outfit(self):
        data = await self._body()
        style_name = str(data.get("style_name") or "").strip()
        name = str(data.get("name") or "").strip()
        description = str(data.get("description") or "").strip()
        iterations = 0
        try:
            iterations = int(data.get("iterations") or 0)
        except Exception:
            iterations = 0
        ok, msg = await self.curated_store.add_outfit(
            style_name=style_name, name=name, description=description, iterations=iterations,
        )
        if not ok:
            return self._err(msg)
        return self._ok({"style": style_name, "name": name, "message": msg})

    async def update_outfit(self):
        data = await self._body()
        style_name = str(data.get("style_name") or "").strip()
        old_name = str(data.get("old_name") or "").strip()
        new_name = data.get("new_name")
        new_description = data.get("new_description")
        ok, msg = await self.curated_store.update_outfit(
            style_name=style_name, old_name=old_name,
            new_name=new_name, new_description=new_description,
        )
        if not ok:
            return self._err(msg)
        return self._ok({"style": style_name, "message": msg})

    async def delete_outfit(self):
        data = await self._body()
        style_name = str(data.get("style_name") or "").strip()
        name = str(data.get("name") or "").strip()
        ok, msg = await self.curated_store.delete_outfit(style_name=style_name, name=name)
        if not ok:
            return self._err(msg)
        return self._ok({"style": style_name, "deleted": name, "message": msg})

    async def set_use_count(self):
        data = await self._body()
        style_name = str(data.get("style_name") or "").strip()
        name = str(data.get("name") or "").strip()
        try:
            count = int(data.get("count"))
        except Exception:
            return self._err("count 必须是整数")
        ok, msg = await self.curated_store.set_use_count(
            style_name=style_name, name=name, count=count,
        )
        if not ok:
            return self._err(msg)
        return self._ok({"style": style_name, "name": name, "use_count": count, "message": msg})

    async def get_or_set_probability(self):
        """GET: 读取（?style=xxx 指定单风格，无则返回全部）
        POST: 设置 {style, probability}
        """
        if self._method() != "POST":
            style = self._query("style")
            if style:
                return self._ok({
                    "style": style,
                    "probability": self.curated_store.get_probability(style),
                    "is_cosplay_default": style == COSPLAY_STYLE_KEY,
                })
            all_probs = self.curated_store.get_all_probabilities()
            return self._ok({
                "probabilities": all_probs,
                "cosplay_default_probability": self.curated_store.get_probability(COSPLAY_STYLE_KEY),
            })
        # POST
        data = await self._body()
        style_name = str(data.get("style") or data.get("style_name") or "").strip()
        try:
            probability = float(data.get("probability"))
        except Exception:
            return self._err("probability 必须是数字")
        ok, msg = await self.curated_store.set_probability(style_name, probability)
        if not ok:
            return self._err(msg)
        return self._ok({"style": style_name, "probability": probability, "message": msg})

    async def export_data(self):
        return self._ok(self.curated_store.export_all())

    async def import_data(self):
        data = await self._body()
        payload = data.get("data")
        mode = str(data.get("mode") or "merge").strip().lower()
        if mode not in ("merge", "overwrite"):
            mode = "merge"
        if not isinstance(payload, dict):
            return self._err("data 字段必须是对象")
        result = await self.curated_store.import_data(payload, mode=mode)
        if not result.get("success"):
            return self._err(result.get("error") or "导入失败")
        return self._ok(result)

    # ------------------------------------------------------------------
    # 提示词 tab
    # ------------------------------------------------------------------
    async def get_or_set_prompts(self):
        if self._method() != "POST":
            # GET：返回用户配置 + 内置默认提示词（供前端展示参考）
            from .designer import DEFAULT_DESIGNER_PROMPT, DEFAULT_REVIEWER_PROMPT
            prompts = self.curated_store.get_prompts()
            return self._ok({
                "designer": prompts.get("designer") or "",
                "reviewer": prompts.get("reviewer") or "",
                "default_designer": DEFAULT_DESIGNER_PROMPT,
                "default_reviewer": DEFAULT_REVIEWER_PROMPT,
                "designer_is_default": not bool(prompts.get("designer")),
                "reviewer_is_default": not bool(prompts.get("reviewer")),
            })
        data = await self._body()
        designer = data.get("designer")
        reviewer = data.get("reviewer")
        # 空字符串也允许（清空覆盖，使用内置默认）
        if designer is None and reviewer is None:
            return self._err("至少需要提供 designer 或 reviewer 之一")
        ok, msg = await self.curated_store.set_prompts(
            designer=designer if designer is not None else None,
            reviewer=reviewer if reviewer is not None else None,
        )
        if not ok:
            return self._err(msg)
        from .designer import DEFAULT_DESIGNER_PROMPT, DEFAULT_REVIEWER_PROMPT
        prompts = self.curated_store.get_prompts()
        return self._ok({
            "message": msg,
            "prompts": prompts,
            "default_designer": DEFAULT_DESIGNER_PROMPT,
            "default_reviewer": DEFAULT_REVIEWER_PROMPT,
            "designer_is_default": not bool(prompts.get("designer")),
            "reviewer_is_default": not bool(prompts.get("reviewer")),
        })

    # ------------------------------------------------------------------
    # 概览 tab
    # ------------------------------------------------------------------
    async def get_overview(self):
        return self._ok({
            "styles": self.curated_store.get_overview(),
            "prompts_configured": {
                "designer": bool(self.curated_store.get_prompts().get("designer")),
                "reviewer": bool(self.curated_store.get_prompts().get("reviewer")),
            },
        })
