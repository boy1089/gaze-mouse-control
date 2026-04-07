"""
monitor_mapper.py - 듀얼 모니터 매핑 모듈
screeninfo를 사용하여 모니터 정보를 가져오고,
0~1 비율 좌표를 전체 가상 데스크톱의 절대 픽셀 좌표로 변환합니다.
"""

from screeninfo import get_monitors


class MonitorMapper:
    """듀얼(멀티) 모니터 좌표 매핑"""

    def __init__(self):
        self.monitors = get_monitors()
        self._calculate_virtual_desktop()

    def _calculate_virtual_desktop(self):
        """전체 가상 데스크톱 영역(바운딩 박스)을 계산합니다."""
        if not self.monitors:
            raise RuntimeError("모니터를 찾을 수 없습니다.")

        # 가상 데스크톱의 좌상단/우하단 좌표 계산
        min_x = min(m.x for m in self.monitors)
        min_y = min(m.y for m in self.monitors)
        max_x = max(m.x + m.width for m in self.monitors)
        max_y = max(m.y + m.height for m in self.monitors)

        self.virtual_x = min_x
        self.virtual_y = min_y
        self.virtual_width = max_x - min_x
        self.virtual_height = max_y - min_y

    def ratio_to_screen(self, ratio_x: float, ratio_y: float) -> tuple[int, int]:
        """
        0~1 비율 좌표를 가상 데스크톱의 절대 픽셀 좌표로 변환합니다.

        Args:
            ratio_x: 수평 비율 (0.0=왼쪽 끝, 1.0=오른쪽 끝)
            ratio_y: 수직 비율 (0.0=위쪽 끝, 1.0=아래쪽 끝)

        Returns:
            (pixel_x, pixel_y): 절대 픽셀 좌표
        """
        pixel_x = int(self.virtual_x + ratio_x * self.virtual_width)
        pixel_y = int(self.virtual_y + ratio_y * self.virtual_height)
        return pixel_x, pixel_y

    def get_info_string(self) -> str:
        """모니터 정보를 문자열로 반환합니다."""
        lines = [f"감지된 모니터 수: {len(self.monitors)}"]
        for i, m in enumerate(self.monitors):
            lines.append(
                f"  모니터 {i + 1}: {m.width}x{m.height} @ ({m.x}, {m.y})"
                f"{' [주 모니터]' if m.is_primary else ''}"
            )
        lines.append(
            f"가상 데스크톱: {self.virtual_width}x{self.virtual_height}"
            f" (원점: {self.virtual_x}, {self.virtual_y})"
        )
        return "\n".join(lines)
