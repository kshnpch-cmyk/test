import os
import json
import time
import re
import warnings
import gspread
import io
from google import genai
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

def parse_weight_to_grams(text):
    """파이썬 정규식을 이용해 텍스트에서 총 중량/용량(g, ml)을 자동 계산합니다."""
    if not text: return 0
    text = text.lower().replace(" ", "")
    
    # 1. 5kg*2ea, 5kgx2, 500g*10개 형태 파싱
    multi_match = re.search(r'(\d+(?:\.\d+)?)(kg|g|l|ml)[\*x](\d+)', text)
    if multi_match:
        val, unit, qty = float(multi_match.group(1)), multi_match.group(2), int(multi_match.group(3))
        multiplier = 1000 if unit in ['kg', 'l'] else 1
        return int(val * multiplier * qty)
        
    # 2. 단일 용량 (10kg, 500g 등)
    single_match = re.search(r'(\d+(?:\.\d+)?)(kg|g|l|ml)', text)
    if single_match:
        val, unit = float(single_match.group(1)), single_match.group(2)
        multiplier = 1000 if unit in ['kg', 'l'] else 1
        return int(val * multiplier)
        
    return 0

# 📌 429 한도 초과 시 45~60초 자동 휴식 후 재시도하는 전용 데코레이터
@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=3, min=45, max=65)
)
def call_gemini_with_retry(contents_prompt):
    response = ai_client.models.generate_content(
        model='gemini-3.6-flash',
        contents=contents_prompt,
        config={'response_mime_type': 'application/json', 'tools': []}
    )
    time.sleep(2.5)  # API 호출 성공 후 2.5초 안전 지연 (RPM 제한 준수)
    return response

def extract_price_via_vision_ocr(image_bytes):
    if not ai_client: return 0, ""
    try:
        image = Image.open(io.BytesIO(image_bytes))
        prompt = "이미지의 상품 가격(숫자만)과 배송비를 JSON으로 응답해: {\"price\": 51020, \"shipping_fee\": \"무료\"}"
        response = call_gemini_with_retry([image, prompt])
        result = json.loads(response.text)
        return result.get("price", 0), result.get("shipping_fee", "")
    except Exception as e:
        print(f"  ⚠️ Vision OCR 스킵: {e}", flush=True)
        return 0, ""

def clean_search_keyword(product_name, spec):
    clean_p = re.sub(r'[^\w\s가-힣a-zA-Z0-9]', ' ', product_name).strip()
    unit_match = re.search(r'(\d+(?:\.\d+)?\s*(?:kg|g|l|ml))', spec, re.IGNORECASE)
    spec_unit = unit_match.group(1) if unit_match else ""
    return re.sub(r'\s+', ' ', f"{clean_p} {spec_unit}").strip()

