# 검증 범위

작성 환경에서 Python 코드 컴파일 및 자동 테스트 5개 통과:

- 그룹·주차별 DB 격리, 재제출 교체 및 오래된 update 재처리 방지
- 생성 요청 중복 방지 및 재시작 후 interrupted 기록
- 주차 계산과 잘못된 날짜 거절
- mock 원문 구분과 PPTX 토큰 교체 후 재열기, 기존 run 글꼴 크기 유지
- 입력에 포함된 토큰 문자열의 재해석 방지, 입력 한도 및 양식 누락 처리

python-telegram-bot 22.8, OpenAI Python SDK 2.54.0으로 모듈 import, Telegram Application 구성, Responses parse 메서드 및 Pydantic strict JSON 스키마 변환 확인. 실제 네트워크 요청은 하지 않았습니다.

실제 Bot Token, API Key, 회사 PPTX는 제공되지 않아 Telegram 송수신, AI 실호출, 회사 양식 시각 검증은 미실시. Docker 이미지 빌드·VM 실행 검증도 미실시입니다.
