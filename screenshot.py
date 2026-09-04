from selenium import webdriver
from selenium.webdriver.common.by import By
import time

options = webdriver.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')

driver = webdriver.Chrome(options=options)
driver.set_window_size(1920, 1080) 

# 1. 새로운 관리자 사이트로 접속
driver.get('https://admin.theborn.co.kr/oms-manager/login')
time.sleep(2) 

# 2. 회사코드, 아이디, 비밀번호 입력
# [주의] 아래 '회사코드_ID', '아이디_ID', '비밀번호_ID' 부분은 실제 웹사이트의 F12(개발자 도구)를 눌러서 확인한 진짜 이름으로 바꿔야 합니다.
driver.find_element(By.ID, '회사코드_ID').send_keys('9000')
driver.find_element(By.ID, '아이디_ID').send_keys('admin')
driver.find_element(By.ID, '비밀번호_ID').send_keys('1234')

# 3. 로그인 버튼 클릭
# [주의] '로그인버튼_클래스' 부분도 실제 버튼의 코드로 바꿔주세요.
driver.find_element(By.CLASS_NAME, '로그인버튼_클래스').click()

time.sleep(5) # 로그인 완료 대기

# 4. 캡처 후 종료
driver.save_screenshot('capture.png')
driver.quit()
