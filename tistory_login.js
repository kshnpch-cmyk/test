const { chromium } = require('playwright');

(async () => {
  console.log("🚀 크로미늄 브라우저를 시작합니다...");
  
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 }
  });
  const page = await context.newPage();

  try {
    console.log("1. 티스토리 로그인 중간 페이지 접속 중...");
    await page.goto('https://www.tistory.com/auth/login', { waitUntil: 'domcontentloaded' });
    
    // 2. 캡처에 나온 '카카오계정으로 로그인' 노란색 버튼 누르기
    console.log("2. 노란색 '카카오계정으로 로그인' 버튼 클릭 시도...");
    
    // 버튼 Selector 지정 (여러 형태 우회 대응)
    const kakaoLoginBtn = page.locator('a.link_kakao_id, a.btn_login, a[href*="kakao"]');
    
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 15000 }).catch(() => {}),
      kakaoLoginBtn.first().click({ force: true })
    ]);

    // 3. 진짜 카카오 로그인 폼(accounts.kakao.com) 진입 대기
    console.log("3. 카카오 아이디/비밀번호 입력창 대기 중...");
    await page.waitForSelector('input[name="loginId"]', { timeout: 15000 });
    
    console.log("4. 아이디/비밀번호 입력 중...");
    await page.fill('input[name="loginId"]', process.env.KAKAO_ID);
    await page.fill('input[name="password"]', process.env.KAKAO_PW);

    console.log("5. 로그인 제출...");
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 30000 }),
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
