from fastapi import FastAPI, UploadFile, File, Request, Form, HTTPException
from fastapi.responses import Response, JSONResponse
from google import genai
from google.genai import types
from google.genai import Client
import sys
import io
import zipfile
import codecs
import traceback
import re
import os
import shutil
import tempfile
import subprocess # For cloning GitHub repos
import time # For adding delays in cleanup
import errno # For checking error numbers in cleanup

import anyio

# LangChain imports
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser # Optional


# --- Initialize FastAPI App ---
app = FastAPI()

# --- CORS middleware ---
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ReportLab imports ---
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Preformatted, PageBreak
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import letter

# Helper function to handle readonly errors during rmtree on Windows
def handle_remove_readonly(func, path, exc):
    """
    Error handler for shutil.rmtree.
    If the error is due to an access error (read only file)
    it attempts to add write permission and then retries the
    remove. If the error is for another reason it re-raises
    the error.
    """
    import stat
    excvalue = exc[1]
    if func in (os.rmdir, os.remove, os.unlink) and excvalue.errno == errno.EACCES:
        os.chmod(path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)  # 0777
        func(path)
    else:
        raise

# --- ReportLab imports ---
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Preformatted, PageBreak
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import letter

# --- Gemini API Configuration ---
API_KEY = "AIzaSyCOgj1wrOhehSxLBfPYi6WUNpyqp7jPv6o"
MODEL_NAME = "gemini-2.0-flash-lite"

# --- Token Limits ---
fixed_max_tokens_analysis = 10
fixed_max_tokens_transcription = 10 # Limit for code transcription. Adjust as needed.

# NEW: Mapping for target language file extensions
LANGUAGE_EXTENSIONS = {
    'Java': '.java',
    'CSharp': '.cs',
    'Python': '.py',
    'JavaScript': '.js',
    'C++': '.cpp',
    'Ruby': '.rb',
    'PHP': '.php',
    'Go': '.go',
    'Swift': '.swift',
    'Kotlin': '.kt'
}

# Initialize LangChain Google Generative AI model for ANALYSIS
try:
    llm_analysis = ChatGoogleGenerativeAI(
        model=MODEL_NAME,
        google_api_key=API_KEY,
        max_output_tokens=fixed_max_tokens_analysis,
    )
    print(f"LangChain analysis model initialized successfully with max_output_tokens={fixed_max_tokens_analysis}.")
except Exception as e:
    print(f"Error initializing LangChain analysis model: {e}", file=sys.stderr)

# Initialize LangChain Google Generative AI model for TRANSCRIPTION (with its own token limit)
try:
    llm_transcription = ChatGoogleGenerativeAI(
        model=MODEL_NAME,
        google_api_key=API_KEY,
        max_output_tokens=fixed_max_tokens_transcription, # Applied here
    )
    print(f"LangChain transcription model initialized successfully with max_output_tokens={fixed_max_tokens_transcription}.")
except Exception as e:
    print(f"Error initializing LangChain transcription model: {e}", file=sys.stderr)


# Keep the google.genai client for direct calls if needed, especially for multi-modal outside LangChain invoke
try:
    client = Client(api_key=API_KEY)
    print("Original Gemini client initialized successfully.")
except Exception as e:
    print(f"Error initializing original Gemini client: {e}", file=sys.stderr)


# --- Define LangChain Prompt Templates ---

# NEW: Prompt Template for Multi-COBOL Analysis
multi_cobol_analysis_template = """
Act as an Expert COBOL Program Analyst. You are provided with the content of multiple COBOL source files that constitute a single program or related modules.
Your task is to analyze these files as a cohesive unit.

Specifically, identify and explain the following:
1.  **Overall Program Purpose/Functionality:** What does this collection of COBOL files achieve together?
2.  **Inter-file Connections:**
    * How do these files interact? (e.g., CALL statements, shared data structures, common copybooks, external dependencies).
    * Identify key entry points and control flow across files.
    * Describe the sequence in which different modules might be executed.
3.  **Shared Variables and Data Flow:**
    * Point out important variables, records, or data structures that are passed or implicitly shared between files (e.g., via `LINKAGE SECTION`, `COPY` statements for common data layouts, external data definitions).
    * Trace the flow of critical data elements across different modules.
4.  **Module Responsibilities:** Briefly explain the primary responsibility of each COBOL file within the context of the entire program.
5.  **Potential Issues/Insights:** Highlight any complex interactions, potential areas for optimization, or common COBOL patterns observed across the files.

Present your analysis in a structured, detailed, and clear manner. Reference specific file names where connections or functionalities are identified.

COBOL Program Files Content (each file is clearly delimited):
{combined_cobol_content}
"""
MULTI_COBOL_ANALYSIS_PROMPT_TEMPLATE = PromptTemplate.from_template(multi_cobol_analysis_template)


# Existing document analysis template (for PDFs and generic text files)
document_analysis_template = """
Act as an expert content analyst and a detailed explainer. Your task is to carefully examine the content of a file I will provide you, analyze
what is happening in it (its logic, functionality, flow, etc.), and then explain it to me in a clear and understandable way.
I want the explanation to be divided into a step-by-step sequence. For each step, provide a detailed description of what happens, why it happens,
and any relevant details that will help me understand it thoroughly. The goal is for me to be able to understand each point individually and the
overall process of the file.

File content:
{file_content}
"""
DOCUMENT_ANALYSIS_PROMPT_TEMPLATE = PromptTemplate.from_template(document_analysis_template)

# Existing code analysis template (could be used for individual code files if not COBOL, or for generic code analysis)
code_analysis_template = """
Act as an Expert Code Analyzer and a Detailed Programming Logic Explicador.
Your primary goal is to thoroughly analyze the code I will provide you. I need you to explain to me its general functionality, its underlying
programming logic, and how each aspect of the code (variables, functions, control structures, etc.) contributes to the final result.
Present your analysis as a detailed, step-by-step explanation of the code's execution flow or logic. For each step:
Concísamente describe what happens in that phase or segment of the code.
Identificar y explicar las partes específicas del código involucradas (ej. "Aquí se llama a la función calculate_average" o "En este punto, se incrementa la variable counter").
Detail how that particular aspect or those lines of code work in this step.
Explain why that step is necessary in the context of the overall program flow.
Ensure that each point is explained with enough detail so that someone studying the code can understand it completely.
Use clear and precise language.

Code:
{file_content}
"""
CODE_ANALYSIS_PROMPT_TEMPLATE = PromptTemplate.from_template(code_analysis_template)

# Existing code transcription template
code_transcription_template = """
Migrate the provided COBOL code to {target_language}.
Provide only the migrated code in the target language, without any additional explanations or formatting markdown like ```.

COBOL Code:
{file_content}
"""
CODE_TRANSCRIPTION_PROMPT_TEMPLATE = PromptTemplate.from_template(code_transcription_template)

