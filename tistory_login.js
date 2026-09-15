const { chromium } = require('playwright');

(async () => {
  console.log("🚀 크로미늄 브라우저를 시작합니다...");
  
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    // 1. 티스토리 메인 이동
    console.log("1. 티스토리 메인 페이지 접속 중...");
    await page.goto('https://www.tistory.com/', { waitUntil: 'networkidle' });

    // 2. 우측 상단 '카카오계정으로 시작하기' 버튼 클릭
    console.log("2. 카카오 로그인 진입 버튼 클릭...");
    await page.click('a.btn_login.link_kakao_id');
    await page.waitForTimeout(2000); // 레이어 애니메이션 대기

    // 3. '카카오계정으로 로그인' 버튼 클릭 (force 옵션 추가로 클릭 가로채기 우회)
    console.log("3. 카카오계정 로그인 선택...");
    await page.waitForSelector('a.link_tit', { timeout: 10000 });
    await page.click('a.link_tit', { force: true });

    // 4. 로그인 폼 대기 및 아이디/비밀번호 입력
    console.log("4. 로그인 정보 입력 중...");
    await page.waitForSelector('input[name="loginId"]', { timeout: 15000 });
    
    await page.fill('input[name="loginId"]', process.env.KAKAO_ID);
    await page.fill('input[name="password"]', process.env.KAKAO_PW);

    // 5. 로그인 제출
    console.log("5. 로그인 제출...");
    await page.click('button[type="submit"]');

    await page.waitForNavigation({ waitUntil: 'networkidle' });
    console.log("✅ 카카오 로그인 성공! 현재 URL:", page.url());

  } catch (error) {
    console.error("❌ 로그인 과정 중 오류 발생:", error);
    
    // 실패한 시점의 화면을 PNG 파일로 캡처 저장
    console.log("📸 오류 화면을 캡처합니다...");
    await page.screenshot({ path: 'error_screenshot.png', fullPage: true });
    
    process.exit(1);
  } finally {
    await browser.close();
  }
})();
