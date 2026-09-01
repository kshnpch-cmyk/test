import os
import json
import time
import re
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from bs4 import BeautifulSoup
from curl_cffi import requests

def fetch_danawa_price(search_query):
    """
    다나와(Danawa) 검색을 통해 판매채널(J열), 판매가(K열), 배송비(L열), 상품링크(S열)를 수집합니다.
    """
    clean_query = search_query.replace("*", " ").strip()
    print(f"  [다나와 검색] 키워드: '{clean_query}'")
    
    encoded_query = requests.utils.quote(clean_query)
    search_url = f"https://search.danawa.com/dsearch.php?k1={encoded_query}&module=goods&act=dispMain"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": "https://www.danawa.com/"
    }

    try:
        # 크롬 브라우저 TLS 우회 접속
        response = requests.get(search_url, headers=headers, impersonate="chrome120", timeout=10)
        print(f"  [응답 코드] {response.status_code}")

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 다나와 상품 리스트 추출 (광고 상품 제외 첫 번째 일반 상품)
            first_product = soup.select_one("li.prod_item:not(.product-pot)") or soup.select_one("li.prod_item")
            
            if first_product:
                # 1. 판매가 (K열)
                price_el = first_product.select_one("p.price_sect a strong") or first_product.select_one("span.num")
                if price_el:
                    sale_price = int(re.sub(r'[^\d]', '', price_el.text))
                else:
                    sale_price = 0

                # 2. 판매채널 (J열) - 다나와로 지정 (특정 몰이 잡히면 해당 몰 이름, 아니면 '다나와')
                channel_el = first_product.select_one("div.memory_sect p.memory_mall") or first_product.select_one("p.mall_name")
                if channel_el and channel_el.text.strip():
                    channel_name = channel_el.text.strip()
                else:
                    channel_name = "다나와"

                # 3. 배송비 (L열)
                delivery_el = first_product.select_one("span.ship_fee") or first_product.select_one("div.delivery_sect")
                if delivery_el:
                    delivery_text = delivery_el.text.strip()
                    if "무료" in delivery_text:
                        shipping_fee = "무료배송"
                    else:
                        nums = re.findall(r'\d+', delivery_text.replace(",", ""))
                        shipping_fee = f"{int(nums[0]):,}원" if nums else "기본배송"
                else:
                    shipping_fee = "기본배송"

                # 4. 링크 추출 (S열) - 상품 개별 상세 링크 추출 (없을 경우 검색 결과 페이지 링크)
                link_el = first_product.select_one("p.prod_name a") or first_product.select_one("a.thumb_link")
                if link_el and link_el.get('href'):
                    product_link = link_el.get('href')
                    if product_link.startswith("//"):
                        product_link = "https:" + product_link
                else:
                    product_link = search_url

                if sale_price > 0:
                    return channel_name, sale_price, shipping_fee, product_link

    except Exception as e:
        print(f"  ❌ 다나와 수집 예외: {e}")

    return None, None, None, None

def run_price_update():
    try:
        # 1. 구글 인증 (GCP_TOKEN_JSON)
        token_secret = os.environ.get('GCP_TOKEN_JSON')
        if not token_secret:
            raise ValueError("❌ GitHub Secret에 'GCP_TOKEN_JSON'이 설정되지 않았습니다.")
        
        token_info = json.loads(token_secret)
        token_info.pop("scopes", None)
        token_info.pop("scope", None)

        credentials = Credentials.from_authorized_user_info(token_info)
        credentials._scopes = None

        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())

        gc = gspread.authorize(credentials)

        # 2. 구글 시트 접속
        sheet_url = "https://docs.google.com/spreadsheets/d/1nA0ZtCztY6Qe8UR8-erB98_BIZDYUUjpIQYWNyuePWA/edit"
        doc = gc.open_by_url(sheet_url)
        worksheet = doc.get_worksheet(0)

        print("==================================================")
        print("📊 [다나와 가격 수집 가동] 3행부터 10행까지 조사를 시작합니다.")
        print("==================================================\n")

        for row_num in range(3, 11):
            row_values = worksheet.row_values(row_num)
            
            product_name = row_values[2] if len(row_values) >= 3 else ""  # C열 (품목명)
            spec = row_values[3] if len(row_values) >= 4 else ""          # D열 (규격)
            category = row_values[8] if len(row_values) >= 9 else ""      # I열 (구분)

            print(f"▶ [{row_num}행] 품목: '{product_name}' | 규격: '{spec}' | 구분(I열): '{category}'")

            # 스킵 조건: I열(구분)에 '전용', '예산', '종료'가 포함된 경우
            if any(skip_word in category for skip_word in ['전용', '예산', '종료']):
                print(f"  ⏭️ 스킵됨 (구분 조건 제외: '{category}')\n")
                continue

            if not product_name.strip():
                print(f"  ⏭️ 스킵됨 (품목명 없음)\n")
                continue

            # 1차 시도: C열(품목명) + D열(규격)
            search_query = f"{product_name} {spec}".strip()
            channel, price, shipping, link = fetch_danawa_price(search_query)

            # 2차 시도 (실패 시): C열(품목명) 단독 검색
            if not channel or price == 0:
                print(f"  └─ ⚠️ 1차 실패 후 품목명 단독 재검색 시도: '{product_name}'")
                channel, price, shipping, link = fetch_danawa_price(product_name.strip())

            if not channel or price == 0:
                channel, price, shipping, link = "검색결과없음", 0, "-", "-"

            # 시트 업데이트
            # J열(10): 판매채널, K열(11): 판매가, L열(12): 택배비
            worksheet.update_cell(row_num, 10, channel)
            worksheet.update_cell(row_num, 11, price)
            worksheet.update_cell(row_num, 12, shipping)

            # S열(19): 클릭 가능한 최저가 상품 링크 수식 입력
            if link and link != "-":
                hyperlink_formula = f'=HYPERLINK("{link}", "링크보기")'
                worksheet.update_cell(row_num, 19, hyperlink_formula)
            else:
                worksheet.update_cell(row_num, 19, "-")

            print(f"  ✔ [완료] J={channel} | K={price:,}원 | L={shipping} | S=링크입력완료\n")
            time.sleep(2.0)

        print("🎉 다나와 가격 수집 및 구글 시트(S열 링크 포함) 업데이트가 완료되었습니다!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    run_price_update()
