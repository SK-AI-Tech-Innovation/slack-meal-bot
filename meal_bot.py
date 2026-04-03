import requests
import datetime
import os
import sys
import base64
import time
import urllib3
import zoneinfo
from pathlib import Path

KST = zoneinfo.ZoneInfo("Asia/Seoul")

# self-signed cert 경고 무시
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# .env 파일 로드 (python-dotenv 없이 직접 파싱)
_env_path = Path(__file__).parent / '.env'
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key.strip(), value)

# ==========================================
# 설정 (로컬: .env / Cloud Run: 환경변수)
# ==========================================
SLACK_WEBHOOK_URL = os.environ.get('SLACK_WEBHOOK_URL', '')

# GitHub 설정 (이미지 호스팅용)
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_REPO = os.environ.get('GITHUB_REPO', 'SK-AI-Tech-Innovation/slack-meal-bot')
GITHUB_BRANCH = os.environ.get('GITHUB_BRANCH', 'main')
GITHUB_PAGES_BASE = os.environ.get('GITHUB_PAGES_BASE', 'https://sk-ai-tech-innovation.github.io/slack-meal-bot/images')

# 식당 정보
CAMPUS_CODE = os.environ.get('CAMPUS_CODE', 'BD')
CAFETERIA_SEQ = os.environ.get('CAFETERIA_SEQ', '21')
MEAL_TYPE = os.environ.get('MEAL_TYPE', 'LN')

# 이미지 저장 경로
IMAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'images')

# 이미지 다운로드 재시도 설정
MAX_RETRIES = 3
RETRY_DELAY = 5  # 초


def get_operating_hours():
    """menu_obj.js에서 식당 운영시간 조회"""
    import re
    url = 'https://mc.skhystec.com/V3/js/menu_obj.js'
    meal_type_key = {'LN': 'T_L', 'BF': 'T_B', 'DN': 'T_D', 'SN': 'T_S'}
    try:
        resp = requests.get(url, verify=False, timeout=10)
        text = resp.content.decode('utf-8', errors='replace')
        # cafeteriaSeq에 해당하는 블록 찾기
        pattern = rf"index:\s*{CAFETERIA_SEQ}.*?name:\s*'([^']*)'.*?{meal_type_key.get(MEAL_TYPE, 'T_L')}:\s*'([^']*)'"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            name = match.group(1).strip()
            hours = match.group(2).replace('<br>', '').replace('|', '/').strip()
            return name, hours
    except Exception as e:
        print(f"  운영시간 조회 실패: {e}")
    return None, None