# NEW: Prompt Template for Application/Integration Guide of Migrated Code
application_guide_template = """
You are an expert software architect and migration specialist.
The following COBOL program has been migrated to {target_language}. Your task is to provide a comprehensive, step-by-step guide on how to integrate and apply this newly migrated code within a typical {target_language} application environment.

Cover the following aspects:

1.  **Dependencies and Prerequisites:**
    * What libraries, frameworks, or runtime environments are typically required for this {target_language} code?
    * Are there any specific version considerations?

2.  **Project Structure and Location:**
    * Where should this {target_language} code typically reside within a standard {target_language} project (e.g., specific folders, namespaces, packages)?
    * Suggest a logical project structure if multiple files are involved.

3.  **Integration Steps:**
    * How would this migrated code typically be called or interacted with from other parts of a {target_language} application? (e.g., instantiation, static method calls, dependency injection).
    * If it's a standalone script, how would it be executed?
    * Address any necessary input/output handling or data type conversions.

4.  **Testing and Verification:**
    * What are the key areas to focus on when testing this migrated code?
    * Suggest approaches for unit testing or integration testing.

5.  **Best Practices and Considerations:**
    * Any {target_language}-specific idioms or best practices that should be applied to this code after migration.
    * Common pitfalls or performance considerations when using this type of code in {target_language}.
    * How to handle error logging or exceptions.

6.  **Usage Example (Optional but Recommended):**
    * Provide a small illustrative code snippet in {target_language} demonstrating how to instantiate and use a key part of the migrated code.

Provide clear and actionable steps. Assume the user has basic knowledge of {target_language}.

Migrated {target_language} Code (for context):
{migrated_code_content}

"""
APPLICATION_GUIDE_PROMPT_TEMPLATE = PromptTemplate.from_template(application_guide_template)


# Helper function to handle API response (kept for direct calls for PDF)
def handle_gemini_response_direct(response, task_type, file_name):
    """Processes the DIRECT Gemini API response for a specific task and file."""
    task_description = f"{task_type} for File: {file_name}"

    response_text = f"Could not get text content for {task_description} (Direct Call)."
    status = f"Error: {task_type} - Direct API/Handling Failed"

    if response is not None:
        if response.text:
            response_text = response.text
            status = f"OK {task_type} for {file_name} (Direct Call)"
            print(f"    Direct API Response OK for '{task_description}'.")
        elif response.candidates:
            candidate_texts = []
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, 'text'):
                            candidate_texts.append(part.text)
            if candidate_texts:
                response_text = " ".join(candidate_texts)
                status = f"OK (candidates) {task_type} for {file_name} (Direct Call)"
                print(f"    Direct API Response OK (candidates) for '{task_description}'.")
            else:
                status = f"Error: {task_type} - Candidates without text for {file_name} (Direct Call)"
                response_text = f"Gemini returned candidates but no text content for {task_description} (Direct Call)."
                print(status, file=sys.stderr)
                print("    Full direct response (candidates without text):", response, file=sys.stderr)

        elif response.prompt_feedback:
            block_reason = response.prompt_feedback.block_reason
            safety_ratings = response.prompt_feedback.safety_ratings
            response_text = f"Prompt or response for {task_description} was blocked by safety (Direct Call). Reason: {block_reason}"
            if safety_ratings:
                response_text += " | Ratings: " + ", ".join([f"{r.category}: {r.probability}" for r in safety_ratings])
            status = f"Blocked: {task_type} for {file_name} (Direct Call)"
            print(status, file=sys.stderr)
            print("    Full prompt feedback (Direct Call):", response.prompt_feedback, file=sys.stderr)

        else:
            response_text = f"Direct API returned unexpected response for {task_description}."
            status = f"Error: {task_type} - Unexpected Direct Response for {file_name}"
            print(status, file=sys.stderr)
            print("    Unexpected direct API response:", response, file=sys.stderr)

    else:
        response_text = f"Direct API call did not return a response object (null response) for {task_description}."
        status = f"Error: {task_type} - Null Direct Call for {file_name}"
        print(status, file=sys.stderr)
        print("    Null direct API call.", file=sys.stderr)

    return status, response_text


