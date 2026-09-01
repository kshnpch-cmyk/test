def analyze_product_with_gemini(sheet_product, sheet_spec, crawled_title, crawled_price):
    """
    Gemini AI를 호출하여 검색된 상품명의 실제 묶음 수량을 파싱하고,
    시트 규격에 맞는 단가를 정밀 계산합니다.
    """
    if not ai_client:
        print("  ⚠️ GEMINI_API_KEY가 설정되지 않아 기본 정규식 파싱으로 대체합니다.")
        return crawled_price, True

    prompt = f"""
너는 쇼핑몰 데이터 분석 전문가야.
아래 [구글 시트 요청 정보]와 크롤링한 [쇼핑몰 검색 결과]를 비교 분석해줘.

[구글 시트 요청 정보]
- 품목명: {sheet_product}
- 규격: {sheet_spec}

[쇼핑몰 검색 결과]
- 검색된 상품명: {crawled_title}
- 검색된 표시 가격: {crawled_price}원

다음 질문에 맞춰 오직 JSON 형식으로만 응답해:
1. "is_matched": 검색된 상품이 구글 시트 요청 품목과 동일한 종류의 상품인지 여부 (true/false)
2. "crawled_unit_qty": 검색된 상품명(예: '1kg (3개)') 속에 포함된 제품 개수 (숫자만, 예: 3). 단품이면 1
3. "single_unit_price": 검색된 표시 가격을 개수로 나눈 '1개당 단가' (숫자만)
4. "reason": 판단 이유 요약

응답 형식(JSON):
{{
  "is_matched": true,
  "crawled_unit_qty": 3,
  "single_unit_price": 5770,
  "reason": "1kg 3개 묶음 17310원이므로 1개당 단가는 5770원입니다."
}}
"""

    try:
        # 모델명을 'gemini-2.5-flash'로 정확히 지정합니다.
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        
        result = json.loads(response.text)
        is_matched = result.get("is_matched", True)
        single_price = result.get("single_unit_price", crawled_price)
        reason = result.get("reason", "")
        
        print(f"  🤖 [Gemini 분석] {reason}")
        return single_price, is_matched

    except Exception as e:
        print(f"  ❌ Gemini 분석 오류: {e}")
        return crawled_price, True
