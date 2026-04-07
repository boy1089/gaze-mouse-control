"""
main.py - 시선 추적 마우스 제어 프로그램 메인 모듈
모든 모듈을 통합하여 실행합니다.

사용법:
    python main.py

단축키:
    F9  - 마우스 제어 On/Off 토글
    F10 - 디버그 녹화 시작/중지
    ESC - 프로그램 종료
"""

import sys
import pyautogui
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer
from pynput import keyboard

from gaze_tracker import GazeTracker
from monitor_mapper import MonitorMapper
from overlay import GazeOverlay
from debug_recorder import DebugRecorder

# pyautogui 안전장치 해제 (듀얼 모니터에서 화면 끝까지 이동 가능하도록)
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0


class GazeMouseController:
    """시선 추적 → 마우스 제어 통합 컨트롤러"""

    TRACKING_INTERVAL_MS = 16  # ~60 FPS
    TOGGLE_KEY = keyboard.Key.f9
    RECORD_KEY = keyboard.Key.f10
    EXIT_KEY = keyboard.Key.esc

    def __init__(self):
        # 1. 모니터 매핑 초기화
        self.mapper = MonitorMapper()
        print(self.mapper.get_info_string())

        # 2. 시선 추적기 초기화
        self.tracker = GazeTracker(camera_index=0, smoothing_window=8)
        print("웹캠 시선 추적기 초기화 완료")

        # 3. PyQt5 앱 및 오버레이 초기화
        self.app = QApplication(sys.argv)
        self.overlay = GazeOverlay(
            self.mapper.virtual_x,
            self.mapper.virtual_y,
            self.mapper.virtual_width,
            self.mapper.virtual_height,
        )
        self.overlay.show()

        # 4. 마우스 제어 상태
        self._mouse_control_enabled = True

        # 5. 디버그 녹화기
        self.recorder = DebugRecorder()
        self._record_frame_counter = 0

        # 6. 글로벌 단축키 리스너 (별도 스레드)
        self._hotkey_listener = keyboard.Listener(on_press=self._on_key_press)
        self._hotkey_listener.daemon = True
        self._hotkey_listener.start()

        # 7. 추적 타이머 (Qt 이벤트 루프에서 실행)
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(self.TRACKING_INTERVAL_MS)

        print(f"시작됨 | F9: 마우스 제어 토글 | F10: 디버그 녹화 | ESC: 종료")

    def _on_key_press(self, key):
        """글로벌 키 이벤트 핸들러"""
        if key == self.TOGGLE_KEY:
            self._mouse_control_enabled = not self._mouse_control_enabled
            state = "ON" if self._mouse_control_enabled else "OFF"
            print(f"마우스 제어: {state}")
            self.overlay.set_mouse_control(self._mouse_control_enabled)
        elif key == self.RECORD_KEY:
            if self.recorder.is_recording:
                self.recorder.stop()
            else:
                self.recorder.start()
        elif key == self.EXIT_KEY:
            print("프로그램 종료 중...")
            self._timer.stop()
            self.recorder.release()
            self.tracker.release()
            self.app.quit()

    def _tick(self):
        """매 프레임마다 실행되는 추적 루프"""
        gaze = self.tracker.get_gaze_position()
        if gaze is None:
            return

        ratio_x, ratio_y = gaze
        screen_x, screen_y = self.mapper.ratio_to_screen(ratio_x, ratio_y)

        # 오버레이 마커는 항상 업데이트
        self.overlay.update_gaze(screen_x, screen_y)

        # 마우스 제어가 켜져 있을 때만 커서 이동
        if self._mouse_control_enabled:
            pyautogui.moveTo(screen_x, screen_y, _pause=False)

        # 디버그 녹화 (~10FPS: 6프레임마다 1번)
        if self.recorder.is_recording:
            self._record_frame_counter += 1
            if self._record_frame_counter >= 6:
                self._record_frame_counter = 0
                debug_frame = self.tracker.get_debug_frame()
                if debug_frame is not None:
                    self.recorder.write_frame(
                        debug_frame, screen_x, screen_y,
                        self.mapper.virtual_x, self.mapper.virtual_y,
                        self.mapper.virtual_width, self.mapper.virtual_height,
                    )

    def run(self):
        """Qt 이벤트 루프 실행"""
        return self.app.exec_()


def main():
    print("=" * 50)
    print("  시선 추적 마우스 제어 프로그램")
    print("=" * 50)

    controller = GazeMouseController()
    sys.exit(controller.run())


if __name__ == "__main__":
    main()
