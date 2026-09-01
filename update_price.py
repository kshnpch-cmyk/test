import os
import json
import time
import re
import gspread
from google import genai
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from bs4 import BeautifulSoup
from curl_cffi import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Gemini API 클라이언트 초기화
gemini_api_key = os.environ.get('GEMINI_API_KEY')
ai_client = genai.Client(api_key=gemini_api_key) if gemini_api_key else None

@retry(
    retry=retry_if_exception_type(gspread.exceptions.APIError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=3, max=30)
)
def safe_open_sheet(gc, sheet_url):
    return gc.open_by_url(sheet_url)

@retry(
    retry=retry_if_exception_type(gspread.exceptions.APIError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=3, max=30)
)
def safe_batch_update(worksheet, cell_range, values_matrix):
    """200개 행의 데이터를 한 번의 API 요청으로 시트에 일괄 업데이트합니다."""
    worksheet.update(cell_range, values_matrix, raw=False)

def analyze_product_with_gemini(sheet_product, sheet_spec, crawled_title, crawled_price, raw_shipping_text=""):
    if not ai_client:
        return crawled_price, True, ""

    prompt = f"""
너는 쇼핑몰 데이터 분석 전문가야.
아래 [구글 시트 요청 정보]와 크롤링한 [쇼핑몰 검색 결과]를 비교 분석해줘.

[구글 시트 요청 정보]
- 품목명: {sheet_product}
- 규격: {sheet_spec}

[쇼핑몰 검색 결과]
- 검색된 상품명: {crawled_title}
- 검색된 표시 가격: {crawled_price}원
- 수집된 배송비 관련 텍스트: {raw_shipping_text}

다음 질문에 맞춰 오직 JSON 형식으로만 응답해:
1. "is_matched": 검색된 상품이 구글 시트 요청 품목과 동일한 종류의 상품인지 여부 (true/false)
2. "crawled_unit_qty": 검색된 상품명 속 제품 개수 (숫자만, 예: 3). 단품이면 1
3. "single_unit_price": 검색된 표시 가격을 개수로 나눈 '1개당 단가' (숫자만)
4. "shipping_fee": 배송비 금액 (무료배송/정보없음 "", 배송비가 있으면 '3,000원' 형태)
5. "reason": 판단 이유 요약

응답 형식(JSON):
{{
  "is_matched": true,
  "crawled_unit_qty": 3,
  "single_unit_price": 5770,
  "shipping_fee": "3,000원",
  "reason": "1kg 3개 묶음 17310원이므로 1개당 단가는 5770원입니다."
}}
"""

    try:
        # 📌 최신 지원 모델 gemini-3.6-flash 로 업데이트
        response = ai_client.models.generate_content(
            model='gemini-3.6-flash',
            contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        
        result = json.loads(response.text)
        is_matched = result.get("is_matched", True)
        single_price = result.get("single_unit_price", crawled_price)
        shipping_fee = result.get("shipping_fee", "")
        reason = result.get("reason", "")
        
        print(f"  🤖 [Gemini 분석] {reason}", flush=True)
        return single_price, is_matched, shipping_fee

    except Exception as e:
        print(f"  ❌ Gemini 분석 오류: {e}", flush=True)
        return crawled_price, True, ""

def extract_pack_quantity(spec_text):
    if not spec_text:
        return 1
    match = re.search(r'\*\s*(\d+)\s*(?:ea|개|팩|box|박스|통|병|개입)?', spec_text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match_unit = re.search(r'(\d+)\s*(?:ea|개|팩|box|박스|통|병)(?!\w)', spec_text, re.IGNORECASE)
    if match_unit:
        return int(match_unit.group(1))
    return 1

def parse_shipping_from_html(prod_element):
    full_text = prod_element.text.strip()
    if "무료" in full_text and "배송" in full_text:
        return ""

    match = re.search(r'(?:배송비|택배비|배송)\s*([\d,]+)\s*원', full_text)
    if match:
        fee_str = match.group(1).replace(",", "")
        if fee_str.isdigit() and int(fee_str) > 0:
            return f"{int(fee_str):,}원"

    return ""

def fetch_danawa_price(product_name, spec):
    clean_p = product_name.replace("*", " ").strip()
    clean_s = spec.replace("*", " ").strip()
    target_qty = extract_pack_quantity(spec)
    
    search_query = clean_p if re.search(r'\d+\s*(g|kg|l|ml|ea)', clean_p, re.IGNORECASE) else f"{clean_p} {clean_s}".strip()
    encoded_query = requests.utils.quote(search_query)
    search_url = f"https://search.danawa.com/dsearch.php?k1={encoded_query}&module=goods&act=dispMain"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.danawa.com/"
    }

    try:
        response = requests.get(search_url, headers=headers, impersonate="chrome120", timeout=6)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            products = soup.select("li.prod_item:not(.product-pot)")
            
            if products:
                first_prod = products[0]
                title_el = first_prod.select_one("p.prod_name a")
                title_text = title_el.text.strip() if title_el else ""

                price_el = first_prod.select_one("p.price_sect a strong") or first_prod.select_one("span.num")
                raw_price = int(re.sub(r'[^\d]', '', price_el.text)) if price_el else 0

                raw_shipping = parse_shipping_from_html(first_prod)

                if raw_price > 0:
                    single_price, is_matched, ai_shipping = analyze_product_with_gemini(
                        product_name, spec, title_text, raw_price, raw_shipping_text=first_prod.text[:200]
                    )
                    
                    if is_matched:
                        total_price = int(single_price * target_qty)
                        shipping_fee = ai_shipping if ai_shipping else raw_shipping

                        channel_el = first_prod.select_one("div.memory_sect p.memory_mall") or first_prod.select_one("p.mall_name")
                        mall_text = channel_el.text.strip() if (channel_el and channel_el.text.strip()) else "다나와"

                        link_el = first_prod.select_one("p.prod_name a") or first_prod.select_one("a.thumb_link")
                        href = link_el.get('href') if link_el else ""
                        product_link = f"https:{href}" if href.startswith("//") else (href or search_url)

                        return mall_text, total_price, shipping_fee, product_link

    except Exception as e:
        print(f"  ❌ 다나와 수집 예외: {e}", flush=True)

    return "다나와", 0, "", "-"

def fetch_baemin_price(product_name, spec):
    clean_p = product_name.replace("*", " ").strip()
    clean_s = spec.replace("*", " ").strip()
    target_qty = extract_pack_quantity(spec)
    
    search_query = clean_p if re.search(r'\d+\s*(g|kg|l|ml|ea)', clean_p, re.IGNORECASE) else f"{clean_p} {clean_s}".strip()
    encoded_query = requests.utils.quote(search_query)
    search_url = f"https://mart.baemin.com/search?keyword={encoded_query}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
        "Referer": "https://mart.baemin.com/"
    }

    try:
        response = requests.get(search_url, headers=headers, impersonate="chrome120", timeout=6)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            products = soup.select("a[class*='ProductItem']") or soup.select("li[class*='ProductList']") or soup.select("a[href*='/goods']")
            
            if products:
                first_prod = products[0]
                title_el = first_prod.select_one("[class*='title']") or first_prod.select_one("[class*='Name']") or first_prod
                title_text = title_el.text.strip() if title_el else ""

                price_el = first_prod.select_one("[class*='price']") or first_prod.select_one("span[class*='Price']")
                if price_el:
                    nums = re.findall(r'[\d,]+', price_el.text)
                    if nums:
                        raw_price = int(nums[0].replace(",", ""))
                        raw_shipping = parse_shipping_from_html(first_prod)
                        
                        single_price, is_matched, ai_shipping = analyze_product_with_gemini(
                            product_name, spec, title_text, raw_price, raw_shipping_text=first_prod.text[:200]
                        )
                        
                        if is_matched:
                            total_price = int(single_price * target_qty)
                            shipping_fee = ai_shipping if ai_shipping else raw_shipping
                            
                            href = first_prod.get('href', '')
                            product_link = f"https://mart.baemin.com{href}" if href.startswith('/') else search_url
                            
                            return "배민상회", total_price, shipping_fee, product_link

    except Exception as e:
        print(f"  ❌ 배민상회 수집 예외: {e}", flush=True)

    return "배민상회", 0, "", "-"

