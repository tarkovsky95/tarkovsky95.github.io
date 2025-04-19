import os
import time
import glob
import re

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from datetime import datetime, timezone, timedelta

import PyPDF2 # PDF 처리를 위한 라이브러리
import google.generativeai as genai # Gemini API 라이브러리

# --- 설정 ---
# 대상 URL
URL = "https://www.nars.go.kr/report/list.do?cmsCode=CM0043"
# 다운로드 폴더 설정 (GitHub Actions 환경 고려)
DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")
# 다운로드 대기 최대 시간 (초)
DOWNLOAD_WAIT_TIMEOUT = 120 # 2분
# Gemini API 키 (환경 변수에서 읽기)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- 함수 정의 ---

def setup_driver(download_dir):
    """Headless Chrome 드라이버를 설정하고 다운로드 폴더를 지정합니다."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # GUI 없이 백그라운드에서 실행
    chrome_options.add_argument("--no-sandbox") # Docker 또는 CI 환경에서 필요할 수 있음
    chrome_options.add_argument("--disable-dev-shm-usage") # 메모리 문제 방지
    chrome_options.add_argument("--window-size=1920x1080") # 일부 웹사이트는 해상도에 따라 레이아웃 변경

    # 다운로드 폴더 설정 및 자동 다운로드 설정
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False, # 다운로드 시 확인 창 띄우지 않음
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True # PDF를 브라우저 내장 뷰어 대신 다운로드하도록 강제
    }
    chrome_options.add_experimental_option("prefs", prefs)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    print(f"Chrome 드라이버 (Headless) 설정 완료. 다운로드 폴더: {download_dir}")
    return driver

def wait_for_download_complete(download_dir, timeout):
    """지정된 폴더에 PDF 파일 다운로드가 완료될 때까지 대기합니다."""
    print(f"'{download_dir}' 폴더에서 PDF 다운로드 완료를 대기합니다 (최대 {timeout}초)...")
    start_time = time.time()
    downloaded_pdf_path = None

    while time.time() - start_time < timeout:
        # .crdownload 파일 (다운로드 중 임시 파일)이 없는지 확인
        crdownload_files = glob.glob(os.path.join(download_dir, "*.crdownload"))
        if crdownload_files:
            print(f"다운로드 중 파일 감지: {crdownload_files}, 1초 대기...")
            time.sleep(1)
            continue

        # .pdf 파일 찾기
        pdf_files = glob.glob(os.path.join(download_dir, "*.pdf"))
        if pdf_files:
            # 여러 PDF가 있을 수 있으므로 가장 최근 파일을 선택 (수정 시간 기준)
            latest_file = max(pdf_files, key=os.path.getmtime)
            current_size = os.path.getsize(latest_file)
            print(f"PDF 파일 감지: {os.path.basename(latest_file)}, 크기: {current_size} bytes. 1초 후 크기 변화 확인...")
            time.sleep(1) # 파일 쓰기가 완료될 시간을 줌
            if current_size == os.path.getsize(latest_file) and current_size > 0: # 크기가 0보다 크고 변동이 없으면 완료로 간주
                print(f"다운로드 완료 확인: {os.path.basename(latest_file)}")
                downloaded_pdf_path = latest_file
                break
            else:
                 print(f"파일 크기 변경 감지 또는 파일 크기 0. 다운로드 진행 중...")
        else:
            # PDF 파일도 없고, crdownload 파일도 없으면 잠시 대기
            print("다운로드된 PDF 파일 없음, 1초 대기...")
            time.sleep(1)

    if downloaded_pdf_path:
        return downloaded_pdf_path
    else:
        raise TimeoutException(f"다운로드 시간 초과({timeout}초) 또는 PDF 파일을 찾을 수 없습니다.")


def extract_text_from_pdf(pdf_path):
    """PDF 파일에서 텍스트를 추출합니다."""
    print(f"PDF 파일에서 텍스트 추출 중: {pdf_path}")
    text = ""
    try:
        with open(pdf_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            num_pages = len(reader.pages)
            print(f"총 {num_pages} 페이지")
            for page_num in range(num_pages):
                page = reader.pages[page_num]
                text += page.extract_text() or "" # 텍스트 추출 실패 시 빈 문자열 반환
            print(f"텍스트 추출 완료 (총 {len(text)}자)")
            return text
    except Exception as e:
        print(f"PDF 텍스트 추출 중 오류 발생: {e}")
        raise # 오류를 다시 발생시켜 상위에서 처리하도록 함

def generate_blog_post_with_gemini(api_key, pdf_text):
    """Gemini API를 사용하여 주어진 텍스트로 블로그 글을 생성합니다."""
    if not api_key:
        raise ValueError("GEMINI_API_KEY 환경 변수가 설정되지 않았습니다.")
    if not pdf_text:
        raise ValueError("PDF에서 추출된 텍스트가 비어 있습니다.")
    print("Gemini API 설정 및 호출 시작...")
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.5-pro-exp-03-25') # 또는 다른 적합한 모델

        # Gemini에게 보낼 프롬프트 정의
        prompt = f"""
        당신은 국회입법조사처의 보고서 내용을 일반 대중이 이해하기 쉽게 **Markdown 형식의 블로그 게시물**로 재작성하는 AI 어시스턴트입니다. 최종 목표는 GitHub Pages에 바로 게시할 수 있는 `.md` 파일을 만드는 것입니다.

        **작성 가이드라인: POSST 구조 기반**

        결과물의 내용 흐름은 아래 설명된 **POSST 이론**의 구조를 따라야 합니다. 하지만 최종 결과물에는 **`[펀치라인]`, `[개요]` 와 같은 명시적인 레이블을 절대 포함하지 마세요.** 각 단계의 목적이 글의 흐름 속에서 자연스럽게 드러나도록 작성해야 합니다.

        **POSST 이론 설명 (내부 참고용):**
        * **Punchline (펀치라인):** 보고서의 가장 핵심적인 결론이나 독자의 시선을 사로잡는 내용을 강력한 첫 문단 또는 첫 몇 문장으로 제시.
        * **Overview (개요):** 보고서의 주제와 중요성을 간략히 소개하며 글의 전체적인 방향 제시.
        * **Storyline (스토리라인):** 보고서의 주요 내용, 근거, 분석 등을 논리적으로 설명. 필요시 Markdown 소제목(## 또는 ###)으로 내용을 구분하여 가독성 높임.
        * **Summary (요약):** 보고서의 핵심 내용을 간결하게 요약하며 메시지 재강조.
        * **Touch Point (터치포인트/공감 포인트):** 보고서 내용과 독자를 연결하고, 생각할 거리나 행동 제안 등으로 마무리.

        **POSST 이론 적용 예시 (내부 참고용):**
        [펀치라인] 이 작은 습관 하나가 저의 아침을, 아니 하루 전체를 완전히 바꿔 놓았습니다. [개요] 저는 매일 아침 허겁지겁 일어나 시간에 쫓기듯 하루를 시작하곤 했습니다. 그러다 우연히 '아침 10분 명상'의 효과에 대해 듣게 되었고, 반신반의하며 시작하게 된 경험을 나누고자 합니다. [스토리라인] 처음 며칠은 10분 가만히 앉아있는 것조차 좀이 쑤시고 잡생각만 가득했습니다. '이게 효과가 있을까?' 의심도 들었지만, 딱 한 달만 해보자는 생각으로 꾸준히 실천했습니다. 2주 정도 지나자 신기하게도 마음이 차분해지고, 복잡했던 머릿속이 정리되는 느낌을 받았습니다. 아침에 명상을 하고 나니 하루를 계획하고 집중하는 데 훨씬 수월해졌습니다. [요약] 결국, 아침의 짧은 명상 시간이 마음의 여유를 만들어 주고 하루의 생산성을 높이는 결정적인 계기가 된 것입니다. [터치포인트] 혹시 여러분도 저처럼 늘 시간에 쫓기고 마음의 여유가 없다고 느끼신다면, 하루 딱 10분만 투자해서 명상을 시작해보시는 것은 어떨까요? 생각보다 훨씬 큰 변화를 경험하실지도 모릅니다.

        **최종 결과물 요구사항 (Markdown 형식):**

        1.  **전체 형식:** 반드시 **Markdown 문법**을 사용하여 작성해주세요.
        2.  **제목:** 글의 맨 처음에 Markdown의 가장 큰 제목 형식 (`# 제목`)을 사용하여 전체 블로그 게시물의 제목을 작성해주세요.
        3.  **소제목 (Headings):** 내용의 구조를 명확히 하고 가독성을 높이기 위해, **Storyline** 부분 등 필요한 곳에 Markdown 소제목 (`## 소제목` 또는 `### 하위 소제목`)을 적절히 사용해주세요. (POSST 레이블을 소제목으로 대체하는 것이 아니라, 내용 흐름상 필요한 곳에 사용)
        4.  **본문:**
            * 단락 구분을 명확히 해주세요 (빈 줄 사용).
            * 중요한 키워드나 문장은 **굵은 글씨 (`**강조**`)** 또는 *기울임꼴 (`*강조*`)*을 사용하여 강조할 수 있습니다.
            * 보고서의 내용을 나열하거나 요약할 때 필요하다면 순서 없는 목록 (`- 항목 1`, `- 항목 2`) 이나 순서 있는 목록 (`1. 항목 1`, `2. 항목 2`)을 사용해주세요.
        5.  **가독성 및 톤:**
            * **일반 대중**이 이해하기 쉽도록 쉬운 용어와 간결한 문장을 사용해주세요.
            * 제공된 보고서 내용에 **충실**하되, 딱딱하지 않고 **자연스러운 블로그 글**의 어조를 유지해주세요.
            * **POSST 구조의 흐름**을 따르되, 명시적인 레이블(`[펀치라인]` 등)은 **절대 포함하지 마세요.**
        6. **사례:** 이 문서에 나온 사례는 보고서에서 예시나 가상이라고 언급하지 않는 한 전부 사실입니다. 확실하지 않다면, 가상의 사례라고 언급하는 일은 없도록 해주세요.

        --- 보고서 내용 시작 ---
        {pdf_text}
        --- 보고서 내용 끝 ---

        **이제 위의 모든 가이드라인과 보고서 내용을 바탕으로, GitHub Pages에 바로 게시할 수 있는 완결된 Markdown 형식의 블로그 게시물 전체를 작성해주세요.**
        """
        # 주의: 입력 텍스트가 너무 길면 API 제한에 걸릴 수 있습니다. pdf_text[:15000] 처럼 일부만 사용할 수 있습니다.

        response = model.generate_content(prompt)
        print("Gemini API 응답 수신 완료.")
        
        # 응답에서 텍스트 부분만 추출 (오류 처리 추가 가능)
        blog_post = response.text

        return blog_post

    except Exception as e:
        print(f"Gemini API 호출 중 오류 발생: {e}")
        # response 객체가 있고 안전하게 parts에 접근 가능한지 확인
        if hasattr(response, 'prompt_feedback') and response.prompt_feedback.block_reason:
             print(f"차단 이유: {response.prompt_feedback.block_reason}")
        raise # 오류를 다시 발생시켜 상위에서 처리하도록 함


# --- 메인 실행 로직 ---
if __name__ == "__main__":
    # 다운로드 폴더 생성
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    driver = None # finally 블록에서 사용하기 위해 미리 선언
    downloaded_pdf_file = None

    try:
        # 1. 웹 드라이버 설정
        driver = setup_driver(DOWNLOAD_DIR)

        # 2. 웹사이트 접속
        print(f"웹사이트 접속 시도: {URL}")
        driver.get(URL)
        # 페이지 로딩 및 요소 표시 대기 (명시적 대기 사용 권장)
        wait = WebDriverWait(driver, 10) # 최대 10초 대기
        print("페이지 로딩 대기...")
        # 최소한 페이지의 특정 요소(예: body)가 로드될 때까지 기다릴 수 있습니다.
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        print("페이지 로딩 완료.")

        # 3. 첫 번째 "[다운로드]" 링크 찾고 클릭
        print("첫 번째 '[다운로드]' 링크 검색 중...")
        # JavaScript 클릭을 시도하여 일반 클릭이 막힌 경우를 대비할 수 있음
        try:
             # 페이지가 동적으로 로드될 수 있으므로, 클릭 가능할 때까지 기다림
             download_link = wait.until(EC.element_to_be_clickable((By.LINK_TEXT, "[다운로드]")))
             print("다운로드 링크를 찾았습니다. 클릭합니다.")
             # JavaScript executor를 사용하면 더 안정적으로 클릭될 수 있음
             driver.execute_script("arguments[0].click();", download_link)
             # download_link.click() # 일반 클릭
             print("다운로드 링크 클릭 완료.")
        except TimeoutException:
             print("오류: '[다운로드]' 링크를 시간 내에 찾거나 클릭할 수 없습니다.")
             raise # 여기서 작업을 중단
        except NoSuchElementException:
            print("오류: '[다운로드]' 링크를 찾을 수 없습니다.")
            raise # 여기서 작업을 중단

        # 4. PDF 다운로드 완료 대기
        downloaded_pdf_file = wait_for_download_complete(DOWNLOAD_DIR, DOWNLOAD_WAIT_TIMEOUT)

        # 5. PDF에서 텍스트 추출
        pdf_content = extract_text_from_pdf(downloaded_pdf_file)

        # 6. Gemini API로 블로그 글 생성
        if pdf_content: # 추출된 내용이 있을 경우에만 API 호출
             blog_output = generate_blog_post_with_gemini(GEMINI_API_KEY, pdf_content)
             print("\n--- 생성된 블로그 글 ---")
             print(blog_output)
             print("--- 블로그 글 끝 ---")
             # TODO: 생성된 블로그 글을 파일로 저장하거나 다른 작업 수행
                         # 7. 생성된 마크다운 파일 저장
             try:
                 print("생성된 내용을 Jekyll 포스트 파일로 저장 시도...")

                 # 마크다운 내용에서 제목 추출 (첫 줄이 '# 제목' 형식이라고 가정)
                 # 앞뒤 공백 제거 후 첫 줄 가져오기
                 cleaned_output = blog_output_markdown.strip()
                 title_line = cleaned_output.split('\n', 1)[0]
                 post_title = title_line.lstrip('# ').strip() if title_line.startswith('#') else "Untitled Report"
                 print(f"추출된 제목: {post_title}")

                 # 파일명으로 사용할 슬러그 생성 (영문/숫자/_/- 만 허용)
                 # 1. 소문자화 및 특수문자 대체
                 slug = re.sub(r'[^\w\-]+', '-', post_title.lower()).strip('-')
                 # 2. 연속된 하이픈을 하나로 축약
                 slug = re.sub(r'--+', '-', slug)
                 # 3. 슬러그가 비어있으면 기본값 사용
                 if not slug:
                     slug = "report"
                 print(f"생성된 슬러그: {slug}")

                 # 현재 시간 (KST 기준) 설정
                 kst = timezone(timedelta(hours=9))
                 now = datetime.now(kst)
                 current_date_str = now.strftime('%Y-%m-%d') # 날짜 문자열 (YYYY-MM-DD)
                 current_datetime_iso = now.isoformat(timespec='seconds') # Jekyll date 형식 (ISO 8601)

                 # Jekyll 포스트 파일 경로 및 이름 정의
                 posts_dir = "_posts"
                 # _posts 디렉토리 존재 확인 및 생성 (없으면 만듦)
                 os.makedirs(posts_dir, exist_ok=True)
                 filename = f"{current_date_str}-{slug}.md"
                 filepath = os.path.join(posts_dir, filename)
                 print(f"저장할 파일 경로: {filepath}")

                 # Jekyll Front Matter 내용 구성
                 # !!! 중요: 'layout' 값은 사용하는 테마(justice-jekyll-template)에 맞게 설정해야 합니다!
                 # 테마의 문서나 _layouts 폴더를 확인하여 'post', 'default', 'page' 등 올바른 값을 사용하세요.
                 front_matter = f"""---
                    layout: post
                    title: "{post_title.replace('"', '\\"')}"
                    date: {current_datetime_iso}
                    # categories: [report] # 필요에 따라 주석 해제 또는 원하는 값으로 수정
                    # tags: [auto-generated, nars] # 필요에 따라 주석 해제 또는 원하는 값으로 수정
                    ---

                    """ # <<< Front Matter 종료 --- 필수!

                 # 파일 작성: Front Matter + Gemini가 생성한 Markdown 본문
                 with open(filepath, "w", encoding="utf-8") as f:
                     f.write(front_matter)
                     # Gemini가 생성한 내용 (시작 부분의 '# 제목' 포함)을 그대로 덧붙입니다.
                     # Jekyll은 Front Matter 아래의 첫 번째 H1(#)을 제목으로 인식하기도 하지만,
                     # 명시적인 title 필드가 있으므로 중복되어도 보통 괜찮습니다.
                     # 만약 본문에서 # 제목 줄을 제외하고 싶다면 추가 처리가 필요합니다.
                     f.write(cleaned_output) # 앞뒤 공백 제거된 내용 사용

                 print(f"블로그 포스트 저장 완료: {filepath}")

             except Exception as e:
                 print(f"오류: 생성된 블로그 포스트를 파일로 저장하는 중 문제 발생 - {e}")
                 # raise # 필요 시 주석 해제하여 워크플로우를 중단시킬 수 있음

    # (else: 이하 동일)
        else:
            print("PDF에서 텍스트를 추출하지 못해 블로그 글을 생성할 수 없습니다.")


    except NoSuchElementException:
        print("스크립트 실행 중단: 필요한 웹 요소를 찾지 못했습니다.")
    except TimeoutException as e:
        print(f"스크립트 실행 중단: 작업 시간 초과 ({e})")
    except ValueError as e:
        print(f"스크립트 실행 중단: 입력값 오류 ({e})")
    except Exception as e:
        print(f"예상치 못한 오류 발생: {e}")
    finally:
        # 웹 드라이버 종료 (항상 실행되도록 finally 사용)
        if driver:
            driver.quit()
            print("웹 드라이버를 종료합니다.")