def get_today_menu():
    """mc.skhystec.com에서 오늘의 메뉴 목록 조회"""
    url = 'https://mc.skhystec.com/V3/prc/selectMenuList.prc'
    today_str = datetime.datetime.now(KST).strftime("%Y%m%d")
    data = {
        'campus': CAMPUS_CODE,
        'cafeteriaSeq': CAFETERIA_SEQ,
        'mealType': MEAL_TYPE,
        'ymd': today_str
    }
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(url, data=data, verify=False, timeout=30)
            response.raise_for_status()
            return response.json().get('menuList', [])
        except Exception as e:
            print(f"  메뉴 조회 실패 (시도 {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
    return None


def build_source_image_url(save_file_nm):
    """원본 이미지 URL 생성 (mc.skhystec.com)"""
    if not save_file_nm:
        return ""
    parts = save_file_nm.split("_")
    if len(parts) >= 4:
        return f"https://mc.skhystec.com/nsf/menuImage/{parts[0]}/{parts[1]}/{parts[2]}/{parts[3]}/{save_file_nm}"
    return ""


def download_images(menu_list):
    """메뉴 이미지를 로컬 images/ 폴더에 다운로드 (재시도 포함)"""
    os.makedirs(IMAGES_DIR, exist_ok=True)

    # 기존 이미지 삭제
    for f in os.listdir(IMAGES_DIR):
        if f.endswith(('.jpg', '.png', '.jpeg')):
            os.remove(os.path.join(IMAGES_DIR, f))

    today_str = datetime.datetime.now(KST).strftime("%Y%m%d")
    downloaded = {}  # course_name -> (파일명, 바이트데이터)
    for idx, item in enumerate(menu_list):
        course = item.get('COURSE_NAME', '').strip()
        save_file_nm = item.get('SAVE_FILE_NM', '')
        source_url = build_source_image_url(save_file_nm)
        if not source_url or not course:
            continue

        filename = f"course_{idx}_{today_str}.jpg"
        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.get(source_url, verify=False, timeout=15)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    filepath = os.path.join(IMAGES_DIR, filename)
                    with open(filepath, 'wb') as f:
                        f.write(resp.content)
                    downloaded[course] = (filename, resp.content)
                    print(f"  📷 {course} 이미지 다운로드 완료 ({len(resp.content)} bytes)")
                    break
                else:
                    print(f"  ⚠️ {course} 이미지 응답 이상 (status={resp.status_code}, size={len(resp.content)})")
            except Exception as e:
                print(f"  ⚠️ {course} 이미지 다운로드 실패 (시도 {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)

    return downloaded


def push_images_to_github(downloaded):
    """GitHub API로 이미지를 repo에 push (git 없이 HTTP API 사용)"""
    if not GITHUB_TOKEN or not downloaded:
        return

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    for course, (filename, content) in downloaded.items():
        path = f"images/{filename}"
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"

        # 기존 파일의 SHA 확인 (업데이트 시 필요)
        sha = None
        get_resp = requests.get(api_url, headers=headers)
        if get_resp.status_code == 200:
            sha = get_resp.json().get("sha")

        payload = {
            "message": f"chore: update {filename}",
            "content": base64.b64encode(content).decode(),
            "branch": GITHUB_BRANCH
        }
        if sha:
            payload["sha"] = sha

        put_resp = requests.put(api_url, headers=headers, json=payload)
        if put_resp.status_code in (200, 201):
            print(f"  📤 {course} GitHub push 완료")
        else:
            print(f"  ❌ {course} GitHub push 실패: {put_resp.status_code} {put_resp.text[:100]}")


def cleanup_github_images():
    """전송 완료 후 GitHub repo의 이미지 파일 삭제 (다음 날 잔여 이미지 방지)"""
    if not GITHUB_TOKEN:
        return

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    # images/ 디렉토리의 파일 목록 조회
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/images"
    resp = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH})
    if resp.status_code != 200:
        print(f"  ⚠️ GitHub 이미지 목록 조회 실패: {resp.status_code}")
        return

    for item in resp.json():
        if item.get("name", "").startswith("course_") and item.get("name", "").endswith(".jpg"):
            delete_url = item["url"]
            payload = {
                "message": f"chore: cleanup {item['name']}",
                "sha": item["sha"],
                "branch": GITHUB_BRANCH
            }
            del_resp = requests.delete(delete_url, headers=headers, json=payload)
            if del_resp.status_code == 200:
                print(f"  🗑️ {item['name']} 삭제 완료")
            else:
                print(f"  ⚠️ {item['name']} 삭제 실패: {del_resp.status_code}")


def wait_for_pages_deploy():
    """GitHub Pages 배포 완료 대기"""
    if not GITHUB_TOKEN:
        return
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    print("  ⏳ GitHub Pages 배포 대기 중...")
    for i in range(12):  # 최대 60초 대기
        time.sleep(5)
        resp = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/pages/builds/latest",
            headers=headers
        )
        if resp.status_code == 200:
            status = resp.json().get("status")
            if status == "built":
                print("  ✅ Pages 배포 완료")
                return
            print(f"    배포 상태: {status}...")
    print("  ⚠️ Pages 배포 대기 타임아웃 (전송은 계속 진행)")


