"""
gaze_tracker.py - 시선 추적 모듈
MediaPipe FaceLandmarker (tasks API)를 사용하여 웹캠에서 홍채 위치를 추출하고,
이동 평균 필터로 스무딩된 시선 좌표(0~1 비율)를 반환합니다.
"""

import os
import cv2
import numpy as np
import mediapipe as mp
from collections import deque


# MediaPipe Face Mesh 홍채 랜드마크 인덱스
# 왼쪽 홍채: 468~472, 오른쪽 홍채: 473~477
LEFT_IRIS = [468, 469, 470, 471, 472]
RIGHT_IRIS = [473, 474, 475, 476, 477]

# 왼쪽 눈 윤곽 (수평 경계용)
LEFT_EYE_INNER = 133
LEFT_EYE_OUTER = 33
LEFT_EYE_TOP = 159
LEFT_EYE_BOTTOM = 145

# 오른쪽 눈 윤곽 (수평 경계용)
RIGHT_EYE_INNER = 362
RIGHT_EYE_OUTER = 263
RIGHT_EYE_TOP = 386
RIGHT_EYE_BOTTOM = 374

# 모델 파일 경로
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "face_landmarker.task")


class GazeTracker:
    """웹캠 기반 시선 추적기"""

    def __init__(self, camera_index: int = 0, smoothing_window: int = 8):
        """
        Args:
            camera_index: 웹캠 장치 인덱스
            smoothing_window: 이동 평균 필터에 사용할 프레임 수
        """
        self.cap = cv2.VideoCapture(camera_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)

        base_options = mp.tasks.BaseOptions(model_asset_path=MODEL_PATH)
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=base_options,
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_faces=1,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
            min_face_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self.face_landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)
        self._timestamp_ms = 0

        # 이동 평균 필터용 버퍼
        self._x_buffer = deque(maxlen=smoothing_window)
        self._y_buffer = deque(maxlen=smoothing_window)

        self._running = True

        # 디버그용: 마지막 프레임/랜드마크 저장
        self._last_frame = None
        self._last_landmarks = None
        self._last_iris_ratio = None

    def _get_iris_ratio(self, landmarks, img_w: int, img_h: int):
        """
        양쪽 눈의 홍채 위치를 눈 경계 내 비율(0~1)로 계산합니다.

        Returns:
            (ratio_x, ratio_y) 또는 None (얼굴 미검출 시)
        """
        # 왼쪽 눈 홍채 중심
        left_iris_pts = np.array(
            [(landmarks[i].x * img_w, landmarks[i].y * img_h) for i in LEFT_IRIS]
        )
        left_iris_center = left_iris_pts.mean(axis=0)

        # 오른쪽 눈 홍채 중심
        right_iris_pts = np.array(
            [(landmarks[i].x * img_w, landmarks[i].y * img_h) for i in RIGHT_IRIS]
        )
        right_iris_center = right_iris_pts.mean(axis=0)

        # 왼쪽 눈 경계
        l_inner = np.array([landmarks[LEFT_EYE_INNER].x * img_w, landmarks[LEFT_EYE_INNER].y * img_h])
        l_outer = np.array([landmarks[LEFT_EYE_OUTER].x * img_w, landmarks[LEFT_EYE_OUTER].y * img_h])
        l_top = np.array([landmarks[LEFT_EYE_TOP].x * img_w, landmarks[LEFT_EYE_TOP].y * img_h])
        l_bottom = np.array([landmarks[LEFT_EYE_BOTTOM].x * img_w, landmarks[LEFT_EYE_BOTTOM].y * img_h])

        # 오른쪽 눈 경계
        r_inner = np.array([landmarks[RIGHT_EYE_INNER].x * img_w, landmarks[RIGHT_EYE_INNER].y * img_h])
        r_outer = np.array([landmarks[RIGHT_EYE_OUTER].x * img_w, landmarks[RIGHT_EYE_OUTER].y * img_h])
        r_top = np.array([landmarks[RIGHT_EYE_TOP].x * img_w, landmarks[RIGHT_EYE_TOP].y * img_h])
        r_bottom = np.array([landmarks[RIGHT_EYE_BOTTOM].x * img_w, landmarks[RIGHT_EYE_BOTTOM].y * img_h])

        # 왼쪽 눈 내 홍채 비율 (웹캠은 좌우 반전이므로 x 반전)
        left_eye_w = np.linalg.norm(l_inner - l_outer)
        left_eye_h = np.linalg.norm(l_top - l_bottom)
        if left_eye_w < 1 or left_eye_h < 1:
            return None

        l_ratio_x = (left_iris_center[0] - l_outer[0]) / left_eye_w
        l_ratio_y = (left_iris_center[1] - l_top[1]) / left_eye_h

        # 오른쪽 눈 내 홍채 비율
        right_eye_w = np.linalg.norm(r_inner - r_outer)
        right_eye_h = np.linalg.norm(r_top - r_bottom)
        if right_eye_w < 1 or right_eye_h < 1:
            return None

        r_ratio_x = (right_iris_center[0] - r_outer[0]) / right_eye_w
        r_ratio_y = (right_iris_center[1] - r_top[1]) / right_eye_h

        # 양쪽 눈의 평균
        avg_x = (l_ratio_x + r_ratio_x) / 2.0
        avg_y = (l_ratio_y + r_ratio_y) / 2.0

        return avg_x, avg_y

    def get_gaze_position(self):
        """
        현재 프레임에서 시선 위치를 가져옵니다.

        Returns:
            (gaze_x, gaze_y): 0.0~1.0 범위의 스무딩된 시선 좌표
            None: 시선 추적 실패 시
        """
        if not self._running:
            return None

        ret, frame = self.cap.read()
        if not ret:
            return None

        # 좌우 반전 (거울 모드)
        frame = cv2.flip(frame, 1)
        img_h, img_w = frame.shape[:2]

        # BGR → RGB 변환 후 처리
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        self._timestamp_ms += 33  # ~30fps increment
        results = self.face_landmarker.detect_for_video(mp_image, self._timestamp_ms)

        # 디버그용: 프레임 저장
        self._last_frame = frame.copy()

        if not results.face_landmarks:
            self._last_landmarks = None
            self._last_iris_ratio = None
            return None

        landmarks = results.face_landmarks[0]
        self._last_landmarks = landmarks
        iris_ratio = self._get_iris_ratio(landmarks, img_w, img_h)

        if iris_ratio is None:
            self._last_iris_ratio = None
            return None

        raw_x, raw_y = iris_ratio
        self._last_iris_ratio = (raw_x, raw_y)

        # 홍채 비율을 화면 좌표 비율로 변환
        # 홍채가 눈 안에서 움직이는 범위는 약 0.25~0.75 정도이므로 스케일링
        # 민감도 조절을 위한 매핑 (중앙 기준 확장)
        center_x, center_y = 0.5, 0.5
        sensitivity_x = 2.5  # 수평 민감도
        sensitivity_y = 3.0  # 수직 민감도

        screen_x = center_x + (raw_x - center_x) * sensitivity_x
        screen_y = center_y + (raw_y - center_y) * sensitivity_y

        # 0~1 범위로 클리핑
        screen_x = np.clip(screen_x, 0.0, 1.0)
        screen_y = np.clip(screen_y, 0.0, 1.0)

        # 이동 평균 필터 적용
        self._x_buffer.append(screen_x)
        self._y_buffer.append(screen_y)

        smoothed_x = sum(self._x_buffer) / len(self._x_buffer)
        smoothed_y = sum(self._y_buffer) / len(self._y_buffer)

        return smoothed_x, smoothed_y

    def get_debug_frame(self):
        """
        디버그용 시각화 프레임을 반환합니다.
        웹캠 프레임 위에 눈 경계, 홍채 랜드마크, iris ratio를 그립니다.

        Returns:
            np.ndarray (BGR) 또는 None
        """
        if self._last_frame is None:
            return None

        frame = self._last_frame.copy()
        img_h, img_w = frame.shape[:2]

        if self._last_landmarks is None:
            cv2.putText(frame, "No face detected", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            return frame

        landmarks = self._last_landmarks

        # --- 눈 경계 그리기 (초록색) ---
        for eye_indices in [
            (LEFT_EYE_OUTER, LEFT_EYE_TOP, LEFT_EYE_INNER, LEFT_EYE_BOTTOM),
            (RIGHT_EYE_OUTER, RIGHT_EYE_TOP, RIGHT_EYE_INNER, RIGHT_EYE_BOTTOM),
        ]:
            pts = []
            for idx in eye_indices:
                x = int(landmarks[idx].x * img_w)
                y = int(landmarks[idx].y * img_h)
                pts.append((x, y))
            pts_arr = np.array(pts, dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(frame, [pts_arr], isClosed=True,
                          color=(0, 255, 0), thickness=1)

        # --- 홍채 랜드마크 그리기 (빨간 점) + 중심 (노란 원) ---
        for iris_indices in [LEFT_IRIS, RIGHT_IRIS]:
            iris_pts = []
            for idx in iris_indices:
                x = int(landmarks[idx].x * img_w)
                y = int(landmarks[idx].y * img_h)
                cv2.circle(frame, (x, y), 2, (0, 0, 255), -1)
                iris_pts.append((x, y))
            # 홍채 중심 (노란 원)
            center = np.array(iris_pts).mean(axis=0).astype(int)
            cv2.circle(frame, tuple(center), 4, (0, 255, 255), 1)

        # --- Iris ratio 텍스트 ---
        if self._last_iris_ratio is not None:
            rx, ry = self._last_iris_ratio
            cv2.putText(frame, f"Iris ratio: ({rx:.3f}, {ry:.3f})", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        return frame

    def release(self):
        """리소스 해제"""
        self._running = False
        if self.cap.isOpened():
            self.cap.release()
        self.face_landmarker.close()
