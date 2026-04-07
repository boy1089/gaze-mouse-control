"""
debug_recorder.py - 디버그 녹화 모듈
웹캠 시선 인식 시각화(좌)와 스크린 캡처+시선 위치(우)를 합성하여 동영상으로 저장합니다.
"""

import os
from datetime import datetime

import cv2
import numpy as np
import pyautogui


class DebugRecorder:
    """디버그 영상 녹화기"""

    RECORD_FPS = 10
    WEBCAM_HEIGHT = 480  # 스크린 캡처를 이 높이에 맞춤

    def __init__(self, output_dir: str = "debug_recordings"):
        self._output_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), output_dir
        )
        self._writer = None
        self._filepath = None

    @property
    def is_recording(self) -> bool:
        return self._writer is not None

    def start(self):
        """녹화를 시작합니다. 파일명은 타임스탬프 기반으로 자동 생성됩니다."""
        if self.is_recording:
            return

        os.makedirs(self._output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._filepath = os.path.join(self._output_dir, f"debug_{timestamp}.mp4")

        # 프레임 크기는 첫 write_frame에서 결정 (lazy init)
        self._pending_path = self._filepath
        self._writer = None  # will be created on first frame
        self._started = True

        print(f"녹화 시작: {self._filepath}")

    def stop(self):
        """녹화를 중지하고 파일을 저장합니다."""
        if not self._started:
            return

        self._started = False
        if self._writer is not None:
            self._writer.release()
            self._writer = None
            print(f"녹화 중지: {self._filepath}")
        self._filepath = None

    @property
    def is_recording(self) -> bool:
        return getattr(self, "_started", False)

    def write_frame(self, webcam_debug_frame, screen_x: int, screen_y: int,
                    virtual_x: int, virtual_y: int,
                    virtual_w: int, virtual_h: int):
        """
        디버그 프레임을 생성하여 동영상에 기록합니다.

        Args:
            webcam_debug_frame: 시선 인식 시각화가 그려진 웹캠 프레임 (BGR, 640x480)
            screen_x, screen_y: 현재 시선의 절대 스크린 좌표
            virtual_x, virtual_y: 가상 데스크톱 원점
            virtual_w, virtual_h: 가상 데스크톱 크기
        """
        if not self.is_recording:
            return

        # --- 좌측: 웹캠 디버그 프레임 ---
        left = webcam_debug_frame
        cam_h, cam_w = left.shape[:2]

        # --- 우측: 스크린 캡처 + 시선 크로스헤어 ---
        screenshot = pyautogui.screenshot()
        screen_img = np.array(screenshot)
        screen_img = cv2.cvtColor(screen_img, cv2.COLOR_RGB2BGR)

        # 시선 위치를 스크린 캡처 이미지 좌표로 변환
        # pyautogui.screenshot()은 주 모니터(또는 전체)를 캡처
        # 시선 좌표는 가상 데스크톱 기준이므로 변환
        gaze_img_x = screen_x - virtual_x
        gaze_img_y = screen_y - virtual_y

        # 스크린 캡처를 웹캠 높이에 맞게 리사이즈
        scale = cam_h / screen_img.shape[0]
        new_w = int(screen_img.shape[1] * scale)
        screen_resized = cv2.resize(screen_img, (new_w, cam_h))

        # 크로스헤어 좌표도 스케일 적용
        cx = int(gaze_img_x * scale)
        cy = int(gaze_img_y * scale)

        # 크로스헤어 그리기
        cross_size = 20
        cv2.line(screen_resized, (cx - cross_size, cy), (cx + cross_size, cy),
                 (0, 0, 255), 2)
        cv2.line(screen_resized, (cx, cy - cross_size), (cx, cy + cross_size),
                 (0, 0, 255), 2)
        cv2.circle(screen_resized, (cx, cy), 6, (0, 0, 255), 2)

        # --- 좌우 합성 ---
        combined = np.hstack([left, screen_resized])

        # 라벨 추가
        cv2.putText(combined, "WEBCAM + GAZE", (10, cam_h - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        cv2.putText(combined, "SCREEN + CURSOR", (cam_w + 10, cam_h - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

        # VideoWriter lazy init (첫 프레임에서 크기 결정)
        if self._writer is None:
            h, w = combined.shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self._writer = cv2.VideoWriter(
                self._pending_path, fourcc, self.RECORD_FPS, (w, h)
            )

        self._writer.write(combined)

    def release(self):
        """리소스 해제"""
        if self.is_recording:
            self.stop()
