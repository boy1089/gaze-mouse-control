"""
calibration.py - 9점 캘리브레이션 모듈
화면의 9개 점을 순서대로 응시하게 하여 iris ratio → screen ratio 매핑 파라미터를 계산합니다.
결과를 calibration.json에 저장/로드하여 재사용할 수 있습니다.
"""

import os
import json
from datetime import datetime

import cv2
import numpy as np
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QTimer, QPoint, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QPen, QFont


CALIBRATION_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "calibration.json"
)

SETTLE_MS = 1000   # 안정화 대기 시간 (ms)
COLLECT_MS = 1000   # 데이터 수집 시간 (ms)
TICK_MS = 33        # 수집 타이머 간격 (~30fps)


def compute_mapping(calib_points):
    """
    캘리브레이션 데이터에서 선형 매핑 파라미터를 계산합니다.

    Args:
        calib_points: [(iris_x, iris_y, target_x, target_y), ...] 리스트

    Returns:
        dict: {offset_x, scale_x, offset_y, scale_y}
    """
    data = np.array(calib_points)
    iris_x = data[:, 0]
    iris_y = data[:, 1]
    target_x = data[:, 2]
    target_y = data[:, 3]

    # X: target_x = offset_x + scale_x * iris_x
    A_x = np.vstack([iris_x, np.ones(len(iris_x))]).T
    result_x, _, _, _ = np.linalg.lstsq(A_x, target_x, rcond=None)
    scale_x, offset_x = result_x

    # Y: target_y = offset_y + scale_y * iris_y
    A_y = np.vstack([iris_y, np.ones(len(iris_y))]).T
    result_y, _, _, _ = np.linalg.lstsq(A_y, target_y, rcond=None)
    scale_y, offset_y = result_y

    return {
        "offset_x": float(offset_x),
        "scale_x": float(scale_x),
        "offset_y": float(offset_y),
        "scale_y": float(scale_y),
    }


def save_calibration(params, filepath=CALIBRATION_PATH):
    """캘리브레이션 파라미터를 JSON 파일로 저장합니다."""
    params["timestamp"] = datetime.now().isoformat()
    with open(filepath, "w") as f:
        json.dump(params, f, indent=2)
    print(f"캘리브레이션 저장됨: {filepath}")


def load_calibration(filepath=CALIBRATION_PATH):
    """
    JSON 파일에서 캘리브레이션 파라미터를 로드합니다.

    Returns:
        dict 또는 None (파일 없을 시)
    """
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r") as f:
        params = json.load(f)
    print(f"캘리브레이션 로드됨: {filepath} ({params.get('timestamp', '?')})")
    return params


