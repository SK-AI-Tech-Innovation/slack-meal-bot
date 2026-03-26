import requests
import datetime
import os
import sys
import urllib3
from pathlib import Path

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
# 설정 (로컬: .env / GitHub Actions: secrets)
# ==========================================
SLACK_WEBHOOK_URL = os.environ.get('SLACK_WEBHOOK_URL', '')

# GitHub Pages URL 베이스 (이미지 호스팅용)
GITHUB_PAGES_BASE = os.environ.get('GITHUB_PAGES_BASE', 'https://sk-ai-tech-innovation.github.io/slack-meal-bot/images')

# 식당 정보
CAMPUS_CODE = os.environ.get('CAMPUS_CODE', 'BD')
CAFETERIA_SEQ = os.environ.get('CAFETERIA_SEQ', '21')
MEAL_TYPE = os.environ.get('MEAL_TYPE', 'LN')

# 이미지 저장 경로
IMAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'images')


def get_today_menu():
    """mc.skhystec.com에서 오늘의 메뉴 목록 조회"""
    url = 'https://mc.skhystec.com/V3/prc/selectMenuList.prc'
    today_str = datetime.datetime.now().strftime("%Y%m%d")
    data = {
        'campus': CAMPUS_CODE,
        'cafeteriaSeq': CAFETERIA_SEQ,
        'mealType': MEAL_TYPE,
        'ymd': today_str
    }
    try:
        response = requests.post(url, data=data, verify=False)
        response.raise_for_status()
        return response.json().get('menuList', [])
    except Exception as e:
        print(f"메뉴를 가져오는 도중 오류 발생: {e}")
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
    """메뉴 이미지를 로컬 images/ 폴더에 다운로드"""
    os.makedirs(IMAGES_DIR, exist_ok=True)

    # 기존 이미지 삭제
    for f in os.listdir(IMAGES_DIR):
        if f.endswith(('.jpg', '.png', '.jpeg')):
            os.remove(os.path.join(IMAGES_DIR, f))

    downloaded = {}  # course_name -> 파일명
    for idx, item in enumerate(menu_list):
        course = item.get('COURSE_NAME', '').strip()
        save_file_nm = item.get('SAVE_FILE_NM', '')
        source_url = build_source_image_url(save_file_nm)
        if not source_url or not course:
            continue

        try:
            resp = requests.get(source_url, verify=False, timeout=10)
            if resp.status_code == 200 and len(resp.content) > 1000:
                # 파일명: 인덱스 기반 영문 (GitHub raw URL 호환)
                filename = f"course_{idx}.jpg"
                filepath = os.path.join(IMAGES_DIR, filename)
                with open(filepath, 'wb') as f:
                    f.write(resp.content)
                downloaded[course] = filename
                print(f"  📷 {course} 이미지 다운로드 완료 ({len(resp.content)} bytes)")
        except Exception as e:
            print(f"  ⚠️ {course} 이미지 다운로드 실패: {e}")

    return downloaded


def send_to_slack(menu_list, downloaded_images):
    """Webhook으로 슬랙에 메뉴 전송 (이미지는 GitHub raw URL)"""
    today_str = datetime.datetime.now().strftime("%Y년 %m월 %d일")
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

        # GitHub raw URL로 이미지 첨부
        filename = downloaded_images.get(course)
        if filename:
            attachment["image_url"] = f"{GITHUB_PAGES_BASE}/{filename}"

        attachments.append(attachment)

    payload = {
        "text": f"🍱 *{today_str} 비원(분당캠퍼스) 점심 메뉴*",
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
    for idx, item in enumerate(menu_list):
        course = item.get('COURSE_NAME', '').strip()
        filename = f"course_{idx}.jpg"
        if os.path.exists(os.path.join(IMAGES_DIR, filename)):
            result[course] = filename
    return result


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else None

    if not SLACK_WEBHOOK_URL:
        print("❌ SLACK_WEBHOOK_URL 환경변수를 설정해주세요.")
        exit(1)

    print("오늘의 식단 가져오는 중...")
    menu_data = get_today_menu()

    if menu_data is None:
        print("식단을 가져오지 못했습니다.")
        exit(1)

    if not menu_data:
        print("오늘은 메뉴가 없습니다.")
        exit(0)

    if mode == '--download-only':
        # GitHub Actions: 이미지만 다운로드 (commit & push 후 별도 단계에서 전송)
        print("이미지 다운로드 중...")
        download_images(menu_data)
    elif mode == '--send-only':
        # GitHub Actions: 이미 push된 이미지의 raw URL로 슬랙 전송
        downloaded = get_existing_images(menu_data)
        print(f"슬랙 전송 중... (이미지 {len(downloaded)}개)")
        send_to_slack(menu_data, downloaded)
    else:
        # 로컬 실행: 다운로드 + 전송 한번에
        print("이미지 다운로드 중...")
        downloaded = download_images(menu_data)
        print(f"슬랙 전송 중... (이미지 {len(downloaded)}개)")
        send_to_slack(menu_data, downloaded)
