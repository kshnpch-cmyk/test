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
        
        # OAuth Playground에서 발급받았을 때 포함된 실제 스코프와 완전히 일치시킵니다.
        scopes = [
            'https://www.googleapis.com/auth/drive',
            'https://www.googleapis.com/auth/spreadsheets'
        ]
        
        # Credentials 생성
        credentials = Credentials.from_authorized_user_info(token_info, scopes=scopes)
        
        # 토큰 만료 시 자동 갱신
        if credentials.expired and credentials.refresh_token:
            print("🔄 토큰 만료 감지, 갱신을 진행합니다...")
            credentials.refresh(Request())

        gc = gspread.authorize(credentials)

        # 구글 시트 연결
        sheet_url = "https://docs.google.com/spreadsheets/d/1nA0ZtCztY6Qe8UR8-erB98_BIZDYUUjpIQYWNyuePWA/edit"
        doc = gc.open_by_url(sheet_url)
        
        # 첫 번째 시트 선택
        worksheet = doc.get_worksheet(0) 

        # 데이터 업데이트 테스트
        worksheet.update_cell(3, 1, "GitHub OAuth 연동 성공!") 
        print("✅ 구글 시트 3행 1열에 성공적으로 데이터를 적었습니다!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    test_connection()
