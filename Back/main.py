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

import anyio # Used by FastAPI for run_sync


from fastapi.middleware.cors import CORSMiddleware


from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Preformatted
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import letter


API_KEY = "AIzaSyCOgj1wrOhehSxLBfPYi6WUNpyqp7jPv6o"


try:
    client = Client(api_key=API_KEY)
except Exception as e:
    print(f"Error initializing Gemini client: {e}", file=sys.stderr)


MODEL_NAME = "gemini-2.0-flash-lite"

# Base prompt for document analysis (PDF, TXT)
DOCUMENT_ANALYSIS_PROMPT = """
Act as an expert content analyst and a detailed explainer. Your task is to carefully examine the content of a file I will provide you, analyze
what is happening in it (its logic, functionality, flow, etc.), and then explain it to me in a clear and understandable way.
I want the explanation to be divided into a step-by-step sequence. For each step, provide a detailed description of what happens, why it happens,
and any relevant details that will help me understand it thoroughly. The goal is for me to be able to understand each point individually and the
overall process of the file.
"""

# Base prompt for code analysis (CBL, COB) - Analysis only, no migration here
CODE_ANALYSIS_PROMPT = """
Act as an Expert Code Analyzer and a Detailed Programming Logic Explicador.
Your primary goal is to thoroughly analyze the code I will provide you. I need you to explain to me its general functionality, its underlying
programming logic, and how each aspect of the code (variables, functions, control structures, etc.) contributes to the final result.
Present your analysis as a detailed, step-by-step explanation of the code's execution flow or logic. For each step:
Concísely describe what happens in that phase or segment of the code.
Identify and explain the specific code parts involved (e.g., "Here the calculate_average function is called" or "At this point, the counter
variable is incremented").
Detail how that particular aspect or those lines of code work in this step.
Explain why that step is necessary in the context of the overall program flow.
Ensure that each point is explained with enough detail so that someone studying the code can understand it completely.
Use clear and precise language.
"""

# Template for code transcription prompt (CBL, COB to target language)
# This prompt ONLY asks for the migrated code, no additional analysis.
CODE_TRANSCRIPTION_PROMPT_TEMPLATE = """
Migrate the provided COBOL code to {target_language}.
Provide only the migrated code in the target language, without any additional explanations or formatting markdown like ```.
"""


app = FastAPI()

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Helper function to handle API response
def handle_gemini_response(response, task_type, file_name):
    """Processes the Gemini API response for a specific task and file."""
    task_description = f"{task_type} for File: {file_name}"

    response_text = f"Could not get text content for {task_description}."
    status = f"Error: {task_type} - API/Handling Failed"

    if response is not None:
        if response.text:
            response_text = response.text
            status = f"OK {task_type} for {file_name}"
            print(f"    API Response OK for '{task_description}'.")
        elif response.candidates:
            candidate_texts = []
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, 'text'):
                            candidate_texts.append(part.text)
            if candidate_texts:
                response_text = " ".join(candidate_texts)
                status = f"OK (candidates) {task_type} for {file_name}"
                print(f"    API Response OK (candidates) for '{task_description}'.")
            else:
                status = f"Error: {task_type} - Candidates without text for {file_name}"
                response_text = f"Gemini returned candidates but no text content for {task_description}."
                print(status, file=sys.stderr)
                print("    Full response (candidates without text):", response, file=sys.stderr)

        elif response.prompt_feedback:
            block_reason = response.prompt_feedback.block_reason
            safety_ratings = response.prompt_feedback.safety_ratings
            response_text = f"Prompt or response for {task_description} was blocked for safety. Reason: {block_reason}"
            if safety_ratings:
                response_text += " | Ratings: " + ", ".join([f"{r.category}: {r.probability}" for r in safety_ratings])
            status = f"Blocked: {task_type} for {file_name}"
            print(status, file=sys.stderr)
            print("    Full prompt feedback:", response.prompt_feedback, file=sys.stderr)

        else:
            response_text = f"API returned unexpected response for {task_description}."
            status = f"Error: {task_type} - Unexpected Response for {file_name}"
            print(status, file=sys.stderr)
            print("    Unexpected API response:", response, file=sys.stderr)

    else:
        response_text = f"API call did not return a response object (null response) for {task_description}."
        status = f"Error: {task_type} - Null Call for {file_name}"
        print(status, file=sys.stderr)
        print("    Null API call.", file=sys.stderr)

    return status, response_text


