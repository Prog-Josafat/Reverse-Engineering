from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, Response # <<< Añade Response aquí # Importar tipos de respuesta
from fastapi.middleware.cors import CORSMiddleware
import io
import zipfile
import codecs # Para decodificar texto
import sys # Para logs de error
import traceback
import re


# --- Importaciones de ReportLab ---
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import letter
# --- Fin Importaciones de ReportLab ---

# --- Importaciones para manejar código síncrono en ruta asíncrona ---
import anyio # Usado por FastAPI para run_sync
# --- Fin Importaciones ---


# --- Configuración de la API de Gemini (Mantener tu clave real) ---
from google import genai
from google.genai import types
from google.genai import Client


# --- Configuración de la API de Gemini ---
# Es una buena práctica no poner la clave directamente aquí.
# Considera usar variables de entorno.
API_KEY = "AIzaSyCOgj1wrOhehSxLBfPYi6WUNpyqp7jPv6o"
genai.Client(api_key=API_KEY)

client = Client(api_key=API_KEY)

# Modelo a usar
MODEL_NAME = "gemini-2.0-flash-lite" # Modelo recomendado para procesar documentos
# Puedes probar con "gemini-1.5-pro" si necesitas más capacidad,
# o "gemini-2.0-flash-lite" si estás seguro de que soporta PDFs de ese tamaño.
# Asegúrate de que tu clave de API tenga acceso al modelo seleccionado.

# --- Prompts para la API de Gemini ---
# Prompt para analizar archivos de código
CODE_ANALYSIS_PROMPT = """
Act as an Expert Code Analyzer and a Detailed Programming Logic Explainer.
Your primary goal is to thoroughly analyze the code I will provide you. I need you to explain to me its general functionality, its underlying
programming logic, and how each aspect of the code (variables, functions, control structures, etc.) contributes to the final result.
Present your analysis as a detailed, step-by-step explanation of the code's execution flow or logic. For each step:
Concísely describe what happens in that phase or segment of the code.
Identify and explain the specific code parts involved (e.g., "Here the calculate_average function is called" or "At this point, the counter
variable is incremented").
Detail how that particular aspect or those lines of code work in this step.
Explain why that step is necessary in the context of the overall program flow.
Ensure that each point is explained with enough detail so that someone studying the code can understand it completely.
Use clear and precise language. I am ready for you to analyze the code.
"""
# Prompt para analizar archivos de documento (PDF, TXT)
DOCUMENT_ANALYSIS_PROMPT = """
Act as an expert content analyst and a detailed explainer. Your task is to carefully examine the content of a file I will provide you, analyze
what is happening in it (its logic, functionality, flow, etc.), and then explain it to me in a clear and understandable way.
I want the explanation to be divided into a step-by-step sequence. For each step, provide a detailed description of what happens, why it happens,
and any relevant details that will help me understand it thoroughly. The goal is for me to be able to understand each point individually and the
overall process of the file.
Please be ready to receive the content. When you have it, proceed with the analysis and the detailed step-by-step explanation.
"""
# Template para el prompt de transcripción de código (CBL, COB a lenguaje de destino)
# Este prompt SÓLO pide el código migrado, sin análisis adicional.
CODE_TRANSCRIPTION_PROMPT_TEMPLATE = """
Migrate the provided COBOL code to {target_language}.
Provide only the migrated code in the target language, without any additional explanations or formatting markdown like ```.
"""
# --- Fin Prompts ---

try:
    client = Client(api_key=API_KEY)
except Exception as e:
    print(f"Error al inicializar el cliente Gemini: {e}", file=sys.stderr)
    # Dependiendo de cómo manejes errores críticos al inicio, podrías salir o registrar el error


# --- Configuración de FastAPI ---
app = FastAPI()

