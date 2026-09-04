import os
import json
import time
import re
import warnings
import gspread
import io
from google import genai
from google.genai import types
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from playwright.sync_api import sync_playwright
from sentence_transformers import SentenceTransformer, util
from PIL import Image

warnings.filterwarnings("ignore", category=UserWarning)

print("📦 BERT 모델(ko-sbert-sts) 로딩 중...", flush=True)
bert_model = SentenceTransformer('jhgan/ko-sbert-sts')

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
    worksheet.update(cell_range, values_matrix, raw=False)

def calculate_bert_similarity(query, candidate_title):
    try:
        emb1 = bert_model.encode(query, convert_to_tensor=True)
        emb2 = bert_model.encode(candidate_title, convert_to_tensor=True)
        return util.cos_sim(emb1, emb2).item()
    except Exception:
        return 0.5

def extract_price_via_vision_ocr(image_bytes):
    """📌 HTML 텍스트 파싱 실패 시, 화면 캡처 이미지에서 Gemini Vision OCR로 가격/배송비 추출"""
    if not ai_client:
        return 0, ""

    try:
        image = Image.open(io.BytesIO(image_bytes))
        prompt = """
이 이미지(쇼핑몰 상품 상세/검색 화면)에서 표시된 '최저가/판매가 금액'과 '배송비'를 찾아줘.
오직 JSON 형식으로만 응답해:
{
  "price": 숫자만(예: 51020),
  "shipping_fee": "무료배송" 또는 "3,000원"
}
"""
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[image, prompt],
            config={'response_mime_type': 'application/json'}
        )
        result = json.loads(response.text)
        return result.get("price", 0), result.get("shipping_fee", "")
    except Exception as e:
        print(f"  ❌ Vision OCR 파싱 실패: {e}", flush=True)
        return 0, ""

def clean_search_keyword(product_name, spec):
    clean_p = re.sub(r'[^\w\s가-힣a-zA-Z0-9]', ' ', product_name).strip()
    unit_match = re.search(r'(\d+(?:\.\d+)?\s*(?:kg|g|l|ml))', spec, re.IGNORECASE)
    spec_unit = unit_match.group(1) if unit_match else ""
    return re.sub(r'\s+', ' ', f"{clean_p} {spec_unit}").strip()

def analyze_product_with_gemini(sheet_product, sheet_spec, crawled_title, crawled_price, raw_shipping_text=""):
    if not ai_client or crawled_price == 0:
        return crawled_price, True, "", "기본가 책정"

    prompt = f"""
너는 쇼핑몰 데이터 분석 및 용량 계산 전문가야.
아래 [구글 시트 요청 정보]와 크롤링한 [쇼핑몰 검색 결과]를 분석해줘.

[구글 시트 요청 정보]
- 품목명: {sheet_product}
- 시트 규격: {sheet_spec}

[쇼핑몰 검색 결과]
- 검색된 상품명: {crawled_title}
- 검색된 표시 가격: {crawled_price}원
- 수집된 배송비 텍스트: {raw_shipping_text}

응답 규칙(오직 JSON만):
1. "is_matched": 요청 품목과 동일 종류인지 검증 (true/false)
2. "crawled_total_capacity_g": 검색 상품명의 전체 총 용량/중량(g/ml 숫자만, 예: 12kg -> 12000). 모르면 0
3. "sheet_target_capacity_g": 시트 규격 전체 목표 총 용량/중량(g/ml 숫자만, 예: 10kg -> 10000). 모르면 0
4. "shipping_fee": 배송비 금액 (무료/로켓배송/정보없음은 "", 유료배송비면 '3,000원' 형태)
5. "calculation_reason": 구글 시트 T열에 적힐 책정 산출 근거 (한 문장으로 명확히 요약)

JSON 응답 예시:
{{
  "is_matched": true,
  "crawled_total_capacity_g": 12000,
  "sheet_target_capacity_g": 10000,
  "shipping_fee": "",
  "calculation_reason": "12kg 51,020원에서 시트 목표 중량인 10kg으로 비례 환산하여 42,516원 책정"
}}
"""

    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config={'response_mime_type': 'application/json', 'tools': []}
        )
        time.sleep(0.8)
        
        result = json.loads(response.text)
        is_matched = result.get("is_matched", True)
        crawled_g = result.get("crawled_total_capacity_g", 0)
        target_g = result.get("sheet_target_capacity_g", 0)
        shipping_fee = result.get("shipping_fee", "")
        reason = result.get("calculation_reason", "비례 환산 적용")

        final_calculated_price = crawled_price
        if is_matched and crawled_g > 0 and target_g > 0:
            final_calculated_price = int((crawled_price / crawled_g) * target_g)
            print(f"  🤖 [Gemini 비례 환산] {crawled_g}g({crawled_price:,}원) ➔ {target_g}g 환산가: {final_calculated_price:,}원", flush=True)

        return final_calculated_price, is_matched, shipping_fee, reason

    except Exception as e:
        print(f"  ⚠️ Gemini 분석 예외: {e}", flush=True)
        return crawled_price, True, "", "기본 단가 책정"

