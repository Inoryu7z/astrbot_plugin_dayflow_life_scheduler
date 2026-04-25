"""
日程图片渲染模块
清新自然风 — 柔和、温暖、有呼吸感的设计
"""

import datetime
import io
import os
import re
from pathlib import Path
from typing import Optional

from astrbot.api import logger

try:
    from PIL import Image, ImageDraw, ImageFont
    import PIL
    _PILLOW_VERSION = tuple(int(x) for x in PIL.__version__.split(".")[:2])
    HAS_PILLOW = _PILLOW_VERSION >= (8, 2)
    if not HAS_PILLOW:
        logger.warning(f"[ScheduleRenderer] Pillow 版本过低 ({PIL.__version__})，需要 >= 8.2.0，图片渲染不可用")
except ImportError:
    HAS_PILLOW = False


class ScheduleRenderer:
    """日程图片渲染器 — 清新自然风"""

    FONT_DOWNLOAD_URLS = [
        "https://cdn.jsdelivr.net/gh/AkisAya/NotoSerifSC-Regular@main/NotoSerifSC-Regular.ttf",
        "https://github.com/AkisAya/NotoSerifSC-Regular/raw/main/NotoSerifSC-Regular.ttf",
        "https://mirror.ghproxy.com/https://github.com/AkisAya/NotoSerifSC-Regular/raw/main/NotoSerifSC-Regular.ttf",
    ]
    FONT_FILENAME = "NotoSerifSC-Regular.ttf"

    EMOJI_FONT_URLS = [
        "https://cdn.jsdelivr.net/gh/googlefonts/noto-emoji@main/fonts/NotoColorEmoji.ttf",
        "https://github.com/googlefonts/noto-emoji/raw/main/fonts/NotoColorEmoji.ttf",
        "https://mirror.ghproxy.com/https://github.com/googlefonts/noto-emoji/raw/main/fonts/NotoColorEmoji.ttf",
    ]
    EMOJI_FONT_FILENAME = "NotoColorEmoji.ttf"

    # 配色方案：柔粉 + 鼠尾草绿双色系（参考 Wedding/Event Planning 配色理念）
    # 主卡片：柔粉色系 — 温暖、优雅、女性化
    BG_TOP = (255, 248, 250)        # 浅粉白背景
    BG_BOTTOM = (250, 240, 245)     # 淡粉背景
    CARD_BG = (255, 252, 253)       # 卡片粉白
    TEXT_PRIMARY = (80, 40, 60)     # 深玫红标题
    TEXT_SECONDARY = (120, 90, 105) # 灰粉正文
    TEXT_MUTED = (180, 150, 165)    # 淡粉副文字
    ACCENT = (212, 175, 55)         # 香槟金强调
    ACCENT_LIGHT = (250, 235, 200)  # 浅金装饰
    TIMELINE_DOT = (212, 175, 55)   # 香槟金圆点
    TIMELINE_LINE = (235, 220, 210) # 暖粉线
    DIVIDER = (240, 225, 230)       # 粉分隔线
    FOOTER_TEXT = (190, 170, 175)   # 暖粉灰文字
    # 换装气泡：鼠尾草绿色系，与柔粉形成优雅对比
    OUTFIT_BG = (245, 250, 245)     # 鼠尾草绿背景
    OUTFIT_BORDER = (150, 190, 150) # 鼠尾草绿边框
    OUTFIT_TEXT = (60, 100, 60)     # 深绿文字
    OUTFIT_BADGE = (100, 150, 100)  # 鼠尾草绿标签

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir) if isinstance(data_dir, str) else data_dir
        self.fonts_dir = self.data_dir / "fonts"
        self._title_font: Optional[ImageFont.FreeTypeFont] = None
        self._body_font: Optional[ImageFont.FreeTypeFont] = None
        self._body_bold_font: Optional[ImageFont.FreeTypeFont] = None
        self._time_big_font: Optional[ImageFont.FreeTypeFont] = None
        self._time_small_font: Optional[ImageFont.FreeTypeFont] = None
        self._small_font: Optional[ImageFont.FreeTypeFont] = None
        self._emoji_font: Optional[ImageFont.FreeTypeFont] = None
        self._emoji_font_size: int = 109
        self._initialized = False

    def _ensure_fonts(self) -> bool:
        if not HAS_PILLOW:
            logger.error("[ScheduleRenderer] Pillow 未安装，无法渲染日程图片")
            return False

        if self._initialized:
            return self._title_font is not None

        font_path = self._find_or_download_font()
        if not font_path:
            logger.error("[ScheduleRenderer] 无法获取字体文件，日程图片渲染不可用")
            return False

        try:
            self._title_font = ImageFont.truetype(str(font_path), 28)
            self._body_font = ImageFont.truetype(str(font_path), 17)
            self._body_bold_font = ImageFont.truetype(str(font_path), 18)
            self._time_big_font = ImageFont.truetype(str(font_path), 20)
            self._time_small_font = ImageFont.truetype(str(font_path), 13)
            self._small_font = ImageFont.truetype(str(font_path), 13)
            self._initialized = True

            emoji_font_path = self._find_or_download_emoji_font()
            if emoji_font_path:
                try:
                    self._emoji_font = ImageFont.truetype(str(emoji_font_path), self._emoji_font_size)
                    logger.info("[ScheduleRenderer] Emoji 字体加载成功")
                except Exception as e:
                    logger.warning(f"[ScheduleRenderer] Emoji 字体加载失败: {e}")

            return True
        except Exception as e:
            logger.error(f"[ScheduleRenderer] 加载字体失败: {e}")
            return False

    def _find_or_download_font(self) -> Optional[Path]:
        cached = self.fonts_dir / self.FONT_FILENAME
        if cached.exists() and cached.stat().st_size > 100_000:
            return cached

        system_font = self._find_system_font()
        if system_font:
            return system_font

        return self._download_font()

    def _find_system_font(self) -> Optional[Path]:
        candidates = []

        if os.name == "nt":
            windir = os.environ.get("WINDIR", r"C:\Windows")
            font_dir = Path(windir) / "Fonts"
            candidates = [
                font_dir / "msyh.ttc",
                font_dir / "msyhbd.ttc",
                font_dir / "simhei.ttf",
                font_dir / "simsun.ttc",
                font_dir / "simfang.ttf",
            ]
        else:
            candidates = [
                Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
                Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"),
                Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
                Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"),
                Path("/usr/share/fonts/noto-cjk/NotoSerifCJK-Regular.ttc"),
                Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
                Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
                Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
            ]

        for path in candidates:
            if path.exists() and path.stat().st_size > 100_000:
                logger.info(f"[ScheduleRenderer] 使用系统字体: {path}")
                return path

        return None

    def _download_font(self) -> Optional[Path]:
        import urllib.request
        self.fonts_dir.mkdir(parents=True, exist_ok=True)
        target = self.fonts_dir / self.FONT_FILENAME

        for url in self.FONT_DOWNLOAD_URLS:
            tmp = target.with_suffix(".tmp")
            try:
                logger.info(f"[ScheduleRenderer] 正在下载字体: {url}")
                urllib.request.urlretrieve(url, str(tmp))

                if tmp.exists() and tmp.stat().st_size > 100_000:
                    tmp.replace(target)
                    logger.info(f"[ScheduleRenderer] 字体下载完成: {target}")
                    return target
                else:
                    tmp.unlink(missing_ok=True)
                    logger.warning(f"[ScheduleRenderer] 下载的字体文件过小，尝试下一个源: {url}")
            except Exception as e:
                tmp.unlink(missing_ok=True)
                logger.warning(f"[ScheduleRenderer] 从 {url} 下载字体失败: {e}")

        logger.error("[ScheduleRenderer] 所有字体下载源均失败")
        return None

    def _find_or_download_emoji_font(self) -> Optional[Path]:
        cached = self.fonts_dir / self.EMOJI_FONT_FILENAME
        if cached.exists() and cached.stat().st_size > 1_000_000:
            return cached

        system_emoji = self._find_system_emoji_font()
        if system_emoji:
            return system_emoji

        return self._download_emoji_font()

    def _find_system_emoji_font(self) -> Optional[Path]:
        candidates = []

        if os.name == "nt":
            windir = os.environ.get("WINDIR", r"C:\Windows")
            font_dir = Path(windir) / "Fonts"
            candidates = [
                font_dir / "seguiemj.ttf",
                font_dir / "Segoe UI Emoji.ttf",
            ]
        else:
            candidates = [
                Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"),
                Path("/usr/share/fonts/opentype/noto/NotoColorEmoji.ttf"),
                Path("/usr/share/fonts/noto/NotoColorEmoji.ttf"),
            ]

        for path in candidates:
            if path.exists():
                logger.info(f"[ScheduleRenderer] 使用系统 Emoji 字体: {path}")
                return path

        return None

    def _download_emoji_font(self) -> Optional[Path]:
        import urllib.request
        self.fonts_dir.mkdir(parents=True, exist_ok=True)
        target = self.fonts_dir / self.EMOJI_FONT_FILENAME

        for url in self.EMOJI_FONT_URLS:
            tmp = target.with_suffix(".tmp")
            try:
                logger.info(f"[ScheduleRenderer] 正在下载 Emoji 字体: {url}")
                urllib.request.urlretrieve(url, str(tmp))

                if tmp.exists() and tmp.stat().st_size > 1_000_000:
                    tmp.replace(target)
                    logger.info(f"[ScheduleRenderer] Emoji 字体下载完成: {target}")
                    return target
                else:
                    tmp.unlink(missing_ok=True)
                    logger.warning(f"[ScheduleRenderer] 下载的 Emoji 字体文件过小，尝试下一个源")
            except Exception as e:
                tmp.unlink(missing_ok=True)
                logger.warning(f"[ScheduleRenderer] 从 {url} 下载 Emoji 字体失败: {e}")

        logger.warning("[ScheduleRenderer] 所有 Emoji 字体下载源均失败，将使用纯文本替代")
        return None

    def render(self, data: dict, date_str: str = "", persona_name: str = "") -> Optional[bytes]:
        if not self._ensure_fonts():
            return None

        try:
            timeline = data.get("timeline")
            if isinstance(timeline, list) and timeline:
                items = self._build_timeline_items(timeline)
            else:
                schedule_text = str(data.get("schedule") or "").strip()
                items = self._parse_schedule_text(schedule_text)

            if not items:
                return None

            img_width = 640
            padding_x = 40
            padding_top = 50
            padding_bottom = 50
            card_gap = 16
            timeline_x = padding_x + 20

            header_height = self._calc_header_height(date_str, persona_name, img_width, padding_x)
            footer_height = 50

            # 计算每个条目的高度（包括主卡片和换装气泡）
            item_layouts = []
            for item in items:
                main_h, outfit_h = self._calc_item_height(item, img_width, padding_x, timeline_x)
                item_layouts.append((main_h, outfit_h))

            content_height = 0
            for main_h, outfit_h in item_layouts:
                content_height += main_h
                if outfit_h > 0:
                    content_height += 8 + outfit_h
            content_height += card_gap * (len(items) - 1)

            img_height = padding_top + header_height + content_height + footer_height + padding_bottom
            img_height = max(img_height, 500)

            img = self._create_background(img_width, img_height)
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            draw = ImageDraw.Draw(img)

            y = padding_top
            y = self._draw_header(draw, img, date_str, persona_name, y, img_width, padding_x)
            y += 16

            for i, item in enumerate(items):
                main_h, outfit_h = item_layouts[i]
                self._draw_timeline_item(draw, img, item, y, main_h, img_width, padding_x, timeline_x)
                y += main_h
                if outfit_h > 0:
                    y += 8
                    self._draw_outfit_bubble(draw, img, item, y, outfit_h, img_width, padding_x, timeline_x)
                    y += outfit_h
                y += card_gap

            self._draw_footer(draw, img, img_height, img_width, padding_x)

            buf = io.BytesIO()
            img = img.convert("RGB")
            img.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
        except Exception as e:
            logger.error(f"[ScheduleRenderer] 渲染日程图片失败: {e}", exc_info=True)
            return None

    def _build_timeline_items(self, timeline: list) -> list[dict]:
        items = []
        for item in timeline:
            if not isinstance(item, dict):
                continue
            time_start = str(item.get("time_start") or "").strip()
            time_end = str(item.get("time_end") or "").strip()
            title = str(item.get("title") or "").strip()
            detail = str(item.get("detail") or "").strip()
            outfit_change = str(item.get("outfit_change") or "").strip()
            time_range = f"{time_start} - {time_end}" if time_start and time_end else time_start or time_end or ""
            if not title and not detail:
                continue
            items.append({
                "time": time_range,
                "title": title,
                "detail": detail,
                "outfit_change": outfit_change,
            })
        return items

    def _parse_schedule_text(self, text: str) -> list[dict]:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        items = []
        current = {"time": "", "title": "", "detail": "", "outfit_change": ""}
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d{2}:\d{2})\s*[-—~～]\s*(\d{2}:\d{2})\s*[|｜]\s*(.+)$", line)
            if m:
                if current["title"] or current["detail"]:
                    items.append(current)
                current = {"time": f"{m.group(1)} - {m.group(2)}", "title": m.group(3), "detail": "", "outfit_change": ""}
                continue
            m2 = re.match(r"^(\d{2}:\d{2})\s*[-—~～]?\s*(\d{2}:\d{2})?\s+(.+)$", line)
            if m2:
                if current["title"] or current["detail"]:
                    items.append(current)
                t1 = m2.group(1) or ""
                t2 = m2.group(2) or ""
                current = {"time": f"{t1} - {t2}" if t2 else t1, "title": m2.group(3), "detail": "", "outfit_change": ""}
                continue
            if line.startswith("换装") or line.startswith("👗"):
                current["outfit_change"] = re.sub(r'^👗\s*换装[：:]\s*', '', line).strip()
                current["outfit_change"] = re.sub(r'^换装[：:]\s*', '', current["outfit_change"]).strip()
                continue
            if current["title"] and not current["detail"]:
                current["detail"] = line
            elif current["detail"]:
                current["detail"] += "\n" + line
            else:
                current["title"] = line
        if current["title"] or current["detail"]:
            items.append(current)
        return items

    def _calc_header_height(self, date_str: str, persona_name: str, img_width: int, padding_x: int) -> int:
        height = 30
        if date_str:
            height += 36
        if persona_name:
            height += 28
        return height

    def _calc_item_height(self, item: dict, img_width: int, padding_x: int, timeline_x: int) -> tuple[int, int]:
        card_padding = 16
        card_x = timeline_x + 20
        card_width = img_width - padding_x - card_x
        time_area_width = 80
        content_width = card_width - time_area_width - 12 - card_padding

        # 主卡片高度
        main_height = card_padding * 2

        title = item.get("title") or ""
        if title:
            title_lines = self._wrap_text(title, content_width, self._body_bold_font)
            main_height += len(title_lines) * 28

        detail = item.get("detail") or ""
        if detail:
            if title:
                main_height += 8
            detail_lines = self._wrap_text(detail, content_width, self._body_font)
            main_height += len(detail_lines) * 24

        main_height = max(main_height, 70)

        # 换装气泡高度（标签占 24px 空间）
        outfit_change = item.get("outfit_change") or ""
        outfit_height = 0
        if outfit_change:
            outfit_padding = 12
            desc_width = card_width - outfit_padding * 2
            outfit_lines = self._wrap_text(outfit_change, desc_width, self._small_font)
            # 标签高度 24px + 标签与内容间距 4px
            outfit_height = outfit_padding + 24 + 4 + len(outfit_lines) * 20 + outfit_padding

        return main_height, outfit_height

    def _create_background(self, width: int, height: int) -> Image.Image:
        img = Image.new("RGB", (width, height), self.BG_TOP)
        draw = ImageDraw.Draw(img)

        step = max(1, height // 64)
        for y in range(0, height, step):
            ratio = y / height
            r = int(self.BG_TOP[0] * (1 - ratio) + self.BG_BOTTOM[0] * ratio)
            g = int(self.BG_TOP[1] * (1 - ratio) + self.BG_BOTTOM[1] * ratio)
            b = int(self.BG_TOP[2] * (1 - ratio) + self.BG_BOTTOM[2] * ratio)
            draw.rectangle([(0, y), (width, min(y + step, height) - 1)], fill=(r, g, b))

        return img

    def _draw_header(self, draw: ImageDraw.Draw, img: Image.Image, date_str: str, persona_name: str, y: int, img_width: int, padding_x: int) -> int:
        if date_str:
            title_text = self._format_date_title(date_str)
            bbox = draw.textbbox((0, 0), title_text, font=self._title_font)
            tw = bbox[2] - bbox[0]
            tx = (img_width - tw) // 2
            self._draw_text_with_emoji(draw, (tx, y), title_text, fill=self.TEXT_PRIMARY, font=self._title_font, img=img)
            y += 36

        if persona_name:
            sub_text = f"{persona_name} 的一日"
            bbox_s = draw.textbbox((0, 0), sub_text, font=self._time_small_font)
            sw = bbox_s[2] - bbox_s[0]
            sx = (img_width - sw) // 2
            self._draw_text_with_emoji(draw, (sx, y), sub_text, fill=self.TEXT_SECONDARY, font=self._time_small_font, img=img)
            y += 26

        line_y = y + 4
        center_x = img_width // 2
        draw.line([(padding_x + 30, line_y), (center_x - 25, line_y)], fill=self.DIVIDER, width=1)
        draw.line([(center_x + 25, line_y), (img_width - padding_x - 30, line_y)], fill=self.DIVIDER, width=1)
        diamond_size = 4
        draw.polygon([
            (center_x, line_y - diamond_size),
            (center_x + diamond_size, line_y),
            (center_x, line_y + diamond_size),
            (center_x - diamond_size, line_y),
        ], fill=self.ACCENT)
        y += 12

        return y

    def _format_date_title(self, date_str: str) -> str:
        if not date_str:
            return datetime.datetime.now().strftime("%Y年%m月%d日")
        try:
            dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
            weekday = weekdays[dt.weekday()]
            return f"{dt.year}年{dt.month}月{dt.day}日  {weekday}"
        except Exception:
            return date_str

    def _draw_timeline_item(self, draw: ImageDraw.Draw, img: Image.Image, item: dict, y: int, item_h: int, img_width: int, padding_x: int, timeline_x: int):
        card_x = timeline_x + 20
        card_width = img_width - padding_x - card_x
        card_padding = 16

        shadow_offset = 2
        self._draw_rounded_rect(draw, card_x + shadow_offset, y + shadow_offset, card_x + card_width + shadow_offset, y + item_h + shadow_offset, radius=12, fill=(230, 235, 225))
        self._draw_rounded_rect(draw, card_x, y, card_x + card_width, y + item_h, radius=12, fill=self.CARD_BG)

        dot_y = y + item_h // 2
        draw.ellipse([(timeline_x - 6, dot_y - 6), (timeline_x + 6, dot_y + 6)], fill=(230, 240, 220))
        draw.ellipse([(timeline_x - 4, dot_y - 4), (timeline_x + 4, dot_y + 4)], fill=self.TIMELINE_DOT)

        draw.line([(timeline_x, y), (timeline_x, y + item_h)], fill=self.TIMELINE_LINE, width=1)

        time_text = item.get("time") or ""
        time_area_width = 80
        content_x = card_x + time_area_width + 12
        content_width = card_width - time_area_width - 12 - card_padding

        if time_text:
            time_parts = time_text.split(" - ")
            time_start = time_parts[0] if time_parts else time_text
            time_end = time_parts[1] if len(time_parts) > 1 else ""

            start_bbox = draw.textbbox((0, 0), time_start, font=self._time_big_font)
            start_w = start_bbox[2] - start_bbox[0]
            start_h = start_bbox[3] - start_bbox[1]

            if time_end:
                end_bbox = draw.textbbox((0, 0), time_end, font=self._time_small_font)
                end_w = end_bbox[2] - end_bbox[0]
                total_w = max(start_w, end_w)
            else:
                total_w = start_w

            time_base_x = card_x + (time_area_width - total_w) // 2

            gap = 6
            total_time_h = start_h + (start_h // 2 + gap if time_end else 0)
            time_base_y = y + (item_h - total_time_h) // 2

            self._draw_text_with_emoji(draw, (time_base_x + (total_w - start_w) // 2, time_base_y), time_start, fill=self.TEXT_PRIMARY, font=self._time_big_font, img=img)
            if time_end:
                self._draw_text_with_emoji(draw, (time_base_x + (total_w - end_w) // 2, time_base_y + start_h + gap), time_end, fill=self.TEXT_MUTED, font=self._time_small_font, img=img)

        sep_x = card_x + time_area_width
        draw.line([(sep_x, y + 12), (sep_x, y + item_h - 12)], fill=self.DIVIDER, width=1)

        content_y = y + card_padding

        title = item.get("title") or ""
        if title:
            title_lines = self._wrap_text(title, content_width, self._body_bold_font)
            for line in title_lines:
                self._draw_text_with_emoji(draw, (content_x, content_y), line, fill=(30, 40, 25), font=self._body_bold_font, img=img)
                content_y += 28

        detail = item.get("detail") or ""
        if detail:
            if title:
                content_y += 8
            detail_lines = self._wrap_text(detail, content_width, self._body_font)
            for line in detail_lines:
                self._draw_text_with_emoji(draw, (content_x, content_y), line, fill=self.TEXT_SECONDARY, font=self._body_font, img=img)
                content_y += 24

    def _draw_outfit_bubble(self, draw: ImageDraw.Draw, img: Image.Image, item: dict, y: int, outfit_h: int, img_width: int, padding_x: int, timeline_x: int):
        card_x = timeline_x + 20
        card_width = img_width - padding_x - card_x
        outfit_padding = 12

        outfit_change = item.get("outfit_change") or ""
        if not outfit_change:
            return

        self._draw_rounded_rect(draw, card_x, y, card_x + card_width, y + outfit_h, radius=10, fill=self.OUTFIT_BG)
        self._draw_dashed_rounded_rect(draw, card_x, y, card_x + card_width, y + outfit_h, radius=10, color=self.OUTFIT_BORDER, width=2)

        badge_text = "换装"
        badge_h = 20
        badge_x = card_x + outfit_padding
        badge_y = y + outfit_padding - 2
        label_bbox = self._small_font.getbbox(badge_text)
        label_w = label_bbox[2] - label_bbox[0]
        badge_w = label_w + 18
        self._draw_rounded_rect(draw, badge_x, badge_y, badge_x + badge_w, badge_y + badge_h, radius=6, fill=self.OUTFIT_BADGE)
        draw.text((badge_x + 9, badge_y + 3), badge_text, fill=(255, 255, 255), font=self._small_font)

        desc_x = card_x + outfit_padding
        desc_y = y + outfit_padding + 24
        desc_width = card_width - outfit_padding * 2
        outfit_lines = self._wrap_text(outfit_change, desc_width, self._small_font)
        for line in outfit_lines:
            self._draw_text_with_emoji(draw, (desc_x, desc_y), line, fill=self.OUTFIT_TEXT, font=self._small_font, img=img)
            desc_y += 20

    def _draw_rounded_rect(self, draw: ImageDraw.Draw, x1: int, y1: int, x2: int, y2: int, radius: int, fill: tuple):
        draw.rounded_rectangle([(x1, y1), (x2, y2)], radius=radius, fill=fill)

    def _draw_dashed_rounded_rect(self, draw: ImageDraw.Draw, x1: int, y1: int, x2: int, y2: int, radius: int, color: tuple, width: int = 2):
        dash_len = 6
        gap_len = 4

        # 上边
        self._draw_dashed_line(draw, (x1 + radius, y1), (x2 - radius, y1), color, width, dash_len, gap_len)
        # 下边
        self._draw_dashed_line(draw, (x1 + radius, y2), (x2 - radius, y2), color, width, dash_len, gap_len)
        # 左边
        self._draw_dashed_line(draw, (x1, y1 + radius), (x1, y2 - radius), color, width, dash_len, gap_len)
        # 右边
        self._draw_dashed_line(draw, (x2, y1 + radius), (x2, y2 - radius), color, width, dash_len, gap_len)
        # 四个圆角
        draw.arc((x1, y1, x1 + 2*radius, y1 + 2*radius), 180, 270, fill=color, width=width)
        draw.arc((x2 - 2*radius, y1, x2, y1 + 2*radius), 270, 360, fill=color, width=width)
        draw.arc((x1, y2 - 2*radius, x1 + 2*radius, y2), 90, 180, fill=color, width=width)
        draw.arc((x2 - 2*radius, y2 - 2*radius, x2, y2), 0, 90, fill=color, width=width)

    def _draw_dashed_line(self, draw: ImageDraw.Draw, start: tuple, end: tuple, color: tuple, width: int, dash: int, gap: int):
        x1, y1 = start
        x2, y2 = end
        dx = x2 - x1
        dy = y2 - y1
        length = (dx**2 + dy**2) ** 0.5
        if length == 0:
            return
        ux, uy = dx / length, dy / length

        pos = 0
        drawing = True
        while pos < length:
            seg_len = dash if drawing else gap
            end_pos = min(pos + seg_len, length)
            if drawing:
                sx = x1 + ux * pos
                sy = y1 + uy * pos
                ex = x1 + ux * end_pos
                ey = y1 + uy * end_pos
                draw.line([(sx, sy), (ex, ey)], fill=color, width=width)
            pos = end_pos
            drawing = not drawing

    def _draw_footer(self, draw: ImageDraw.Draw, img: Image.Image, img_height: int, img_width: int, padding_x: int):
        footer_y = img_height - 45
        brand_text = "Dayflow"
        bbox = draw.textbbox((0, 0), brand_text, font=self._small_font)
        tw = bbox[2] - bbox[0]
        self._draw_text_with_emoji(draw, ((img_width - tw) // 2, footer_y), brand_text, fill=self.FOOTER_TEXT, font=self._small_font, img=img)

        line_y = footer_y - 12
        center_x = img_width // 2
        draw.line([(padding_x + 30, line_y), (center_x - 25, line_y)], fill=self.DIVIDER, width=1)
        draw.line([(center_x + 25, line_y), (img_width - padding_x - 30, line_y)], fill=self.DIVIDER, width=1)
        diamond_size = 3
        draw.polygon([
            (center_x, line_y - diamond_size),
            (center_x + diamond_size, line_y),
            (center_x, line_y + diamond_size),
            (center_x - diamond_size, line_y),
        ], fill=self.ACCENT_LIGHT)

    def _wrap_text(self, text: str, max_width: int, font: ImageFont.FreeTypeFont) -> list[str]:
        result: list[str] = []
        for paragraph in text.split("\n"):
            current_line = ""
            for char in paragraph:
                test_line = current_line + char
                try:
                    bbox = font.getbbox(test_line)
                    w = bbox[2] - bbox[0]
                except Exception:
                    est = 18 if ord(char) > 127 else 10
                    w = len(current_line) * 14 + est
                if w > max_width:
                    if current_line:
                        result.append(current_line)
                    current_line = char
                else:
                    current_line = test_line
            if current_line:
                result.append(current_line)
        return result if result else [text] if text else [""]

    def _is_emoji(self, char: str) -> bool:
        if not char or len(char) == 0:
            return False
        if len(char) == 1 and ord(char) < 0x2000:
            return False
        if "\u200D" in char or "\uFE0F" in char:
            return True
        try:
            code_point = ord(char[0])
        except TypeError:
            return False
        emoji_ranges = [
            (0x1F000, 0x1F9FF),
            (0x1FA00, 0x1FAFF),
            (0x2600, 0x26FF),
            (0x2700, 0x27BF),
            (0x1F300, 0x1F5FF),
            (0x1F600, 0x1F64F),
            (0x1F680, 0x1F6FF),
            (0x1F900, 0x1F9FF),
        ]
        return any(start <= code_point <= end for start, end in emoji_ranges)

    def _measure_text_width(self, text: str, font: ImageFont.FreeTypeFont) -> int:
        total_w = 0
        idx = 0
        text_length = len(text)
        while idx < text_length:
            max_combo = min(6, text_length - idx)
            is_emoji_char = False
            for combo_len in range(max_combo, 0, -1):
                test_seg = text[idx:idx+combo_len]
                if self._is_emoji(test_seg):
                    is_emoji_char = True
                    if self._emoji_font:
                        try:
                            scale_ratio = font.size / self._emoji_font_size
                            emoji_bbox = self._emoji_font.getbbox(test_seg)
                            emoji_w = emoji_bbox[2] - emoji_bbox[0]
                            total_w += int(emoji_w * scale_ratio)
                        except Exception:
                            bbox = font.getbbox(test_seg)
                            total_w += (bbox[2] - bbox[0]) if bbox else font.size
                    else:
                        bbox = font.getbbox(test_seg)
                        total_w += (bbox[2] - bbox[0]) if bbox else font.size
                    idx += combo_len
                    break
            if not is_emoji_char:
                end_idx = idx
                while end_idx < text_length and not self._is_emoji(text[end_idx:end_idx+1]):
                    end_idx += 1
                text_seg = text[idx:end_idx]
                bbox = font.getbbox(text_seg)
                total_w += (bbox[2] - bbox[0]) if bbox else 0
                idx = end_idx
        return total_w

    def _draw_text_with_emoji(self, draw: ImageDraw.ImageDraw, xy: tuple, text: str,
                               fill: tuple, font: ImageFont.FreeTypeFont, img: Image.Image = None):
        x, y = xy
        if not text:
            return

        if not self._emoji_font:
            draw.text((x, y), text, fill=fill, font=font)
            return

        target_font_size = font.size
        font_bbox = font.getbbox("Ag")
        font_ascent = font_bbox[3] - font_bbox[1]

        current_x = x
        idx = 0
        text_length = len(text)

        while idx < text_length:
            max_combo = min(6, text_length - idx)
            current_emoji = ""
            for combo_len in range(max_combo, 0, -1):
                test_seg = text[idx:idx+combo_len]
                if self._is_emoji(test_seg):
                    current_emoji = test_seg
                    break

            if current_emoji:
                try:
                    scale_ratio = target_font_size / self._emoji_font_size
                    emoji_bbox = self._emoji_font.getbbox(current_emoji)
                    emoji_w = emoji_bbox[2] - emoji_bbox[0]
                    emoji_h = emoji_bbox[3] - emoji_bbox[1]
                    scaled_w = int(emoji_w * scale_ratio)
                    scaled_h = int(emoji_h * scale_ratio)
                    if scaled_w <= 0 or scaled_h <= 0:
                        raise ValueError("缩放后尺寸无效")

                    temp_canvas = Image.new("RGBA", (emoji_w, emoji_h), (0, 0, 0, 0))
                    temp_draw = ImageDraw.Draw(temp_canvas)
                    temp_draw.text(
                        (-emoji_bbox[0], -emoji_bbox[1]),
                        current_emoji,
                        fill=fill,
                        font=self._emoji_font,
                        embedded_color=True
                    )
                    scaled_emoji = temp_canvas.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
                    emoji_draw_y = y + (font_ascent - scaled_h) // 2
                    if img:
                        img.paste(scaled_emoji, (int(current_x), int(emoji_draw_y)), scaled_emoji)
                    current_x += scaled_w
                    idx += len(current_emoji)

                except Exception:
                    draw.text((current_x, y), current_emoji, fill=fill, font=font)
                    bbox = font.getbbox(current_emoji)
                    current_x += (bbox[2] - bbox[0]) if bbox else target_font_size
                    idx += len(current_emoji)
            else:
                end_idx = idx
                while end_idx < text_length and not self._is_emoji(text[end_idx:end_idx+1]):
                    end_idx += 1
                text_seg = text[idx:end_idx]
                draw.text((current_x, y), text_seg, fill=fill, font=font)
                bbox = font.getbbox(text_seg)
                current_x += (bbox[2] - bbox[0]) if bbox else 0
                idx = end_idx
