const { chromium } = require('playwright');

(async () => {
  console.log("🚀 크로미늄 브라우저를 시작합니다...");
  
  // 브라우저 해상도 설정 추가
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 }
  });
  const page = await context.newPage();

  try {
    // 1. 티스토리 메인 접속
    console.log("1. 티스토리 메인 페이지 접속 중...");
    await page.goto('https://www.tistory.com/', { waitUntil: 'networkidle' });

    // 2. 레이어 팝업/버튼 클릭 대신 카카오 로그인 OAuth 페이지로 직접 이동 (확실한 우회)
    console.log("2. 카카오 로그인 페이지 직접 진입...");
    await page.goto('https://www.tistory.com/auth/login', { waitUntil: 'networkidle' });
    
    // 만약 바로 이동하지 않고 한번 더 클릭이 필요한 페이지라면 아래 처리 실행
    if (page.url().includes('tistory.com/auth/login')) {
      const kakaoBtn = page.locator('a.link_kakao_id, a.btn_login');
      if (await kakaoBtn.isVisible()) {
        await kakaoBtn.click({ force: true });
      }
    }

    // 3. 카카오 로그인 입력창 대기 (accounts.kakao.com 진입 확인)
    console.log("3. 로그인 정보 입력 창 대기 중...");
    await page.waitForSelector('input[name="loginId"]', { timeout: 15000 });
    
    // 4. 아이디 및 비밀번호 입력
    console.log("4. 아이디/비밀번호 입력 중...");
    await page.fill('input[name="loginId"]', process.env.KAKAO_ID);
    await page.fill('input[name="password"]', process.env.KAKAO_PW);

    // 5. 로그인 제출
    console.log("5. 로그인 제출...");
    await page.click('button[type="submit"]');

    // 6. 로그인 완료 후 리다이렉션 대기
    await page.waitForNavigation({ waitUntil: 'networkidle' });
    console.log("✅ 카카오 로그인 성공! 현재 이동된 URL:", page.url());

  } catch (error) {
    console.error("❌ 로그인 과정 중 오류 발생:", error);
    
    // 실패 시점 화면 캡처 저장
    console.log("📸 오류 화면을 캡처합니다...");
    await page.screenshot({ path: 'error_screenshot.png', fullPage: true });
    
    process.exit(1);
  } finally {
    await browser.close();
  }
})();
