import datetime
import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image as ImageComponent, Plain
from astrbot.api.star import Context, Star
from astrbot.core.provider.entities import ProviderRequest

from .core.generator import (
    is_schedule_valid,
    render_schedule_display,
)
from .core.service import DayflowService

DAYFLOW_INJECTION_HEADER = "<DayFlow-Schedule>"
DAYFLOW_INJECTION_FOOTER = "</DayFlow-Schedule>"
_INJECTION_PATTERN = re.compile(
    re.escape(DAYFLOW_INJECTION_HEADER) + r".*?" + re.escape(DAYFLOW_INJECTION_FOOTER),
    flags=re.DOTALL,
)

PRESENCE_INJECTION_HEADER = "<DayFlow-Presence>"
PRESENCE_INJECTION_FOOTER = "</DayFlow-Presence>"
_PRESENCE_PATTERN = re.compile(
    re.escape(PRESENCE_INJECTION_HEADER) + r".*?" + re.escape(PRESENCE_INJECTION_FOOTER),
    flags=re.DOTALL,
)

SUB_ACTIVITY_INJECTION_HEADER = "<DayFlow-Current-Activity>"
SUB_ACTIVITY_INJECTION_FOOTER = "</DayFlow-Current-Activity>"
_SUB_ACTIVITY_PATTERN = re.compile(
    re.escape(SUB_ACTIVITY_INJECTION_HEADER) + r".*?" + re.escape(SUB_ACTIVITY_INJECTION_FOOTER),
    flags=re.DOTALL,
)


def _build_injection_text(data: dict) -> str | None:
    if not is_schedule_valid(data):
        return None
    outfit = str(data.get("outfit") or "").strip()
    schedule = str(data.get("schedule") or "").strip()
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


def _build_presence_injection_text(body: str) -> str:
    return f"{PRESENCE_INJECTION_HEADER}\n{body}\n{PRESENCE_INJECTION_FOOTER}"


def _remove_presence_injection(system_prompt: str | None) -> tuple[str, bool]:
    if not system_prompt:
        return system_prompt or "", False
    cleaned = _PRESENCE_PATTERN.sub("", system_prompt)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, cleaned != system_prompt


def _build_sub_activity_injection_text(body: str) -> str:
    return f"{SUB_ACTIVITY_INJECTION_HEADER}\n{body}\n{SUB_ACTIVITY_INJECTION_FOOTER}"


