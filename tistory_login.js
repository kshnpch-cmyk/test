const { chromium } = require('playwright-extra');
const stealth = require('puppeteer-extra-plugin-stealth')();

// Stealth 플러그인 등록 (봇 관련 속성 우회)
chromium.use(stealth);

(async () => {
  console.log("🚀 Stealth 모드로 크로미늄 브라우저를 시작합니다...");
  
  // 봇 탐지 우회를 위한 브라우저 실행 옵션
  const browser = await chromium.launch({ 
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-blink-features=AutomationControlled' // 자동화 플래그 제거
    ]
  });

  // 일반 데스크톱 Chrome 브라우저로 위장 (User-Agent 세팅)
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    locale: 'ko-KR'
  });

  const page = await context.newPage();

  try {
    const kakaoOAuthUrl = "https://accounts.kakao.com/login/?continue=https%3A%2F%2Fkauth.kakao.com%2Foauth%2Fauthorize%3Fis_popup%3Dfalse%26callback_origin%3Dhttps%253A%252F%252Fwww.tistory.com%26response_type%3Dcode%26redirect_uri%3Dhttps%253A%252F%252Fwww.tistory.com%252Fauth%252Fkakao%252Fredirect%26client_id%3D3e6bcab134b28b7eec7328ff9a397984#login";

    console.log("1. 카카오 로그인 폼 직접 진입 중...");
    await page.goto(kakaoOAuthUrl, { waitUntil: 'domcontentloaded' });
    
    console.log("2. 아이디/비밀번호 입력창 대기 중...");
    await page.waitForSelector('input[name="loginId"]', { timeout: 15000 });
    
    console.log("3. 실제 사람처럼 타이핑 중...");
    // 사람처럼 보이도록 delay 옵션 추가 (입력 속도 조절)
    await page.type('input[name="loginId"]', process.env.KAKAO_ID, { delay: 100 });
    await page.type('input[name="password"]', process.env.KAKAO_PW, { delay: 100 });

    console.log("4. 로그인 제출...");
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {}),
      page.click('button[type="submit"]')
    ]);

    // 잠시 대기 후 이동된 URL 확인
    await page.waitForTimeout(3000);
    console.log("✅ 현재 이동 완료된 URL:", page.url());

    // 성공/진행 상태 캡처 저장
    console.log("📸 로그인 결과 화면을 캡처합니다...");
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
