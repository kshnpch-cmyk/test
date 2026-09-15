const { chromium } = require('playwright');

(async () => {
  console.log("🚀 크로미늄 브라우저를 시작합니다...");
  
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 }
  });
  const page = await context.newPage();

  try {
    console.log("1. 티스토리 메인 페이지 접속 중...");
    await page.goto('https://www.tistory.com/', { waitUntil: 'domcontentloaded' });

    console.log("2. 카카오 로그인 페이지 직접 진입...");
    await page.goto('https://www.tistory.com/auth/login', { waitUntil: 'domcontentloaded' });
    
    console.log("3. 로그인 정보 입력 창 대기 중...");
    await page.waitForSelector('input[name="loginId"]', { timeout: 15000 });
    
    console.log("4. 아이디/비밀번호 입력 중...");
    await page.fill('input[name="loginId"]', process.env.KAKAO_ID);
    await page.fill('input[name="password"]', process.env.KAKAO_PW);

    console.log("5. 로그인 제출...");
    
    // Promise.all을 통해 클릭과 페이지 이동을 동시에 안전하게 기다립니다.
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 30000 }), // networkidle 대신 domcontentloaded 사용
      page.click('button[type="submit"]')
    ]);

    console.log("✅ 카카오 로그인 성공! 현재 이동된 URL:", page.url());

  } catch (error) {
    console.error("❌ 로그인 과정 중 오류 발생:", error);
    
    console.log("📸 오류 화면을 캡처합니다...");
    await page.screenshot({ path: 'error_screenshot.png', fullPage: true });
    
    process.exit(1);
  } finally {
    await browser.close();
  }
})();