def send_to_slack(menu_list, downloaded_images, operating_hours=None):
    """Webhook으로 슬랙에 메뉴 전송 (이미지는 GitHub Pages URL)"""
    weekdays = ['월', '화', '수', '목', '금', '토', '일']
    now = datetime.datetime.now(KST)
    today_str = now.strftime("%Y년 %m월 %d일") + f"({weekdays[now.weekday()]})"
    meal_name = {'LN': '점심', 'BF': '조식', 'DN': '석식', 'SN': '야식'}.get(MEAL_TYPE, '식사')
    colors = ["#FF9900", "#33CC33", "#3366FF", "#FF3366", "#9933CC", "#00BFFF", "#FFD700"]
    attachments = []

    for idx, item in enumerate(menu_list):
        course = item.get('COURSE_NAME', '?')
        menu_name = item.get('MENU_NAME', '?')
        sides = [item.get(f'SIDE_{i}', '').strip() for i in range(1, 7) if item.get(f'SIDE_{i}')]
        sides_str = ", ".join(sides) if sides else "-"
        kcal = item.get('KCAL', '')
        kcal_str = f" ({kcal}kcal)" if kcal else ""

        attachment = {
            "color": colors[idx % len(colors)],
            "title": f"{course}: {menu_name}{kcal_str}",
            "text": f"🍽️ {sides_str}",
            "fallback": f"{course} 메뉴"
        }

        # GitHub Pages URL로 이미지 첨부
        img_data = downloaded_images.get(course)
        if img_data:
            filename = img_data[0] if isinstance(img_data, tuple) else img_data
            attachment["image_url"] = f"{GITHUB_PAGES_BASE}/{filename}"

        attachments.append(attachment)

    # context 요소 구성
    context_elements = [{"type": "mrkdwn", "text": f"📍 *비원(분당캠퍼스)*"}]
    if operating_hours:
        context_elements.append({"type": "mrkdwn", "text": f"🕐 {operating_hours}"})

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"📅 {today_str}  🍱 {meal_name}",
                    "emoji": True
                }
            },
            {
                "type": "context",
                "elements": context_elements
            },
            {"type": "divider"}
        ],
        "attachments": attachments
    }
    response = requests.post(SLACK_WEBHOOK_URL, json=payload)
    response.raise_for_status()
    print("✅ 슬랙 전송 성공!")


def get_existing_images(menu_list):
    """images/ 폴더의 이미지를 메뉴 코너명에 매핑"""
    result = {}
    if not os.path.exists(IMAGES_DIR):
        return result
    today_str = datetime.datetime.now(KST).strftime("%Y%m%d")
    for idx, item in enumerate(menu_list):
        course = item.get('COURSE_NAME', '').strip()
        filename = f"course_{idx}_{today_str}.jpg"
        if os.path.exists(os.path.join(IMAGES_DIR, filename)):
            result[course] = filename
    return result


def count_menu_images(menu_list):
    """이미지가 업로드된 메뉴 코너 수 반환 (SAVE_FILE_NM 기준)"""
    count = 0
    for item in menu_list:
        if item.get('SAVE_FILE_NM', '').strip():
            count += 1
    return count


