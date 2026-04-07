"""
overlay.py - 시선 위치 오버레이 모듈
PyQt5를 사용하여 전체 가상 데스크톱에 투명한 Always-on-top 창을 띄우고,
시선 위치에 십자선 마커를 실시간으로 렌더링합니다.
클릭 이벤트는 하위 창으로 투과됩니다.
"""

from PyQt5.QtWidgets import QWidget, QApplication
from PyQt5.QtCore import Qt, QTimer, QPoint
from PyQt5.QtGui import QPainter, QColor, QPen


class GazeOverlay(QWidget):
    """투명 오버레이 위에 시선 마커를 표시하는 위젯"""

    CROSSHAIR_SIZE = 20       # 십자선 팔 길이 (px)
    CROSSHAIR_THICKNESS = 2   # 십자선 두께 (px)
    DOT_RADIUS = 5            # 중앙 점 반지름 (px)

    def __init__(self, virtual_x: int, virtual_y: int,
                 virtual_w: int, virtual_h: int):
        """
        Args:
            virtual_x, virtual_y: 가상 데스크톱 원점
            virtual_w, virtual_h: 가상 데스크톱 크기
        """
        super().__init__()

        self._gaze_pos = QPoint(virtual_x + virtual_w // 2,
                                virtual_y + virtual_h // 2)
        self._mouse_control_on = True

        # 윈도우 설정: 프레임 없음 + 최상단 + 투명 배경 + 클릭 투과
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool                   # 태스크바에서 숨김
            | Qt.WindowTransparentForInput  # 클릭 투과
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        # 전체 가상 데스크톱 크기로 창 배치
        self.setGeometry(virtual_x, virtual_y, virtual_w, virtual_h)

        # 상태 표시 색상
        self._color_on = QColor(0, 255, 0, 200)     # 초록 (제어 ON)
        self._color_off = QColor(255, 165, 0, 200)   # 주황 (제어 OFF)

    def update_gaze(self, screen_x: int, screen_y: int):
        """시선 위치를 업데이트하고 다시 그리기를 요청합니다."""
        self._gaze_pos = QPoint(screen_x - self.x(), screen_y - self.y())
        self.update()

    def set_mouse_control(self, enabled: bool):
        """마우스 제어 상태를 설정합니다 (색상 변경용)."""
        self._mouse_control_on = enabled
        self.update()

    def paintEvent(self, event):
        """십자선 + 중앙 점 렌더링"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        color = self._color_on if self._mouse_control_on else self._color_off
        pen = QPen(color, self.CROSSHAIR_THICKNESS)
        painter.setPen(pen)

        cx, cy = self._gaze_pos.x(), self._gaze_pos.y()
        s = self.CROSSHAIR_SIZE

        # 십자선
        painter.drawLine(cx - s, cy, cx + s, cy)  # 가로
        painter.drawLine(cx, cy - s, cx, cy + s)  # 세로

        # 중앙 원
        painter.setBrush(color)
        painter.drawEllipse(self._gaze_pos, self.DOT_RADIUS, self.DOT_RADIUS)

        # 상태 텍스트
        painter.setPen(QColor(255, 255, 255, 220))
        label = "GAZE CONTROL: ON" if self._mouse_control_on else "GAZE CONTROL: OFF"
        painter.drawText(20, 30, label)

        painter.end()