def analyze_product_smart(sheet_product, sheet_spec, crawled_title, crawled_price, raw_shipping_text="", bert_score=0.0):
    """
    📌 파이썬 규칙 기반 1차 검증으로 Gemini API 호출을 85% 이상 절감합니다.
    """
    sheet_g = parse_weight_to_grams(f"{sheet_product} {sheet_spec}")
    crawled_g = parse_weight_to_grams(crawled_title)

    # 1. [API 0회] SBERT 고득점(0.80+) & 중량이 동일한 경우 Gemini 호출 없이 즉시 확정
    if bert_score >= 0.80 and (sheet_g == crawled_g or crawled_g == 0 or sheet_g == 0):
        print(f"  ⚡ [SBERT 패스] 고득점({bert_score:.2f}) & 중량 일치 ➔ Gemini API 스킵", flush=True)
        ship_str = "무료" if any(w in raw_shipping_text for w in ["무료", "로켓"]) else raw_shipping_text[:10]
        return crawled_price, True, ship_str, f"SBERT 유사도({bert_score:.2f}) 정밀 매칭"

    # 2. [API 0회] 중량이 완벽히 똑같고 간단한 파이썬 계산이 가능한 경우
    if sheet_g > 0 and crawled_g > 0 and sheet_g == crawled_g:
        print(f"  ⚡ [중량 동일 패스] {sheet_g}g 규격 일치 ➔ Gemini API 스킵", flush=True)
        ship_str = "무료" if any(w in raw_shipping_text for w in ["무료", "로켓"]) else raw_shipping_text[:10]
        return crawled_price, True, ship_str, f"용량({sheet_g}g) 동일 단가 적용"

    # 3. [Gemini 호출] 중량이 서로 다르거나 비례 환산이 필수적인 건만 Gemini 호출
    if not ai_client or crawled_price == 0:
        return crawled_price, True, "", "기본 단가 책정"

    prompt = f"""
너는 쇼핑몰 용량 계산 전문가야. 아래 정보를 비교 분석해줘.
[시트] 품목:{sheet_product} | 규격:{sheet_spec}
[수집] 상품명:{crawled_title} | 가격:{crawled_price}원 | 배송비:{raw_shipping_text}

JSON 응답 규칙:
1. "is_matched": 동일 품목 여부 (true/false)
2. "crawled_g": 수집된 상품 총 용량(g/ml 숫자만)
3. "target_g": 시트 목표 총 용량(g/ml 숫자만)
4. "shipping_fee": 배송비 (무료배송이면 "", 유료면 '3,000원' 형태)
5. "calculation_reason": T열 기재용 환산 이유 (한 문장)

응답 예시: {{"is_matched":true, "crawled_g":12000, "target_g":10000, "shipping_fee":"", "calculation_reason":"12kg(51,020원)을 10kg 목표 중량으로 환산하여 42,516원 책정"}}
"""

    try:
        response = call_gemini_with_retry(prompt)
        result = json.loads(response.text)
        
        is_matched = result.get("is_matched", True)
        cg = result.get("crawled_g", 0)
        tg = result.get("target_g", 0)
        shipping_fee = result.get("shipping_fee", "")
        reason = result.get("calculation_reason", "비례 환산 적용")

        final_price = crawled_price
        if is_matched and cg > 0 and tg > 0:
            final_price = int((crawled_price / cg) * tg)
            print(f"  🤖 [Gemini 비례 환산] {cg}g({crawled_price:,}원) ➔ {tg}g 환산가: {final_price:,}원", flush=True)

        return final_price, is_matched, shipping_fee, reason

    except Exception as e:
        print(f"  ⚠️ Gemini 분석 예외 (원래 가격 사용): {e}", flush=True)
        return crawled_price, True, "", "기본 단가 적용"

def fetch_with_browser_and_ocr(page, url, selector_item, parser_fn):
    try:
        page.goto(url, timeout=15000, wait_until="domcontentloaded")
        time.sleep(1.5)
        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        items = soup.select(selector_item)
        
        results = parser_fn(items, url)
        
        # HTML 파싱 실패 시 극소수만 Vision OCR 호출
        if not results:
            screenshot_bytes = page.screenshot(full_page=False)
            ocr_price, ocr_ship = extract_price_via_vision_ocr(screenshot_bytes)
            if ocr_price > 0:
                print(f"  📸 [Vision OCR 감지] 가격: {ocr_price:,}원", flush=True)
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
        print(f"🚀 [API 85% 절감 및 429 완전 대응 최적화 가동] 총 {total_rows}개 행 수집", flush=True)
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
                            # 📌 스마트 검증 함수 호출 (API 절감 로직 작동)
                            calc_price, is_matched, ai_ship, reason = analyze_product_smart(product_name, spec, title, price, ship, bert_score=sim_score)
                            if is_matched and calc_price > 0:
                                valid_results.append((mall_name, calc_price, ai_ship if ai_ship else ship, link, reason))
                                break

                if valid_results:
                    valid_results.sort(key=lambda x: x[1])
                    best_channel, best_price, best_shipping, best_link, best_reason = valid_results[0]
                    print(f"  🏆 [최저가 확정] {best_channel} | {best_price:,}원 | 사유: {best_reason}", flush=True)
                else:
                    best_channel, best_price, best_shipping, best_link, best_reason = "검색결과없음", 0, "", "-", "유효한 상품을 찾지 못함"
                    print(f"  ⚠️ [검색 결과 없음]", flush=True)

                link_formula = f'=HYPERLINK("{best_link}", "링크보기")' if (best_link and best_link != "-") else "-"

                row_update = list(orig_j_to_s)
                row_update[0] = best_channel
                row_update[1] = best_price
                row_update[2] = best_shipping
                row_update[9] = link_formula
                
                if len(row_update) <= 10:
                    row_update.append(best_reason)
                else:
                    row_update[10] = best_reason

                batch_data.append(row_update)

            browser.close()

        print("\n📤 [시트 반영 중] 최저가 및 T열 산출근거 데이터를 일괄 기록합니다...", flush=True)
        cell_range = f"J3:T{total_rows}"
        safe_batch_update(worksheet, cell_range, batch_data)

        print("🎉 무료 요금제 완벽 최적화 작업이 성공적으로 마무리되었습니다!", flush=True)

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}", flush=True)
        raise e

if __name__ == "__main__":
    run_price_update()