def run_with_image_check():
    """
    11:00~11:10: 모든 코너 이미지가 올라왔는지 주기적 체크, 올라오면 바로 전송
    11:11: 이미지가 모두 준비되지 않았으면 있는 것만으로 전송
    """
    deadline_minute = 11  # 11:11 이후에는 있는 이미지만으로 전송
    check_interval = 60   # 60초마다 체크

    print("🕐 이미지 체크 모드 시작...")

    while True:
        now = datetime.datetime.now(KST)

        print(f"\n[{now.strftime('%H:%M:%S')}] 메뉴 조회 중...")
        menu_data = get_today_menu()

        if menu_data is None:
            print("  식단을 가져오지 못했습니다. 재시도...")
            time.sleep(check_interval)
            continue

        if not menu_data:
            print("  오늘은 메뉴가 없습니다.")
            return

        total_courses = len(menu_data)
        uploaded_images = count_menu_images(menu_data)
        print(f"  메뉴 {total_courses}개, 이미지 업로드됨 {uploaded_images}/{total_courses}개")

        # 이미지가 모두 업로드되지 않았으면 대기, deadline 지나면 있는 것만으로 진행
        if uploaded_images < total_courses:
            now = datetime.datetime.now(KST)
            past_deadline = now.hour >= 11 and now.minute >= deadline_minute
            if not past_deadline:
                print(f"  이미지 미완료 ({uploaded_images}/{total_courses}). {check_interval}초 후 재시도...")
                time.sleep(check_interval)
                continue
            else:
                print(f"  ⏰ 11:{deadline_minute:02d} 경과 — 이미지 {uploaded_images}/{total_courses}개만 업로드됨, 있는 것만으로 전송")

        # 이미지 다운로드
        downloaded = download_images(menu_data)
        actual_images = len(downloaded)
        print(f"  실제 다운로드: {actual_images}/{total_courses}개")

        # 다운로드 실패한 이미지가 있으면 재시도, deadline 지나면 있는 것만으로 진행
        if actual_images < total_courses:
            now = datetime.datetime.now(KST)
            past_deadline = now.hour >= 11 and now.minute >= deadline_minute
            if not past_deadline:
                print(f"  다운로드 미완료 ({actual_images}/{total_courses}). {check_interval}초 후 재시도...")
                time.sleep(check_interval)
                continue
            else:
                print(f"  ⏰ 11:{deadline_minute:02d} 경과 — 다운로드 {actual_images}/{total_courses}개만 성공, 있는 것만으로 전송")

        # GitHub: 기존 이미지 정리 → 새 이미지 push → Pages 배포 대기
        if GITHUB_TOKEN:
            print("  🗑️ GitHub 기존 이미지 정리 중...")
            cleanup_github_images()
            if downloaded:
                print("  GitHub에 이미지 push 중...")
                push_images_to_github(downloaded)
                wait_for_pages_deploy()

        _, hours = get_operating_hours()
        print(f"  슬랙 전송 중... (이미지 {actual_images}개)")
        send_to_slack(menu_data, downloaded, operating_hours=hours)
        return


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else None

    if not SLACK_WEBHOOK_URL:
        print("❌ SLACK_WEBHOOK_URL 환경변수를 설정해주세요.")
        exit(1)

    # 운영시간 조회
    _, hours = get_operating_hours()
    if hours:
        print(f"운영시간: {hours}")

    if mode == '--check':
        run_with_image_check()
    elif mode == '--send-only':
        print("오늘의 식단 가져오는 중...")
        menu_data = get_today_menu()
        if not menu_data:
            print("메뉴가 없습니다.")
            exit(0)
        downloaded = get_existing_images(menu_data)
        print(f"슬랙 전송 중... (이미지 {len(downloaded)}개)")
        send_to_slack(menu_data, downloaded, operating_hours=hours)
    else:
        print("오늘의 식단 가져오는 중...")
        menu_data = get_today_menu()
        if menu_data is None:
            print("식단을 가져오지 못했습니다.")
            exit(1)
        if not menu_data:
            print("오늘은 메뉴가 없습니다.")
            exit(0)

        print("이미지 다운로드 중...")
        downloaded = download_images(menu_data)

        # GitHub: 기존 이미지 정리 → 새 이미지 push → Pages 배포 대기
        if GITHUB_TOKEN:
            print("🗑️ GitHub 기존 이미지 정리 중...")
            cleanup_github_images()
            if downloaded:
                print("GitHub에 이미지 push 중...")
                push_images_to_github(downloaded)
                wait_for_pages_deploy()

        print(f"슬랙 전송 중... (이미지 {len(downloaded)}개)")
        send_to_slack(menu_data, downloaded, operating_hours=hours)