def fetch_with_browser_and_ocr(page, url, selector_item, parser_fn):
    try:
        page.goto(url, timeout=15000, wait_until="domcontentloaded")
        time.sleep(2)
        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        items = soup.select(selector_item)
        
        results = parser_fn(items, url)
        
        # 📌 만약 HTML 파싱에서 가격을 찾지 못했다면 화면 캡처 + Vision OCR 시도
        if not results:
            screenshot_bytes = page.screenshot(full_page=False)
            ocr_price, ocr_ship = extract_price_via_vision_ocr(screenshot_bytes)
            if ocr_price > 0:
                print(f"  📸 [Vision OCR 성공] 캡처 이미지에서 가격({ocr_price:,}원) 감지", flush=True)
                results.append(("OCR감지", "화면캡처 상품", ocr_price, ocr_ship, url))

        return results
    except Exception:
        return []

def parse_danawa(items, search_url):
    results = []
    for first_prod in items[:5]:
        title_el = first_prod.select_one("p.prod_name a")
        title_text = title_el.text.strip() if title_el else ""
        price_el = first_prod.select_one("p.price_sect a strong") or first_prod.select_one("span.num")
        raw_price = int(re.sub(r'[^\d]', '', price_el.text)) if price_el else 0
        ship_el = first_prod.select_one("span.ship_fee") or first_prod.select_one("td.ship") or first_prod.select_one("span.stxt")
        ship_text = ship_el.text.strip() if ship_el else ""

        if raw_price > 0 and title_text:
            channel_el = first_prod.select_one("div.memory_sect p.memory_mall") or first_prod.select_one("p.mall_name")
            mall_text = channel_el.text.strip() if (channel_el and channel_el.text.strip()) else "다나와"
            link_el = first_prod.select_one("p.prod_name a") or first_prod.select_one("a.thumb_link")
            href = link_el.get('href') if link_el else ""
            product_link = f"https:{href}" if href.startswith("//") else (href or search_url)
            results.append((mall_text, title_text, raw_price, ship_text, product_link))
    return results

def parse_naver(items, search_url):
    results = []
    for first_prod in items[:5]:
        title_el = first_prod.select_one("a[class*='product_link']") or first_prod.select_one("a[title]")
        title_text = title_el.text.strip() if title_el else ""
        price_el = first_prod.select_one("span[class*='price_num']") or first_prod.select_one("em[class*='num']")
        raw_price = int(re.sub(r'[^\d]', '', price_el.text)) if price_el else 0
        ship_el = first_prod.select_one("span[class*='price_delivery']") or first_prod.select_one("div[class*='delivery']")
        ship_text = ship_el.text.strip() if ship_el else ""

        if raw_price > 0 and title_text:
            href = title_el.get('href', '') if title_el else ""
            product_link = href if href.startswith("http") else search_url
            results.append(("네이버쇼핑", title_text, raw_price, ship_text, product_link))
    return results

def parse_coupang(items, search_url):
    results = []
    for first_prod in items[:5]:
        if first_prod.select_one("span.ad-badge"): continue
        title_el = first_prod.select_one("div.name")
        title_text = title_el.text.strip() if title_el else ""
        price_el = first_prod.select_one("strong.price-value")
        raw_price = int(re.sub(r'[^\d]', '', price_el.text)) if price_el else 0
        delivery_el = first_prod.select_one("span.delivery-badge") or first_prod.select_one("div.delivery")
        delivery_text = delivery_el.text.strip() if delivery_el else ""

        if raw_price > 0 and title_text:
            href = first_prod.select_one("a").get('href', '') if first_prod.select_one("a") else ""
            product_link = f"https://www.coupang.com{href}" if href.startswith('/') else search_url
            results.append(("쿠팡", title_text, raw_price, delivery_text, product_link))
    return results

