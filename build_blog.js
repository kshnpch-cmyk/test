const fs = require('fs');
const path = require('path');

const GAS_URL = "https://script.google.com/macros/s/AKfycbxTqkNEsNRFWkYV_zbwRaHow3R_ykZRLUge2IysUU-gCr1nJaQhUDw4QHd9CSJDDuY/exec";

async function build() {
  console.log("🚀 구글 시트 데이터 수신 중...");
  
  try {
    const response = await fetch(GAS_URL);
    const data = await response.json();

    if (!data.title || !data.html) {
      console.log("⚠️ 생성할 데이터가 없거나 유효하지 않습니다.");
      return;
    }

    // 1. posts 디렉토리 생성
    const postsDir = path.join(__dirname, 'posts');
    if (!fs.existsSync(postsDir)) {
      fs.mkdirSync(postsDir);
    }

    // 2. 파일명 생성 (특수문자 제거)
    const fileName = `post-${Date.now()}.html`;
    const filePath = path.join(postsDir, fileName);

    // 3. 개별 포스팅 HTML 템플릿 생성
    const postHtml = `<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${data.title}</title>
  <style>
    body { font-family: '맑은 고딕', sans-serif; line-height: 1.6; max-width: 800px; margin: 0 auto; padding: 20px; color: #333; }
    .tags { background: #edf2f7; padding: 8px 12px; border-radius: 6px; color: #2b6cb0; font-weight: bold; margin-bottom: 20px; display: inline-block; }
    h1 { border-bottom: 2px solid #e2e8f0; padding-bottom: 10px; }
    .content { margin-top: 20px; }
    .back-btn { display: inline-block; margin-top: 30px; color: #3182ce; text-decoration: none; font-weight: bold; }
  </style>
</head>
<body>
  <div class="tags">🏷️ ${data.tags || '태그 없음'}</div>
  <h1>${data.title}</h1>
  <div class="content">${data.html}</div>
  <a href="../index.html" class="back-btn">← 목록으로 돌아가기</a>
</body>
</html>`;

    fs.writeFileSync(filePath, postHtml, 'utf8');
    console.log(`✅ 글 생성 완료: posts/${fileName}`);

    // 4. index.html 목록 업데이트
    updateIndex(fileName, data.title, data.tags);

  } catch (error) {
    console.error("❌ 빌드 실패:", error);
    process.exit(1);
  }
}

function updateIndex(fileName, title, tags) {
  const indexPath = path.join(__dirname, 'index.html');
  const postItem = `<li><a href="posts/${fileName}">${title}</a> <span style="color:#718096; font-size:14px;">(${tags || ''})</span></li>\n<!-- POST_LIST_END -->`;
  
  let indexHtml = "";
  if (fs.existsSync(indexPath)) {
    indexHtml = fs.readFileSync(indexPath, 'utf8');
    indexHtml = indexHtml.replace('<!-- POST_LIST_END -->', postItem);
  } else {
    indexHtml = `<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>자동화 기술 블로그</title>
  <style>
    body { font-family: sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; line-height: 1.8; }
    ul { list-style: none; padding: 0; }
    li { margin-bottom: 12px; border-bottom: 1px solid #eee; padding-bottom: 8px; }
    a { color: #2b6cb0; text-decoration: none; font-weight: bold; font-size: 18px; }
  </style>
</head>
<body>
  <h1>📰 최신 리포트 블로그</h1>
  <ul>
    ${postItem}
  </ul>
</body>
</html>`;
  }
  fs.writeFileSync(indexPath, indexHtml, 'utf8');
  console.log("✅ index.html 목록 갱신 완료");
}

build();
