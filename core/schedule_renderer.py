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

    # 配色方案：清新自然风
    BG_TOP = (245, 250, 240)
    BG_BOTTOM = (235, 245, 230)
    CARD_BG = (255, 255, 252)
    TEXT_PRIMARY = (50, 60, 45)
    TEXT_SECONDARY = (100, 115, 90)
    TEXT_MUTED = (150, 165, 140)
    ACCENT = (120, 160, 100)
    ACCENT_LIGHT = (180, 210, 165)
    TIMELINE_DOT = (140, 180, 120)
    TIMELINE_LINE = (200, 220, 190)
    DIVIDER = (220, 230, 215)
    FOOTER_TEXT = (170, 185, 160)

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir) if isinstance(data_dir, str) else data_dir
        self.fonts_dir = self.data_dir / "fonts"
        self._title_font: Optional[ImageFont.FreeTypeFont] = None
        self._body_font: Optional[ImageFont.FreeTypeFont] = None
        self._time_font: Optional[ImageFont.FreeTypeFont] = None
        self._small_font: Optional[ImageFont.FreeTypeFont] = None
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
            self._body_font = ImageFont.truetype(str(font_path), 18)
            self._time_font = ImageFont.truetype(str(font_path), 15)
            self._small_font = ImageFont.truetype(str(font_path), 13)
            self._initialized = True
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
            padding_x = 50
            padding_top = 60
            padding_bottom = 60
            card_gap = 16
            timeline_x = padding_x + 20

            header_height = self._calc_header_height(date_str, persona_name, img_width, padding_x)
            footer_height = 50

            item_heights = []
            for item in items:
                h = self._calc_item_height(item, img_width, padding_x, timeline_x)
                item_heights.append(h)

            content_height = sum(item_heights) + card_gap * (len(items) - 1)
            img_height = padding_top + header_height + content_height + footer_height + padding_bottom
            img_height = max(img_height, 500)

            img = self._create_background(img_width, img_height)
            draw = ImageDraw.Draw(img)

            y = padding_top
            y = self._draw_header(draw, date_str, persona_name, y, img_width, padding_x)
            y += 20

            for i, item in enumerate(items):
                item_h = item_heights[i]
                self._draw_timeline_item(draw, item, y, item_h, img_width, padding_x, timeline_x)
                y += item_h + card_gap

            self._draw_footer(draw, img_height, img_width, padding_x)

            buf = io.BytesIO()
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
            if line.startswith("👗") or line.startswith("换装"):
                current["outfit_change"] = re.sub(r'^👗\s*换装[：:]\s*', '', line).strip()
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
        height = 40
        if date_str:
            height += 36
        if persona_name:
            height += 28
        return height

    def _calc_item_height(self, item: dict, img_width: int, padding_x: int, timeline_x: int) -> int:
        card_padding = 16
        content_width = img_width - padding_x - timeline_x - 30
        height = card_padding * 2

        title = item.get("title") or ""
        if title:
            title_lines = self._wrap_text(title, content_width - card_padding * 2, self._body_font)
            height += len(title_lines) * 26

        detail = item.get("detail") or ""
        if detail:
            if title:
                height += 4
            detail_lines = self._wrap_text(detail, content_width - card_padding * 2, self._body_font)
            height += len(detail_lines) * 24

        outfit_change = item.get("outfit_change") or ""
        if outfit_change:
            if title or detail:
                height += 8
            height += 22

        return max(height, 70)

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

    def _draw_header(self, draw: ImageDraw.Draw, date_str: str, persona_name: str, y: int, img_width: int, padding_x: int) -> int:
        if date_str:
            title_text = self._format_date_title(date_str)
            bbox = draw.textbbox((0, 0), title_text, font=self._title_font)
            tw = bbox[2] - bbox[0]
            tx = (img_width - tw) // 2
            draw.text((tx, y), title_text, fill=self.TEXT_PRIMARY, font=self._title_font)
            y += 36

        if persona_name:
            sub_text = f"{persona_name} 的一日"
            bbox_s = draw.textbbox((0, 0), sub_text, font=self._time_font)
            sw = bbox_s[2] - bbox_s[0]
            sx = (img_width - sw) // 2
            draw.text((sx, y), sub_text, fill=self.TEXT_SECONDARY, font=self._time_font)
            y += 28

        line_y = y
        center_x = img_width // 2
        draw.line([(padding_x, line_y), (center_x - 40, line_y)], fill=self.DIVIDER, width=1)
        draw.line([(center_x + 40, line_y), (img_width - padding_x, line_y)], fill=self.DIVIDER, width=1)
        draw.ellipse([(center_x - 4, line_y - 4), (center_x + 4, line_y + 4)], fill=self.ACCENT)
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

    def _draw_timeline_item(self, draw: ImageDraw.Draw, item: dict, y: int, item_h: int, img_width: int, padding_x: int, timeline_x: int):
        card_x = timeline_x + 16
        card_width = img_width - padding_x - card_x
        card_padding = 16

        self._draw_rounded_rect(draw, card_x, y, card_x + card_width, y + item_h, radius=12, fill=self.CARD_BG)

        dot_y = y + item_h // 2
        draw.ellipse([(timeline_x - 6, dot_y - 6), (timeline_x + 6, dot_y + 6)], fill=self.TIMELINE_DOT)

        time_text = item.get("time") or ""
        if time_text:
            time_bbox = draw.textbbox((0, 0), time_text, font=self._time_font)
            time_w = time_bbox[2] - time_bbox[0]
            time_x = timeline_x - 12 - time_w
            if time_x < padding_x:
                time_x = padding_x
            draw.text((time_x, dot_y - 8), time_text, fill=self.TEXT_MUTED, font=self._time_font)

        content_x = card_x + card_padding
        content_y = y + card_padding
        content_width = card_width - card_padding * 2

        title = item.get("title") or ""
        if title:
            title_lines = self._wrap_text(title, content_width, self._body_font)
            for line in title_lines:
                draw.text((content_x, content_y), line, fill=self.TEXT_PRIMARY, font=self._body_font)
                content_y += 26

        detail = item.get("detail") or ""
        if detail:
            if title:
                content_y += 4
            detail_lines = self._wrap_text(detail, content_width, self._body_font)
            for line in detail_lines:
                draw.text((content_x, content_y), line, fill=self.TEXT_SECONDARY, font=self._body_font)
                content_y += 24

        outfit_change = item.get("outfit_change") or ""
        if outfit_change:
            if title or detail:
                content_y += 6
            tag_text = f"👗 {outfit_change}"
            draw.text((content_x, content_y), tag_text, fill=self.ACCENT, font=self._small_font)

    def _draw_rounded_rect(self, draw: ImageDraw.Draw, x1: int, y1: int, x2: int, y2: int, radius: int, fill: tuple):
        draw.rounded_rectangle([(x1, y1), (x2, y2)], radius=radius, fill=fill)

    def _draw_footer(self, draw: ImageDraw.Draw, img_height: int, img_width: int, padding_x: int):
        footer_y = img_height - 45
        brand_text = "Dayflow"
        bbox = draw.textbbox((0, 0), brand_text, font=self._small_font)
        tw = bbox[2] - bbox[0]
        draw.text(((img_width - tw) // 2, footer_y), brand_text, fill=self.FOOTER_TEXT, font=self._small_font)

        line_y = footer_y - 12
        draw.line([(padding_x + 60, line_y), (img_width // 2 - 40, line_y)], fill=self.DIVIDER, width=1)
        draw.line([(img_width // 2 + 40, line_y), (img_width - padding_x - 60, line_y)], fill=self.DIVIDER, width=1)

    def _wrap_text(self, text: str, max_width: int, font: ImageFont.FreeTypeFont) -> list[str]:
        result: list[str] = []
        current_line = ""
        for char in text:
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
