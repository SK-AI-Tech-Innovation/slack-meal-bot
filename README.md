# Slack Meal Bot

SK hystemc 구내식당(비원 분당캠퍼스) 메뉴를 크롤링하여 Slack 채널에 자동 전송하는 봇입니다.

![Slack 메시지 예시](https://sk-ai-tech-innovation.github.io/slack-meal-bot/images/course_0.jpg)

## 주요 기능

- **메뉴 자동 크롤링**: mc.skhystec.com에서 당일 식단 정보 조회
- **이미지 첨부**: 각 코너별 메뉴 이미지를 GitHub Pages로 호스팅하여 Slack에 표시
- **운영시간 표시**: `menu_obj.js`에서 식당 운영시간 파싱
- **이미지 체크 모드**: 11:00~11:10 이미지 업로드 대기, 11:11 이후 강제 전송
- **자동 스케줄링**: Google Cloud Run + Cloud Scheduler로 평일 11:00 KST 자동 실행

## 아키텍처

```
Cloud Scheduler (평일 11:00 KST)
  └─ Cloud Run Job (서울 리전)
       ├─ mc.skhystec.com 메뉴 크롤링
       ├─ 이미지 다운로드 + GitHub Pages push
       └─ Slack Webhook 전송
```

## 실행 모드

```bash
# 기본: 메뉴 조회 → 이미지 다운로드 → GitHub push → Slack 전송
python meal_bot.py

# 이미지 체크 모드 (Cloud Run용): 이미지 대기 후 전송
python meal_bot.py --check

# 전송만 (로컬 이미지 사용)
python meal_bot.py --send-only
```

## 설정

`.env.example`을 `.env`로 복사 후 값을 채워주세요.

```bash
cp .env.example .env
```

| 변수 | 설명 |
|------|------|
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |
| `GITHUB_TOKEN` | GitHub PAT (repo 스코프, 이미지 push용) |
| `GITHUB_REPO` | GitHub 저장소 (예: `org/repo`) |
| `GITHUB_BRANCH` | 브랜치명 (기본: `main`) |
| `CAMPUS_CODE` | 캠퍼스 코드 (기본: `BD`) |
| `CAFETERIA_SEQ` | 식당 번호 (기본: `21`, 비원) |
| `MEAL_TYPE` | 식사 유형 (`BF`/`LN`/`DN`/`SN`) |

## Cloud Run 배포

```bash
# 빌드 & 배포
gcloud run jobs deploy slack-meal-bot \
  --source . \
  --region asia-northeast3 \
  --project <PROJECT_ID> \
  --set-env-vars "SLACK_WEBHOOK_URL=...,GITHUB_TOKEN=...,GITHUB_REPO=...,GITHUB_PAGES_BASE=..."

# 스케줄러 등록 (평일 11:00 KST)
gcloud scheduler jobs create http meal-bot-schedule \
  --location asia-northeast3 \
  --schedule "0 11 * * 1-5" \
  --time-zone "Asia/Seoul" \
  --uri "https://asia-northeast3-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/<PROJECT_ID>/jobs/slack-meal-bot:run" \
  --http-method POST \
  --oauth-service-account-email <SERVICE_ACCOUNT>

# 수동 테스트
gcloud run jobs execute slack-meal-bot --region asia-northeast3
```
