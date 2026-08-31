import os
import json
import gspread
from google.oauth2.credentials import Credentials

def test_connection():
    # 1. GitHub Secret에서 OAuth 토큰 정보 가져오기
    token_secret = os.environ.get('GCP_TOKEN_JSON')
    if not token_secret:
        raise ValueError("❌ GitHub Secret에 'GCP_TOKEN_JSON'이 없거나 비어 있습니다.")
    
    token_info = json.loads(token_secret)
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    
    # Credentials 객체 생성
    credentials = Credentials.from_authorized_user_info(token_info, scopes=scopes)
    gc = gspread.authorize(credentials)

    # 2. 지정된 구글 시트 접속
    sheet_url = "https://docs.google.com/spreadsheets/d/1nA0ZtCztY6Qe8UR8-erB98_BIZDYUUjpIQYWNyuePWA/edit"
    doc = gc.open_by_url(sheet_url)
    
    # 첫 번째 시트(0번 인덱스) 선택
    worksheet = doc.get_worksheet(0) 

    # 3. 데이터 입력 테스트 (3행 1열 위치에 텍스트 작성)
    worksheet.update_cell(3, 1, "GitHub OAuth 연동 성공!") 
    print("✅ 구글 시트 3행 1열에 'GitHub OAuth 연동 성공!' 데이터를 성공적으로 업로드했습니다!")

if __name__ == "__main__":
    test_connection()
