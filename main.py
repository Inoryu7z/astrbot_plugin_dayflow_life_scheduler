import datetime
import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.provider.entities import ProviderRequest

from .core.service import DayflowService

DAYFLOW_INJECTION_HEADER = "<DayFlow-Schedule>"
DAYFLOW_INJECTION_FOOTER = "</DayFlow-Schedule>"
_INJECTION_PATTERN = re.compile(
    re.escape(DAYFLOW_INJECTION_HEADER) + r".*?" + re.escape(DAYFLOW_INJECTION_FOOTER),
    flags=re.DOTALL,
)


def _render_schedule_display(data: dict) -> str:
    outfit = str(data.get("outfit") or "").strip()
    summary = str(data.get("summary") or "").strip()
    timeline = data.get("timeline")

    header = f"👕 今日穿搭：{outfit}"
    if summary:
        header += f"\n💬 {summary}"

    if isinstance(timeline, list) and timeline:
        parts = []
        for i, item in enumerate(timeline, 1):
            if not isinstance(item, dict):
                continue
            time_start = str(item.get("time_start") or "").strip()
            time_end = str(item.get("time_end") or "").strip()
            title = str(item.get("title") or "").strip()
            detail = str(item.get("detail") or "").strip()
            outfit_change = str(item.get("outfit_change") or "").strip()
            time_range = f"{time_start}-{time_end}" if time_start and time_end else ""
            block = f"── 第 {i} 项 ──\n🕐 {time_range}"
            if title:
                block += f"\n📌 {title}"
            if detail:
                block += f"\n📄 {detail}"
            if outfit_change:
                block += f"\n👗 换装：{outfit_change}"
            parts.append(block)
        schedule_text = "\n\n".join(parts)
    else:
        schedule_text = str(data.get("schedule") or "").strip()

    return f"{header}\n📝 日程安排：\n{schedule_text}"


def _build_injection_text(data: dict) -> str | None:
    meta = data.get("meta") or {}
    if meta.get("error") or meta.get("fallback"):
        return None
    outfit = str(data.get("outfit") or "").strip()
    schedule = str(data.get("schedule") or "").strip()
    if not outfit and not schedule:
        return None
    parts = []
    if outfit:
        parts.append(f"今日穿搭：{outfit}")
    if schedule:
        parts.append(f"今日日程：\n{schedule}")
    body = "\n".join(parts)
    return f"{DAYFLOW_INJECTION_HEADER}\n{body}\n{DAYFLOW_INJECTION_FOOTER}"


def _remove_dayflow_injection(system_prompt: str | None) -> tuple[str, bool]:
    if not system_prompt:
        return system_prompt or "", False
    cleaned = _INJECTION_PATTERN.sub("", system_prompt)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, cleaned != system_prompt


class DayflowPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.context = context
        self.config = config or {}
        self.service = DayflowService(context=context, config=self.config)

    async def initialize(self):
        await self.service.initialize()

    async def terminate(self):
        await self.service.terminate()

    async def get_life_context(self, session_id: str | None = None, persona_name: str | None = None, target_date: str | None = None) -> dict:
        return await self.service.get_life_context(session_id=session_id, persona_name=persona_name, target_date=target_date)

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        try:
            session_id = getattr(event, "unified_msg_origin", None)
            if not session_id:
                return

            if getattr(req, "system_prompt", None) is None:
                req.system_prompt = ""

            req.system_prompt, removed = _remove_dayflow_injection(req.system_prompt)
            if removed:
                logger.debug(f"[dayflow] 已清理上次日程注入: session={session_id}")

            today = datetime.datetime.now().strftime("%Y-%m-%d")
            data = await self.service.get_life_context(session_id=session_id, target_date=today)

            injection = _build_injection_text(data)
            if injection:
                req.system_prompt += f"\n\n{injection}"
                logger.info(
                    f"[dayflow] 已注入今日日程到系统提示词: session={session_id}, "
                    f"persona={str((data.get('meta') or {}).get('persona_name') or '')}, date={today}"
                )
        except Exception as e:
            logger.warning(f"[dayflow] on_llm_request 注入失败: {e}")

    async def _generate_for_event(self, event: AstrMessageEvent, persona_name: str, persona_desc: str, store_key: str, force_regenerate: bool = False, extra_requirement: str | None = None):
        if not self.service.is_persona_configured(persona_name):
            yield event.plain_result(f"当前人格未在 Dayflow 中启用：{persona_name}")
            return

        today = datetime.datetime.now().strftime("%Y-%m-%d")
        session_id = getattr(event, "unified_msg_origin", None)

        existing_before_lock = await self.service.get_life_context(
            session_id=session_id,
            persona_name=persona_name,
            target_date=today,
        )
        if existing_before_lock and not existing_before_lock.get("meta", {}).get("error") and not existing_before_lock.get("meta", {}).get("fallback") and not force_regenerate:
            yield event.plain_result(
                f"🧠 人格：{persona_name}\n{_render_schedule_display(existing_before_lock)}"
            )
            return

        ok = await self.service.enter_generation(store_key)
        if not ok:
            existing_while_busy = await self.service.get_life_context(
                session_id=session_id,
                persona_name=persona_name,
                target_date=today,
            )
            if existing_while_busy and not existing_while_busy.get("meta", {}).get("error") and not existing_while_busy.get("meta", {}).get("fallback") and not force_regenerate:
                yield event.plain_result(
                    f"🧠 人格：{persona_name}\n{_render_schedule_display(existing_while_busy)}"
                )
            else:
                yield event.plain_result(f"当前人格 {persona_name} 已有生成任务在进行中，请稍后再试。")
            return

        try:
            existing_after_lock = await self.service.get_life_context(
                session_id=session_id,
                persona_name=persona_name,
                target_date=today,
            )
            if existing_after_lock and not existing_after_lock.get("meta", {}).get("error") and not existing_after_lock.get("meta", {}).get("fallback") and not force_regenerate:
                yield event.plain_result(
                    f"🧠 人格：{persona_name}\n{_render_schedule_display(existing_after_lock)}"
                )
                return

            if extra_requirement:
                yield event.plain_result(f"🪄 正在为 {persona_name} 定制今日日程，请稍候...")
            elif force_regenerate:
                yield event.plain_result(f"🪄 正在为 {persona_name} 强制重生成今日日程，请稍候...")
            else:
                yield event.plain_result(f"🪄 正在为 {persona_name} 生成今日日程，请稍候...")
            data = await self.service.generate_schedule(event=event, persona_name=store_key, persona_desc=persona_desc, target_date=today, auto_retry=True, extra_requirement=extra_requirement)
            if data.get("meta", {}).get("error"):
                yield event.plain_result(f"⚠️ 生成失败：{data.get('memo', '未知错误')}")
                return
            self.service.save_generated(store_key, data)
            if extra_requirement:
                yield event.plain_result(
                    f"✅ 已为 {persona_name} 定制今日日程\n{_render_schedule_display(data)}"
                )
            elif force_regenerate:
                yield event.plain_result(
                    f"✅ 已强制重生成 {persona_name} 的今日日程\n{_render_schedule_display(data)}"
                )
            else:
                yield event.plain_result(
                    f"✅ 已生成 {persona_name} 的今日日程\n{_render_schedule_display(data)}"
                )
        finally:
            await self.service.exit_generation(store_key)

    @filter.command("查看日程", alias={"life_show", "dayflow_show"})
    async def dayflow_show(self, event: AstrMessageEvent):
        persona_ctx = await self.service.resolve_persona_context(event=event)
        persona_name = persona_ctx["persona_name"]
        persona_desc = persona_ctx["persona_desc"]
        if not self.service.is_persona_configured(persona_name, persona_ctx.get("persona_id")):
            yield event.plain_result(f"当前人格未在 Dayflow 中启用：{persona_name}")
            return
        store_key = self.service.normalize_persona_key(persona_name, persona_ctx.get("persona_id"))

        today = datetime.datetime.now().strftime("%Y-%m-%d")
        today_schedule = await self.service.get_life_context(
            session_id=getattr(event, "unified_msg_origin", None),
            persona_name=persona_name,
            target_date=today,
        )

        if today_schedule and not today_schedule.get("meta", {}).get("error") and not today_schedule.get("meta", {}).get("fallback"):
            yield event.plain_result(
                f"🧠 人格：{persona_name}\n{_render_schedule_display(today_schedule)}"
            )
            return

        async for result in self._generate_for_event(event, persona_name, persona_desc, store_key, force_regenerate=False):
            yield result

    @filter.command("生成日程", alias={"life_renew", "dayflow_gen"})
    async def dayflow_generate(self, event: AstrMessageEvent):
        persona_ctx = await self.service.resolve_persona_context(event=event)
        persona_name = persona_ctx["persona_name"]
        persona_desc = persona_ctx["persona_desc"]
        if not self.service.is_persona_configured(persona_name, persona_ctx.get("persona_id")):
            yield event.plain_result(f"当前人格未在 Dayflow 中启用：{persona_name}")
            return
        store_key = self.service.normalize_persona_key(persona_name, persona_ctx.get("persona_id"))
        async for result in self._generate_for_event(event, persona_name, persona_desc, store_key, force_regenerate=True):
            yield result

    @filter.command("定制日程", alias={"life_custom", "dayflow_custom"})
    async def dayflow_custom(self, event: AstrMessageEvent):
        message_str = str(getattr(event, "message_str", "") or "").strip()
        extra_requirement = ""
        for cmd in ("定制日程", "life_custom", "dayflow_custom"):
            if message_str.startswith(cmd):
                rest = message_str[len(cmd):].strip()
                if rest:
                    extra_requirement = rest
                break
        if not extra_requirement:
            yield event.plain_result("请提供定制要求，例如：/定制日程 今天穿洛丽塔，下午约会")
            return

        persona_ctx = await self.service.resolve_persona_context(event=event)
        persona_name = persona_ctx["persona_name"]
        persona_desc = persona_ctx["persona_desc"]
        if not self.service.is_persona_configured(persona_name, persona_ctx.get("persona_id")):
            yield event.plain_result(f"当前人格未在 Dayflow 中启用：{persona_name}")
            return
        store_key = self.service.normalize_persona_key(persona_name, persona_ctx.get("persona_id"))
        async for result in self._generate_for_event(
            event, persona_name, persona_desc, store_key,
            force_regenerate=True, extra_requirement=extra_requirement,
        ):
            yield result

    @filter.command("查看人格日程", alias={"life_personas", "dayflow_personas"})
    async def dayflow_personas(self, event: AstrMessageEvent):
        lines = self.service.describe_personas()
        yield event.plain_result("已启用人格：\n" + "\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("dayflow_debug", alias={"查看日程调试", "日程调试"})
    async def dayflow_debug(self, event: AstrMessageEvent):
        snapshot = await self.service.build_debug_snapshot(event=event)
        yield event.plain_result(
            "[dayflow debug]\n"
            f"persona_name: {snapshot.get('persona_name', '')}\n"
            f"persona_source: {snapshot.get('persona_source', '')}\n"
            f"persona_desc_preview:\n{snapshot.get('persona_desc_preview', '')}\n\n"
            f"variation_configured: {snapshot.get('variation_configured', '')}\n"
            f"variation_effective: {snapshot.get('variation_effective', '')}\n"
            f"variation_definition: {snapshot.get('variation_definition', '')}\n\n"
            f"outfit_styles_pool: {snapshot.get('outfit_styles_pool', [])}\n"
            f"schedule_main_types_pool: {snapshot.get('schedule_main_types_pool', [])}\n"
            f"core_event_drivers_pool: {snapshot.get('core_event_drivers_pool', [])}\n"
            f"today_weather_pool: {snapshot.get('today_weather_pool', [])}\n\n"
            f"style_research_cache_hit: {snapshot.get('style_research_cache_hit', '')}\n"
            f"style_research_query_preview:\n{snapshot.get('style_research_query_preview', '')}\n\n"
            f"style_research_raw_response_preview:\n{snapshot.get('style_research_raw_response_preview', '')}\n\n"
            f"style_research_payload_preview:\n{snapshot.get('style_research_payload_preview', '')}\n\n"
            f"style_research_sources_preview:\n{snapshot.get('style_research_sources_preview', '')}\n\n"
            f"recent_chats_preview:\n{snapshot.get('recent_chats_preview', '')}\n\n"
            f"recent_diaries_preview:\n{snapshot.get('recent_diaries_preview', '')}\n\n"
            f"rendered_prompt_preview:\n{snapshot.get('rendered_prompt_preview', '')}"
        )
