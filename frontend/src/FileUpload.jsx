import React, { useState, useEffect } from 'react';
import './FileUpload.css'; // Importa el archivo CSS

function FileUpload() {
  const [selectedFile, setSelectedFile] = useState(null);
  const [statusMessage, setStatusMessage] = useState('');
  const [messageType, setMessageType] = useState(''); // 'info', 'success', 'error'

  const [isUploading, setIsUploading] = useState(false);
  const [downloadUrl, setDownloadUrl] = useState(null);
  const [downloadFilename, setDownloadFilename] = useState('');

  // --- Nuevo estado para el lenguaje de transcripción seleccionado ---
  // Inicialmente sin lenguaje seleccionado (solo resumen)
  const [targetLanguage, setTargetLanguage] = useState('');

  // Limpia estados anteriores al seleccionar un nuevo archivo
  const handleFileChange = (event) => {
    setSelectedFile(event.target.files[0]);
    setStatusMessage('');
    setMessageType('');
    setDownloadUrl(null);
    setDownloadFilename('');
    // No reseteamos targetLanguage aquí, el usuario podría querer usar la misma opción
  };

  // Maneja el cambio en la selección del lenguaje de transcripción
  const handleLanguageChange = (event) => {
    setTargetLanguage(event.target.value);
    // Opcional: limpiar mensajes o estado al cambiar la opción
    // setStatusMessage('');
    // setMessageType('');
  };


  // Maneja la subida del archivo
  const handleUpload = async () => {
    if (!selectedFile) {
      setStatusMessage('Por favor, selecciona un archivo ZIP primero.');
      setMessageType('error');
      return;
    }

    setIsUploading(true); // Indica que la subida ha comenzado
    setStatusMessage('Subiendo archivo...'); // Mensaje inicial
    setMessageType('info');
    setDownloadUrl(null); // Limpia URL de descarga anterior
    setDownloadFilename('');

    const formData = new FormData();
    formData.append('archive_file', selectedFile);
    // --- Añadir el lenguaje de transcripción seleccionado al FormData ---
    if (targetLanguage) { // Solo añade si se ha seleccionado un lenguaje (no está vacío)
        formData.append('target_language', targetLanguage);
        console.log("Lenguaje de transcripción seleccionado:", targetLanguage); // Log para verificar
    } else {
        console.log("No se seleccionó lenguaje de transcripción. Solo se resumirán archivos COBOL.");
    }


    try {
      setStatusMessage('Archivo subido. Procesando en el backend...');
      setMessageType('info');

      // --- Asegúrate de que la URL apunte a tu servidor FastAPI (ej. puerto 8000) ---
      const response = await fetch('http://localhost:8000/upload', {
        method: 'POST',
        body: formData,
      });

      if (response.ok) {
        // --- Handle success response (expecting a ZIP) ---
        setStatusMessage('Procesamiento completo. Preparando descarga del ZIP...'); // Updated message
        setMessageType('info');

        // Get the response body as a Blob (correct for binary data)
        const fileBlob = await response.blob(); // Renamed from pdfBlob

        // Get filename from Content-Disposition header
        const contentDisposition = response.headers.get('Content-Disposition');
        // Default filename if header is missing or invalid - use .zip extension
        let filename = 'analisis_resultados.zip'; // Default filename for ZIP
        if (contentDisposition) {
          const filenameMatch = contentDisposition.match(/filename="(.+)"/);
          if (filenameMatch && filenameMatch[1]) {
            // Use the filename from the header (should be a .zip name)
            filename = filenameMatch[1];
          }
        }
        console.log("Nombre de archivo para descarga:", filename); // Log para verificar


        // Create object URL
        const url = window.URL.createObjectURL(fileBlob); // Use fileBlob

        // Set state for download link
        setDownloadUrl(url);
        setDownloadFilename(filename); // Use the extracted or default .zip filename
        setStatusMessage('Procesamiento completo. Haz clic para descargar el archivo ZIP.'); // Updated message
        setMessageType('success');

      } else {
        // --- Handle error response (backend returned non-200 status) ---
         setStatusMessage(`Error al procesar archivo (código: ${response.status} ${response.statusText}).`);
         setMessageType('error');
         console.error('Error response from backend:', response);

         try {
             // Attempt to read error details as text or JSON from backend
             const errorDetailsText = await response.text(); // Read as text first

             // Try parsing as JSON if backend sends JSON errors
             let backendErrorMessage = errorDetailsText.slice(0, 300) + '...'; // Default to text extract
             try {
                 const errorJson = JSON.parse(errorDetailsText);
                 // Verifica si existe una clave 'error' (como en tus JSONResponse de error del backend)
                 if (errorJson && errorJson.error) {
                     backendErrorMessage = errorJson.error; // Usa el mensaje de error de la clave 'error'
                 } else {
                      // Si JSON parsing exitoso pero sin clave 'error' (otra estructura JSON inesperada)
                      backendErrorMessage = 'Respuesta del backend fue JSON válido pero con estructura inesperada. Verifica la consola del navegador.';
                      console.error('Respuesta JSON del backend:', errorJson);
                 }
             } catch (jsonParseError) {
                 // Si JSON parsing falla, asume que es texto plano o HTML de error (como antes)
                 console.warn('La respuesta del backend no fue JSON. Manejando como texto/HTML.', jsonParseError);
                 // Se mantiene el extracto de texto como backendErrorMessage
             }

             // Actualiza el mensaje de estado con detalles del backend
             setStatusMessage(prevMsg => `${prevMsg} Detalles del Backend: ${backendErrorMessage}`);
             console.error('Detalles del error del backend:', errorDetailsText); // Log la respuesta original como texto

         } catch (readError) {
             console.error('Error al leer detalles de la respuesta de error:', readError);
             setStatusMessage(prevMsg => `${prevMsg} No se pudieron obtener detalles específicos del error.`);
         }
      }

    } catch (error) {
      // --- Handle network errors or errors before response.ok (like "Failed to fetch") ---
      console.error('Error durante la subida o conexión:', error);
      // Este catch a menudo captura errores de red o si la petición no pudo ni completarse al punto de tener un response.status
      setStatusMessage(`Error de conexión o subida: ${error.message}. Asegúrate de que el servidor FastAPI esté corriendo.`);
      setMessageType('error');
    } finally {
      setIsUploading(false);
    }
  };

  // ... other state and useEffect ...

  // Handles the download button click
  const handleDownloadClick = () => {
    if (downloadUrl) {
      const a = document.createElement('a');
      a.href = downloadUrl;
      // Usa downloadFilename que ya tiene la extensión .zip del backend, o el default .zip
      a.download = downloadFilename || 'analisis_resultados.zip';
      // No es estrictamente necesario añadirlo al body en la mayoría de navegadores modernos para el click programático
      // document.body.appendChild(a);
      a.click(); // Activa la descarga

      // Limpiar la URL del objeto después de un pequeño retraso
      setTimeout(() => {
         window.URL.revokeObjectURL(downloadUrl);
         console.log("Revocando URL de descarga.");
      }, 100); // Pequeño retraso para asegurar que la descarga se inició
      // Si lo añadiste al body, remuévelo aquí:
      // if (a.parentElement) {
      //    a.parentElement.removeChild(a);
      // }
    }
  };

  // Limpieza de la URL de descarga cuando el componente se desmonta o la URL cambia
  React.useEffect(() => {
    return () => {
      if (downloadUrl) {
        window.URL.revokeObjectURL(downloadUrl);
        console.log("Revocando URL de descarga anterior.");
      }
    };
  }, [downloadUrl]); // Depende de downloadUrl para limpiar la URL anterior cuando se setea una nueva


  return (
    <div className="file-upload-container">
      <h2>Análisis y Migración de Archivos con IA</h2> {/* Título actualizado */}
      <form onSubmit={(e) => { e.preventDefault(); handleUpload(); }}> {/* Llama a handleUpload */}
          <label htmlFor="archiveFile" className="file-input-label">Selecciona archivo ZIP</label>
          <input
            type="file"
            id="archiveFile"
            accept=".zip"
            onChange={handleFileChange}
            disabled={isUploading}
            className="hidden-file-input" // Clase para ocultar el input nativo
          />
          {selectedFile && <p className="selected-file-name">Archivo seleccionado: {selectedFile.name}</p>} {/* Muestra el nombre del archivo seleccionado */}

          <br/><br/> {/* Espacio entre input de archivo y opciones */}

          {/* --- Nuevo menú desplegable para seleccionar lenguaje de transcripción --- */}
          <div className="language-select-container"> {/* Contenedor para estilos */}
            <label htmlFor="targetLanguage">Transcribir COBOL a:</label>
            <select
              id="targetLanguage"
              value={targetLanguage} // Controlado por el estado
              onChange={handleLanguageChange}
              disabled={isUploading} // Deshabilita durante la subida/procesamiento
            >
              <option value="">Solo Resumir COBOL (No transcribir)</option> {/* Opción por defecto (no transcribir) */}
              {/* Aquí podrías mapear una lista de lenguajes si la pasas como prop o la defines aquí */}
              <option value="Java">Java</option>
              <option value="Python">Python</option>
              <option value="CSharp">C#</option> {/* Usaremos 'CSharp' como valor */}
              <option value="JavaScript">JavaScript</option>
              <option value="C++">C++</option>
              <option value="Ruby">Ruby</option>
              <option value="PHP">PHP</option>
              <option value="Go">Go</option>
              <option value="Swift">Swift</option>
              <option value="Kotlin">Kotlin</option>
            </select>
          </div>
            {/* --- Fin menú desplegable --- */}

          <br/><br/> {/* Espacio entre opciones y botón */}

          <button
            onClick={handleUpload}
            disabled={!selectedFile || isUploading} // Deshabilitado si no hay archivo o está subiendo
          >
            {isUploading ? 'Procesando...' : 'Subir y Analizar'}
          </button>
      </form>

      {/* Área para mostrar mensajes de estado, error o éxito */}
      {statusMessage && (
        <p className={`message ${messageType}`}>
          {statusMessage}
        </p>
      )}

        {/* Mostrar sección de descarga si la URL está disponible */}
        {downloadUrl && (
            <div className="download-section">
              <p>Descarga tu archivo de análisis y transcripción:</p> {/* Mensaje actualizado */}
               {/* Usa el manejador de clic para iniciar la descarga */}
              <button onClick={handleDownloadClick}>
                  Descargar "{downloadFilename || 'resultados_analisis.zip'}" {/* Nombre de archivo por defecto para ZIP */}
              </button>
            </div>
        )}

    </div>
  );
}

export default FileUpload;