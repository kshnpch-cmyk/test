import os
import time
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

# ⚠️ 1단계에서 배포한 구글 앱스 스크립트 웹 앱 URL을 아래에 넣으세요!
WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbykxUSe-5RA20YOav7F5ohYZrD1739O7FgqJV2jO94HlKNk2fHz-7G2p9Kwew7SRqWi/exec"

options = webdriver.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')

driver = webdriver.Chrome(options=options)
driver.set_window_size(1920, 1080)

try:
    # 1. 로그인
    print("사이트 접속 중...")
    driver.get('https://admin.theborn.co.kr/oms-manager/login')
    time.sleep(2) 

    driver.find_element(By.ID, 'companyCd').send_keys('9000')
    driver.find_element(By.ID, 'userId').send_keys('admin')
    driver.find_element(By.ID, 'userPw').send_keys('1234' + Keys.ENTER)
    time.sleep(4) 

    # 2. 메뉴 이동
    driver.find_element(By.CSS_SELECTOR, "a[data-menu-id='BOR']").click()
    time.sleep(2) 

    driver.find_element(By.XPATH, "//*[contains(text(), '기간별 주문현황')]").click()
    time.sleep(5) 

    # 3. 날짜 설정
    target_start_date = os.environ.get('START_DATE', '2026/08/01')
    target_end_date = os.environ.get('END_DATE', '2026/08/31')

    print(f"날짜 세팅 진행: {target_start_date} ~ {target_end_date}")
    js_script = f"""
        var rangeInput = document.getElementsByName('BOR111_dateRange')[0];
        if(rangeInput) {{
            rangeInput.value = '{target_start_date} ~ {target_end_date}';
            rangeInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
        }}
        var startInput = document.getElementById('BOR111_startDt');
        var endInput = document.getElementById('BOR111_endDt');
        if(startInput) startInput.value = '{target_start_date}';
        if(endInput) endInput.value = '{target_end_date}';
    """
    driver.execute_script(js_script)
    time.sleep(2)

    # 4. F2 조회 실행
    body = driver.find_element(By.TAG_NAME, 'body')
    body.send_keys(Keys.F2)
    print("F2키로 데이터 조회 실행!")
    time.sleep(5) 

    # 5. HTML 테이블 데이터 추출
    html = driver.page_source
    soup = BeautifulSoup(html, 'html.parser')
    
    rows = []
    table_rows = soup.select('table tr') 
    for tr in table_rows:
        cols = [td.get_text(strip=True) for td in tr.select('td, th')]
        if cols:
            rows.append(cols)

    # 6. JSON 인증 없이 웹훅(HTTP POST)으로 구글 시트에 바로 전송
    if rows and "script.google.com" in WEBHOOK_URL:
        print("웹훅을 통해 구글 시트로 데이터 전송 중...")
        response = requests.post(WEBHOOK_URL, json=rows)
        if response.status_code == 200:
            print("✅ 구글 시트 '202608' 탭으로 데이터 전송 성공!")
        else:
            print(f"❌ 전송 실패 (상태 코드: {response.status_code})")

    driver.save_screenshot('capture.png')

except Exception as e:
    print(f"오류 발생: {e}")
    driver.save_screenshot('capture.png') 
    
finally:
    driver.quit()