# Configurar CORS
# Esto permite que tu frontend de React se comunique con tu backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # >>> CAMBIA ESTO EN PRODUCCIÓN por la URL de tu frontend React (ej. "https://tu-app-react.vercel.app")
    allow_credentials=True,
    allow_methods=["*"], # Permite todos los métodos (POST, GET, etc.)
    allow_headers=["*"], # Permite todas las cabeceras
)

# --- Función auxiliar para manejar la respuesta de la API ---
# Esta función simplifica el procesamiento del objeto 'response' de Gemini
# Le añadimos un parámetro 'task_type' para diferenciar en los mensajes
def handle_gemini_response(response, task_type, file_name):
    """Procesa la respuesta de la API de Gemini para una tarea y archivo específico."""
    task_description = f"{task_type} para Archivo: {file_name}" # Descripción para logs y resultados

    # Inicializa con estado y texto de error por defecto
    response_text = f"No se pudo obtener contenido textual para {task_description}." # Cambiamos nombre a response_text
    status = f"Error: {task_type} - Fallo API/Manejo" # Estado más genérico para fallos

    if response is not None:
        if response.text:
            response_text = response.text
            # Simplifica el estado - ej: "OK Análisis Doc para archivo.pdf"
            status = f"OK {task_type} para {file_name}"
            print(f"    API Response OK para '{task_description}'.")
        elif response.candidates:
            candidate_texts = []
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, 'text'):
                            candidate_texts.append(part.text)
            if candidate_texts:
                response_text = " ".join(candidate_texts)
                status = f"OK (candidatos) {task_type} para {file_name}"
                print(f"    API Response OK (candidatos) para '{task_description}'.")
            else:
                status = f"Error: {task_type} - Candidatos sin texto para {file_name}"
                response_text = f"Gemini devolvió candidatos pero sin contenido textual para {task_description}."
                print(status, file=sys.stderr)
                print("    Respuesta completa (candidatos sin texto):", response, file=sys.stderr)

        elif response.prompt_feedback:
            block_reason = response.prompt_feedback.block_reason
            safety_ratings = response.prompt_feedback.safety_ratings
            response_text = f"El prompt o la respuesta para {task_description} fueron bloqueados por seguridad. Razón: {block_reason}"
            if safety_ratings:
                response_text += " | Calificaciones: " + ", ".join([f"{r.category}: {r.probability}" for r in safety_ratings])
            status = f"Bloqueado: {task_type} para {file_name}"
            print(status, file=sys.stderr)
            print("    Feedback completo del prompt:", response.prompt_feedback, file=sys.stderr)

        else: # Respuesta inesperada de la API
            response_text = f"La API devolvió una respuesta inesperada para {task_description}."
            status = f"Error: {task_type} - Respuesta inesperada para {file_name}"
            print(status, file=sys.stderr)
            print("    Respuesta API inesperada:", response, file=sys.stderr)

    else: # response is None (la llamada API no retornó objeto)
        response_text = f"La llamada a API no devolvió un objeto de respuesta (respuesta nula) para {task_description}."
        status = f"Error: {task_type} - Llamada nula para {file_name}"
        print(status, file=sys.stderr)
        print("    Llamada a API nula.", file=sys.stderr)

    return status, response_text



