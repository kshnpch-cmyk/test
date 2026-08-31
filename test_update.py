import os
import json
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

def test_connection():
    try:
        token_secret = os.environ.get('GCP_TOKEN_JSON')
        if not token_secret:
            raise ValueError("❌ GitHub Secret에 'GCP_TOKEN_JSON'이 설정되지 않았습니다.")
        
        token_info = json.loads(token_secret)
        
        # 1. token_info 내부에서 스코프 관련 필드 제거 (구글 서버 자동 인용 유도)
        token_info.pop("scopes", None)
        token_info.pop("scope", None)

        # 2. Credentials 객체 생성
        credentials = Credentials.from_authorized_user_info(token_info)
        
        # 3. 내부 _scopes 필드를 강제로 None 처리하여 리프레시 시 invalid_scope 에러 방지
        credentials._scopes = None

        # 4. 토큰 만료 시 갱신 진행
        if credentials.expired and credentials.refresh_token:
            print("🔄 토큰 만료 감지, 안전하게 갱신을 진행합니다...")
            credentials.refresh(Request())

        gc = gspread.authorize(credentials)

        # 5. 구글 시트 연결
        sheet_url = "https://docs.google.com/spreadsheets/d/1nA0ZtCztY6Qe8UR8-erB98_BIZDYUUjpIQYWNyuePWA/edit"
        doc = gc.open_by_url(sheet_url)
        
        # 첫 번째 시트 선택
        worksheet = doc.get_worksheet(0) 

        # 6. 데이터 업데이트 테스트
        worksheet.update_cell(3, 1, "GitHub OAuth 연동 성공!") 
        print("✅ 구글 시트 3행 1열에 성공적으로 데이터를 적었습니다!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    test_connection()
