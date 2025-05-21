import React, { useState, useEffect } from 'react';
import './FileUpload.css';
import JSZip from 'jszip';

function FileUpload() {
    const [selectedFile, setSelectedFile] = useState(null);
    const [statusMessage, setStatusMessage] = useState('');
    const [messageType, setMessageType] = useState('');
    const [isUploading, setIsUploading] = useState(false);
    const [downloadUrl, setDownloadUrl] = useState(null);
    const [downloadFilename, setDownloadFilename] = useState('');
    const [targetLanguage, setTargetLanguage] = useState('');
    const [zipFiles, setZipFiles] = useState([]);
    const [selectedPreviewFile, setSelectedPreviewFile] = useState(null);
    const [isPreviewModalOpen, setIsPreviewModalOpen] = useState(false);

    const handleFileChange = (event) => {
        setSelectedFile(event.target.files[0]);
        setStatusMessage('');
        setMessageType('');
        setDownloadUrl(null);
        setDownloadFilename('');
    };

    const handleLanguageChange = (event) => {
        setTargetLanguage(event.target.value);
    };

    const handleUpload = async () => {
        if (!selectedFile) {
            setStatusMessage('Please select a ZIP file first.');
            setMessageType('error');
            return;
        }

        setIsUploading(true);
        setStatusMessage('Uploading file...');
        setMessageType('info');
        setDownloadUrl(null);
        setDownloadFilename('');

        const formData = new FormData();
        formData.append('archive_file', selectedFile);
        if (targetLanguage) {
            formData.append('target_language', targetLanguage);
        }

        try {
            setStatusMessage('File uploaded. Processing on the backend...');
            setMessageType('info');

            const response = await fetch('http://localhost:8000/upload', {
                method: 'POST',
                body: formData,
            });

            if (response.ok) {
                const fileBlob = await response.blob();
                const contentDisposition = response.headers.get('Content-Disposition');
                let filename = 'analysis_results.zip';
                if (contentDisposition) {
                    const filenameMatch = contentDisposition.match(/filename="(.+)"/);
                    if (filenameMatch && filenameMatch[1]) {
                        filename = filenameMatch[1];
                    }
                }

                const url = window.URL.createObjectURL(fileBlob);
                setDownloadUrl(url);
                setDownloadFilename(filename);
                setStatusMessage('Processing complete. Click to download the ZIP file.');
                setMessageType('success');

                await extractZipContents(fileBlob);

            } else {
                setStatusMessage(`Error processing file (code: ${response.status} ${response.statusText}).`);
                setMessageType('error');

                try {
                    const errorDetailsText = await response.text();
                    let backendErrorMessage = errorDetailsText.slice(0, 300) + '...';
                    try {
                        const errorJson = JSON.parse(errorDetailsText);
                        if (errorJson && errorJson.error) {
                            backendErrorMessage = errorJson.error;
                        } else {
                            backendErrorMessage = 'Backend response was valid JSON but with unexpected structure. Check browser console.';
                            console.error('Backend JSON response:', errorJson);
                        }
                    } catch (jsonParseError) {
                        console.warn('Backend response was not JSON. Handling as text/HTML.', jsonParseError);
                    }

                    setStatusMessage(prevMsg => `${prevMsg} Backend Details: ${backendErrorMessage}`);
                    console.error('Backend error details:', errorDetailsText);

                } catch (readError) {
                    console.error('Error reading error response details:', readError);
                    setStatusMessage(prevMsg => `${prevMsg} Could not get specific error details.`);
                }
            }

        } catch (error) {
            console.error('Error during upload or connection:', error);
            setStatusMessage(`Connection or upload error: ${error.message}. Ensure the FastAPI server is running.`);
            setMessageType('error');
        } finally {
            setIsUploading(false);
        }
    };

    const handleDownloadClick = () => {
        if (downloadUrl) {
            const a = document.createElement('a');
            a.href = downloadUrl;
            a.download = downloadFilename || 'analysis_results.zip';
            a.click();

            setTimeout(() => {
                window.URL.revokeObjectURL(downloadUrl);
            }, 100);
        }
    };

    useEffect(() => {
        return () => {
            if (downloadUrl) {
                window.URL.revokeObjectURL(downloadUrl);
            }
        };
    }, [downloadUrl]);

    const extractZipContents = async (zipBlob) => {
        try {
            const zip = await JSZip.loadAsync(zipBlob);
            const files = [];
            const loadTextFiles = [];

            zip.forEach((relativePath, zipEntry) => {
                const file = {
                    name: zipEntry.name,
                    content: null,
                    type: getFileType(zipEntry.name),
                    zipEntry: zipEntry,
                };
                files.push(file);

                if (file.type === 'text') {
                    loadTextFiles.push(zipEntry.async('string').then(text => {
                        file.content = text;
                    }).catch(err => {
                        console.error(`Error loading text file ${file.name}:`, err);
                    }));
                }
            });

            await Promise.all(loadTextFiles);
            setZipFiles(files);
            console.log('zipFiles after extraction:', files);
        } catch (error) {
            console.error('Error extracting ZIP contents:', error);
            setStatusMessage('Error processing ZIP for preview.');
            setMessageType('error');
        }
    };

    const getFileType = (filename) => {
        const ext = filename.split('.').pop().toLowerCase();
        if (['txt', 'cbl', 'cob', 'java', 'py', 'cs', 'js', 'html', 'css'].includes(ext)) {
            return 'text';
        } else if (ext === 'pdf') {
            return 'pdf';
        } else if (['jpg', 'jpeg', 'png', 'gif'].includes(ext)) {
            return 'image';
        } else {
            return 'other';
        }
    };

    const fetchFileContent = async (file) => {
        console.log('Fetching content for:', file);
        if (file.content === null && file.type !== 'text') {
            try {
                const content = await file.zipEntry.async(file.type === 'text' ? 'string' : 'blob');
                setSelectedPreviewFile({ ...file, content });
                console.log('setSelectedPreviewFile called with (after fetch):', { ...file, content });
                setIsPreviewModalOpen(true);
            } catch (error) {
                console.error(`Error fetching content for ${file.name}:`, error);
                setStatusMessage(`Error previewing ${file.name}`);
                setMessageType('error');
            }
        } else {
            setSelectedPreviewFile(file);
            console.log('setSelectedPreviewFile called with (immediate):', file);
            setIsPreviewModalOpen(true);
        }
    };

    const closePreviewModal = () => {
        setIsPreviewModalOpen(false);
        setSelectedPreviewFile(null);
    };

    const handleDownloadPdf = (file) => {
        if (file && file.content) {
            const pdfBlob = new Blob([file.content], { type: 'application/pdf' });
            const url = window.URL.createObjectURL(pdfBlob);
            const a = document.createElement('a');
            a.href = url;
            a.download = file.name;
            a.click();
            window.URL.revokeObjectURL(url);
        } else {
            setStatusMessage('No PDF content to download.');
            setMessageType('error');
        }
    };

    return (
        <div className="file-upload-container">
            <h2>AI Code Analysis and Migration</h2>
            <form onSubmit={(e) => { e.preventDefault(); handleUpload(); }}>
                <label htmlFor="archiveFile" className="file-input-label">Select ZIP File</label>
                <input
                    type="file"
                    id="archiveFile"
                    accept=".zip"
                    onChange={handleFileChange}
                    disabled={isUploading}
                    className="hidden-file-input"
                />
                {selectedFile && <p className="selected-file-name">Selected file: {selectedFile.name}</p>}

                <div className="language-select-container">
                    <label htmlFor="targetLanguage">Transcribe COBOL to:</label>
                    <select
                        id="targetLanguage"
                        value={targetLanguage}
                        onChange={handleLanguageChange}
                        disabled={isUploading}
                    >
                        <option value="">Summarize COBOL Only (No transcription)</option>
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

                <button
                    onClick={handleUpload}
                    disabled={!selectedFile || isUploading}
                >
                    {isUploading ? 'Processing...' : 'Upload and Analyze'}
                </button>
            </form>

            {statusMessage && (
                <p className={`message ${messageType}`}>
                    {statusMessage}
                </p>
            )}

            {downloadUrl && (
                <div className="download-section">
                    <p>Processing complete! Download your results:</p>
                    <button onClick={handleDownloadClick}>
                        Download "{downloadFilename || 'analysis_results.zip'}"
                    </button>
                    <button onClick={() => setIsPreviewModalOpen(true)}>
                        Preview ZIP
                    </button>
                </div>
            )}

            {isPreviewModalOpen && zipFiles.length > 0 && (
                <div className="preview-modal">
                    <div className="preview-modal-content">
                        <span className="close-button" onClick={closePreviewModal}>&times;</span>
                        <h3>Archivos en el ZIP</h3>
                        <ul>
                            {zipFiles.map((file, index) => (
                                <li
                                    key={index}
                                    onClick={() => fetchFileContent(file)}
                                    style={{
                                        cursor: 'pointer',
                                        color: selectedPreviewFile?.name === file.name ? 'blue' : 'black',
                                        fontWeight: selectedPreviewFile?.name === file.name ? 'bold' : 'normal',
                                    }}
                                >
                                    {file.name}
                                </li>
                            ))}
                        </ul>
                        {selectedPreviewFile && (
                            <>
                                <h4>Preview: {selectedPreviewFile.name}</h4>
                                {selectedPreviewFile.type === 'text' && (
                                    <pre className="preview-text">{selectedPreviewFile.content}</pre>
                                )}
                                {selectedPreviewFile.type === 'pdf' && (
                                    <p>
                                        PDF file.
                                        <button onClick={() => handleDownloadPdf(selectedPreviewFile)}>
                                            Click here to download and view.
                                        </button>
                                    </p>
                                )}
                                {selectedPreviewFile.type === 'image' && selectedPreviewFile.content && (
                                    <img src={URL.createObjectURL(selectedPreviewFile.content)} alt={selectedPreviewFile.name} />
                                )}
                                {selectedPreviewFile.type === 'other' && (
                                    <p>
                                        Unknown file type.
                                        <a href={downloadUrl} download={selectedPreviewFile.name}>
                                            Download to view.
                                        </a>
                                    </p>
                                )}
                            </>
                        )}
                    </div>
                </div>
            )}

        </div>
    );
}

export default FileUpload;