# Synchronous ZIP Processing Logic (will run in a separate thread)
# This function DOES NOT use 'await'. API calls within it will be handled
# synchronously in the thread provided by anyio.to_thread.run_sync.
def process_zip_files_sync(zip_data: bytes, max_tokens_analysis: int, max_tokens_transcription: int, target_language: str = None):
    """
    Processes binary ZIP data synchronously, analyzes/transcribes
    supported files with Gemini API, and collects results.
    Returns two lists: analysis_results and transcription_results.
    """
    print("--> Entered process_zip_files_sync function")

    analysis_results = []
    transcription_results = []
    processed_file_count = 0

    # Define processable extensions (PDF, TXT, CBL, COB) within the synchronous function
    document_extensions = ('.pdf', '.txt')
    code_extensions = ('.cbl', '.cob')
    processable_extensions = document_extensions + code_extensions

    # Validate target language if provided
    valid_target_languages = ['Java', 'CSharp', 'Python', 'JavaScript', 'C++', 'Ruby', 'PHP', 'Go', 'Swift', 'Kotlin']
    request_transcription_for_cobol = False

    if target_language and target_language in valid_target_languages:
        request_transcription_for_cobol = True
        print(f"  Transcription requested for COBOL to: {target_language} (in sync thread)")
    elif target_language:
        print(f"  Invalid target language received: '{target_language}'. COBOL will be summarized only. (in sync thread)", file=sys.stderr)
        target_language = None
    else:
        print("  No target language selected for COBOL transcription. (in sync thread)")


    try: # Main Try/Except for general errors when opening/reading ZIP
        with zipfile.ZipFile(io.BytesIO(zip_data), 'r') as zip_archive:
            print("--> Zip file opened successfully in sync thread")
            print("Processing files within the ZIP (Synchronous Thread)...")

            for file_info in zip_archive.infolist():
                if file_info.is_dir():
                    continue

                file_name = file_info.filename
                file_extension = '.' + file_name.lower().split('.')[-1] if '.' in file_name else ''

                print(f"\n  --> Processing file {file_name} in sync thread loop")


                if file_extension in processable_extensions:
                    processed_file_count += 1

                    current_file_data = None
                    current_file_text = None
                    mime_type = None
                    is_code_file = file_extension in code_extensions

                    analysis_status = "Error: Initial processing failed (sync)"
                    analysis_text = f"Could not process file for analysis: {file_name}"
                    transcription_status = "Omitted: Not applicable" if not is_code_file else "Omitted: Not requested or Prep failed (sync)"
                    transcribed_code = "Transcription not applicable for this file type." if not is_code_file else f"Transcription not performed due to read/prep error for {file_name} (sync)."


                    try: # Try/Except for read/decode errors within the sync thread
                        if file_extension == '.pdf':
                            print(f"  Processing PDF file: {file_name} (sync)")
                            current_file_data = zip_archive.read(file_info)
                            mime_type = 'application/pdf'

                        elif file_extension in document_extensions or file_extension in code_extensions:
                            print(f"  Processing text file ({file_extension.strip('.')}) : {file_name} (sync)")
                            txt_bytes = zip_archive.read(file_info)
                            try:
                                current_file_text = txt_bytes.decode('utf-8')
                            except UnicodeDecodeError:
                                try:
                                    current_file_text = txt_bytes.decode('latin-1')
                                    print(f"    Decoded '{file_name}' using latin-1 (sync).", file=sys.stderr)
                                except Exception as e:
                                    error_msg = f"Error decoding text file '{file_name}': {e} (sync)"
                                    print(f"--- {error_msg} ---", file=sys.stderr)
                                    analysis_results.append({'filename': file_name, 'status': f"Decoding Error {file_extension.upper().strip('.')}", 'text': error_msg})
                                    if is_code_file and request_transcription_for_cobol:
                                        transcription_results.append({'filename': file_name, 'status': "Omitted: Read/prep error (sync)", 'text': f"Transcription for '{file_name}' omitted due to read/prep error (sync)."})
                                    continue

                            mime_type = 'text/plain'


                        # Determine BASE Prompts and prepare Content Parts for API (sync)
                        analysis_prompt_base = None
                        transcription_prompt_base = None
                        api_content_parts_summary = []
                        api_content_parts_transcription = []

                        if file_extension in document_extensions:
                            analysis_prompt_base = DOCUMENT_ANALYSIS_PROMPT
                        elif file_extension in code_extensions:
                            analysis_prompt_base = CODE_ANALYSIS_PROMPT
                            if request_transcription_for_cobol and target_language:
                                transcription_prompt_base = CODE_TRANSCRIPTION_PROMPT_TEMPLATE


                        if analysis_prompt_base:
                            api_content_parts_summary.append(types.Part.from_text(text=analysis_prompt_base))
                        else:
                            analysis_status = "Error: Analysis base prompt not determined (sync)"
                            analysis_text = "Analysis base prompt could not be determined for this file type. (sync)"
                            analysis_results.append({'filename': file_name, 'status': analysis_status, 'text': analysis_text})
                            if is_code_file and request_transcription_for_cobol:
                                transcription_results.append({'filename': file_name, 'status': "Omitted: Previous analysis error (sync)", 'text': f"Transcription for '{file_name}' omitted because initial analysis failed. (sync)"})
                            continue


                        file_content_part = None
                        if current_file_data is not None:
                            file_content_part = types.Part.from_bytes(data=current_file_data, mime_type=mime_type)
                        elif current_file_text is not None and current_file_text:
                            file_content_part = types.Part.from_text(text=current_file_text)
                        else:
                            analysis_status = "Error: API content not prepared (sync)"
                            analysis_text = "File content could not be prepared for the API call. (sync)"
                            analysis_results.append({'filename': file_name, 'status': analysis_status, 'text': analysis_text})
                            if is_code_file and request_transcription_for_cobol:
                                transcription_results.append({'filename': file_name, 'status': "Omitted: Content prep error (sync)", 'text': f"Transcription for '{file_name}' omitted because content could not be prepared for the API. (sync)"})
                            continue

                        api_content_parts_summary.append(file_content_part)

                        if is_code_file and request_transcription_for_cobol and transcription_prompt_base and file_content_part:
                            if target_language:
                                transcription_prompt_text = transcription_prompt_base.format(target_language=target_language)
                                api_content_parts_transcription.append(types.Part.from_text(text=transcription_prompt_text))
                                api_content_parts_transcription.append(file_content_part)
                            else:
                                transcription_status = "Omitted: Null target language (sync)"
                                transcribed_code = "Transcription requested but target language is null. (sync)"
                                transcription_results.append({'filename': file_name, 'status': transcription_status, 'text': transcribed_code})


                        # Generation configuration (synchronous)
                        # Keep config objects - if TypeError with config persists, remove them below
                        generation_config_object_analysis = types.GenerateContentConfig(max_output_tokens=max_tokens_analysis)
                        generation_config_object_transcription = types.GenerateContentConfig(max_output_tokens=max_tokens_transcription)


                        # Perform API calls (synchronous in this thread)
                        try: # Try/Except for API calls and handling
                            print(f"    --> Attempting API call for Analysis/Summary for {file_name} (sync)")

                            # >>> API CALL for ANALYSIS/SUMMARY (NO AWAIT) <<<
                            if api_content_parts_summary:
                                analysis_task_desc = f"Document Analysis for File: {file_name}" if file_extension in document_extensions else f"Code Analysis for File: {file_name}"
                                print(f"    Sending '{analysis_task_desc}' of '{file_name}' to Gemini API ({MODEL_NAME})... (NO AWAIT in sync thread)")

                                summary_response = client.models.generate_content( # !!! NOTE: NO 'await' here !!!
                                    model=MODEL_NAME,
                                    contents=api_content_parts_summary,
                                    config=generation_config_object_analysis # Keep config here to attempt token control
                                )
                                analysis_status, analysis_text = handle_gemini_response(summary_response, "Analysis", file_name)
                                print(f"    Analysis result for '{file_name}': {analysis_status} (sync)")

                            else:
                                analysis_status = "Error: Analysis API parts not prepared (sync)"
                                analysis_text = "Parts for analysis API call could not be prepared. (sync)"


                            analysis_results.append({
                                'filename': file_name,
                                'status': analysis_status,
                                'text': analysis_text
                            })

                            print(f"    --> Finished API call for Analysis/Summary for {file_name} (sync)")


                            # >>> API CALL for TRANSCRIPTION (if applicable and analysis was OK) <<<
                            if is_code_file and request_transcription_for_cobol and analysis_status.startswith("OK") and api_content_parts_transcription:
                                transcription_task_desc = f"Transcription to {target_language} for File: {file_name}"
                                print(f"    --> Attempting API call for Transcription for {file_name} (sync)")
                                print(f"    Sending '{transcription_task_desc}' of '{file_name}' to Gemini API ({MODEL_NAME})... (NO AWAIT in sync thread)")

                                transcription_response = client.models.generate_content( # !!! NOTE: NO 'await' here !!!
                                    model=MODEL_NAME,
                                    contents=api_content_parts_transcription,
                                    config=generation_config_object_transcription # Keep config here to attempt token control
                                )
                                transcription_status, transcribed_code = handle_gemini_response(transcription_response, "Transcription", file_name)

                                if transcription_status.startswith("OK"):
                                    match = re.search(r'```(?:[a-zA-Z0-9_+#-]+)?\n(.*?)\n```', transcribed_code, re.DOTALL)
                                    transcribed_text = match.group(1).strip() if match else transcribed_code.strip()
                                else:
                                    transcribed_text = transcribed_code

                                transcription_results.append({
                                    'filename': file_name,
                                    'status': transcription_status,
                                    'text': transcribed_text
                                })

                                print(f"    --> Finished API call for Transcription for {file_name} (sync)")


                            elif is_code_file and not request_transcription_for_cobol:
                                transcription_results.append({
                                    'filename': file_name,
                                    'status': "Omitted: Not requested (sync)",
                                    'text': f"Transcription for '{file_name}' was omitted because no target language was selected. (sync)"
                                })


                        except Exception as e: # Catch EXCEPTIONS during API calls or handling (sync)
                            error_text = f"Exception during API calls or response handling: {e} (sync)"
                            print(f"--- {error_text} for '{file_name}' ---", file=sys.stderr)
                            traceback.print_exc(file=sys.stderr)

                            analysis_entry = next((item for item in analysis_results if item['filename'] == file_name), None)
                            if analysis_entry and not analysis_entry['status'].startswith("OK"):
                                analysis_entry['status'] = "Exception API/Handling Analysis (sync)"
                                analysis_entry['text'] = error_text
                            elif not analysis_entry:
                                analysis_results.append({'filename': file_name, 'status': "Exception API/Handling Analysis (sync)", 'text': error_text})


                            if is_code_file and request_transcription_for_cobol:
                                transcription_entry = next((item for item in transcription_results if item['filename'] == file_name), None)
                                if transcription_entry and (transcription_entry['status'].startswith('Omitted') or transcription_entry['status'].startswith('Error') or transcription_entry['status'] == "Transcription Status Pending (sync)"):
                                    transcription_entry['status'] = "Exception API/Handling Transcription (sync)"
                                    transcription_entry['text'] = error_text
                                elif not transcription_entry:
                                    transcription_results.append({'filename': file_name, 'status': "Exception API/Handling Transcription (sync)", 'text': error_text})
                            elif is_code_file and not request_transcription_for_cobol:
                                existing_entry = next((item for item in transcription_results if item['filename'] == file_name), None)
                                if not existing_entry:
                                    transcription_results.append({'filename': file_name, 'status': "Omitted: Not requested (sync)", 'text': f"Transcription for '{file_name}' was omitted because no target language was selected (and a general exception occurred)."})


                    except Exception as e: # Catch unexpected errors during read/decode/prep (sync)
                        error_text = f"Unexpected error during early read/prep for this file: {e} (sync)"
                        print(f"--- {error_text} for '{file_name}' ---", file=sys.stderr)
                        traceback.print_exc(file=sys.stderr)
                        analysis_results.append({'filename': file_name, 'status': "Read/prep error (sync)", 'text': error_text})
                        if is_code_file and request_transcription_for_cobol:
                            transcription_results.append({'filename': file_name, 'status': "Omitted: Read/prep error (sync)", 'text': f"Transcription for '{file_name}' was omitted due to read/preparation error. (sync)"})
                        elif is_code_file and not request_transcription_for_cobol:
                            existing_entry = next((item for item in transcription_results if item['filename'] == file_name), None)
                            if not existing_entry:
                                transcription_results.append({'filename': file_name, 'status': "Omitted: Not requested (sync)", 'text': f"Transcription for '{file_name}' was omitted because no target language was selected (and an early error occurred)."})


                else:
                    print(f"  Ignoring file with unsupported extension: {file_name} (sync)")


    except zipfile.BadZipFile:
        error_msg = "Error: Uploaded file is not a valid ZIP archive. (sync)"
        print(error_msg, file=sys.stderr)
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"An general error occurred while processing the ZIP file: {e} (sync)"
        print(error_msg, file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise Exception(error_msg)


    print("Processing files completed in synchronous thread. Returning results.")
    return analysis_results, transcription_results, processed_file_count



# FastAPI POST route to receive the file
@app.post("/upload")
async def upload_archive_endpoint(
    archive_file: UploadFile = File(...),
    target_language: str = Form(None)
):
    print("--> Request received in /upload async route")
    print("POST request received at /upload (FastAPI async)") # Original log kept for structure

    print(f"  Uploaded file: {archive_file.filename}")
    print(f"  Target language received from form: {target_language}")

    if not archive_file.filename or not archive_file.filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail="Only .zip files are allowed")

    try:
        await archive_file.seek(0)
        zip_data = await archive_file.read()
        print(f"File '{archive_file.filename}' ({len(zip_data)} bytes) read successfully (FastAPI async).")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading the content of the ZIP file: {e}")

    # Execute the SYNCHRONOUS processing logic in a separate thread
    try:
        fixed_max_tokens_analysis = 400
        fixed_max_tokens_transcription = 800
        print("--> Starting process_zip_files_sync in separate thread")
        analysis_results, transcription_results, processed_file_count = await anyio.to_thread.run_sync(
            process_zip_files_sync,
            zip_data,
            fixed_max_tokens_analysis,
            fixed_max_tokens_transcription,
            target_language
        )
        print(f"--> Finished process_zip_files_sync. Results received: Analysis={len(analysis_results)}, Transcription={len(transcription_results)}.")

    except Exception as e: # Catch exceptions propagated from process_zip_files_sync
        print(f"--- Exception propagated to async FastAPI route from sync thread: {e} ---", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        if "ZIP valid" in str(e) or "ZIP válido" in str(e): # Check for messages from BadZipFile
            raise HTTPException(status_code=400, detail=str(e))
        else:
            raise HTTPException(status_code=500, detail=f"Error during ZIP file processing in separate thread: {e}")


    # Generate the general ANALYSIS PDF with collected results
    print("--> Starting general Analysis PDF generation...")
    analysis_pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(analysis_pdf_buffer, pagesize=letter)
    story = []

    styles = getSampleStyleSheet()
    style_title = styles['h1']
    style_filename = styles['h3']
    style_body = styles['Normal']
    style_error = styles['Normal']
    style_error.textColor = (1, 0, 0)

    story.append(Paragraph(f"Analysis of ZIP File: {archive_file.filename}", style_title))
    story.append(Spacer(1, 0.2*letter[1]))

    if processed_file_count == 0:
        story.append(Paragraph(f"No files with supported extensions ({', '.join(processable_extensions)}) found within the ZIP.", style_body))
    elif not analysis_results and processed_file_count > 0:
        story.append(Paragraph("Files with supported extensions were found, but no results could be obtained for ANALYSIS (possible early errors).", style_error))
    elif analysis_results:
        for result in analysis_results:
            story.append(Paragraph(f"File: {result['filename']} ({result['status']})", style_filename))
            story.append(Spacer(1, 6))
            text_style = style_body
            if result['status'].startswith("Error") or result['status'].startswith("Blocked") or result['status'].startswith("Exception API/Handling"):
                text_style = style_error
            story.append(Paragraph(str(result['text']), text_style))
            story.append(Spacer(1, 18))

    analysis_pdf_bytes = None
    try:
        doc.build(story)
        analysis_pdf_bytes = analysis_pdf_buffer.getvalue()
        analysis_pdf_buffer.close()
        print("--> General Analysis PDF generated.")
    except Exception as e:
        print(f"--- Error generating general Analysis PDF: {e} ---", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)


    # Generate individual TRANSCRIPTION PDFs for code files
    print(f"--> Starting individual Transcription PDF generation for {len(transcription_results)} results...")
    transcription_pdf_files = []

    if transcription_results:
        for result in transcription_results:
            transcription_individual_buffer = io.BytesIO()
            doc_individual = SimpleDocTemplate(transcription_individual_buffer, pagesize=letter)
            story_individual = []

            styles_individual = getSampleStyleSheet()
            style_title_individual = styles_individual['h3']
            style_body_individual = styles_individual['Normal']
            style_code_individual = styles_individual['Normal']
            style_code_individual.fontName = 'Courier'
            style_code_individual.fontSize = 9
            style_code_individual.leading = 10
            style_error_individual = styles_individual['Normal']
            style_error_individual.textColor = (1, 0, 0)

            # Determine target language for the individual PDF title
            pdf_target_language = target_language if target_language else "N/A"


            story_individual.append(Paragraph(f"Transcription to {pdf_target_language} for: {result['filename']} ({result['status']})", style_title_individual))
            story_individual.append(Spacer(1, 12))

            text_style_individual = style_body_individual
            if not result['status'].startswith("OK"):
                text_style_individual = style_error_individual

            if result['status'].startswith("OK") and result['text']:
                story_individual.append(Preformatted(str(result['text']), style_code_individual))
            else:
                story_individual.append(Paragraph(str(result['text']), text_style_individual))


            try:
                doc_individual.build(story_individual)
                transcription_pdf_bytes = transcription_individual_buffer.getvalue()
                transcription_individual_buffer.close()

                transcription_pdf_filename = f"transcription_{result['filename'].replace('.', '_')}_to_{pdf_target_language}.pdf"
                if result['status'].startswith("Omitted"):
                    transcription_pdf_filename = f"transcription_{result['filename'].replace('.', '_')}_{result['status'].replace(' ', '_').replace(':', '')}.pdf"

                transcription_pdf_files.append({
                    'filename': transcription_pdf_filename,
                    'bytes': transcription_pdf_bytes
                })
                print(f"--> Transcription PDF for '{result['filename']}' generated successfully and added to list.")
            except Exception as e:
                print(f"--- Error generating Transcription PDF for '{result['filename']}': {e} ---", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                existing_error_entry = next((item for item in analysis_results if item['filename'] == result['filename'] and 'Transcription' in item.get('status', '')), None)
                if not existing_error_entry:
                    analysis_results.append({
                        'filename': result['filename'],
                        'status': f"Error generating Transcription PDF for {result['filename']}",
                        'text': f"An error occurred while generating the transcription PDF file: {e}"
                    })


    print(f"--> Finished individual Transcription PDF generation. {len(transcription_pdf_files)} PDFs ready to zip.")


    # Create the final ZIP file with all PDFs
    print("--> Starting final ZIP file creation...")
    final_zip_buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(final_zip_buffer, 'w', zipfile.ZIP_DEFLATED) as final_zip:
            if analysis_pdf_bytes is not None:
                analysis_pdf_filename = f"analysis_{archive_file.filename.replace('.zip', '')}.pdf"
                final_zip.writestr(analysis_pdf_filename, analysis_pdf_bytes)
                print(f"  Added '{analysis_pdf_filename}' to ZIP.")
            else:
                print("  Error: General Analysis PDF not generated or null, not adding to ZIP.", file=sys.stderr)

            if transcription_pdf_files:
                for pdf_file in transcription_pdf_files:
                    final_zip.writestr(pdf_file['filename'], pdf_file['bytes'])
                    print(f"  Added '{pdf_file['filename']}' to ZIP.")
            else:
                print("  No transcription PDFs to add to ZIP.", file=sys.stderr)

        final_zip_bytes = final_zip_buffer.getvalue()
        final_zip_buffer.close()

        print(f"--> Final ZIP file generated ({len(final_zip_bytes)} bytes).")

        print("--> Returning HTTP response with the ZIP file.")
        return Response(content=final_zip_bytes, media_type='application/zip', headers={
            'Content-Disposition': f'attachment; filename="analysis_results_{archive_file.filename}"',
            'Content-Length': str(len(final_zip_bytes))
        })

    except Exception as e:
        print(f"--- Error creating final ZIP file: {e} ---", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return JSONResponse(status_code=500, content={"error": f"Error creating the final ZIP file: {e}"})


# To run the FastAPI application with uvicorn
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)