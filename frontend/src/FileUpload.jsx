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
  const [targetLanguage, setTargetLanguage] = useState(''); // Puedes usar un valor por defecto como 'None' o ''

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
        setStatusMessage('Procesamiento completo. Preparando descarga...');
        setMessageType('info');

        const pdfBlob = await response.blob();

        const contentDisposition = response.headers.get('Content-Disposition');
        let filename = 'analisis_descargado.pdf';
        if (contentDisposition) {
          const filenameMatch = contentDisposition.match(/filename="(.+)"/);
          if (filenameMatch && filenameMatch[1]) {
            filename = filenameMatch[1];
          }
        }

        const url = window.URL.createObjectURL(pdfBlob);

        setDownloadUrl(url);
        setDownloadFilename(filename);
        setStatusMessage('Análisis completo. Haz clic para descargar el PDF.');
        setMessageType('success');

      } else {
        setStatusMessage(`Error al procesar archivo (código: ${response.status} ${response.statusText}).`);
        setMessageType('error');
        console.error('Error response from backend:', response);

        try {
            const errorDetails = await response.text();
            const parser = new DOMParser();
            const htmlDoc = parser.parseFromString(errorDetails, 'text/html');
            // Intenta buscar el mensaje de error en el HTML de error de FastAPI (si devuelve HTML, aunque no es lo típico para 500)
            // O si FastAPI devuelve JSON en caso de error (más típico), deberías parsear JSON: await response.json()
            // Por ahora, intentamos buscar en un posible HTML o mostramos un extracto del texto plano
            const errorMessageElement = htmlDoc.body.textContent ? htmlDoc.body : null; // Intenta obtener texto del body si no hay estructura específica
            const backendErrorMessage = errorMessageElement ? errorMessageElement.textContent.slice(0, 300) + '...' : errorDetails.slice(0, 300) + '...'; // Usa el texto del body o un extracto

            setStatusMessage(prevMsg => `${prevMsg} Detalles del Backend: ${backendErrorMessage}`);
            console.error('Error details from backend:', errorDetails);
        } catch (readError) {
            console.error('Error al leer detalles de la respuesta de error o parsear HTML:', readError);
            setStatusMessage(prevMsg => `${prevMsg} No se pudieron obtener detalles específicos del error.`);
        }
      }

    } catch (error) {
      console.error('Error durante la subida o conexión:', error);
      setStatusMessage(`Error de conexión o subida: ${error.message}. Asegúrate de que el servidor FastAPI esté corriendo.`);
      setMessageType('error');
    } finally {
      setIsUploading(false);
    }
  };

   // Maneja la descarga del archivo cuando el usuario hace clic en el enlace
  const handleDownloadClick = () => {
    if (downloadUrl) {
      const a = document.createElement('a');
      a.href = downloadUrl;
      a.download = downloadFilename || 'analisis_descargado.pdf';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
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
  }, [downloadUrl]);


  return (
    <div className="file-upload-container">
      <h2>Análisis de Archivos ZIP con IA</h2> {/* Título actualizado */}
      <form onSubmit={(e) => e.preventDefault()}>
          <input
            type="file"
            id="archiveFile"
            accept=".zip"
            onChange={handleFileChange}
            disabled={isUploading}
          />
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
                <option value="">Solo Resumir COBOL</option> {/* Opción por defecto (no transcribir) */}
                <option value="Java">Java</option>
                <option value="CSharp">C#</option> {/* Usaremos 'CSharp' como valor */}
                <option value="Python">Python</option>
                {/* Puedes añadir otros lenguajes si el modelo los soporta y quieres implementarlos */}
            </select>
          </div>
           {/* --- Fin menú desplegable --- */}

          <br/><br/> {/* Espacio entre opciones y botón */}

          <button
            onClick={handleUpload}
            disabled={!selectedFile || isUploading}
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
              <p>Descarga tu archivo de análisis:</p>
              <button onClick={handleDownloadClick}>
                  Descargar "{downloadFilename || 'analisis_descargado.pdf'}"
              </button>
          </div>
      )}

    </div>
  );
}

export default FileUpload;