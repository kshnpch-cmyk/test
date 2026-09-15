const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  console.log("🚀 크로미늄 브라우저를 시작합니다...");
  
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 }
  });
  const page = await context.newPage();

  try {
    // 티스토리 카카오 OAuth 직접 진입 주소 (중간 버튼을 우회하는 가장 확실한 방법)
    const kakaoOAuthUrl = "https://accounts.kakao.com/login/?continue=https%3A%2F%2Fkauth.kakao.com%2Foauth%2Fauthorize%3Fis_popup%3Dfalse%26callback_origin%3Dhttps%253A%252F%252Fwww.tistory.com%26response_type%3Dcode%26redirect_uri%3Dhttps%253A%252F%252Fwww.tistory.com%252Fauth%252Fkakao%252Fredirect%26client_id%3D3e6bcab134b28b7eec7328ff9a397984#login";

    console.log("1. 카카오 로그인 폼 직접 진입 중...");
    await page.goto(kakaoOAuthUrl, { waitUntil: 'domcontentloaded' });
    
    console.log("2. 아이디/비밀번호 입력창 대기 중...");
    await page.waitForSelector('input[name="loginId"]', { timeout: 15000 });
    
    console.log("3. 아이디/비밀번호 입력 중...");
    await page.fill('input[name="loginId"]', process.env.KAKAO_ID);
    await page.fill('input[name="password"]', process.env.KAKAO_PW);

    console.log("4. 로그인 제출...");
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {}),
      page.click('button[type="submit"]')
    ]);

    console.log("✅ 현재 이동 완료된 URL:", page.url());

    // 성공하더라도 현재 상태 화면을 무조건 캡처하여 저장 (저장 확인용)
    console.log("📸 실행 결과 화면을 캡처합니다...");
    await page.screenshot({ path: 'error_screenshot.png', fullPage: true });

  } catch (error) {
    console.error("❌ 오류 발생:", error);
    
    console.log("📸 오류 화면을 캡처합니다...");
    await page.screenshot({ path: 'error_screenshot.png', fullPage: true });
    
    process.exit(1);
  } finally {
    await browser.close();
  }
})();