def run_price_update():
    try:
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
        sheet_url = "https://docs.google.com/spreadsheets/d/1nA0ZtCztY6Qe8UR8-erB98_BIZDYUUjpIQYWNyuePWA/edit"
        
        doc = safe_open_sheet(gc, sheet_url)
        worksheet = doc.get_worksheet(0)

        all_rows = worksheet.get_all_values()
        total_rows = len(all_rows)

        print("==================================================", flush=True)
        print(f"🚀 [gemini-3.6-flash 적용 완료] 총 {total_rows}개 행 조사를 시작합니다.", flush=True)
        print("==================================================\n", flush=True)

        batch_data = []

        for row_idx in range(3, total_rows + 1):
            row_values = all_rows[row_idx - 1] if (row_idx - 1) < len(all_rows) else []

            orig_j_to_s = row_values[9:19] if len(row_values) >= 19 else [""] * 10
            while len(orig_j_to_s) < 10:
                orig_j_to_s.append("")

            product_name = row_values[2] if len(row_values) >= 3 else ""
            spec = row_values[3] if len(row_values) >= 4 else ""
            category = row_values[8] if len(row_values) >= 9 else ""

            print(f"▶ [{row_idx}/{total_rows}행] 품목: '{product_name}' | 규격: '{spec}' | 구분: '{category}'", flush=True)

            if any(skip_word in category for skip_word in ['전용', '예산', '종료']) or not product_name.strip():
                print(f"  ⏭️ 스킵됨", flush=True)
                batch_data.append(orig_j_to_s)
                continue

            d_channel, d_price, d_shipping, d_link = fetch_danawa_price(product_name, spec)
            b_channel, b_price, b_shipping, b_link = fetch_baemin_price(product_name, spec)

            final_channel, final_price, final_shipping, final_link = d_channel, d_price, d_shipping, d_link

            if b_price > 0 and (d_price == 0 or b_price < d_price):
                print(f"  💡 [배민상회 우세] 배민({b_price:,}원) < 다나와({d_price:,}원)", flush=True)
                final_channel, final_price, final_shipping, final_link = b_channel, b_price, b_shipping, b_link
            elif d_price > 0:
                print(f"  ⚖️ [다나와 우세/동일] 다나와({d_price:,}원) <= 배민({b_price:,}원)", flush=True)

            if final_price == 0:
                final_channel, final_price, final_shipping, final_link = "검색결과없음", 0, "", "-"

            link_formula = f'=HYPERLINK("{final_link}", "링크보기")' if (final_link and final_link != "-") else "-"

            row_update = list(orig_j_to_s)
            row_update[0] = final_channel
            row_update[1] = final_price
            row_update[2] = final_shipping
            row_update[9] = link_formula

            batch_data.append(row_update)

            disp_shipping = final_shipping if final_shipping else "공란(무료/없음)"
            print(f"  ✔ [완료] 최종채널={final_channel} | 최저가={final_price:,}원 | 배송비={disp_shipping}\n", flush=True)

            time.sleep(0.3)

        print("\n📤 [시트 반영 중] 수집된 전체 최저가 데이터를 구글 시트에 일괄 기록합니다...", flush=True)
        cell_range = f"J3:S{total_rows}"
        safe_batch_update(worksheet, cell_range, batch_data)

        print("🎉 모든 최저가 조사 및 시트 일괄 업데이트가 완료되었습니다!", flush=True)

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}", flush=True)
        raise e

if __name__ == "__main__":
    run_price_update()