# Synchronous ZIP Processing Logic (will run in a separate thread)
def process_zip_files_sync(zip_data: bytes, target_language: str = None, is_reprocess: bool = False):
    """
    Processes binary ZIP data synchronously.
    Separates COBOL files for combined analysis and other files for individual analysis.
    Performs transcription for individual COBOL files if requested.
    Generates application guide for transcribed COBOL files.
    Returns analysis_results, transcription_results, and application_guide_results.
    
    NEW: Added is_reprocess parameter to handle reprocessing logic
    """
    process_type = "REPROCESS" if is_reprocess else "INITIAL PROCESS"
    print(f"--> Entered process_zip_files_sync function ({process_type})")

    analysis_results = []
    transcription_results = [] # Will now store text content, not PDF bytes
    application_guide_results = []
    processed_file_count = 0

    document_extensions = ('.pdf', '.txt')
    cobol_extensions = ('.cbl', '.cob')
    processable_extensions = document_extensions + cobol_extensions

    valid_target_languages = list(LANGUAGE_EXTENSIONS.keys()) # Use keys from LANGUAGE_EXTENSIONS
    request_transcription_for_cobol = False

    if target_language and target_language in valid_target_languages:
        request_transcription_for_cobol = True
        print(f"  Transcription requested for COBOL to: {target_language} (in sync thread) - {process_type}")
    elif target_language:
        print(f"  Invalid target language received: '{target_language}'. COBOL will be summarized only. (in sync thread) - {process_type}", file=sys.stderr)
        target_language = None
    else:
        print(f"  No target language selected for COBOL transcription. (in sync thread) - {process_type}")


    cobol_files_content = []
    other_files_to_process = []

    try:
        with zipfile.ZipFile(io.BytesIO(zip_data), 'r') as zip_archive:
            print(f"--> Zip file opened successfully in sync thread ({process_type})")
            print(f"Collecting files within the ZIP ({process_type})...")

            for file_info in zip_archive.infolist():
                if file_info.is_dir():
                    continue

                file_name = file_info.filename
                file_extension = '.' + file_name.lower().split('.')[-1] if '.' in file_name else ''

                if file_extension in processable_extensions:
                    processed_file_count += 1
                    try:
                        if file_extension == '.pdf':
                            other_files_to_process.append({
                                'filename': file_name,
                                'data': zip_archive.read(file_info),
                                'mime_type': 'application/pdf',
                                'extension': file_extension
                            })
                            print(f"  Collected PDF file: {file_name} ({process_type})")
                        elif file_extension == '.txt':
                            txt_bytes = zip_archive.read(file_info)
                            try:
                                file_text = txt_bytes.decode('utf-8')
                            except UnicodeDecodeError:
                                file_text = txt_bytes.decode('latin-1')
                                print(f"    Decoded '{file_name}' using latin-1 during collection ({process_type}).", file=sys.stderr)

                            other_files_to_process.append({
                                'filename': file_name,
                                'text': file_text,
                                'mime_type': 'text/plain',
                                'extension': file_extension
                            })
                            print(f"  Collected TXT file: {file_name} ({process_type})")
                        elif file_extension in cobol_extensions:
                            cobol_txt_bytes = zip_archive.read(file_info)
                            try:
                                cobol_file_text = cobol_txt_bytes.decode('utf-8')
                            except UnicodeDecodeError:
                                cobol_file_text = cobol_txt_bytes.decode('latin-1')
                                print(f"    Decoded '{file_name}' using latin-1 during collection ({process_type}).", file=sys.stderr)

                            cobol_files_content.append({
                                'filename': file_name,
                                'text': cobol_file_text
                            })
                            print(f"  Collected COBOL file: {file_name} ({process_type})")
                        else:
                            print(f"  Ignoring file with unsupported extension during collection: {file_name} ({process_type})")

                    except Exception as e:
                        error_msg = f"Error collecting/decoding file '{file_name}': {e} ({process_type})"
                        print(f"--- {error_msg} ---", file=sys.stderr)
                        traceback.print_exc(file=sys.stderr)
                        analysis_results.append({'filename': file_name, 'status': f"Collection Error {file_extension.upper().strip('.')} ({process_type})", 'text': error_msg})
                        if file_extension in cobol_extensions and request_transcription_for_cobol:
                            transcription_results.append({'filename': file_name, 'status': f"Omitted: Read/prep error ({process_type})", 'text': f"Transcription for '{file_name}' omitted due to read/prep error during collection. ({process_type})"})
                        continue
                else:
                    print(f"  Ignoring file with unsupported extension: {file_name} ({process_type})")

            print(f"Finished collecting files. Starting processing... ({process_type})")

            # --- Process Other Files (PDFs, TXT) Individually for Analysis ---
            for file_data_obj in other_files_to_process:
                file_name = file_data_obj['filename']
                file_extension = file_data_obj['extension']
                mime_type = file_data_obj['mime_type']
                current_file_data = file_data_obj.get('data')
                current_file_text = file_data_obj.get('text')

                print(f"\n  --> Processing individual file {file_name} for Analysis ({process_type})")

                analysis_status = f"Error: Processing failed for {file_name} ({process_type})"
                analysis_text = f"Could not process file for analysis: {file_name}"

                analysis_prompt_template = DOCUMENT_ANALYSIS_PROMPT_TEMPLATE

                try:
                    print(f"    --> Attempting API call for Analysis/Summary for {file_name} ({process_type})")

                    if current_file_text is not None:
                        print(f"    Using LangChain for Analysis of text file: {file_name} ({process_type})")
                        analysis_chain = analysis_prompt_template | llm_analysis
                        summary_response_lc = analysis_chain.invoke({'file_content': current_file_text})

                        if hasattr(summary_response_lc, 'content') and summary_response_lc.content:
                            analysis_status = f"OK Analysis for {file_name} (LangChain {process_type})"
                            analysis_text = summary_response_lc.content
                            print(f"    Analysis result for '{file_name}': {analysis_status}")
                        else:
                            analysis_status = f"Error: LangChain response empty/no content for {file_name} ({process_type})"
                            analysis_text = f"LangChain invoke returned no content for analysis of {file_name}."
                            print(analysis_status, file=sys.stderr)
                            print("    LangChain Response object:", summary_response_lc, file=sys.stderr)

                    elif current_file_data is not None and file_extension == '.pdf':
                        print(f"    Using Direct Client for Analysis of PDF file: {file_name} ({process_type})")
                        pdf_analysis_parts = [types.Part.from_text(text=analysis_prompt_template.format(file_content=""))]
                        pdf_analysis_parts.append(types.Part.from_bytes(data=current_file_data, mime_type=mime_type))

                        summary_response = client.models.generate_content(
                            model=MODEL_NAME,
                            contents=pdf_analysis_parts,
                            config=types.GenerateContentConfig(max_output_tokens=fixed_max_tokens_analysis),
                        )
                        analysis_status, analysis_text = handle_gemini_response_direct(summary_response, "Analysis", file_name)
                        print(f"    Analysis result for '{file_name}': {analysis_status}")
                    else:
                        analysis_status = f"Error: No processable content found for individual file ({process_type})"
                        analysis_text = f"No text or binary content prepared for {file_name} for API call."
                        print(analysis_status, file=sys.stderr)

                    analysis_results.append({
                        'filename': file_name,
                        'status': analysis_status,
                        'text': analysis_text
                    })
                    print(f"    --> Finished API call for Analysis/Summary for {file_name} ({process_type})")

                except Exception as e:
                    error_text = f"Exception during API call for {file_name}: {e} ({process_type})"
                    print(f"--- {error_text} ---", file=sys.stderr)
                    traceback.print_exc(file=sys.stderr)
                    analysis_results.append({'filename': file_name, 'status': f"Exception during Analysis ({process_type})", 'text': error_text})


            # --- Process COBOL Files Collectively for Analysis ---
            if cobol_files_content:
                print(f"\n  --> Processing {len(cobol_files_content)} COBOL files for combined analysis ({process_type})")
                combined_cobol_content_str = ""
                for cobol_file in cobol_files_content:
                    combined_cobol_content_str += f"--- Start File: {cobol_file['filename']} ---\n"
                    combined_cobol_content_str += cobol_file['text']
                    combined_cobol_content_str += f"\n--- End File: {cobol_file['filename']} ---\n\n"

                combined_analysis_status = f"Error: Combined COBOL analysis failed ({process_type})"
                combined_analysis_text = "No combined COBOL analysis could be generated."

                try:
                    print(f"    --> Attempting LangChain call for Combined COBOL Analysis ({process_type})")
                    analysis_chain_combined = MULTI_COBOL_ANALYSIS_PROMPT_TEMPLATE | llm_analysis
                    combined_response_lc = analysis_chain_combined.invoke({'combined_cobol_content': combined_cobol_content_str})

                    if hasattr(combined_response_lc, 'content') and combined_response_lc.content:
                        combined_analysis_status = f"OK Combined Analysis for {len(cobol_files_content)} COBOL files (LangChain {process_type})"
                        combined_analysis_text = combined_response_lc.content
                        print(f"    Combined Analysis result: {combined_analysis_status}")
                    else:
                        combined_analysis_status = f"Error: LangChain response empty/no content for combined COBOL analysis ({process_type})"
                        combined_analysis_text = f"LangChain invoke returned no content for combined COBOL analysis."
                        print(combined_analysis_status, file=sys.stderr)
                        print("    LangChain Response object (combined):", combined_response_lc, file=sys.stderr)

                except Exception as e:
                    error_text = f"Exception during LangChain Combined COBOL Analysis invoke: {e} ({process_type})"
                    print(f"--- {error_text} ---", file=sys.stderr)
                    traceback.print_exc(file=sys.stderr)
                    combined_analysis_status = f"Exception LangChain Combined Analysis ({process_type})"
                    combined_analysis_text = error_text

                analysis_results.append({
                    'filename': f"Combined_COBOL_Program_Analysis_({len(cobol_files_content)}_files).txt",
                    'status': combined_analysis_status,
                    'text': combined_analysis_text
                })
                print(f"  --> Finished Combined COBOL Analysis ({process_type})")
            else:
                print(f"  No COBOL files found for combined analysis. ({process_type})")

            # --- Process COBOL Files Individually for Transcription (if requested) ---
            if request_transcription_for_cobol and target_language:
                # llm_transcription is now initialized globally with its own token limit
                print(f"  LangChain transcription model (llm_transcription) ready for use. ({process_type})")
                # Get the target extension, default to .txt if not found (shouldn't happen with valid_target_languages)
                target_ext = LANGUAGE_EXTENSIONS.get(target_language, '.txt')


                for cobol_file in cobol_files_content:
                    file_name = cobol_file['filename']
                    current_file_text = cobol_file['text']

                    transcription_status = f"Omitted: Not requested or Prep failed for {file_name} ({process_type})"
                    transcribed_result_text = f"Transcription not performed due to read/prep error for {file_name} ({process_type})."


                    print(f"\n  --> Processing COBOL file {file_name} for Transcription ({process_type})")
                    transcription_task_desc = f"Transcription to {target_language} for File: {file_name}"
                    print(f"    --> Attempting LangChain call for Transcription for {file_name} ({process_type})")
                    print(f"    Sending '{transcription_task_desc}' of '{file_name}' to Gemini API via LangChain ({MODEL_NAME})... (NO AWAIT in sync thread)")

                    try:
                        transcription_chain = CODE_TRANSCRIPTION_PROMPT_TEMPLATE | llm_transcription
                        transcription_response_lc = transcription_chain.invoke({'file_content': current_file_text, 'target_language': target_language})

                        if hasattr(transcription_response_lc, 'content') and transcription_response_lc.content:
                            transcription_status = f"OK Transcription for {file_name} (LangChain {process_type})"
                            transcribed_code = transcription_response_lc.content
                            print(f"    Transcription result for '{file_name}': {transcription_status}")

                            match = re.search(r'```(?:[a-zA-Z0-9_+#-]+)?\n(.*?)\n```', transcribed_code, re.DOTALL)
                            # If the model wraps the code in markdown, extract it. Otherwise, use as is.
                            transcribed_result_text = match.group(1).strip() if match else transcribed_code.strip()

                        else:
                            transcription_status = f"Error: LangChain response empty/no content for {file_name} ({process_type})"
                            transcribed_code = f"LangChain invoke returned no content for transcription of {file_name}."
                            transcribed_result_text = transcribed_code
                            print(transcription_status, file=sys.stderr)
                            print("    LangChain Response object:", transcription_response_lc, file=sys.stderr)

                    except Exception as e:
                        error_text = f"Exception during LangChain Transcription invoke for {file_name}: {e} ({process_type})"
                        print(f"--- {error_text} ---", file=sys.stderr)
                        traceback.print_exc(file=sys.stderr)
                        transcription_status = f"Exception LangChain Transcription ({process_type})"
                        transcribed_result_text = error_text

                    # Store the transcription text content, not PDF bytes
                    transcription_results.append({
                        'filename': file_name,
                        'status': transcription_status,
                        'text': transcribed_result_text,
                        'target_extension': target_ext # Store the determined extension
                    })
                    print(f"    --> Finished LangChain call for Transcription for {file_name} ({process_type})")

                    # Generate Application Guide for this Transcribed File (this remains a PDF)
                    if transcription_status.startswith("OK"):
                        print(f"\n    --> Generating Application Guide for transcribed code from {file_name} to {target_language} ({process_type})")
                        app_guide_status = f"Error: Application Guide generation failed ({process_type})"
                        app_guide_text = "Could not generate the application guide."
                        try:
                            app_guide_chain = APPLICATION_GUIDE_PROMPT_TEMPLATE | llm_analysis
                            app_guide_response_lc = app_guide_chain.invoke({
                                'migrated_code_content': transcribed_result_text,
                                'target_language': target_language
                            })

                            if hasattr(app_guide_response_lc, 'content') and app_guide_response_lc.content:
                                app_guide_status = f"OK Application Guide for {file_name} ({target_language}) (LangChain {process_type})"
                                app_guide_text = app_guide_response_lc.content
                                print(f"    Application Guide result for '{file_name}': {app_guide_status}")
                            else:
                                app_guide_status = f"Error: LangChain response empty/no content for Application Guide of {file_name} ({process_type})"
                                app_guide_text = f"LangChain invoke returned no content for Application Guide of {file_name}."
                                print(app_guide_status, file=sys.stderr)
                                print("    LangChain Response object (Application Guide):", app_guide_response_lc, file=sys.stderr)

                        except Exception as e:
                            error_text = f"Exception during LangChain Application Guide invocation for {file_name}: {e} ({process_type})"
                            print(f"--- {error_text} ---", file=sys.stderr)
                            traceback.print_exc(file=sys.stderr)
                            app_guide_status = f"Exception in LangChain Application Guide ({process_type})"
                            app_guide_text = error_text

                        application_guide_results.append({
                            'filename': file_name,
                            'status': app_guide_status,
                            'text': app_guide_text,
                            'target_language': target_language
                        })
                        print(f"    --> Finished Application Guide generation for {file_name} ({process_type})")
                    else:
                        print(f"    Skipping Application Guide generation for {file_name} due to transcription error or omission.")
                        application_guide_results.append({
                            'filename': file_name,
                            'status': f"Omitted: Transcription error for {file_name} ({process_type})",
                            'text': f"Application guide for '{file_name}' omitted due to transcription error or omission.",
                            'target_language': target_language
                        })

            else:
                print(f"  No COBOL files to transcribe or transcription was not requested. ({process_type})")
                for cobol_file in cobol_files_content:
                    existing_transcription_entry = next((item for item in transcription_results if item['filename'] == cobol_file['filename']), None)
                    if not existing_transcription_entry:
                        transcription_results.append({
                            'filename': cobol_file['filename'],
                            'status': f"Omitted: Not requested ({process_type})",
                            'text': f"Transcription for '{cobol_file['filename']}' was omitted because no target language was selected. ({process_type})"
                        })


    except zipfile.BadZipFile:
        error_msg = f"Error: The uploaded file is not a valid ZIP file. ({process_type})"
        print(error_msg, file=sys.stderr)
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"An general error occurred while processing the ZIP file: {e} ({process_type})"
        print(error_msg, file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise Exception(error_msg)


    print(f"File processing completed in synchronous thread ({process_type}). Returning results.")
    return analysis_results, transcription_results, application_guide_results, processed_file_count


# NEW: Helper function to clone GitHub repository
def clone_repo(repo_url: str, target_path: str) -> str:
    """
    Clones a public GitHub repository to the specified target_path.
    Returns the name of the cloned repository directory.
    """
    try:
        print(f"Attempting to clone repository: {repo_url} into {target_path}")
        # Use --depth 1 for a shallow clone to save time and space
        subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, target_path],
            check=True,
            capture_output=True,
            text=True
        )
        repo_name = os.path.basename(target_path) # Or parse from URL if preferred
        print(f"Repository '{repo_name}' cloned successfully to '{target_path}'.")
        return repo_name
    except subprocess.CalledProcessError as e:
        error_message = f"Error cloning repository {repo_url}. Return code: {e.returncode}\nStdout: {e.stdout}\nStderr: {e.stderr}"
        print(error_message, file=sys.stderr)
        raise Exception(error_message)
    except FileNotFoundError:
        error_message = "Error: Git command not found. Please ensure Git is installed and in your system's PATH."
        print(error_message, file=sys.stderr)
        raise Exception(error_message)
    except Exception as e:
        error_message = f"An unexpected error occurred during repository cloning: {e}"
        print(error_message, file=sys.stderr)
        raise Exception(error_message)

