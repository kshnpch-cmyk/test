from selenium import webdriver
from selenium.webdriver.common.by import By
import time

# 가상 브라우저 설정 (화면 없이 실행)
options = webdriver.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')

driver = webdriver.Chrome(options=options)
driver.set_window_size(1920, 1080) 

driver.get('https://gw.theborn.co.kr/gw/uat/uia/egovLoginUsr.do')

time.sleep(2) # 페이지 로딩 대기

# 1. 아이디와 비밀번호 입력 (여기에 본인 정보를 넣습니다)
# 주의: 'userId'와 'userPw'는 실제 해당 사이트의 입력창 ID(이름)로 바꿔야 합니다.
driver.find_element(By.ID, 'userId').send_keys('1220503 ')
driver.find_element(By.ID, 'userPw').send_keys('chanho0801!')

# 2. 로그인 버튼 클릭 (실제 로그인 버튼의 클래스나 ID로 변경 필요)
driver.find_element(By.CLASS_NAME, 'login_submit').click()

time.sleep(5) # 로그인 완료될 때까지 5초 대기

# 3. 화면 캡처 및 덮어쓰기
driver.save_screenshot('capture.png')
driver.quit()