def _remove_sub_activity_injection(system_prompt: str | None) -> tuple[str, bool]:
    if not system_prompt:
        return system_prompt or "", False
    cleaned = _SUB_ACTIVITY_PATTERN.sub("", system_prompt)
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

            resolved_ctx = await self.service._resolve_persona_context_internal(session_id=session_id)
            persona_cfg = self.service.get_persona_config(resolved_ctx.get("persona_name"), resolved_ctx.get("persona_id"))
            presence_level = int(persona_cfg.get("presence_injection_level", 2)) if persona_cfg else 2
            presence_interval = int(persona_cfg.get("presence_min_interval_minutes", 0)) if persona_cfg else 0

            presence_body = self.service.build_presence_injection(
                session_id, level=presence_level, min_interval=presence_interval,
            )
            if presence_body is not None:
                req.system_prompt, _ = _remove_presence_injection(req.system_prompt)
                presence_injection = _build_presence_injection_text(presence_body)
                req.system_prompt += f"\n\n{presence_injection}"
                logger.info(f"[dayflow] 已注入存在感: session={session_id}, level={presence_level}")

            today = datetime.datetime.now().strftime("%Y-%m-%d")
            data = await self.service.get_life_context(session_id=session_id, target_date=today)

            injection = _build_injection_text(data)
            if injection:
                req.system_prompt += f"\n\n{injection}"
                logger.info(f"[dayflow] 已注入日程: {today}")

            persona_name_for_sub = resolved_ctx.get("persona_name") if resolved_ctx else None
            if persona_name_for_sub:
                sub_activity = self.service.build_current_sub_activity_injection(persona_name_for_sub)
                if sub_activity:
                    req.system_prompt, _ = _remove_sub_activity_injection(req.system_prompt)
                    sub_injection = _build_sub_activity_injection_text(sub_activity)
                    req.system_prompt += f"\n\n{sub_injection}"
                    logger.info(f"[dayflow] 已注入细分活动: persona={persona_name_for_sub}")
        except Exception as e:
            logger.warning(f"[dayflow] on_llm_request 注入失败: {e}")

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp):
        try:
            session_id = getattr(event, "unified_msg_origin", None)
            if session_id and resp and getattr(resp, "completion_text", None):
                self.service.record_interaction(session_id)
        except Exception as e:
            logger.debug(f"[dayflow] on_llm_response 处理失败: {e}")

    def _should_send_image(self, persona_name: str) -> bool:
        persona = self.service.get_persona_config(persona_name)
        if persona is None:
            persona = self.service.cfg.find_persona(persona_name)
        return bool(persona.get("push_image_enabled", False)) if persona else False

    async def _send_schedule_result(self, event: AstrMessageEvent, persona_name: str, data: dict):
        text = render_schedule_display(data)
        if self._should_send_image(persona_name):
            image_bytes = await self.service._render_push_image(data, persona_name)
            if image_bytes is not None:
                logger.info(f"[dayflow] 图片渲染成功，同时输出文字日志: {persona_name}")
                logger.info(f"[dayflow] 日程内容:\n{text}")
                yield event.chain_result([ImageComponent.fromBytes(image_bytes)])
                return
        yield event.plain_result(text)

    async def _generate_for_event(self, event: AstrMessageEvent, persona_name: str, persona_desc: str, store_key: str, force_regenerate: bool = False, extra_requirement: str | None = None, reset_randoms: bool = False):
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
        if is_schedule_valid(existing_before_lock) and not force_regenerate:
            async for result in self._send_schedule_result(event, persona_name, existing_before_lock):
                yield result
            return

        ok = await self.service.enter_generation(store_key)
        if not ok:
            existing_while_busy = await self.service.get_life_context(
                session_id=session_id,
                persona_name=persona_name,
                target_date=today,
            )
            if is_schedule_valid(existing_while_busy) and not force_regenerate:
                async for result in self._send_schedule_result(event, persona_name, existing_while_busy):
                    yield result
            else:
                yield event.plain_result(f"当前人格 {persona_name} 已有生成任务在进行中，请稍后再试。")
            return

        existing_after_lock = await self.service.get_life_context(
            session_id=session_id,
            persona_name=persona_name,
            target_date=today,
        )
        if is_schedule_valid(existing_after_lock) and not force_regenerate:
            await self.service.exit_generation(store_key)
            async for result in self._send_schedule_result(event, persona_name, existing_after_lock):
                yield result
            return

        if extra_requirement:
            yield event.plain_result(f"🪄 正在为 {persona_name} 定制今日日程，请稍候...")
        elif force_regenerate:
            yield event.plain_result(f"🪄 正在为 {persona_name} 强制重生成今日日程，请稍候...")
        else:
            yield event.plain_result(f"🪄 正在为 {persona_name} 生成今日日程，请稍候...")

        collected: list = []
        try:
            data = await self.service.generate_schedule(event=event, persona_name=store_key, persona_desc=persona_desc, target_date=today, auto_retry=True, extra_requirement=extra_requirement, force_regenerate=reset_randoms)
            if data.get("meta", {}).get("error"):
                collected.append(event.plain_result(f"⚠️ 生成失败：{data.get('memo', '未知错误')}"))
            else:
                persona_cfg = self.service.get_persona_config(persona_name)
                enable_subdivision = bool((persona_cfg or {}).get("enable_subdivision", False))
                if enable_subdivision:
                    sub_events = await self.service._generate_subdivision(
                        result=data,
                        persona_name=persona_name,
                        persona_desc=persona_desc,
                        persona=persona_cfg or {},
                        event=event,
                    )
                    if sub_events:
                        data["sub_events"] = sub_events
                await self.service.save_generated(store_key, data)
                current_session = getattr(event, "unified_msg_origin", None)
                await self.service.push_schedule_to_targets(store_key, data, exclude_umo=current_session)
                async for result in self._send_schedule_result(event, persona_name, data):
                    collected.append(result)
        finally:
            await self.service.exit_generation(store_key)

        for result in collected:
            yield result

    @filter.command("今日日程", alias={"life_today", "dayflow_today"})
    async def dayflow_today(self, event: AstrMessageEvent):
        persona_ctx = await self.service.resolve_persona_context(event=event)
        persona_name = persona_ctx["persona_name"]
        if not self.service.is_persona_configured(persona_name, persona_ctx.get("persona_id")):
            yield event.plain_result(f"当前人格未在 Dayflow 中启用：{persona_name}")
            return

        today = datetime.datetime.now().strftime("%Y-%m-%d")
        today_schedule = await self.service.get_life_context(
            session_id=getattr(event, "unified_msg_origin", None),
            persona_name=persona_name,
            target_date=today,
        )

        if is_schedule_valid(today_schedule):
            async for result in self._send_schedule_result(event, persona_name, today_schedule):
                yield result
        else:
            yield event.plain_result(f"📅 {persona_name} 今天还没有日程，可以使用 /生成日程 来生成。")

    @filter.command("生成日程", alias={"life_renew", "dayflow_gen"})
    async def dayflow_generate(self, event: AstrMessageEvent):
        persona_ctx = await self.service.resolve_persona_context(event=event)
        persona_name = persona_ctx["persona_name"]
        persona_desc = persona_ctx["persona_desc"]
        if not self.service.is_persona_configured(persona_name, persona_ctx.get("persona_id")):
            yield event.plain_result(f"当前人格未在 Dayflow 中启用：{persona_name}")
            return
        store_key = self.service.normalize_persona_key(persona_name, persona_ctx.get("persona_id"))
        async for result in self._generate_for_event(event, persona_name, persona_desc, store_key, force_regenerate=True, reset_randoms=False):
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
            reset_randoms=True,
        ):
            yield result

    @filter.command("明日日程", alias={"life_tomorrow", "dayflow_tomorrow"})
    async def dayflow_tomorrow(self, event: AstrMessageEvent):
        message_str = str(getattr(event, "message_str", "") or "").strip()
        requirement = ""
        for cmd in ("明日日程", "life_tomorrow", "dayflow_tomorrow"):
            if message_str.startswith(cmd):
                rest = message_str[len(cmd):].strip()
                if rest:
                    requirement = rest
                break
        if not requirement:
            yield event.plain_result("请提供定制要求，例如：/明日日程 穿洛丽塔，下午约会")
            return

        persona_ctx = await self.service.resolve_persona_context(event=event)
        persona_name = persona_ctx["persona_name"]
        if not self.service.is_persona_configured(persona_name, persona_ctx.get("persona_id")):
            yield event.plain_result(f"当前人格未在 Dayflow 中启用：{persona_name}")
            return
        store_key = self.service.normalize_persona_key(persona_name, persona_ctx.get("persona_id"))
        self.service.set_tomorrow_custom_request(store_key, requirement)
        tomorrow = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        yield event.plain_result(f"✅ 已为 {persona_name} 设置明日（{tomorrow}）定制日程要求：{requirement}")

    @filter.command("取消明日日程", alias={"life_cancel_tomorrow", "dayflow_cancel_tomorrow"})
    async def dayflow_cancel_tomorrow(self, event: AstrMessageEvent):
        persona_ctx = await self.service.resolve_persona_context(event=event)
        persona_name = persona_ctx["persona_name"]
        if not self.service.is_persona_configured(persona_name, persona_ctx.get("persona_id")):
            yield event.plain_result(f"当前人格未在 Dayflow 中启用：{persona_name}")
            return
        store_key = self.service.normalize_persona_key(persona_name, persona_ctx.get("persona_id"))
        if self.service.clear_tomorrow_custom_request(store_key):
            yield event.plain_result(f"✅ 已取消 {persona_name} 的明日定制日程要求")
        else:
            yield event.plain_result(f"ℹ️ {persona_name} 没有设置明日定制日程要求")

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
            f"location: {snapshot.get('location', '')}\n"
            f"variation_configured: {snapshot.get('variation_configured', '')}\n"
            f"variation_effective: {snapshot.get('variation_effective', '')}\n"
            f"variation_definition: {snapshot.get('variation_definition', '')}\n\n"
            f"outfit_styles_pool: {snapshot.get('outfit_styles_pool', [])}\n"
            f"schedule_main_types_pool: {snapshot.get('schedule_main_types_pool', [])}\n"
            f"core_event_drivers_pool: {snapshot.get('core_event_drivers_pool', [])}\n"
            f"today_weather_pool: {snapshot.get('today_weather_pool', [])}\n\n"
            f"style_research_cache_hit: {snapshot.get('style_research_cache_hit', '')}\n"
            f"style_research_weather: {snapshot.get('style_research_weather', '')}\n"
            f"style_research_query_preview:\n{snapshot.get('style_research_query_preview', '')}\n\n"
            f"style_research_raw_response_preview:\n{snapshot.get('style_research_raw_response_preview', '')}\n\n"
            f"style_research_payload_preview:\n{snapshot.get('style_research_payload_preview', '')}\n\n"
            f"style_research_sources_preview:\n{snapshot.get('style_research_sources_preview', '')}\n\n"
            f"recent_chats_preview:\n{snapshot.get('recent_chats_preview', '')}\n\n"
            f"recent_diaries_preview:\n{snapshot.get('recent_diaries_preview', '')}\n\n"
            f"rendered_prompt_preview:\n{snapshot.get('rendered_prompt_preview', '')}"
        )
