# law.py (수정본)

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
# 저장될 게시물 폴더 (Handmade Blog 템플릿 기준)
POSTS_DIR = "_articles" # <--- 경로 변경!
# 다운로드 대기 최대 시간 (초)
DOWNLOAD_WAIT_TIMEOUT = 120 # 2분
# Gemini API 키 (환경 변수에서 읽기)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- 함수 정의 ---

def setup_driver(download_dir):
    """Headless Chrome 드라이버를 설정하고 다운로드 폴더를 지정합니다."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920x1080")
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True
    }
    chrome_options.add_experimental_option("prefs", prefs)
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        print(f"Chrome 드라이버 (Headless) 설정 완료. 다운로드 폴더: {download_dir}")
        return driver
    except ValueError as e:
        # WebDriver Manager가 드라이버를 찾지 못하는 경우 등 처리
        print(f"WebDriver 설정 중 오류 발생: {e}")
        print("시스템에 Chrome이 설치되어 있는지 확인하거나, GitHub Actions 환경의 Runner 구성을 확인하세요.")
        raise

def wait_for_download_complete(download_dir, timeout):
    """지정된 폴더에 PDF 파일 다운로드가 완료될 때까지 대기합니다."""
    print(f"'{download_dir}' 폴더에서 PDF 다운로드 완료를 대기합니다 (최대 {timeout}초)...")
    start_time = time.time()
    downloaded_pdf_path = None

    while time.time() - start_time < timeout:
        crdownload_files = glob.glob(os.path.join(download_dir, "*.crdownload"))
        if crdownload_files:
            print(f"다운로드 중 파일 감지: {crdownload_files}, 1초 대기...")
            time.sleep(1)
            continue

        pdf_files = glob.glob(os.path.join(download_dir, "*.pdf"))
        if pdf_files:
            latest_file = max(pdf_files, key=os.path.getmtime)
            try:
                current_size = os.path.getsize(latest_file)
                print(f"PDF 파일 감지: {os.path.basename(latest_file)}, 크기: {current_size} bytes. 1초 후 크기 변화 확인...")
                time.sleep(1) # 파일 쓰기 완료 시간 확보
                final_size = os.path.getsize(latest_file)
                if current_size == final_size and current_size > 0:
                    print(f"다운로드 완료 확인: {os.path.basename(latest_file)}")
                    downloaded_pdf_path = latest_file
                    break
                else:
                    print(f"파일 크기 변경 감지 또는 크기 0. 다운로드 진행 중...")
            except FileNotFoundError:
                print(f"파일 접근 중 오류 발생 (파일이 삭제되었을 수 있음): {latest_file}, 계속 대기...")
                time.sleep(1)
            except Exception as e:
                 print(f"파일 크기 확인 중 예상치 못한 오류: {e}")
                 time.sleep(1)
        else:
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
                try:
                    page = reader.pages[page_num]
                    extracted = page.extract_text()
                    if extracted: # None이 아닌 경우에만 추가
                        text += extracted
                except Exception as page_e:
                    print(f"{page_num + 1}번째 페이지 텍스트 추출 중 오류: {page_e}")
            print(f"텍스트 추출 완료 (총 {len(text)}자)")
            return text
    except Exception as e:
        print(f"PDF 텍스트 추출 중 오류 발생: {e}")
        raise

def generate_blog_post_with_gemini(api_key, pdf_text):
    """Gemini API를 사용하여 주어진 텍스트로 블로그 글을 생성합니다."""
    if not api_key:
        raise ValueError("GEMINI_API_KEY 환경 변수가 설정되지 않았습니다.")
    if not pdf_text or pdf_text.strip() == "":
        raise ValueError("PDF에서 추출된 텍스트가 비어 있습니다.")
    print("Gemini API 설정 및 호출 시작...")
    try:
        genai.configure(api_key=api_key)
        # 사용 가능한 최신 모델 중 하나를 선택하거나, 특정 버전을 명시할 수 있습니다.
        # 예: 'gemini-1.5-flash-latest', 'gemini-1.5-pro-latest', 'gemini-pro'
        # 모델 이름은 변경될 수 있으므로 Gemini 문서를 확인하는 것이 좋습니다.
        model = genai.GenerativeModel('gemini-1.5-flash-latest') # 예시 모델

        prompt = f"""
        당신은 국회입법조사처의 보고서 내용을 일반 대중이 이해하기 쉽게 **Markdown 형식의 블로그 게시물**로 재작성하는 AI 어시스턴트입니다. 최종 목표는 GitHub Pages 블로그('Handmade Blog' 템플릿 사용)에 게시할 수 있는 `.md` 파일을 만드는 것입니다.

        **작성 가이드라인: POSST 구조 기반**
        결과물의 내용 흐름은 아래 설명된 **POSST 이론**의 구조를 따라야 합니다. 하지만 최종 결과물에는 **`[펀치라인]`, `[개요]` 와 같은 명시적인 레이블을 절대 포함하지 마세요.** 각 단계의 목적이 글의 흐름 속에서 자연스럽게 드러나도록 작성해야 합니다.

        **POSST 이론 설명 (내부 참고용):**
        * **Punchline (펀치라인):** 보고서의 가장 핵심적인 결론이나 독자의 시선을 사로잡는 내용을 강력한 첫 문단 또는 첫 몇 문장으로 제시.
        * **Overview (개요):** 보고서의 주제와 중요성을 간략히 소개하며 글의 전체적인 방향 제시.
        * **Storyline (스토리라인):** 보고서의 주요 내용, 근거, 분석 등을 논리적으로 설명. 필요시 Markdown 소제목(## 또는 ###)으로 내용을 구분하여 가독성 높임.
        * **Summary (요약):** 보고서의 핵심 내용을 간결하게 요약하며 메시지 재강조.
        * **Touch Point (터치포인트/공감 포인트):** 보고서 내용과 독자를 연결하고, 생각할 거리나 행동 제안 등으로 마무리.

        **최종 결과물 요구사항 (Markdown 형식):**
        1.  **전체 형식:** 반드시 **Markdown 문법**을 사용하여 작성해주세요.
        2.  **제목:** 글의 맨 처음에 Markdown의 가장 큰 제목 형식 (`# 제목`)을 사용하여 전체 블로그 게시물의 제목을 작성해주세요. (Front Matter의 title과 별개로 본문에도 포함)
        3.  **소제목 (Headings):** 내용의 구조를 명확히 하고 가독성을 높이기 위해, **Storyline** 부분 등 필요한 곳에 Markdown 소제목 (`## 소제목` 또는 `### 하위 소제목`)을 적절히 사용해주세요.
        4.  **본문:** 단락 구분 명확히(빈 줄 사용). 필요시 **굵은 글씨**, *기울임꼴*, 목록 사용.
        5.  **가독성 및 톤:** **일반 대중**이 이해하기 쉽도록 쉬운 용어, 간결한 문장. 보고서 내용에 **충실**하되 **자연스러운 블로그 글** 어조 유지. POSST 구조 따르되 명시적 레이블 금지.
        6.  **사례:** 보고서에서 명확히 가상이라고 언급하지 않는 한 사실로 간주. 불확실하면 가상이라고 단정하지 말 것.

        --- 보고서 내용 시작 ---
        {pdf_text}
        --- 보고서 내용 끝 ---

        **이제 위의 모든 가이드라인과 보고서 내용을 바탕으로, 완결된 Markdown 형식의 블로그 게시물 본문 전체를 작성해주세요.**
        """
        # 입력 텍스트 길이 제한 고려 (예: 첫 10000자만 사용)
        max_input_length = 10000
        if len(pdf_text) > max_input_length:
            print(f"입력 텍스트가 너무 깁니다 ({len(pdf_text)}자). 앞부분 {max_input_length}자만 사용합니다.")
            pdf_text_input = pdf_text[:max_input_length]
        else:
            pdf_text_input = pdf_text

        response = model.generate_content(prompt)
        print("Gemini API 응답 수신 완료.")

        # 응답 유효성 검사 및 텍스트 추출
        if hasattr(response, 'text'):
             blog_post = response.text
             # 가끔 API 응답 시작/끝에 ```markdown ``` 같은 마커가 붙는 경우 제거
             blog_post = re.sub(r'^```markdown\s*', '', blog_post, flags=re.IGNORECASE)
             blog_post = re.sub(r'\s*```$', '', blog_post)
             return blog_post.strip()
        elif hasattr(response, 'prompt_feedback') and response.prompt_feedback.block_reason:
             print(f"콘텐츠 생성 차단됨. 이유: {response.prompt_feedback.block_reason}")
             raise ValueError(f"Gemini 콘텐츠 생성 차단됨: {response.prompt_feedback.block_reason}")
        else:
             print("오류: Gemini API 응답에서 유효한 텍스트를 추출할 수 없습니다.")
             # print("전체 응답:", response) # 디버깅용
             raise ValueError("Gemini API 응답 형식 오류")

    except Exception as e:
        print(f"Gemini API 호출 중 오류 발생: {e}")
        raise

def save_markdown_post(markdown_content):
    """생성된 마크다운 내용을 파일로 저장합니다."""
    try:
        print("생성된 내용을 블로그 포스트 파일로 저장 시도...")

        # 마크다운 내용에서 제목 추출 (첫 줄이 '# 제목' 형식이라고 가정)
        cleaned_output = markdown_content.strip()
        if not cleaned_output:
            raise ValueError("저장할 마크다운 내용이 비어 있습니다.")

        title_line = cleaned_output.split('\n', 1)[0]
        post_title = title_line.lstrip('# ').strip() if title_line.startswith('#') else "Untitled Report"
        print(f"추출된 제목: {post_title}")

        # 파일명 슬러그 생성
        slug = re.sub(r'[^\w\s\-]+', '', post_title.lower()) # 영문/숫자/공백/- 외 제거
        slug = re.sub(r'\s+', '-', slug).strip('-') # 공백을 하이픈으로 변경
        slug = re.sub(r'--+', '-', slug)
        if not slug: slug = "report"
        print(f"생성된 슬러그: {slug}")

        # KST 시간 설정 및 파일명 생성
        kst = timezone(timedelta(hours=9))
        now = datetime.now(kst)
        current_date_str = now.strftime('%Y-%m-%d')
        current_datetime_iso = now.isoformat(timespec='seconds') # Front Matter용

        # 경로 및 파일명 정의 (_articles 폴더 사용)
        os.makedirs(POSTS_DIR, exist_ok=True)
        filename = f"{current_date_str}-{slug}.md"
        filepath = os.path.join(POSTS_DIR, filename)
        print(f"저장할 파일 경로: {filepath}")

        # Front Matter 내용 구성 (Handmade Blog 템플릿에 맞게 layout 수정 필요!)
        # 'handmade-blog' 템플릿은 EJS 기반이므로 Front Matter를 다르게 처리할 수 있음.
        # README나 템플릿 구조를 보고 `_articles`의 md 파일이 어떻게 처리되는지 확인 필요.
        # 만약 EJS 템플릿에서 직접 마크다운을 렌더링한다면 Front Matter가 필요 없을 수도 있음.
        # 여기서는 일반적인 Jekyll/SSG 형식으로 가정하고 작성 (수정 필요 가능성 높음!)
        # 확인 결과: Handmade Blog는 Front Matter를 사용하지 않고, 파일명과 publish 스크립트로 관리.
        # 따라서 Front Matter 부분은 제거하고 순수 Markdown 내용만 저장해야 함.
        # front_matter = f"""---
        # layout: article # <--- Handmade Blog 템플릿에 맞는 레이아웃 이름 확인 필요!
        # title: "{post_title.replace('"', '\\"')}"
        # date: {current_datetime_iso}
        # ---
        #
        # """

        # 파일 작성: 순수 Markdown 본문만 저장
        with open(filepath, "w", encoding="utf-8") as f:
            # f.write(front_matter) # Front Matter 제거
            f.write(cleaned_output) # Gemini가 생성한 내용 (제목 포함)

        print(f"블로그 포스트 저장 완료: {filepath}")
        return filepath # 저장된 파일 경로 반환

    except Exception as e:
        print(f"오류: 생성된 블로그 포스트를 파일로 저장하는 중 문제 발생 - {e}")
        raise

# --- 메인 실행 로직 ---
if __name__ == "__main__":
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    driver = None
    downloaded_pdf_file = None
    new_post_filepath = None

    try:
        driver = setup_driver(DOWNLOAD_DIR)
        print(f"웹사이트 접속 시도: {URL}")
        driver.get(URL)
        wait = WebDriverWait(driver, 20) # 로딩 시간 고려하여 대기 시간 늘림
        print("페이지 로딩 대기...")
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='.pdf']"))) # PDF 링크가 보일 때까지 대기
        print("페이지 로딩 완료.")

        print("첫 번째 '[다운로드]' 링크 검색 중...")
        try:
            # CSS 선택자를 사용하여 PDF 링크를 포함하는 첫 번째 다운로드 링크 찾기
            # 좀 더 구체적인 선택자가 필요할 수 있음 (사이트 구조에 따라)
            # 예: download_link = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".board_list tbody tr:first-child a[href*='.pdf']")))
            download_links = driver.find_elements(By.PARTIAL_LINK_TEXT, "[다운로드]") # 모든 [다운로드] 링크 찾기
            if not download_links:
                 raise NoSuchElementException("어떤 '[다운로드]' 링크도 찾을 수 없습니다.")

            download_link = download_links[0] # 첫 번째 링크 선택
            print("다운로드 링크를 찾았습니다. 클릭합니다.")
            driver.execute_script("arguments[0].click();", download_link)
            print("다운로드 링크 클릭 완료.")
        except (TimeoutException, NoSuchElementException) as e:
            print(f"오류: '[다운로드]' 링크를 찾거나 클릭하는 중 문제 발생 - {e}")
            raise

        downloaded_pdf_file = wait_for_download_complete(DOWNLOAD_DIR, DOWNLOAD_WAIT_TIMEOUT)
        pdf_content = extract_text_from_pdf(downloaded_pdf_file)

        if pdf_content:
            blog_output_markdown = generate_blog_post_with_gemini(GEMINI_API_KEY, pdf_content) # 변수명 수정
            print("\n--- 생성된 블로그 글 (Markdown) ---")
            print(blog_output_markdown)
            print("--- 블로그 글 끝 ---")

            new_post_filepath = save_markdown_post(blog_output_markdown) # 저장 함수 호출

        else:
            print("PDF에서 텍스트를 추출하지 못해 블로그 글을 생성할 수 없습니다.")

    except Exception as e:
        print(f"스크립트 실행 중 오류 발생: {e}")
        # 실패 시 종료 코드를 반환하여 Actions에서 실패로 인지하도록 할 수 있음
        # import sys
        # sys.exit(1)
    finally:
        if driver:
            driver.quit()
            print("웹 드라이버를 종료합니다.")
        # 다운로드한 PDF 파일 삭제 (선택 사항)
        if downloaded_pdf_file and os.path.exists(downloaded_pdf_file):
             try:
                 os.remove(downloaded_pdf_file)
                 print(f"다운로드한 PDF 파일 삭제 완료: {downloaded_pdf_file}")
             except Exception as e:
                 print(f"오류: 다운로드한 PDF 파일 삭제 중 문제 발생 - {e}")

        # Actions 워크플로우에서 이 스크립트가 새 파일을 생성했는지 여부를 알 수 있도록
        # 생성된 파일 경로를 출력하거나, 특정 출력 변수를 설정할 수 있음.
        if new_post_filepath:
             print(f"::set-output name=new_post_path::{new_post_filepath}") # Actions 출력 변수 설정 (예시)