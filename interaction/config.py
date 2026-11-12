"""页语 B方交互服务配置。

所有路径与开关均可通过环境变量覆盖；默认值面向本机单机演示。
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# A端生成服务地址（仅本机访问）
A_BASE_URL = os.getenv('YU_A_BASE_URL', 'http://127.0.0.1:8765')

# 交互服务监听地址
HOST = os.getenv('YU_INTERACTION_HOST', '127.0.0.1')
PORT = int(os.getenv('YU_INTERACTION_PORT', '8801'))

# 演示数据开关：允许返回 is_mock=True 的书目（界面必须显示"演示数据"角标）
ALLOW_MOCK = os.getenv('YU_ALLOW_MOCK', '0') == '1'

# 索引与缓存目录
STATE_DIR = Path(os.getenv('YU_INTERACTION_STATE_DIR', REPO_ROOT / 'interaction' / 'state'))
FRAME_CACHE_DIR = STATE_DIR / 'cache' / 'frames'

# 前端构建产物（存在则由交互服务托管，实现同源部署）
WEB_DIST = Path(os.getenv('YU_WEB_DIST', REPO_ROOT / 'web' / 'dist'))

# 本地 ASR CLI（Qwen3-ASR-1.7B，见 ~/models/asr/）
ASR_CLI = os.getenv('YU_ASR_CLI', str(Path.home() / 'models' / 'asr' / 'qwen-asr'))
ASR_TIMEOUT_S = float(os.getenv('YU_ASR_TIMEOUT_S', '12'))

# 演示模式：演示/评测期间跳过GPU长任务，交互短任务优先
DEMO_MODE = os.getenv('YU_DEMO_MODE', '0') == '1'

# 检索与点选的时间窗
DEFAULT_TIME_WINDOW_MS = int(os.getenv('YU_TIME_WINDOW_MS', '3000'))
TOUCH_FRAME_WINDOW_MS = int(os.getenv('YU_TOUCH_FRAME_WINDOW_MS', '1200'))

# 版本失效轮询间隔（秒）
SYNC_INTERVAL_S = float(os.getenv('YU_SYNC_INTERVAL_S', '30'))
