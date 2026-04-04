import datetime

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .core.service import DayflowService


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

    async def get_life_context(self, session_id: str | None = None, persona_name: str | None = None) -> dict:
        return await self.service.get_life_context(session_id=session_id, persona_name=persona_name)

    async def _generate_for_event(self, event: AstrMessageEvent, persona_name: str, persona_desc: str, store_key: str, force_regenerate: bool = False):
        if not self.service.is_persona_configured(persona_name):
            yield event.plain_result(f"当前人格未在 Dayflow 中启用：{persona_name}")
            return

        today = datetime.datetime.now().strftime("%Y-%m-%d")

        existing_before_lock = self.service.store.get_schedule_for_date(store_key, today)
        if existing_before_lock and not existing_before_lock.get("meta", {}).get("error") and not force_regenerate:
            yield event.plain_result(
                f"🧠 人格：{persona_name}\n👕 今日穿搭：{existing_before_lock['outfit']}\n📝 日程安排：\n{existing_before_lock['schedule']}"
            )
            return

        ok = await self.service.enter_generation(store_key)
        if not ok:
            existing_while_busy = self.service.store.get_schedule_for_date(store_key, today)
            if existing_while_busy and not existing_while_busy.get("meta", {}).get("error") and not force_regenerate:
                yield event.plain_result(
                    f"🧠 人格：{persona_name}\n👕 今日穿搭：{existing_while_busy['outfit']}\n📝 日程安排：\n{existing_while_busy['schedule']}"
                )
            else:
                yield event.plain_result(f"当前人格 {persona_name} 已有生成任务在进行中，请稍后再试。")
            return

        try:
            existing_after_lock = self.service.store.get_schedule_for_date(store_key, today)
            if existing_after_lock and not existing_after_lock.get("meta", {}).get("error") and not force_regenerate:
                yield event.plain_result(
                    f"🧠 人格：{persona_name}\n👕 今日穿搭：{existing_after_lock['outfit']}\n📝 日程安排：\n{existing_after_lock['schedule']}"
                )
                return

            if force_regenerate:
                yield event.plain_result(f"🪄 正在为 {persona_name} 强制重生成今日日程，请稍候...")
            else:
                yield event.plain_result(f"🪄 正在为 {persona_name} 生成今日日程，请稍候...")
            data = await self.service.generate_schedule(event=event, persona_name=store_key, persona_desc=persona_desc)
            if data.get("meta", {}).get("error"):
                yield event.plain_result(f"⚠️ 生成失败：{data.get('memo', '未知错误')}")
                return
            self.service.save_generated(store_key, data)
            if force_regenerate:
                yield event.plain_result(
                    f"✅ 已强制重生成 {persona_name} 的今日日程\n👕 今日穿搭：{data['outfit']}\n📝 日程安排：\n{data['schedule']}"
                )
            else:
                yield event.plain_result(
                    f"✅ 已生成 {persona_name} 的今日日程\n👕 今日穿搭：{data['outfit']}\n📝 日程安排：\n{data['schedule']}"
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
        today_schedule = self.service.store.get_schedule_for_date(store_key, today)

        if today_schedule and not today_schedule.get("meta", {}).get("error"):
            yield event.plain_result(
                f"🧠 人格：{persona_name}\n👕 今日穿搭：{today_schedule['outfit']}\n📝 日程安排：\n{today_schedule['schedule']}"
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

    @filter.command("查看人格日程", alias={"life_personas", "dayflow_personas"})
    async def dayflow_personas(self, event: AstrMessageEvent):
        lines = self.service.describe_personas()
        yield event.plain_result("已启用人格：\n" + "\n".join(lines))

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
            f"history_structure_summary:\n{snapshot.get('history_structure_summary', '')}\n\n"
            f"recent_chats_preview:\n{snapshot.get('recent_chats_preview', '')}\n\n"
            f"recent_diaries_preview:\n{snapshot.get('recent_diaries_preview', '')}\n\n"
            f"rendered_prompt_preview:\n{snapshot.get('rendered_prompt_preview', '')}"
        )