# NEW: Synchronous Repository Files Processing Logic (will run in a separate thread)
def process_repository_files_sync(repo_path: str, target_language: str = None, is_reprocess: bool = False):
    """
    Processes files from a cloned repository directory.
    Separates COBOL files for combined analysis and other files for individual analysis.
    Performs transcription for individual COBOL files if requested.
    Generates application guide for transcribed COBOL files.
    Returns analysis_results, transcription_results, and application_guide_results.
    NEW: Added is_reprocess parameter.
    """
    repo_process_indicator = "(REPO_REPROCESS)" if is_reprocess else "(REPO_PROCESS)"
    print(f"--> Entered process_repository_files_sync function for path: {repo_path} {repo_process_indicator}")

    analysis_results = []
    transcription_results = []
    application_guide_results = []
    processed_file_count = 0

    document_extensions = ('.pdf', '.txt')
    cobol_extensions = ('.cbl', '.cob')
    processable_extensions = document_extensions + cobol_extensions

    valid_target_languages = list(LANGUAGE_EXTENSIONS.keys())
    request_transcription_for_cobol = False

    if target_language and target_language in valid_target_languages:
        request_transcription_for_cobol = True
        print(f"  Transcription requested for COBOL to: {target_language} {repo_process_indicator}")
    elif target_language:
        print(f"  Invalid target language received: '{target_language}'. COBOL will be summarized only. {repo_process_indicator}", file=sys.stderr)
        target_language = None
    else:
        print(f"  No target language selected for COBOL transcription. {repo_process_indicator}")

    cobol_files_content = []
    other_files_to_process = []

    print(f"Collecting files within the repository path: {repo_path}...")
    for root, _, files in os.walk(repo_path):
        # Skip .git directory
        if '.git' in root.split(os.sep):
            continue
        for filename in files:
            file_path = os.path.join(root, filename)
            file_extension = os.path.splitext(filename)[1].lower()

            if file_extension in processable_extensions:
                processed_file_count += 1
                try:
                    if file_extension == '.pdf':
                        with open(file_path, 'rb') as f:
                            pdf_data = f.read()
                        other_files_to_process.append({
                            'filename': filename, 'data': pdf_data, 'mime_type': 'application/pdf', 'extension': file_extension
                        })
                        print(f"  Collected PDF file: {filename} {repo_process_indicator}")
                    elif file_extension == '.txt':
                        with open(file_path, 'r', encoding='utf-8', errors='latin-1') as f:
                            txt_content = f.read()
                        other_files_to_process.append({
                            'filename': filename, 'text': txt_content, 'mime_type': 'text/plain', 'extension': file_extension
                        })
                        print(f"  Collected TXT file: {filename} {repo_process_indicator}")
                    elif file_extension in cobol_extensions:
                        with open(file_path, 'r', encoding='utf-8', errors='latin-1') as f:
                            cobol_content = f.read()
                        cobol_files_content.append({'filename': filename, 'text': cobol_content})
                        print(f"  Collected COBOL file: {filename} {repo_process_indicator}")
                except Exception as e:
                    error_msg = f"Error collecting/reading file '{filename}' from repo {repo_process_indicator}: {e}"
                    print(f"--- {error_msg} ---", file=sys.stderr)
                    analysis_results.append({'filename': filename, 'status': f"Collection Error {file_extension.upper().strip('.')} {repo_process_indicator}", 'text': error_msg})
            else:
                print(f"  Ignoring file with unsupported extension: {filename} {repo_process_indicator}")

    # The rest of the processing logic (API calls, etc.) is very similar to process_zip_files_sync
    # For brevity, I'm showing the structure. You'd essentially adapt the API call sections
    # from process_zip_files_sync here, using the collected 'other_files_to_process' and 'cobol_files_content'.
    # --- Process Other Files (PDFs, TXT) Individually for Analysis ---
    # (Similar loop and logic as in process_zip_files_sync, using data from other_files_to_process)
    # ... (implementation detail: copy and adapt from process_zip_files_sync)
    for file_data_obj in other_files_to_process:
        file_name = file_data_obj['filename']
        file_extension = file_data_obj['extension']
        mime_type = file_data_obj['mime_type']
        current_file_data = file_data_obj.get('data')
        current_file_text = file_data_obj.get('text')

        print(f"\n  --> Processing individual file {file_name} for Analysis {repo_process_indicator}")
        analysis_status = f"Error: Processing failed for {file_name} {repo_process_indicator}"
        analysis_text = f"Could not process file for analysis: {file_name}"
        analysis_prompt_template = DOCUMENT_ANALYSIS_PROMPT_TEMPLATE

        try:
            print(f"    --> Attempting API call for Analysis/Summary for {file_name} {repo_process_indicator}")
            if current_file_text is not None: # TXT files
                analysis_chain = analysis_prompt_template | llm_analysis
                summary_response_lc = analysis_chain.invoke({'file_content': current_file_text})
                if hasattr(summary_response_lc, 'content') and summary_response_lc.content:
                    analysis_status = f"OK Analysis for {file_name} (LangChain {repo_process_indicator})"
                    analysis_text = summary_response_lc.content
                else:
                    analysis_status = f"Error: LangChain response empty for {file_name} {repo_process_indicator}"
                    analysis_text = f"LangChain invoke returned no content for analysis of {file_name}."
            elif current_file_data is not None and file_extension == '.pdf': # PDF files
                pdf_analysis_parts = [types.Part.from_text(text=analysis_prompt_template.format(file_content=""))]
                pdf_analysis_parts.append(types.Part.from_bytes(data=current_file_data, mime_type=mime_type))
                summary_response = client.models.generate_content(
                    model=MODEL_NAME, contents=pdf_analysis_parts,
                    config=types.GenerateContentConfig(max_output_tokens=fixed_max_tokens_analysis)
                )
                analysis_status, analysis_text = handle_gemini_response_direct(summary_response, "Analysis", file_name)
            else:
                analysis_status = f"Error: No processable content for {file_name} {repo_process_indicator}"
                analysis_text = f"No text or binary content for {file_name}."
            
            analysis_results.append({'filename': file_name, 'status': analysis_status, 'text': analysis_text})
        except Exception as e:
            error_text = f"Exception during API call for {file_name} {repo_process_indicator}: {e}"
            print(f"--- {error_text} ---", file=sys.stderr)
            analysis_results.append({'filename': file_name, 'status': f"Exception during Analysis {repo_process_indicator}", 'text': error_text})

    # --- Process COBOL Files Collectively for Analysis ---
    # (Similar loop and logic as in process_zip_files_sync, using data from cobol_files_content)
    # ... (implementation detail: copy and adapt from process_zip_files_sync)
    if cobol_files_content:
        print(f"\n  --> Processing {len(cobol_files_content)} COBOL files for combined analysis {repo_process_indicator}")
        combined_cobol_content_str = ""
        for cobol_file in cobol_files_content:
            combined_cobol_content_str += f"--- Start File: {cobol_file['filename']} ---\n{cobol_file['text']}\n--- End File: {cobol_file['filename']} ---\n\n"
        
        combined_analysis_status = f"Error: Combined COBOL analysis failed ({process_type})"
        combined_analysis_text = "No combined COBOL analysis could be generated."
        try:
            analysis_chain_combined = MULTI_COBOL_ANALYSIS_PROMPT_TEMPLATE | llm_analysis
            combined_response_lc = analysis_chain_combined.invoke({'combined_cobol_content': combined_cobol_content_str})
            if hasattr(combined_response_lc, 'content') and combined_response_lc.content:
                combined_analysis_status = f"OK Combined Analysis for {len(cobol_files_content)} COBOL files (LangChain {repo_process_indicator})"
                combined_analysis_text = combined_response_lc.content
            else:
                combined_analysis_status = f"Error: LangChain response empty for combined COBOL {repo_process_indicator}"
                combined_analysis_text = "LangChain invoke returned no content for combined COBOL analysis."
        except Exception as e:
            error_text = f"Exception during LangChain Combined COBOL Analysis {repo_process_indicator}: {e}"
            print(f"--- {error_text} ---", file=sys.stderr)
            combined_analysis_status = f"Exception LangChain Combined Analysis {repo_process_indicator}"
            combined_analysis_text = error_text
        
        analysis_results.append({
            'filename': f"Combined_COBOL_Program_Analysis_({len(cobol_files_content)}_files).txt",
            'status': combined_analysis_status, 'text': combined_analysis_text
        })

    # --- Process COBOL Files Individually for Transcription (if requested) ---
    # (Similar loop and logic as in process_zip_files_sync, using data from cobol_files_content)
    # ... (implementation detail: copy and adapt from process_zip_files_sync, including application guide generation)
    if request_transcription_for_cobol and target_language:
        target_ext = LANGUAGE_EXTENSIONS.get(target_language, '.txt')
        for cobol_file in cobol_files_content:
            file_name = cobol_file['filename']
            current_file_text = cobol_file['text']
            transcription_status = f"Omitted: Prep failed for {file_name} {repo_process_indicator}"
            transcribed_result_text = f"Transcription not performed for {file_name}."

            try:
                transcription_chain = CODE_TRANSCRIPTION_PROMPT_TEMPLATE | llm_transcription
                transcription_response_lc = transcription_chain.invoke({'file_content': current_file_text, 'target_language': target_language})
                if hasattr(transcription_response_lc, 'content') and transcription_response_lc.content:
                    transcription_status = f"OK Transcription for {file_name} (LangChain {repo_process_indicator})"
                    transcribed_code = transcription_response_lc.content
                    match = re.search(r'```(?:[a-zA-Z0-9_+#-]+)?\n(.*?)\n```', transcribed_code, re.DOTALL)
                    transcribed_result_text = match.group(1).strip() if match else transcribed_code.strip()
                else:
                    transcription_status = f"Error: LangChain response empty for {file_name} {repo_process_indicator}"
                    transcribed_result_text = f"LangChain invoke returned no content for transcription of {file_name}."
            except Exception as e:
                error_text = f"Exception during LangChain Transcription for {file_name} {repo_process_indicator}: {e}"
                print(f"--- {error_text} ---", file=sys.stderr)
                transcription_status = f"Exception LangChain Transcription {repo_process_indicator}"
                transcribed_result_text = error_text
            
            transcription_results.append({
                'filename': file_name, 'status': transcription_status,
                'text': transcribed_result_text, 'target_extension': target_ext
            })

            # Generate Application Guide
            if transcription_status.startswith("OK"):
                app_guide_status = f"Error: App Guide generation failed {repo_process_indicator}"
                app_guide_text = "Could not generate application guide."
                try:
                    app_guide_chain = APPLICATION_GUIDE_PROMPT_TEMPLATE | llm_analysis
                    app_guide_response_lc = app_guide_chain.invoke({
                        'migrated_code_content': transcribed_result_text, 'target_language': target_language
                    })
                    if hasattr(app_guide_response_lc, 'content') and app_guide_response_lc.content:
                        app_guide_status = f"OK Application Guide for {file_name} ({target_language}) (LangChain {repo_process_indicator})"
                        app_guide_text = app_guide_response_lc.content
                    else:
                        app_guide_status = f"Error: LangChain response empty for App Guide of {file_name} {repo_process_indicator}"
                        app_guide_text = f"LangChain invoke returned no content for App Guide of {file_name}."
                except Exception as e:
                    error_text = f"Exception during LangChain App Guide for {file_name} {repo_process_indicator}: {e}"
                    print(f"--- {error_text} ---", file=sys.stderr)
                    app_guide_status = f"Exception LangChain App Guide {repo_process_indicator}"
                    app_guide_text = error_text
                application_guide_results.append({
                    'filename': file_name, 'status': app_guide_status,
                    'text': app_guide_text, 'target_language': target_language
                })
            else: # Transcription failed or omitted
                 application_guide_results.append({
                    'filename': file_name, 'status': f"Omitted: Transcription error for {file_name} {repo_process_indicator}",
                    'text': f"App guide for '{file_name}' omitted due to transcription error {repo_process_indicator}.",
                    'target_language': target_language
                })
    else: # No transcription requested or no COBOL files
        for cobol_file in cobol_files_content: # Ensure entries for all cobol files
            transcription_results.append({
                'filename': cobol_file['filename'], 'status': "Omitted: Not requested or no COBOL files",
                'text': "Transcription not performed."
            })

    print(f"Repository file processing completed. Returning results.")
    return analysis_results, transcription_results, application_guide_results, processed_file_count

