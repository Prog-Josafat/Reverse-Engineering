import React, { useState, useEffect } from 'react';
import './FileUpload.css'; // Import the CSS file

function FileUpload() {
  // State variables for file selection and upload process
  const [selectedFile, setSelectedFile] = useState(null);
  const [statusMessage, setStatusMessage] = useState('');
  const [messageType, setMessageType] = useState(''); // 'info', 'success', 'error'

  const [isUploading, setIsUploading] = useState(false);
  // State variables for download link after successful processing
  const [downloadUrl, setDownloadUrl] = useState(null);
  const [downloadFilename, setDownloadFilename] = useState('');

  // State for the selected transcription target language
  // Initially no language selected (only summary)
  const [targetLanguage, setTargetLanguage] = useState('');

  // Clears previous states when a new file is selected
  const handleFileChange = (event) => {
    setSelectedFile(event.target.files[0]);
    setStatusMessage('');
    setMessageType('');
    setDownloadUrl(null); // Clear previous download URL
    setDownloadFilename(''); // Clear previous filename
    // Keep targetLanguage selection
  };

  // Handles changes in the transcription language selection
  const handleLanguageChange = (event) => {
    setTargetLanguage(event.target.value);
    // Optional: clear messages or state on option change
    // setStatusMessage('');
    // setMessageType('');
  };


  // Handles the file upload process
  const handleUpload = async () => {
    if (!selectedFile) {
      setStatusMessage('Please select a ZIP file first.');
      setMessageType('error');
      return;
    }

    setIsUploading(true); // Indicate upload has started
    setStatusMessage('Uploading file...'); // Initial message
    setMessageType('info');
    setDownloadUrl(null); // Clear previous download URL
    setDownloadFilename(''); // Clear previous filename

    const formData = new FormData();
    formData.append('archive_file', selectedFile);
    // Add the selected transcription language to FormData
    if (targetLanguage) { // Only add if a language is selected (not empty)
        formData.append('target_language', targetLanguage);
        console.log("Selected transcription language:", targetLanguage); // Log for verification
    } else {
        console.log("No target transcription language selected. Only COBOL files will be summarized.");
    }


    try {
      setStatusMessage('File uploaded. Processing on the backend...');
      setMessageType('info');

      // Ensure the URL points to your FastAPI server (e.g., port 8000)
      const response = await fetch('http://localhost:8000/upload', {
        method: 'POST',
        body: formData,
      });

      if (response.ok) {
        // Handle success response (expecting a ZIP)
        setStatusMessage('Processing complete. Preparing ZIP download...'); // Updated message
        setMessageType('info');

        // Get the response body as a Blob (correct for binary data)
        const fileBlob = await response.blob();

        // Get filename from Content-Disposition header
        const contentDisposition = response.headers.get('Content-Disposition');
        // Default filename if header is missing or invalid - use .zip extension
        let filename = 'analysis_results.zip'; // Default filename for ZIP
        if (contentDisposition) {
          const filenameMatch = contentDisposition.match(/filename="(.+)"/);
          if (filenameMatch && filenameMatch[1]) {
            // Use the filename from the header (should be a .zip name)
            filename = filenameMatch[1];
          }
        }
        console.log("Filename for download:", filename); // Log for verification


        // Create object URL
        const url = window.URL.createObjectURL(fileBlob);

        // Set state for download link
        setDownloadUrl(url);
        setDownloadFilename(filename); // Use the extracted or default .zip filename
        setStatusMessage('Processing complete. Click to download the ZIP file.'); // Updated message
        setMessageType('success');

      } else {
        // Handle error response (backend returned non-200 status)
          setStatusMessage(`Error processing file (code: ${response.status} ${response.statusText}).`);
          setMessageType('error');
          console.error('Error response from backend:', response);

          try {
              // Attempt to read error details as text or JSON from backend
              const errorDetailsText = await response.text(); // Read as text first

              // Try parsing as JSON if backend sends JSON errors
              let backendErrorMessage = errorDetailsText.slice(0, 300) + '...'; // Default to text extract
              try {
                  const errorJson = JSON.parse(errorDetailsText);
                  // Check if an 'error' key exists (as in your backend JSONResponse errors)
                  if (errorJson && errorJson.error) {
                      backendErrorMessage = errorJson.error; // Use the error message from the 'error' key
                  } else {
                      // If JSON parsing successful but no 'error' key (another unexpected JSON structure)
                      backendErrorMessage = 'Backend response was valid JSON but with unexpected structure. Check browser console.';
                      console.error('Backend JSON response:', errorJson);
                  }
              } catch (jsonParseError) {
                  // If JSON parsing fails, assume it's plain text or error HTML (as before)
                  console.warn('Backend response was not JSON. Handling as text/HTML.', jsonParseError);
                  // Text extract is kept as backendErrorMessage
              }

              // Update status message with backend details
              setStatusMessage(prevMsg => `${prevMsg} Backend Details: ${backendErrorMessage}`);
              console.error('Backend error details:', errorDetailsText); // Log the original response as text

          } catch (readError) {
              console.error('Error reading error response details:', readError);
              setStatusMessage(prevMsg => `${prevMsg} Could not get specific error details.`);
          }
      }

    } catch (error) {
      // Handle network errors or errors before response.ok (like "Failed to fetch")
      console.error('Error during upload or connection:', error);
      // This catch often captures network errors or if the request couldn't even complete to the point of having a response.status
      setStatusMessage(`Connection or upload error: ${error.message}. Ensure the FastAPI server is running.`);
      setMessageType('error');
    } finally {
      setIsUploading(false); // Reset uploading state
    }
  };

    // Handles the download button click
  const handleDownloadClick = () => {
    if (downloadUrl) {
      const a = document.createElement('a');
      a.href = downloadUrl;
      // Use downloadFilename which should already have the .zip extension from backend, or the .zip default
      a.download = downloadFilename || 'analysis_results.zip';
      a.click(); // Trigger the download

      // Clean up the object URL after a small delay
      setTimeout(() => {
          window.URL.revokeObjectURL(downloadUrl);
          console.log("Revoking download URL.");
      }, 100);
    }
  };

    // Cleanup of the download URL when the component unmounts or the URL changes
    useEffect(() => {
      return () => {
        if (downloadUrl) {
          window.URL.revokeObjectURL(downloadUrl);
          console.log("Revoking previous download URL.");
        }
      };
    }, [downloadUrl]);


  return (
    <div className="file-upload-container">
      <h2>AI File Analysis and Migration</h2> {/* Updated title */}
      <form onSubmit={(e) => { e.preventDefault(); handleUpload(); }}> {/* Call handleUpload */}
          {/* Custom file input label */}
          <label htmlFor="archiveFile" className="file-input-label">Select ZIP File</label>
          <input
            type="file"
            id="archiveFile"
            accept=".zip" // Accept only zip files
            onChange={handleFileChange}
            disabled={isUploading}
            className="hidden-file-input" // Class to hide the native input
          />
          {/* Display selected file name */}
          {selectedFile && <p className="selected-file-name">Selected file: {selectedFile.name}</p>}

          <br/><br/> {/* Space between file input and options */}

          {/* Dropdown menu for selecting transcription language */}
          <div className="language-select-container">
            <label htmlFor="targetLanguage">Transcribe COBOL to:</label>
            <select
              id="targetLanguage"
              value={targetLanguage} // Controlled by state
              onChange={handleLanguageChange}
              disabled={isUploading} // Disable during upload/processing
            >
              <option value="">Summarize COBOL Only (No transcription)</option> {/* Default option */}
              {/* Add other languages here if needed */}
              <option value="Java">Java</option>
              <option value="Python">Python</option>
              <option value="CSharp">C#</option>
              <option value="JavaScript">JavaScript</option>
              <option value="C++">C++</option>
              <option value="Ruby">Ruby</option>
              <option value="PHP">PHP</option>
              <option value="Go">Go</option>
              <option value="Swift">Swift</option>
              <option value="Kotlin">Kotlin</option>
            </select>
          </div>

          <br/><br/> {/* Space between options and button */}

          {/* Upload button */}
          <button
            onClick={handleUpload}
            disabled={!selectedFile || isUploading} // Disabled if no file selected or uploading
          >
            {isUploading ? 'Processing...' : 'Upload and Analyze'}
          </button>
      </form>

      {/* Area to display status, error, or success messages */}
      {statusMessage && (
        <p className={`message ${messageType}`}>
          {statusMessage}
        </p>
      )}

        {/* Show download section if URL is available */}
        {downloadUrl && (
            <div className="download-section">
              <p>Download your analysis and transcription file:</p> {/* Updated message */}
                {/* Use the click handler to initiate download */}
              <button onClick={handleDownloadClick}>
                  Download "{downloadFilename || 'analysis_results.zip'}" {/* Default filename for ZIP */}
              </button>
            </div>
        )}

    </div>
  );
}

export default FileUpload;