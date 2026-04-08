"""
main.py - 시선 추적 마우스 제어 프로그램 메인 모듈
모든 모듈을 통합하여 실행합니다.

사용법:
    python main.py

단축키:
    F9  - 마우스 제어 On/Off 토글
    F10 - 디버그 녹화 시작/중지
    F11 - 캘리브레이션 재실행
    Alt - 돋보기(Zoom) 정밀 모드 진입
    Space/Enter - 돋보기 모드에서 클릭 실행
    ESC - 프로그램 종료 / 돋보기 취소
"""

import sys
import pyautogui
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer
from pynput import keyboard

from gaze_tracker import GazeTracker
from monitor_mapper import MonitorMapper
from overlay import GazeOverlay
from zoom_overlay import ZoomOverlay
from debug_recorder import DebugRecorder
from calibration import CalibrationOverlay, load_calibration

# pyautogui 안전장치 해제 (듀얼 모니터에서 화면 끝까지 이동 가능하도록)
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0


class GazeMouseController:
    """시선 추적 → 마우스 제어 통합 컨트롤러"""

    TRACKING_INTERVAL_MS = 16  # ~60 FPS
    TOGGLE_KEY = keyboard.Key.f9
    RECORD_KEY = keyboard.Key.f10
    CALIBRATE_KEY = keyboard.Key.f11
    EXIT_KEY = keyboard.Key.esc
    ZOOM_KEY_L = keyboard.Key.alt_l       # 돋보기 트리거 (왼쪽 Alt)
    ZOOM_KEY_R = keyboard.Key.alt_r       # 돋보기 트리거 (오른쪽 Alt)
    ZOOM_CLICK_KEY = keyboard.Key.space   # 돋보기 클릭 실행
    ZOOM_CLICK_KEY2 = keyboard.Key.enter  # 돋보기 클릭 실행 (대체)

    def __init__(self):
        # 1. 모니터 매핑 초기화
        self.mapper = MonitorMapper()
        print(self.mapper.get_info_string())

        # 2. 시선 추적기 초기화
        self.tracker = GazeTracker(camera_index=0, smoothing_window=8)
        print("웹캠 시선 추적기 초기화 완료")

        # 3. PyQt5 앱 초기화
        self.app = QApplication(sys.argv)

        # 4. 캘리브레이션 오버레이 (재사용 가능)
        self.calib_overlay = CalibrationOverlay(
            self.mapper.virtual_x, self.mapper.virtual_y,
            self.mapper.virtual_width, self.mapper.virtual_height,
            self.tracker, self.mapper.monitors,
        )
        self.calib_overlay.calibration_complete.connect(self._on_calibration_done)
        self.calib_overlay.calibration_cancelled.connect(self._on_calibration_cancelled)

        # 5. 시선 오버레이 초기화
        self.overlay = GazeOverlay(
            self.mapper.virtual_x,
            self.mapper.virtual_y,
            self.mapper.virtual_width,
            self.mapper.virtual_height,
        )
        self.overlay.show()

        # 5-1. 돋보기(Zoom) 오버레이 초기화
        self.zoom_overlay = ZoomOverlay(
            self.mapper.virtual_x,
            self.mapper.virtual_y,
            self.mapper.virtual_width,
            self.mapper.virtual_height,
        )

        # 6. 마우스 제어 상태
        self._mouse_control_enabled = True
        self._zoom_mode_active = False          # 돋보기 모드 플래그
        self._last_screen_x = 0                 # 줌 트리거 시점의 시선 좌표 기억용
        self._last_screen_y = 0

        # 7. 디버그 녹화기
        self.recorder = DebugRecorder()
        self._record_frame_counter = 0

        # 8. 글로벌 단축키 리스너 (별도 스레드)
        self._pending_actions = []  # 스레드 안전 액션 큐
        self._hotkey_listener = keyboard.Listener(on_press=self._on_key_press)
        self._hotkey_listener.daemon = True
        self._hotkey_listener.start()

        # 9. 액션 폴링 타이머 (pynput 스레드의 요청을 Qt 메인 스레드에서 처리)
        self._action_timer = QTimer()
        self._action_timer.timeout.connect(self._process_actions)
        self._action_timer.start(50)

        # 10. 추적 타이머 (Qt 이벤트 루프에서 실행)
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)

        # 11. 캘리브레이션 확인 후 시작
        calib = load_calibration()
        if calib is not None:
            self.tracker.set_calibration(calib)
            self._start_tracking()
        else:
            print("캘리브레이션 필요 — 시작합니다...")
            self._mouse_control_enabled = False
            self.overlay.set_mouse_control(False)
            self.calib_overlay.start()

    def _on_calibration_done(self, params):
        """캘리브레이션 완료 시 호출"""
        self.tracker.set_calibration(params)
        self._mouse_control_enabled = True
        self.overlay.set_mouse_control(True)
        self._start_tracking()

    def _on_calibration_cancelled(self):
        """캘리브레이션 취소 시 호출 (기존 파라미터로 폴백)"""
        print("기존 파라미터로 시작합니다")
        self._mouse_control_enabled = True
        self.overlay.set_mouse_control(True)
        self._start_tracking()

    def _start_tracking(self):
        """추적 타이머를 시작합니다."""
        if not self._timer.isActive():
            self._timer.start(self.TRACKING_INTERVAL_MS)
        print(f"시작됨 | F9: 마우스 제어 | F10: 녹화 | F11: 재캘리브레이션 | Alt: 돋보기 | ESC: 종료")

    def _on_key_press(self, key):
        """글로벌 키 이벤트 핸들러 (pynput 스레드 — 액션만 큐에 넣음)"""
        # 돋보기 모드 활성 중에는 전용 키만 처리
        if self._zoom_mode_active:
            if key in (self.ZOOM_CLICK_KEY, self.ZOOM_CLICK_KEY2):
                self._pending_actions.append("zoom_click")
            elif key == self.EXIT_KEY:
                self._pending_actions.append("zoom_cancel")
            return  # 줌 모드에서는 다른 단축키 무시

        if key in (self.ZOOM_KEY_L, self.ZOOM_KEY_R):
            self._pending_actions.append("zoom_activate")
        elif key == self.TOGGLE_KEY:
            self._pending_actions.append("toggle_mouse")
        elif key == self.RECORD_KEY:
            self._pending_actions.append("toggle_record")
        elif key == self.CALIBRATE_KEY:
            self._pending_actions.append("recalibrate")
        elif key == self.EXIT_KEY:
            self._pending_actions.append("exit")

    def _process_actions(self):
        """Qt 메인 스레드에서 대기 중인 액션을 안전하게 처리"""
        while self._pending_actions:
            action = self._pending_actions.pop(0)

            # --- 돋보기(Zoom) 관련 액션 ---
            if action == "zoom_activate":
                # 마우스 제어 ON + 줌 모드 비활성 + 캘리브레이션 비활성일 때만
                if (self._mouse_control_enabled
                        and not self._zoom_mode_active
                        and not self.calib_overlay.isVisible()):
                    self._zoom_mode_active = True
                    # 기존 시선 오버레이 숨김 (시각적 혼란 방지)
                    self.overlay.hide()
                    # 현재 시선 절대 좌표를 중심으로 돋보기 활성화
                    self.zoom_overlay.activate(
                        self._last_screen_x, self._last_screen_y)
                    print("돋보기 모드 ON")

            elif action == "zoom_click":
                if self._zoom_mode_active:
                    # 돋보기 창 내 시선 → 원본 절대 좌표로 역변환
                    abs_x, abs_y = self.zoom_overlay.gaze_to_absolute(
                        self._last_screen_x, self._last_screen_y)
                    # 돋보기 닫기 (클릭 전에 닫아야 아래 창에 클릭이 도달)
                    self.zoom_overlay.deactivate()
                    self._zoom_mode_active = False
                    self.overlay.show()
                    # 원본 좌표에서 좌클릭 수행
                    pyautogui.click(abs_x, abs_y, _pause=False)
                    print(f"돋보기 클릭: ({abs_x}, {abs_y})")

            elif action == "zoom_cancel":
                if self._zoom_mode_active:
                    self.zoom_overlay.deactivate()
                    self._zoom_mode_active = False
                    self.overlay.show()
                    print("돋보기 모드 취소")

            # --- 기존 액션 ---
            elif action == "toggle_mouse":
                if not self.calib_overlay.isVisible():
                    self._mouse_control_enabled = not self._mouse_control_enabled
                    state = "ON" if self._mouse_control_enabled else "OFF"
                    print(f"마우스 제어: {state}")
                    self.overlay.set_mouse_control(self._mouse_control_enabled)
            elif action == "toggle_record":
                if self.recorder.is_recording:
                    self.recorder.stop()
                else:
                    self.recorder.start()
            elif action == "recalibrate":
                if not self.calib_overlay.isVisible():
                    print("재캘리브레이션 시작...")
                    self._timer.stop()
                    self._mouse_control_enabled = False
                    self.overlay.set_mouse_control(False)
                    self.calib_overlay.start()
            elif action == "exit":
                if self.calib_overlay.isVisible():
                    # 캘리브레이션 중 ESC → 캘리브레이션 취소만
                    self.calib_overlay._cancel()
                else:
                    self._exit()

    def _exit(self):
        """프로그램 종료"""
        print("프로그램 종료 중...")
        self._action_timer.stop()
        self._timer.stop()
        self.zoom_overlay.release()
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

        # 시선 절대 좌표를 항상 기억 (줌 트리거/클릭 시 사용)
        self._last_screen_x = screen_x
        self._last_screen_y = screen_y

        if self._zoom_mode_active:
            # ── 돋보기 정밀 모드 ──
            # 전체 화면 시선 좌표를 줌 팝업 위의 좌표로 간주하여
            # 원본 200×200 캡처 영역 내의 절대 좌표로 역변환합니다.
            abs_x, abs_y = self.zoom_overlay.gaze_to_absolute(
                screen_x, screen_y)

            # 줌 팝업 내 십자선 갱신
            self.zoom_overlay.update_gaze(screen_x, screen_y)

            # 마우스 커서를 원본 영역 내 정밀 좌표로 이동
            if self._mouse_control_enabled:
                pyautogui.moveTo(abs_x, abs_y, _pause=False)
        else:
            # ── 일반 추적 모드 ──
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