# --- Lógica de Procesamiento Síncrono del ZIP ---
# Esta función contiene la mayor parte de la lógica que estaba en process_archive de Flask,
# pero adaptada para no depender de objetos de solicitud/respuesta de Flask.
# Se ejecutará en un hilo separado usando run_sync.
def process_zip_sync(zip_data: bytes, max_tokens: int, target_language: str = None) -> tuple[list[dict], tuple]: # <<< El tipo de retorno cambia a una tupla (lista de dicts, tupla de extensiones)
    """
    Procesa los datos binarios de un archivo ZIP, analiza/transcribe archivos soportados
    con Gemini API. Recopila los resultados estructurados.
    Retorna una tupla: (lista de resultados estructurados, tupla de extensiones procesables).
    No genera el PDF final aquí.
    """
    results = [] # Lista para almacenar los resultados estructurados para cada archivo
    processed_file_count = 0 # Contador de archivos que intentamos procesar
    
    # ===> DEFINIR LAS EXTENSIONES PROCESABLES DENTRO DE LA FUNCIÓN <===
    document_extensions = ('.pdf', '.txt')
    code_extensions = ('.cbl', '.cob')
    processable_extensions = document_extensions + code_extensions
    # ===> FIN DEFINICIÓN <===

    # Validar el lenguaje de destino si se proporcionó
    valid_target_languages = ['', 'Java', 'CSharp', 'Python', 'JavaScript', 'C++', 'Ruby', 'PHP', 'Go', 'Swift', 'Kotlin'] # Incluye el valor vacío/None
    request_transcription_for_cobol = False # Inicialmente no se pide transcripción

    if target_language and target_language in valid_target_languages:
        request_transcription_for_cobol = True
        print(f"Transcripción solicitada para COBOL a: {target_language}")
    elif target_language: # Si proporcionó algo pero no es válido
        print(f"Lenguaje de destino inválido recibido: {target_language}. Se procesará COBOL solo para resumen.", file=sys.stderr)
        target_language = None # Tratar como si no se hubiera seleccionado ninguno


    try: # Try/Except principal para errores generales al abrir/leer ZIP
        with zipfile.ZipFile(io.BytesIO(zip_data), 'r') as zip_archive:
            print("Procesando archivos dentro del ZIP (Sync)...")


            try: # TRY...EXCEPT ALREDEDOR DEL BUCLE FOR
                # Itera sobre cada archivo dentro del ZIP
                for file_info in zip_archive.infolist():
                    if file_info.is_dir():
                        continue

                    file_name = file_info.filename
                    file_extension = '.' + file_name.lower().split('.')[-1] if '.' in file_name else ''

                    print(f"\n  Considerando archivo: {file_name}") # Log para cada archivo considerado


                    # --- Inicializar una entrada de resultado para este archivo ---
                    # Esta entrada se llenará con los resultados (resumen, transcripción)
                    file_result = {
                        'filename': file_name,
                        'original_extension': file_extension,
                        'processed': False, # Flag para saber si intentamos procesarlo
                        'summary_status': 'No procesado',
                        'summary_text': '',
                        'transcription_status': 'No solicitada', # Estado específico para transcripción
                        'transcription_text': '',
                        'target_language': target_language if request_transcription_for_cobol else None # Registrar el lenguaje si se pidió transcripción
                    }


                    if file_extension in processable_extensions:
                        processed_file_count += 1
                        file_result['processed'] = True # Marcamos como intentado procesar

                        current_file_data = None # Bytes del archivo (para PDF)
                        current_file_text = None # Texto del archivo (para TXT, CBL, COB)
                        mime_type = None

                        is_code_file = file_extension in code_extensions # Verificar si es código (CBL, COB)


                        # --- Lógica para leer el contenido según el tipo de archivo ---
                        try: # Try/Except para errores de lectura/decodificación
                            if file_extension == '.pdf':
                                print(f"  Procesando archivo PDF: {file_name}")
                                current_file_data = zip_archive.read(file_info)
                                mime_type = 'application/pdf'

                            elif file_extension in document_extensions or file_extension in code_extensions: # TXT, CBL, COB
                                print(f"  Procesando archivo de texto ({file_extension.strip('.')}) : {file_name}")
                                txt_bytes = zip_archive.read(file_info)
                                try:
                                    current_file_text = txt_bytes.decode('utf-8')
                                except UnicodeDecodeError:
                                    try:
                                        current_file_text = txt_bytes.decode('latin-1')
                                        print(f"    Decodificado '{file_name}' usando latin-1 (Sync).", file=sys.stderr)
                                    except Exception as e:
                                        error_msg = f"Error al decodificar archivo de texto '{file_name}': {e}"
                                        print(f"    {error_msg} (Sync)", file=sys.stderr)
                                        # Registrar el error de decodificación en la entrada del resultado de este archivo
                                        file_result['summary_status'] = f"Error decodificación {file_extension.upper().strip('.')}" # Estado para el resumen (falló antes)
                                        file_result['summary_text'] = error_msg
                                        if is_code_file and request_transcription_for_cobol:
                                            file_result['transcription_status'] = f"Error decodificación {file_extension.upper().strip('.')}" # También para transcripción
                                            file_result['transcription_text'] = error_msg

                                        results.append(file_result) # Añadir el resultado con el error
                                        continue # <<< CONTINUA al siguiente archivo si la decodificación falla

                                mime_type = 'text/plain'

                            # elif file_extension == '.docx': # Sigue sin implementarse
                            #    pass


                            # --- Determinar Tareas y Prompts BASE (antes de añadir contenido) ---
                            analysis_prompt_base = None # Esto contendrá el prompt template (DOCUMENT_ANALYSIS_PROMPT o CODE_ANALYSIS_PROMPT)
                            transcription_prompt_base = None # Esto contendrá el template de transcripción

                            if file_extension in document_extensions: # PDF, TXT
                                task = "Summarize"
                                analysis_prompt_base = DOCUMENT_ANALYSIS_PROMPT # ===> Usamos el prompt base para documentos
                                transcription_prompt_base = None # No transcription for docs
                            elif file_extension in code_extensions: # CBL, COB
                                if request_transcription_for_cobol:
                                    task = "Summarize & Transcribe"
                                    transcription_prompt_base = CODE_TRANSCRIPTION_PROMPT_TEMPLATE # The template needs formatting later
                                else:
                                    task = "Summarize" # Solo resumen para COBOL si no se pidió transcripción
                                    transcription_prompt_base = None # No transcription requested
                                analysis_prompt_base = CODE_ANALYSIS_PROMPT # ===> Usamos el prompt base para código para el análisis

                            # --- Si llegamos aquí (lectura y decodificación exitosa o PDF) ---

                            # ===> Prepare API Content Parts (including prompt text as a part) <===
                            api_content_parts_summary = [] # Lista de partes para la llamada de Análisis/Resumen
                            api_content_parts_transcription = [] # Lista de partes para la llamada de Transcripción

                            # Añadir el prompt de análisis como parte de texto
                            if analysis_prompt_base:
                                api_content_parts_summary.append(types.Part.from_text(text=analysis_prompt_base))
                            else: # Esto no debería pasar si la lógica es correcta, pero defensivamente
                                file_result['summary_status'] = "Error lógico de prompt"
                                file_result['summary_text'] = "El prompt de análisis no se pudo preparar lógicamente."
                                # No continuar con API calls si el prompt base falló
                                results.append(file_result)
                                continue # <<< CONTINUA al siguiente archivo

                            # Añadir el contenido del archivo (bytes o texto) como parte
                            if current_file_data is not None: # Es PDF
                                api_content_parts_summary.append(types.Part.from_bytes(data=current_file_data, mime_type=mime_type))
                                # No hay transcripción para PDFs
                            elif current_file_text is not None and current_file_text: # Es texto plano no vacío (TXT, CBL, COB)
                                api_content_parts_summary.append(types.Part.from_text(text=current_file_text))
                                if is_code_file and request_transcription_for_cobol and transcription_prompt_base:
                                    # Para la transcripción, también añadimos el prompt y el texto del código
                                    transcription_prompt_text = transcription_prompt_base.format(target_language=target_language)
                                    api_content_parts_transcription.append(types.Part.from_text(text=transcription_prompt_text))
                                    api_content_parts_transcription.append(types.Part.from_text(text=current_file_text))

                            else: # Si api_content_part sigue siendo None (ej. archivo de texto vacío)
                                # El error de archivo vacío ya fue registrado y continuado.
                                # Si llegamos aquí, hubo un problema inesperado en la preparación del contenido.
                                file_result['summary_status'] = "Error: Contenido API no preparado"
                                file_result['summary_text'] = "El contenido del archivo no se pudo preparar para la llamada a la API."
                                # No continuar con API calls
                                results.append(file_result)
                                continue # <<< CONTINUA al siguiente archivo


                            # --- Configuración de generación para las llamadas API ---
                            # CREAMOS LOS OBJETOS DE CONFIG AQUÍ
                            generation_config_object = types.GenerateContentConfig(
                                max_output_tokens=max_tokens # Usa el max_tokens proporcionado
                            )
                            transcription_config_object = types.GenerateContentConfig(
                                max_output_tokens=max_tokens * 3 # Ejemplo: dar más tokens para código
                                # max_output_tokens=2000 # O un valor fijo alto
                            )

                            # --- Realiza las llamadas a la API (una o dos) ---
                            try: # Try/Except para las llamadas API y su manejo
                                # >>> LLAMADA API para el ANÁLISIS/RESUMEN (si aplica) <<<
                                if analysis_prompt_base: # Si hay un prompt de análisis base (siempre para procesables)
                                    print(f"    Enviando contenido de '{file_name}' a Gemini API ({MODEL_NAME}) para ANÁLISIS/RESUMEN...")
                                    summary_response = client.models.generate_content(
                                        model=MODEL_NAME,
                                        contents=api_content_parts_summary, # Usamos la lista de partes preparada
                                        config=generation_config_object # Mantenemos el config que da error intermitente
                                    )
                                    # Manejo de la respuesta del resumen (usando la función auxiliar)
                                    file_result['summary_status'], file_result['summary_text'] = handle_gemini_response(summary_response, "Análisis", file_name)
                                    print(f"    Resultado análisis para '{file_name}': {file_result['summary_status']}")


                                # >>> LLAMADA API para la TRANSCRIPCIÓN (si aplica y si el análisis fue OK) <<<
                                # Solo intentamos transcripción si la tarea incluye Transcribe Y el análisis fue OK
                                if task == "Summarize & Transcribe" and file_result['summary_status'].startswith("OK") and api_content_parts_transcription:
                                    print(f"    Enviando contenido de '{file_name}' a Gemini API ({MODEL_NAME}) para TRANSCRIPCIÓN a {target_language}...")
                                    transcription_response = client.models.generate_content(
                                        model=MODEL_NAME,
                                        contents=api_content_parts_transcription, # Usamos la lista de partes preparada
                                        config=transcription_config_object # Usamos el config para transcripción
                                    )
                                    # Manejo de la respuesta de la transcripción (usando la función auxiliar)
                                    transcription_status, transcribed_code = handle_gemini_response(transcription_response, "Transcripción", file_name)

                                    # Opcional: Limpiar el código transcrito (extraer bloque) si el estado es OK
                                    if transcription_status.startswith("OK"):
                                        match = re.search(r'```(?:[a-zA-Z0-9_+#-]+)?\n(.*?)\n```', transcribed_code, re.DOTALL)
                                        file_result['transcription_text'] = match.group(1).strip() if match else transcribed_code.strip() # Usar texto limpio si se encuentra bloque
                                    else:
                                        file_result['transcription_text'] = transcribed_code # Mantener el mensaje de error/estado

                                    file_result['transcription_status'] = transcription_status # Actualizar el estado de transcripción

                                    print(f"    Resultado transcripción para '{file_name}': {file_result['transcription_status']}")


                                elif is_code_file and not request_transcription_for_cobol:
                                    # Si es COBOL pero NO se pidió transcripción
                                    file_result['transcription_status'] = "Omitida: No solicitada"
                                    file_result['transcription_text'] = f"La transcripción para '{file_name}' fue omitida porque no se seleccionó un lenguaje de destino."


                            except Exception as e:
                                # Captura EXCEPCIONES durante las llamadas API o su manejo para este archivo
                                error_text = f"Excepción durante llamadas API o manejo de respuesta: {e}"
                                print(f"--- {error_text} para '{file_name}' (Sync) ---", file=sys.stderr)
                                traceback.print_exc(file=sys.stderr)
                                # Registrar la excepción en la entrada del resultado de este archivo
                                # Podría ser una excepción de resumen O transcripción
                                if file_result.get('summary_status', '').startswith('No procesado') or file_result.get('summary_status', '').startswith('Error'): # Si el resumen no se llegó a procesar o falló
                                    file_result['summary_status'] = "Excepción API/Manejo Análisis"
                                    file_result['summary_text'] = error_text
                                # Si la transcripción no se llegó a procesar o falló (y se pidió)
                                if is_code_file and request_transcription_for_cobol and (file_result.get('transcription_status', '').startswith('No solicitada') or file_result.get('transcription_status', '').startswith('Error')):
                                    file_result['transcription_status'] = "Excepción API/Manejo Transcripción"
                                    file_result['transcription_text'] = error_text

                                # Si se llegó aquí, la entrada de resultado para este archivo ya está llena
                                # con los estados y textos (resumen, transcripción) o mensajes de error específicos.


                        except Exception as e:
                            # Captura errores durante lectura/decodificación/preparación que NO fueron manejados por los continues
                            error_text = f"Error inesperado durante lectura/preparación para este archivo: {e}"
                            print(f"--- {error_text} para '{file_name}' (Sync) ---", file=sys.stderr)
                            traceback.print_exc(file=sys.stderr)
                            # Registrar el error en la entrada del resultado de este archivo
                            file_result['summary_status'] = "Error lectura/prep"
                            file_result['summary_text'] = error_text
                            if is_code_file and request_transcription_for_cobol:
                                file_result['transcription_status'] = "Error lectura/prep"
                                file_result['transcription_text'] = error_text


                    # Después de procesar (o intentar procesar) un archivo, añadir su entrada a la lista global de resultados
                    # Asegurarse de que la entrada de resultado tiene el 'task' correcto
                    if file_result['processed']: # Solo añadimos si intentamos procesarlo
                        if is_code_file and request_transcription_for_cobol:
                            file_result['task'] = "Summarize & Transcribe"
                        elif is_code_file:
                            file_result['task'] = "Summarize" # Solo resumen para COBOL si no se pidió transcripción
                        elif file_extension in document_extensions: # PDF, TXT
                            file_result['task'] = "Summarize"
                        else: # Esto no debería pasar si file_extension in processable_extensions
                            file_result['task'] = "Desconocida"


                        results.append(file_result)


                # Si el bucle for se completa sin excepciones que lo detengan, llegamos aquí.
                # La lista 'results' ahora tiene una entrada para cada archivo procesado.
            except Exception as e: # >>> CATCH EXCEPCIONES FUERA DEL BUCLE FOR INTERNO <<<
                error_msg = f"Excepción que interrumpió el procesamiento del ZIP: {e}"
                print(f"--- {error_msg} (Sync) ---", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                # Levantar una excepción para que el endpoint de FastAPI la capture
                raise Exception(error_msg)


    except zipfile.BadZipFile: # Captura error si el archivo no es un ZIP válido
        error_msg = "Error: El archivo subido no es un archivo ZIP válido."
        print(error_msg + " (Sync)", file=sys.stderr)
        raise Exception(error_msg)
    except Exception as e: # Captura cualquier otro error general al leer/abrir el ZIP ANTES de entrar al with zipfile
        error_msg = f"Ocurrió un error general al procesar el archivo ZIP: {e}"
        print(error_msg + " (Sync)", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise Exception(error_msg)

    # --- Retornar la lista de resultados y las extensiones procesables ---
    # La generación de PDFs y el ZIP final se hará fuera de esta función síncrona.
    print("Procesamiento de archivos completado. Retornando resultados.")
    return results, processable_extensions


# --- Ruta POST de FastAPI para recibir el archivo ---
@app.post("/upload")
async def upload_archive_endpoint(
    archive_file: UploadFile = File(...),
    # Añadir el parámetro para el lenguaje de destino del formulario
    target_language: str = Form(None) # Recibe el lenguaje de destino del formulario
):
    print("Petición POST recibida en /upload (FastAPI)")
    print(f"  Archivo subido: {archive_file.filename}") # Añadimos este log
    print(f"  Lenguaje de destino recibido del formulario: {target_language}")


    if not archive_file.filename or not archive_file.filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail="Solo se permiten archivos con extensión .zip")

    try:
        zip_data = await archive_file.read()
        print(f"Archivo '{archive_file.filename}' ({len(zip_data)} bytes) leído exitosamente (FastAPI).")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al leer el contenido del archivo ZIP: {e}")

    # >>> Ejecutar la lógica de procesamiento síncrono en un hilo separado <<<
    try:
        fixed_max_tokens = 30 # Puedes ajustar este valor
        # Llama a la función síncrona que ahora retorna resultados y extensiones
        results, processable_extensions = await anyio.to_thread.run_sync(
            process_zip_sync,
            zip_data,
            fixed_max_tokens,
            target_language # Pasa el lenguaje de destino
        )

        # --- AHORA GENERAR EL PDF TEMPORAL CON LOS RESULTADOS RECOPILADOS ---
        # Esto verifica que la lista de resultados se llenó correctamente antes de crear los múltiples archivos.
        # Reutilizamos la lógica de generación de PDF del paso anterior, adaptada a la nueva estructura 'results'.
        print(f"Resultados obtenidos de process_zip_sync: {len(results)} entradas.") # Log de cuántos resultados se obtuvieron
        print("--> Iniciando generación del PDF TEMPORAL de resultados...")

        buffer = io.BytesIO()
        try:
            doc = SimpleDocTemplate(buffer, pagesize=letter)
            story = []

            styles = getSampleStyleSheet()
            style_title = styles['h1']
            style_filename = styles['h3']
            style_body = styles['Normal']
            style_code = styles['Normal'] # Nuevo estilo para código
            style_code.fontName = 'Courier' # Usar una fuente monoespaciada para código
            style_code.fontSize = 9
            style_code.leading = 10
            style_error = styles['Normal']
            style_error.textColor = (1, 0, 0)


            story.append(Paragraph(f"Resultados del Análisis/Transcripción para {archive_file.filename}:", style_title)) # Título general con nombre de archivo
            story.append(Spacer(1, 0.2*letter[1]))

            if not results:
                story.append(Paragraph(f"No se encontraron archivos procesables ({', '.join(processable_extensions)}) dentro del ZIP o todos fallaron tempranamente.", style_body))
            else:
                for result in results:
                    # Título del archivo con su tarea y estado(s)
                    title_text = f"Archivo: {result['filename']}"
                    # Añadir información de tarea y estado(s)
                    status_info = f"Tarea: {result['task']}"
                    # Añadir estado del resumen si existe y no es "No procesado"
                    if result.get('summary_status') and result['summary_status'] != 'No procesado':
                        status_info += f", Resumen: {result['summary_status']}"
                    # Añadir estado de la transcripción si existe y no es "No solicitada"
                    if result.get('transcription_status') and result['transcription_status'] != 'No solicitada':
                        status_info += f", Transcripción: {result['transcription_status']}"
                    if result.get('target_language'): # Añadir lenguaje de destino si aplica
                        status_info += f" a {result['target_language']}"

                    story.append(Paragraph(title_text, style_filename))
                    story.append(Paragraph(status_info, styles['Italic'])) # Usar estilo itálico para estado
                    story.append(Spacer(1, 6))

                    # Mostrar el contenido basado en la tarea y estado
                    # Determinar si hay algún estado de error o bloqueo
                    is_error = False
                    if result.get('summary_status', '').startswith("Error") or result.get('summary_status') == "Bloqueado" or result.get('summary_status') == "Excepción API/Manejo" or \
                        result.get('transcription_status', '').startswith("Error") or result.get('transcription_status') == "Bloqueado" or result.get('transcription_status') == "Excepción API/Manejo":
                        is_error = True

                    if is_error:
                        # Si hay un error en alguna tarea, mostrar el mensaje de error específico
                        error_content = "Detalles del error no disponibles."
                        if result.get('summary_status', '').startswith("Error") or result.get('summary_status') == "Bloqueado" or result.get('summary_status') == "Excepción API/Manejo":
                            error_content = f"Error/Estado Análisis: {result.get('summary_status')}\nDetalles: {result.get('summary_text', 'N/A')}"
                        if result.get('transcription_status', '').startswith("Error") or result.get('transcription_status') == "Bloqueado" or result.get('transcription_status') == "Excepción API/Manejo":
                            if error_content != "Detalles del error no disponibles.": error_content += "\n\n" # Añadir salto de línea si ya hay error de análisis
                            error_content += f"Error/Estado Transcripción: {result.get('transcription_status')}\nDetalles: {result.get('transcription_text', 'N/A')}"

                        story.append(Paragraph("Detalles del Error:", style_error))
                        story.append(Paragraph(str(error_content), style_error)) # Usar estilo de error para el texto del error

                    else:
                        # Si no hay errores, mostrar resumen y/o transcripción
                        if result.get('summary_text'):
                            story.append(Paragraph("Resumen:", style_body))
                            story.append(Paragraph(str(result['summary_text']), style_body))
                            story.append(Spacer(1, 12)) # Espacio entre resumen y transcripción si ambos existen

                        if result.get('transcription_text') and result['task'] in ("Transcribe", "Summarize & Transcribe") and result['transcription_status'].startswith("OK"):
                            story.append(Paragraph(f"Código Transcrito ({result.get('target_language', 'N/A')}):", style_body))
                            story.append(Spacer(1, 4))
                            # Usar Preformatted si quieres mantener formato exacto (espacios, indentación)
                            from reportlab.platypus import Preformatted
                            story.append(Preformatted(str(result['transcription_text']), style_code))
                            # O solo Paragraph si el formato no es crítico
                            # story.append(Paragraph(str(result['transcription_text']), style_code))


                    story.append(Spacer(1, 18)) # Espacio grande entre archivos


            doc.build(story) # Genera el PDF en el buffer

            pdf_output = buffer.getvalue()
            buffer.close()

            print("PDF de resumen TEMPORAL generado para verificación.")
            # Retornar este PDF TEMPORAL como respuesta para verificar los resultados recopilados
            download_filename = f"resultados_temp_{archive_file.filename.replace('.zip', '.pdf')}" if archive_file.filename else "resultados_temp.pdf"
            return Response(content=pdf_output, media_type="application/pdf",
                            headers={"Content-Disposition": f"attachment; filename=\"{download_filename}\""})

        except Exception as e:
            error_msg = f"Error al generar el PDF TEMPORAL de resultados: {e}"
            print(f"--- {error_msg} ---", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            raise HTTPException(status_code=500, detail=f"Error interno al generar PDF de resultados: {e}")


    except Exception as e:
        # Captura excepciones que vengan de process_zip_sync o run_sync
        print(f"--- Excepción propagada a la ruta FastAPI: {e} ---", file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Error durante el procesamiento del archivo: {e}")

# --- Para ejecutar la aplicación FastAPI con uvicorn ---
# Asegúrate de tener esto al final de tu archivo Python (ej. main.py)
if __name__ == "__main__":
    import uvicorn
    # Asegúrate que "nombre_del_archivo:app" coincide (ej. "main:app")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True) # Asegúrate del puerto correcto