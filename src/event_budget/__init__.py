"""event_budget — 증권사 이벤트 신청고객수·예산 예측 엔진.

구성:
- schema:   이벤트 명세(YAML) + 실적 CSV 로더/검증
- demand:   신청고객수 2단계 간접 추정(기준 고객수 투영 → 시즌 take-rate)
- budget:   조정 가능한 3대 레버(목표달성률·지급률·평균리워드)로 예산 산출
- scenario: 레버 grid 스윕·시나리오 매트릭스·민감도(tornado)
- simulate: 몬테카를로 예산 분포
- calibrate: 실적으로 시즌 take-rate 보정
- report:   한국어 마크다운 리포트 생성
"""

__all__ = [
    "schema",
    "demand",
    "budget",
    "scenario",
    "simulate",
    "calibrate",
    "report",
]
