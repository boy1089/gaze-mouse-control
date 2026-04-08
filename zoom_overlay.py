"""
zoom_overlay.py - 화면 돋보기(Zoom-and-Gaze) 정밀 포지셔닝 모듈

시선이 가리키는 위치의 화면 영역(200×200px)을 mss로 초고속 캡처하여
3배 확대한 팝업을 화면 중앙에 띄우고, 시선 입력을 확대 영역 내부로 매핑합니다.

좌표 역변환 수식:
    # 줌 팝업 창의 화면 위치
    popup_left, popup_top = self.geometry().x(), self.geometry().y()
    # 줌 팝업 창 내부의 상대 좌표
    rel_x = gaze_screen_x - popup_left
    rel_y = gaze_screen_y - popup_top
    # 원본 캡처 영역의 절대 좌표로 역변환
    abs_x = capture_center_x - (capture_size / 2) + (rel_x / zoom_factor)
    abs_y = capture_center_y - (capture_size / 2) + (rel_y / zoom_factor)
"""

import numpy as np
import mss
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QPoint, QTimer
from PyQt5.QtGui import QPainter, QColor, QPen, QImage, QPixmap


class ZoomOverlay(QWidget):
    """화면 돋보기 팝업: 캡처 → 확대 → 정밀 시선 좌표 역변환"""

    CAPTURE_SIZE = 200       # 캡처 영역 크기 (px)
    ZOOM_FACTOR = 3.0        # 확대 비율
    REFRESH_MS = 33          # 캡처 갱신 주기 (~30 FPS)
    CROSSHAIR_SIZE = 16      # 십자선 팔 길이 (px)
    CROSSHAIR_THICKNESS = 2  # 십자선 두께 (px)
    DOT_RADIUS = 4           # 중앙 점 반지름 (px)

    def __init__(self, virtual_x: int, virtual_y: int,
                 virtual_width: int, virtual_height: int):
        """
        Args:
            virtual_x, virtual_y: 가상 데스크톱 원점 (절대 좌표)
            virtual_width, virtual_height: 가상 데스크톱 크기
        """
        super().__init__()

        self._virtual_x = virtual_x
        self._virtual_y = virtual_y
        self._virtual_width = virtual_width
        self._virtual_height = virtual_height

        # 팝업 크기 = 캡처 크기 × 확대 비율
        self._popup_size = int(self.CAPTURE_SIZE * self.ZOOM_FACTOR)

        # 캡처 중심 좌표 (activate 시 설정)
        self._capture_cx = 0
        self._capture_cy = 0

        # 줌 창 내부에서 시선이 가리키는 상대 좌표 (paintEvent용)
        self._gaze_rel = QPoint(self._popup_size // 2, self._popup_size // 2)

        # 확대된 캡처 이미지
        self._pixmap = QPixmap(self._popup_size, self._popup_size)
        self._pixmap.fill(QColor(0, 0, 0))

        # mss 화면 캡처 인스턴스
        self._sct = mss.mss()

        # 캡처 갱신 타이머
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._update_capture)

        # 윈도우 설정: 프레임 없음 + 최상단 + 클릭 투과
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool                          # 태스크바에서 숨김
            | Qt.WindowTransparentForInput     # 클릭 투과 (pyautogui로 제어)
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        # 크기 고정
        self.setFixedSize(self._popup_size, self._popup_size)

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def activate(self, center_x: int, center_y: int):
        """
        돋보기 모드를 활성화합니다.

        Args:
            center_x, center_y: 캡처 중심의 절대 화면 좌표
        """
        self._capture_cx = center_x
        self._capture_cy = center_y

        # 팝업을 가상 데스크톱 중앙에 배치
        popup_x = self._virtual_x + (self._virtual_width - self._popup_size) // 2
        popup_y = self._virtual_y + (self._virtual_height - self._popup_size) // 2
        self.move(popup_x, popup_y)

        # 초기 캡처 수행 후 표시
        self._update_capture()
        self.show()
        self.raise_()
        self._refresh_timer.start(self.REFRESH_MS)

    def deactivate(self):
        """돋보기 모드를 비활성화합니다."""
        self._refresh_timer.stop()
        self.hide()

    def update_gaze(self, screen_x: int, screen_y: int):
        """
        돋보기 창 위의 시선 절대 좌표를 전달받아
        팝업 내부 상대 좌표로 변환합니다.
        """
        # 팝업 창 내부의 상대 좌표
        rel_x = screen_x - self.x()
        rel_y = screen_y - self.y()
        # 팝업 영역 안으로 클램핑
        rel_x = max(0, min(rel_x, self._popup_size - 1))
        rel_y = max(0, min(rel_y, self._popup_size - 1))
        self._gaze_rel = QPoint(rel_x, rel_y)
        self.update()

    def gaze_to_absolute(self, screen_x: int, screen_y: int) -> tuple[int, int]:
        """
        돋보기 창 위의 시선 절대 좌표를 → 원본 캡처 영역의 절대 좌표로 역변환합니다.

        [좌표 역변환 수식]
        1. 줌 팝업 창의 화면 위치(popup_left, popup_top)를 기준으로
           시선 절대 좌표를 팝업 내부의 상대 좌표(rel_x, rel_y)로 변환합니다.
        2. 상대 좌표를 확대 비율(zoom_factor)로 나누어
           원본 캡처 영역 내 상대 좌표로 축소합니다.
        3. 캡처 영역의 좌상단 절대 좌표에 더하면
           원본 화면의 최종 절대 좌표(abs_x, abs_y)가 됩니다.

        Args:
            screen_x, screen_y: 시선이 가리키는 화면 절대 좌표

        Returns:
            (abs_x, abs_y): 원본 캡처 영역 내의 절대 좌표
        """
        # (1) 줌 팝업 창 내부의 상대 좌표 계산
        popup_left = self.x()
        popup_top = self.y()
        rel_x = screen_x - popup_left
        rel_y = screen_y - popup_top

        # 팝업 영역 안으로 클램핑 (벗어나지 않도록)
        rel_x = max(0, min(rel_x, self._popup_size - 1))
        rel_y = max(0, min(rel_y, self._popup_size - 1))

        # (2) 확대 비율로 나누어 원본 캡처 영역 내 상대 좌표로 축소
        orig_rel_x = rel_x / self.ZOOM_FACTOR
        orig_rel_y = rel_y / self.ZOOM_FACTOR

        # (3) 캡처 영역 좌상단의 절대 좌표 + 원본 상대 좌표 = 최종 절대 좌표
        #     캡처 좌상단 = 캡처 중심 - (캡처 크기의 절반)
        capture_left = self._capture_cx - self.CAPTURE_SIZE // 2
        capture_top = self._capture_cy - self.CAPTURE_SIZE // 2
        abs_x = int(capture_left + orig_rel_x)
        abs_y = int(capture_top + orig_rel_y)

        return abs_x, abs_y

    @property
    def is_active(self) -> bool:
        """돋보기 모드가 활성 상태인지 반환합니다."""
        return self.isVisible()

    # ------------------------------------------------------------------
    # 내부 메서드
    # ------------------------------------------------------------------

    def _update_capture(self):
        """mss로 캡처 영역을 초고속으로 가져와 확대 QPixmap을 생성합니다."""
        half = self.CAPTURE_SIZE // 2

        # 캡처 영역 BBox (화면 경계 클리핑 포함)
        left = self._capture_cx - half
        top = self._capture_cy - half
        bbox = {
            "left": left,
            "top": top,
            "width": self.CAPTURE_SIZE,
            "height": self.CAPTURE_SIZE,
        }

        # mss 캡처 (BGRA 형식)
        shot = self._sct.grab(bbox)
        img = np.array(shot, dtype=np.uint8)  # shape: (H, W, 4) BGRA

        # BGRA → RGB 변환
        rgb = img[:, :, :3][:, :, ::-1].copy()  # BGR→RGB

        # numpy → QImage → QPixmap
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, w * ch, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)

        # 확대 (smooth 변환으로 고품질 스케일링)
        self._pixmap = pixmap.scaled(
            self._popup_size, self._popup_size,
            Qt.IgnoreAspectRatio, Qt.SmoothTransformation,
        )
        self.update()

    def paintEvent(self, event):
        """확대된 캡처 이미지 위에 시선 십자선을 렌더링합니다."""
        painter = QPainter(self)

        # 1. 확대된 캡처 이미지 배경
        painter.drawPixmap(0, 0, self._pixmap)

        # 2. 팝업 테두리 (시각적 구분용)
        border_pen = QPen(QColor(255, 255, 0, 220), 3)
        painter.setPen(border_pen)
        painter.drawRect(1, 1, self._popup_size - 2, self._popup_size - 2)

        # 3. 시선 위치 십자선 (빨간색)
        painter.setRenderHint(QPainter.Antialiasing, True)
        gaze_pen = QPen(QColor(255, 0, 0, 220), self.CROSSHAIR_THICKNESS)
        painter.setPen(gaze_pen)

        cx, cy = self._gaze_rel.x(), self._gaze_rel.y()
        s = self.CROSSHAIR_SIZE
        painter.drawLine(cx - s, cy, cx + s, cy)
        painter.drawLine(cx, cy - s, cx, cy + s)

        # 4. 시선 중앙 점
        painter.setBrush(QColor(255, 0, 0, 220))
        painter.drawEllipse(self._gaze_rel, self.DOT_RADIUS, self.DOT_RADIUS)

        # 5. 안내 텍스트
        painter.setPen(QColor(255, 255, 255, 230))
        painter.drawText(8, 18, "ZOOM MODE | Space/Enter: 클릭 | ESC: 취소")

        painter.end()

    def release(self):
        """리소스 정리"""
        self._refresh_timer.stop()
        self.hide()
        self._sct.close()