class CalibrationOverlay(QWidget):
    """9점 캘리브레이션 UI 오버레이 (주 모니터에 표시)"""

    calibration_complete = pyqtSignal(dict)
    calibration_cancelled = pyqtSignal()

    POINT_RADIUS = 18
    DONE_RADIUS = 8

    def __init__(self, virtual_x, virtual_y, virtual_w, virtual_h,
                 gaze_tracker, monitors):
        super().__init__()

        self._vx = virtual_x
        self._vy = virtual_y
        self._vw = virtual_w
        self._vh = virtual_h
        self._tracker = gaze_tracker

        # 주 모니터 찾기
        primary = None
        for m in monitors:
            if m.is_primary:
                primary = m
                break
        if primary is None:
            primary = monitors[0]

        self._pm_x = primary.x
        self._pm_y = primary.y
        self._pm_w = primary.width
        self._pm_h = primary.height

        # 9개 점: 주 모니터 내 10% 마진 격자 (화면 비율)
        # 화면에 그리는 위치 (0~1 비율, 주 모니터 내)
        grid_ratios = [
            (col, row)
            for row in [0.1, 0.5, 0.9]
            for col in [0.1, 0.5, 0.9]
        ]
        self._display_points = grid_ratios  # paintEvent용 (주 모니터 내 비율)

        # 각 점의 타겟값: 주 모니터 내 절대 좌표를 가상 데스크톱 비율로 변환
        self._target_ratios = []
        for gx, gy in grid_ratios:
            abs_x = self._pm_x + gx * self._pm_w
            abs_y = self._pm_y + gy * self._pm_h
            vr_x = (abs_x - virtual_x) / virtual_w
            vr_y = (abs_y - virtual_y) / virtual_h
            self._target_ratios.append((vr_x, vr_y))

        self._current_idx = 0
        self._phase = "settle"
        self._phase_elapsed = 0

        self._samples_x = []
        self._samples_y = []
        self._calib_data = []

        # 윈도우 설정: 주 모니터에만 표시
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setGeometry(self._pm_x, self._pm_y, self._pm_w, self._pm_h)
        self.setStyleSheet("background-color: rgba(0, 0, 0, 200);")

        # 타이머
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)

    def start(self):
        """캘리브레이션을 시작합니다."""
        self._current_idx = 0
        self._phase = "settle"
        self._phase_elapsed = 0
        self._calib_data = []

        # 디버그 녹화 초기화
        rec_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_recordings")
        os.makedirs(rec_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._video_path = os.path.join(rec_dir, f"calibration_{ts}.mp4")
        self._video_writer = None  # lazy init on first frame

        self.show()
        self.raise_()
        self.activateWindow()
        self._timer.start(TICK_MS)
        print("캘리브레이션 시작 (9점)")

    def _cancel(self):
        self._timer.stop()
        self._stop_video()
        self.hide()
        print("캘리브레이션 취소됨")
        self.calibration_cancelled.emit()

    def _tick(self):
        """매 틱마다 호출: 안정화/수집 상태 관리"""
        self._phase_elapsed += TICK_MS

        if self._phase == "settle":
            # 안정화 시간 동안 대기 — 프레임은 읽어서 버퍼 갱신
            self._tracker.get_gaze_position()
            if self._phase_elapsed >= SETTLE_MS:
                self._phase = "collect"
                self._phase_elapsed = 0
                self._samples_x.clear()
                self._samples_y.clear()

        elif self._phase == "collect":
            # iris ratio 수집
            self._tracker.get_gaze_position()  # 프레임 읽기 (내부 상태 갱신)
            ratio = self._tracker.get_raw_iris_ratio()
            if ratio is not None:
                self._samples_x.append(ratio[0])
                self._samples_y.append(ratio[1])

            if self._phase_elapsed >= COLLECT_MS:
                self._finish_point()

        # 디버그 프레임 녹화
        self._record_debug_frame()

        self.update()  # paintEvent 다시 호출

    def _record_debug_frame(self):
        """웹캠 디버그 프레임을 동영상으로 기록"""
        frame = self._tracker.get_debug_frame()
        if frame is None:
            return

        # 캘리브레이션 상태 정보 오버레이
        h, w = frame.shape[:2]
        label = f"Point {self._current_idx + 1}/9  [{self._phase}]"
        cv2.putText(frame, label, (10, h - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

        # lazy init VideoWriter
        if self._video_writer is None:
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self._video_writer = cv2.VideoWriter(
                self._video_path, fourcc, 30, (w, h)
            )

        self._video_writer.write(frame)

    def _stop_video(self):
        """동영상 저장 종료"""
        if self._video_writer is not None:
            self._video_writer.release()
            self._video_writer = None
            print(f"캘리브레이션 녹화 저장: {self._video_path}")

    def _finish_point(self):
        """현재 점의 수집 완료 처리"""
        target_x, target_y = self._target_ratios[self._current_idx]
        display_x, display_y = self._display_points[self._current_idx]

        if len(self._samples_x) >= 3:
            median_x = float(np.median(self._samples_x))
            median_y = float(np.median(self._samples_y))
            self._calib_data.append((median_x, median_y, target_x, target_y))
            print(f"  점 {self._current_idx + 1}/9: "
                  f"iris=({median_x:.3f}, {median_y:.3f}) → "
                  f"target=({target_x:.3f}, {target_y:.3f}) "
                  f"[{len(self._samples_x)}샘플]")
        else:
            self._calib_data.append((target_x, target_y, target_x, target_y))
            print(f"  점 {self._current_idx + 1}/9: 샘플 부족, 기본값 사용")

        self._current_idx += 1
        if self._current_idx >= len(self._display_points):
            self._complete()
        else:
            self._phase = "settle"
            self._phase_elapsed = 0

    def _complete(self):
        """캘리브레이션 완료"""
        self._timer.stop()
        self._stop_video()
        self.hide()
        params = compute_mapping(self._calib_data)
        print(f"캘리브레이션 완료: scale=({params['scale_x']:.3f}, {params['scale_y']:.3f}), "
              f"offset=({params['offset_x']:.3f}, {params['offset_y']:.3f})")
        save_calibration(params)
        self.calibration_complete.emit(params)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        w, h = self.width(), self.height()

        # 완료된 점 (초록)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 200, 0, 220))
        for i in range(self._current_idx):
            tx, ty = self._display_points[i]
            px, py = int(tx * w), int(ty * h)
            painter.drawEllipse(QPoint(px, py), self.DONE_RADIUS, self.DONE_RADIUS)

        # 현재 활성 점
        if self._current_idx < len(self._display_points):
            tx, ty = self._display_points[self._current_idx]
            px, py = int(tx * w), int(ty * h)

            # 노란 원 (활성 점)
            painter.setBrush(QColor(255, 255, 0, 240))
            painter.setPen(QPen(QColor(255, 255, 255, 200), 2))
            painter.drawEllipse(QPoint(px, py), self.POINT_RADIUS, self.POINT_RADIUS)

            # 중앙 작은 빨간 점 (응시 타겟)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 0, 0))
            painter.drawEllipse(QPoint(px, py), 3, 3)

            # 진행 상태 텍스트
            total_ms = SETTLE_MS + COLLECT_MS
            progress = self._phase_elapsed + (SETTLE_MS if self._phase == "collect" else 0)
            remaining = max(0, (total_ms - progress) / 1000)

            font = QFont("Arial", 16)
            painter.setFont(font)
            painter.setPen(QColor(255, 255, 255, 220))

            if self._phase == "settle":
                text = f"여기를 응시하세요... ({remaining:.1f}s)"
            else:
                text = f"수집 중... ({remaining:.1f}s)"

            # 텍스트를 점 아래에 표시
            text_y = py + self.POINT_RADIUS + 30
            if text_y > h - 30:
                text_y = py - self.POINT_RADIUS - 15
            painter.drawText(px - 100, text_y, 200, 30,
                             Qt.AlignCenter, text)

        # 미완료 점 (어두운 회색)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(100, 100, 100, 120))
        for i in range(self._current_idx + 1, len(self._display_points)):
            tx, ty = self._display_points[i]
            px, py = int(tx * w), int(ty * h)
            painter.drawEllipse(QPoint(px, py), self.DONE_RADIUS, self.DONE_RADIUS)

        # 상단 안내 텍스트
        font = QFont("Arial", 20, QFont.Bold)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 240))
        painter.drawText(0, 30, w, 40, Qt.AlignCenter,
                         f"캘리브레이션 ({self._current_idx + 1}/{len(self._display_points)})  |  ESC: 취소")

        painter.end()
