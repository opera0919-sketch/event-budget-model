"""테스트용 경로 부트스트랩 (프로젝트 루트를 sys.path에 추가)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
