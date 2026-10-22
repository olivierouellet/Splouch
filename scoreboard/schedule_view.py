"""The Schedule view's widget: paints the lines `schedule.layout` gives it.

Painted rather than built from labels: the set of rows changes with every heat,
and a fixed grid of `schedule.LINES` text lines is all there is to it. Each heat
sits on its own stripe, as the Schedule page's cards alternate; the title line
reads like the page's card header — EVENT and HEAT words in `schedule_event`,
their numbers and the swimmers in `schedule_name`, the start time in
`schedule_time` — and each swimmer's line carries lane, name, club and seed time.
"""

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from . import schedule
from .theme import Config

# Shares of a swimmer's line: lane, name, club, seed time.
_W_LANE, _W_NAME, _W_CLUB, _W_SEED = 0.07, 0.48, 0.27, 0.18
# Text height as a share of a line, and the side padding as a share of the width.
_FONT = 0.58
_PAD = 0.012


class ScheduleView(QWidget):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.heats: list = []
        self.lane_direction = "up"

    def set_heats(self, heats: list):
        """Show *heats*, the first being the next to swim; only the whole ones
        that fit are drawn (`schedule.fit`)."""
        self.heats = schedule.fit(heats)
        self.update()

    def set_lane_direction(self, direction: str):
        self.lane_direction = "down" if direction == "down" else "up"
        self.update()

    def lines(self) -> list:
        return schedule.layout(self.heats, self.lane_direction)

    def paintEvent(self, event):
        cfg = self.cfg
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.fillRect(self.rect(), QColor(cfg.color("bg")))
        line_h = self.height() / schedule.LINES
        pad = int(self.width() * _PAD)
        px = max(10, int(line_h * _FONT))
        text_font = QFont(cfg.family)
        text_font.setPixelSize(px)
        time_font = QFont(cfg.timing_family)
        time_font.setPixelSize(px)
        small = QFont(cfg.family)
        small.setPixelSize(max(10, int(px * 0.85)))
        top = 0.0
        for kind, item, index in self.lines():
            rect = QRect(0, int(top), self.width(), int(top + line_h) - int(top))
            stripe = cfg.color("row_even" if index % 2 == 0 else "row_odd")
            painter.fillRect(rect, QColor(stripe))
            inner = rect.adjusted(pad, 0, -pad, 0)
            if kind == "title":
                self._paint_title(painter, inner, item, text_font, time_font)
                painter.setPen(QColor(cfg.color("header_border")))
                painter.drawLine(rect.bottomLeft(), rect.bottomRight())
            else:
                self._paint_lane(painter, inner, item, text_font, small, time_font)
            top += line_h
        painter.end()

    def _text(self, painter, rect, text, font, colour, align, elide=True):
        painter.setFont(font)
        painter.setPen(QColor(colour))
        if elide:
            text = QFontMetrics(font).elidedText(
                text, Qt.TextElideMode.ElideRight, max(0, rect.width())
            )
        painter.drawText(rect, int(align | Qt.AlignmentFlag.AlignVCenter), text)

    def _paint_title(self, painter, rect, heat, font, time_font):
        cfg = self.cfg
        left = Qt.AlignmentFlag.AlignLeft
        x = rect.left()
        metrics = QFontMetrics(font)
        # EVENT 3  HEAT 1 — words and numbers in their own colours, as the page does.
        for word, value in (
            (cfg.labels.get("event", "EVENT"), str(heat.get("event", ""))),
            (cfg.labels.get("heat", "HEAT"), str(heat.get("heat", ""))),
        ):
            for text, colour in (
                (word + " ", "schedule_event"),
                (value + "   ", "schedule_name"),
            ):
                width = metrics.horizontalAdvance(text)
                self._text(
                    painter,
                    QRect(x, rect.top(), width, rect.height()),
                    text,
                    font,
                    cfg.color(colour),
                    left,
                    elide=False,
                )
                x += width
        start = str(heat.get("time") or "")
        time_w = QFontMetrics(time_font).horizontalAdvance(start + " ") if start else 0
        if start:
            self._text(
                painter,
                QRect(rect.right() - time_w, rect.top(), time_w, rect.height()),
                start,
                time_font,
                cfg.color("schedule_time"),
                Qt.AlignmentFlag.AlignRight,
                elide=False,
            )
        name_rect = QRect(
            x, rect.top(), max(0, rect.right() - time_w - x), rect.height()
        )
        self._text(
            painter,
            name_rect,
            str(heat.get("event_name") or ""),
            font,
            cfg.color("schedule_event"),
            left,
        )

    def _paint_lane(self, painter, rect, lane, font, small, time_font):
        cfg = self.cfg
        width = rect.width()
        x = rect.left()
        cells = (
            (
                str(lane.get("lane", "")),
                font,
                "th_text",
                Qt.AlignmentFlag.AlignCenter,
                _W_LANE,
            ),
            (
                str(lane.get("name") or ""),
                font,
                "schedule_name",
                Qt.AlignmentFlag.AlignLeft,
                _W_NAME,
            ),
            (
                str(lane.get("club") or ""),
                small,
                "schedule_club",
                Qt.AlignmentFlag.AlignLeft,
                _W_CLUB,
            ),
            (
                schedule.display_time(lane.get("seed_time")),
                time_font,
                "schedule_seed",
                Qt.AlignmentFlag.AlignRight,
                _W_SEED,
            ),
        )
        for text, cell_font, colour, align, share in cells:
            cell_w = int(width * share)
            self._text(
                painter,
                QRect(x, rect.top(), cell_w, rect.height()),
                text,
                cell_font,
                cfg.color(colour),
                align,
            )
            x += cell_w