def run_price_update():
    try:
        token_secret = os.environ.get('GCP_TOKEN_JSON')
        if not token_secret: raise ValueError("❌ GCP_TOKEN_JSON 설정이 필요합니다.")
        
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
        print(f"🚀 [T열 산출근거 자동입력 + OCR 보조 엔진 가동] 총 {total_rows}개 행 수집", flush=True)
        print("==================================================\n", flush=True)

        batch_data = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
            page = context.new_page()

            for row_idx in range(3, total_rows + 1):
                row_values = all_rows[row_idx - 1] if (row_idx - 1) < len(all_rows) else []

                orig_j_to_s = row_values[9:19] if len(row_values) >= 19 else [""] * 10
                while len(orig_j_to_s) < 10: orig_j_to_s.append("")

                product_name = row_values[2] if len(row_values) >= 3 else ""
                spec = row_values[3] if len(row_values) >= 4 else ""
                category = row_values[8] if len(row_values) >= 9 else ""

                print(f"▶ [{row_idx}/{total_rows}행] 품목: '{product_name}' | 규격: '{spec}'", flush=True)

                if any(skip_word in category for skip_word in ['전용', '예산', '종료']) or not product_name.strip():
                    print(f"  ⏭️ 스킵됨", flush=True)
                    batch_data.append(orig_j_to_s)
                    continue

                search_query = clean_search_keyword(product_name, spec)
                encoded_query = re.sub(r'\s+', '+', search_query)

                targets = [
                    (f"https://search.danawa.com/dsearch.php?k1={encoded_query}&module=goods&act=dispMain", "li.prod_item:not(.product-pot)", parse_danawa),
                    (f"https://search.shopping.naver.com/search/all?query={encoded_query}", "div[class*='product_item'], li[class*='basicList_item']", parse_naver),
                    (f"https://www.coupang.com/np/search?q={encoded_query}", "li.search-product", parse_coupang)
                ]

                valid_results = []
                for url, selector, parser in targets:
                    candidates = fetch_with_browser_and_ocr(page, url, selector, parser)
                    
                    for mall_name, title, price, ship, link in candidates:
                        sim_score = calculate_bert_similarity(search_query, title)
                        if sim_score >= 0.55:
                            calc_price, is_matched, ai_ship, reason = analyze_product_with_gemini(product_name, spec, title, price, ship)
                            if is_matched and calc_price > 0:
                                valid_results.append((mall_name, calc_price, ai_ship if ai_ship else ship, link, reason))
                                break

                if valid_results:
                    valid_results.sort(key=lambda x: x[1])
                    best_channel, best_price, best_shipping, best_link, best_reason = valid_results[0]
                    print(f"  🏆 [최저가 확정] {best_channel} | {best_price:,}원 | 이유: {best_reason}", flush=True)
                else:
                    best_channel, best_price, best_shipping, best_link, best_reason = "검색결과없음", 0, "", "-", "유효한 상품을 찾지 못함"
                    print(f"  ⚠️ [검색 결과 없음]", flush=True)

                link_formula = f'=HYPERLINK("{best_link}", "링크보기")' if (best_link and best_link != "-") else "-"

                row_update = list(orig_j_to_s)
                row_update[0] = best_channel
                row_update[1] = best_price
                row_update[2] = best_shipping
                row_update[9] = link_formula
                
                # 📌 시트 T열(인덱스 10번째 영역, S열 다음)에 산출근거 적어주기
                if len(row_update) <= 10:
                    row_update.append(best_reason)
                else:
                    row_update[10] = best_reason

                batch_data.append(row_update)

            browser.close()

        print("\n📤 [시트 반영 중] 최저가 및 T열 산출근거 데이터를 일괄 기록합니다...", flush=True)
        cell_range = f"J3:T{total_rows}"
        safe_batch_update(worksheet, cell_range, batch_data)

        print("🎉 T열 산출근거 자동 기록 및 OCR 보조 수집이 성공적으로 완료되었습니다!", flush=True)

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}", flush=True)
        raise e

if __name__ == "__main__":
    run_price_update()