# Helper function to create final ZIP file
def create_final_zip(analysis_results, transcription_results, application_guide_results, 
                    archive_filename, target_language, is_reprocess=False):
    """
    Creates the final ZIP file with all results.
    """
    process_suffix = "_reprocessed" if is_reprocess else ""
    base_archive_name = archive_filename.replace('.zip', '') if archive_filename.endswith('.zip') else archive_filename

    print(f"--> Starting final ZIP file creation{' (REPROCESS)' if is_reprocess else ''}...")
    final_zip_buffer = io.BytesIO()
    
    # Create analysis PDF
    analysis_pdf_bytes = None
    try:
        analysis_pdf_buffer = io.BytesIO()
        doc = SimpleDocTemplate(analysis_pdf_buffer, pagesize=letter)
        story = []

        styles = getSampleStyleSheet()
        style_title = styles['h1']
        style_filename = styles['h3']
        style_body = styles['Normal']
        style_error = styles['Normal']
        style_error.textColor = (1, 0, 0)

        title_suffix = " (Reprocessed)" if is_reprocess else ""
        story.append(Paragraph(f"Content Analysis: {base_archive_name}{title_suffix}", style_title))
        story.append(Spacer(1, 0.2*letter[1]))

        if not analysis_results:
            story.append(Paragraph("No analysis results could be obtained.", style_error))
        else:
            for result in analysis_results:
                story.append(Paragraph(f"File: {result['filename']} ({result['status']})", style_filename))
                story.append(Spacer(1, 6))
                text_style = style_body
                if result['status'].startswith("Error") or result['status'].startswith("Blocked") or result['status'].startswith("Exception"):
                    text_style = style_error
                story.append(Paragraph(str(result['text']), text_style))
                story.append(Spacer(1, 18))

        doc.build(story)
        analysis_pdf_bytes = analysis_pdf_buffer.getvalue()
        analysis_pdf_buffer.close()
        print(f"--> Analysis PDF generated{' (REPROCESS)' if is_reprocess else ''}.")
    except Exception as e:
        print(f"--- Error generating analysis PDF: {e} ---", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

    # Create application guide PDF
    application_guide_master_pdf_bytes = None
    if application_guide_results:
        try:
            app_guide_master_buffer = io.BytesIO()
            doc_master_guide = SimpleDocTemplate(app_guide_master_buffer, pagesize=letter)
            story_master_guide = []

            styles_master_guide = getSampleStyleSheet()
            style_title_master_guide = styles_master_guide['h1']
            style_section_title = styles_master_guide['h2']
            style_body_master_guide = styles_master_guide['Normal']
            style_error_master_guide = styles_master_guide['Normal']
            style_error_master_guide.textColor = (1, 0, 0)

            guide_title_suffix = " (Reprocessed)" if is_reprocess else ""
            story_master_guide.append(Paragraph(f"Application Guide for Code Migrated to {target_language if target_language else 'N/A'}{guide_title_suffix}", style_title_master_guide))
            story_master_guide.append(Spacer(1, 0.2*letter[1]))
            story_master_guide.append(Paragraph("This document contains step-by-step guides for integrating and applying the newly migrated code.", style_body_master_guide))
            story_master_guide.append(Spacer(1, 18))

            for i, result in enumerate(application_guide_results):
                if result['status'].startswith("OK"):
                    if i > 0:
                        story_master_guide.append(PageBreak())

                    story_master_guide.append(Paragraph(f"Guide for: {result['filename']} (Migrated to {result.get('target_language', 'N/A')})", style_section_title))
                    story_master_guide.append(Spacer(1, 12))
                    story_master_guide.append(Paragraph(str(result['text']), style_body_master_guide))
                    story_master_guide.append(Spacer(1, 18))
                else:
                    story_master_guide.append(Paragraph(f"Error or Omission in Application Guide for: {result['filename']} ({result['status']})", style_error_master_guide))
                    story_master_guide.append(Paragraph(str(result['text']), style_error_master_guide))
                    story_master_guide.append(Spacer(1, 18))

            doc_master_guide.build(story_master_guide)
            application_guide_master_pdf_bytes = app_guide_master_buffer.getvalue()
            app_guide_master_buffer.close()
            print(f"--> Application Guide PDF generated{' (REPROCESS)' if is_reprocess else ''}.")
        except Exception as e:
            print(f"--- Error generating Application Guide PDF: {e} ---", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    # Create the final ZIP
    try:
        with zipfile.ZipFile(final_zip_buffer, 'w', zipfile.ZIP_DEFLATED) as final_zip:
            # Add analysis PDF
            if analysis_pdf_bytes is not None:
                analysis_pdf_filename = f"analysis_summary_{base_archive_name}{process_suffix}.pdf"
                final_zip.writestr(analysis_pdf_filename, analysis_pdf_bytes)
                print(f"  Added '{analysis_pdf_filename}' to the ZIP.")
            else:
                print("  Error: Analysis PDF not generated, not adding to ZIP.", file=sys.stderr)

            # Add transcribed code files
            if transcription_results:
                for result in transcription_results:
                    if result['status'].startswith("OK") and result['text']:
                        original_base_name = result['filename'].rsplit('.', 1)[0]
                        target_extension = result.get('target_extension', '.txt')
                        transcribed_code_filename = f"{original_base_name}{target_extension}"
                        final_zip.writestr(transcribed_code_filename, result['text'].encode('utf-8'))
                        print(f"  Added transcribed code file '{transcribed_code_filename}' to the ZIP.")
                    else:
                        original_base_name = result['filename'].rsplit('.', 1)[0]
                        error_filename = f"{original_base_name}_transcription_error{process_suffix}.txt"
                        final_zip.writestr(error_filename, result['text'].encode('utf-8'))
                        print(f"  Added error file '{error_filename}' for transcription error.")
            else:
                print("  No transcription results to add to the ZIP.", file=sys.stderr)

            # Add application guide PDF
            if application_guide_master_pdf_bytes is not None:
                master_guide_pdf_filename = f"application_guide_all_migrated_code_to_{target_language if target_language else 'N_A'}{process_suffix}.pdf"
                final_zip.writestr(master_guide_pdf_filename, application_guide_master_pdf_bytes)
                print(f"  Added '{master_guide_pdf_filename}' to the ZIP.")
            else:
                print("  Application guide PDF was not generated, not added to the ZIP.", file=sys.stderr)

        final_zip_bytes = final_zip_buffer.getvalue()
        final_zip_buffer.close()
        
        print(f"--> Final ZIP file generated ({len(final_zip_bytes)} bytes){' (REPROCESS)' if is_reprocess else ''}.")
        return final_zip_bytes
        
    except Exception as e:
        print(f"--- Error creating the final ZIP file: {e} ---", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise Exception(f"Error creating the final ZIP file: {e}")


@app.post("/upload")
async def upload_archive_endpoint(
    archive_file: UploadFile = File(...),
    target_language: str = Form(None)
):
    print("--> Request received in /upload async route")
    print("POST request received at /upload (FastAPI async)")

    print(f"  Uploaded file: {archive_file.filename}")
    print(f"  Target language received from form: {target_language}")

    if not archive_file.filename or not archive_file.filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail="Only .zip files are allowed.")

    try:
        await archive_file.seek(0)
        zip_data = await archive_file.read()
        print(f"File '{archive_file.filename}' ({len(zip_data)} bytes) read successfully (FastAPI async).")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading ZIP file content: {e}")

    try:
        analysis_results, transcription_results, application_guide_results, processed_file_count = await anyio.to_thread.run_sync(
            process_zip_files_sync,
            zip_data,
            target_language,
            False  # is_reprocess = False for initial upload
        )
        print(f"--> Finished process_zip_files_sync. Results received: Analysis={len(analysis_results)}, Transcription={len(transcription_results)}, Application_Guides={len(application_guide_results)}.")

    except Exception as e:
        print(f"--- Exception propagated to FastAPI route from synchronous thread: {e} ---", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        if "valid ZIP" in str(e):
            raise HTTPException(status_code=400, detail=str(e))
        else:
            raise HTTPException(status_code=500, detail=f"Error during ZIP file processing in a separate thread: {e}")

    try:
        final_zip_bytes = create_final_zip(
            analysis_results, 
            transcription_results, 
            application_guide_results,
            archive_file.filename,
            target_language,
            is_reprocess=False
        )

        print("--> Returning HTTP response with the ZIP file.")
        return Response(content=final_zip_bytes, media_type='application/zip', headers={
            'Content-Disposition': f'attachment; filename="migration_results_{archive_file.filename}"',
            'Content-Length': str(len(final_zip_bytes))
        })

    except Exception as e:
        print(f"--- Error creating the final ZIP file: {e} ---", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return JSONResponse(status_code=500, content={"error": f"Error creating the final ZIP file: {e}"})


# NEW: Reprocess endpoint
@app.post("/reprocess")
async def reprocess_archive_endpoint(
    archive_file: UploadFile = File(...),
    target_language: str = Form(None),
    is_reprocess: str = Form(None)
):
    print("--> Request received in /reprocess async route")
    print("POST request received at /reprocess (FastAPI async)")

    print(f"  Reprocessing file: {archive_file.filename}")
    print(f"  Target language received from form: {target_language}")
    print(f"  Is reprocess flag: {is_reprocess}")

    if not archive_file.filename or not archive_file.filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail="Only .zip files are allowed.")

    try:
        await archive_file.seek(0)
        zip_data = await archive_file.read()
        print(f"File '{archive_file.filename}' ({len(zip_data)} bytes) read successfully for reprocessing (FastAPI async).")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading ZIP file content for reprocessing: {e}")

    try:
        analysis_results, transcription_results, application_guide_results, processed_file_count = await anyio.to_thread.run_sync(
            process_zip_files_sync,
            zip_data,
            target_language,
            True  # is_reprocess = True for reprocessing
        )
        print(f"--> Finished reprocess_zip_files_sync. Results received: Analysis={len(analysis_results)}, Transcription={len(transcription_results)}, Application_Guides={len(application_guide_results)}.")

    except Exception as e:
        print(f"--- Exception propagated to FastAPI reprocess route from synchronous thread: {e} ---", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        if "valid ZIP" in str(e):
            raise HTTPException(status_code=400, detail=str(e))
        else:
            raise HTTPException(status_code=500, detail=f"Error during ZIP file reprocessing in a separate thread: {e}")

    try:
        final_zip_bytes = create_final_zip(
            analysis_results, 
            transcription_results, 
            application_guide_results,
            archive_file.filename,
            target_language,
            is_reprocess=True
        )

        print("--> Returning HTTP response with the reprocessed ZIP file.")
        return Response(content=final_zip_bytes, media_type='application/zip', headers={
            'Content-Disposition': f'attachment; filename="migration_results_{archive_file.filename.replace(".zip", "_reprocessed.zip")}"',
            'Content-Length': str(len(final_zip_bytes))
        })

    except Exception as e:
        print(f"--- Error creating the reprocessed final ZIP file: {e} ---", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return JSONResponse(status_code=500, content={"error": f"Error creating the reprocessed final ZIP file: {e}"})

# NEW: Endpoint to analyze a GitHub repository
@app.post("/analyze_repo")
async def analyze_repo_endpoint(
    repo_url: str = Form(...),
    target_language: str = Form(None),
    is_reprocess: bool = Form(False) # NEW: Flag for reprocessing
):
    repo_process_indicator = "(REPROCESS)" if is_reprocess else "(INITIAL)"
    print(f"--> Request received in /analyze_repo async route {repo_process_indicator}")
    print(f"  Repository URL: {repo_url}")
    print(f"  Target language: {target_language} / Is Reprocess: {is_reprocess}")

    # Basic validation for GitHub URL (can be improved)
    if not repo_url.startswith("https://github.com/") or not repo_url.endswith(".git"):
        # Allow URLs without .git at the end for user convenience
        if not repo_url.startswith("https://github.com/"):
             raise HTTPException(status_code=400, detail="Invalid GitHub repository URL format. Must start with 'https://github.com/'.")
        if not repo_url.endswith(".git"):
            repo_url += ".git" # Append .git if missing for cloning
            print(f"  Appended .git to repo_url: {repo_url}")


    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        print(f"  Created temporary directory: {temp_dir}")

        # Derive a base name for the output from the repo URL
        repo_name_for_file = repo_url.split('/')[-1].replace('.git', '')

        cloned_repo_path = os.path.join(temp_dir, repo_name_for_file)
        actual_repo_name = clone_repo(repo_url, cloned_repo_path) # actual_repo_name might be slightly different if git changes it

        analysis_results, transcription_results, application_guide_results, processed_file_count = await anyio.to_thread.run_sync(
            process_repository_files_sync, # Use the new function
            cloned_repo_path,
            target_language,
            is_reprocess # Pass the flag
        )
        print(f"--> Finished process_repository_files_sync. Results: Analysis={len(analysis_results)}, Transcription={len(transcription_results)}, AppGuides={len(application_guide_results)}.")

        final_zip_bytes = create_final_zip(
            analysis_results,
            transcription_results,
            application_guide_results,
            actual_repo_name, # Use the actual cloned repo name for the ZIP content
            target_language,
            is_reprocess=is_reprocess # Pass the flag
        )

        if is_reprocess:
            output_zip_filename = f"analysis_results_{actual_repo_name}_reprocessed.zip"
        else:
            output_zip_filename = f"analysis_results_{actual_repo_name}.zip"
        return Response(content=final_zip_bytes, media_type='application/zip', headers={
            'Content-Disposition': f'attachment; filename="{output_zip_filename}"',
            'Content-Length': str(len(final_zip_bytes))
        })
    except Exception as e:
        print(f"--- Error in /analyze_repo endpoint: {e} ---", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Error processing GitHub repository: {str(e)}")
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir, onerror=handle_remove_readonly)
                print(f"  Cleaned up temporary directory: {temp_dir}")
            except Exception as e_rm:
                print(f"  Warning: Could not completely remove temp directory {temp_dir} after first attempt: {e_rm}. Retrying after delay.")
                time.sleep(1) # Wait 1 second
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True) # Attempt again, ignoring errors this time
                    print(f"  Successfully cleaned up temporary directory {temp_dir} after delay and retry.")
                except Exception as e_rm_retry:
                    print(f"  Error: Failed to clean up temporary directory {temp_dir} even after retry: {e_rm_retry}", file=sys.stderr)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)