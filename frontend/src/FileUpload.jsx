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
    
    // Estados para la funcionalidad de rehacer ZIP
    const [originalZipData, setOriginalZipData] = useState(null);
    const [hasProcessedResults, setHasProcessedResults] = useState(false);
    const [isReprocessing, setIsReprocessing] = useState(false);
    
    // Estados para la funcionalidad de análisis de repositorios GitHub
    const [repoUrl, setRepoUrl] = useState('');
    const [isAnalyzingRepo, setIsAnalyzingRepo] = useState(false);
    
    // Estados para reprocesar repositorios
    const [lastRepoUrl, setLastRepoUrl] = useState('');
    const [lastRepoTargetLanguage, setLastRepoTargetLanguage] = useState('');
    const [isLastAnalysisRepo, setIsLastAnalysisRepo] = useState(false);


    const handleFileChange = (event) => {
        setSelectedFile(event.target.files[0]);
        setStatusMessage('');
        setMessageType('');
        setDownloadUrl(null);
        setDownloadFilename('');
        setOriginalZipData(null);
        setHasProcessedResults(false);
        setIsLastAnalysisRepo(false); 
        setRepoUrl(''); 
    };

    const handleLanguageChange = (event) => {
        setTargetLanguage(event.target.value);
    };

    const handleRepoUrlChange = (event) => {
        setRepoUrl(event.target.value);
        if (event.target.value) {
            setSelectedFile(null);
            setOriginalZipData(null);
        }
        setStatusMessage('');
        setMessageType('');
    };

    const handleUpload = async () => {
        if (!selectedFile) {
            setStatusMessage('Please select a ZIP file first.');
            setMessageType('error');
            return;
        }
        setRepoUrl('');
        setHasProcessedResults(false);
        setIsLastAnalysisRepo(false);
        await processUploadedFile(selectedFile, false);
    };

    const handleReprocessZip = async () => {
        if (!originalZipData) {
            setStatusMessage('No original ZIP file to reprocess.');
            setMessageType('error');
            return;
        }
        await processUploadedFile(originalZipData, true);
    };

    const processUploadedFile = async (fileData, isReprocess = false) => {
        const actionType = isReprocess ? 'Reprocessing ZIP' : 'Uploading ZIP';
        const setLoadingState = isReprocess ? setIsReprocessing : setIsUploading;
        
        setLoadingState(true);
        setStatusMessage(`${actionType}...`);
        setMessageType('info');
        setDownloadUrl(null);
        setDownloadFilename('');
        setZipFiles([]);

        let formData = new FormData();
        
        if (isReprocess) {
            const blob = new Blob([fileData], { type: 'application/zip' });
            formData.append('archive_file', blob, 'reprocess.zip');
        } else {
            formData.append('archive_file', fileData);
            const arrayBuffer = await fileData.arrayBuffer();
            setOriginalZipData(arrayBuffer);
        }
        
        if (targetLanguage) {
            formData.append('target_language', targetLanguage);
        }
        if (isReprocess) {
            formData.append('is_reprocess', 'true');
        }

        try {
            const endpoint = isReprocess ? 'http://localhost:8000/reprocess' : 'http://localhost:8000/upload';
            const response = await fetch(endpoint, {
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
                setStatusMessage(`Processing complete. Click to download the ${isReprocess ? 'reprocessed ' : ''}ZIP file.`);
                setMessageType('success');
                setHasProcessedResults(true);
                setIsLastAnalysisRepo(false);
                await extractZipContents(fileBlob);
            } else {
                const errorText = await response.text();
                let backendErrorMessage = `Error (code: ${response.status} ${response.statusText}).`;
                try {
                    const errorJson = JSON.parse(errorText);
                    backendErrorMessage += ` Details: ${errorJson.detail || errorJson.error || 'Unknown backend error.'}`;
                } catch (e) {
                    backendErrorMessage += ` Details: ${errorText.slice(0,200)}...`;
                }
                setStatusMessage(backendErrorMessage);
                setMessageType('error');
                console.error('Backend error:', errorText);
            }
        } catch (error) {
            console.error(`Error during ${actionType.toLowerCase()} or connection:`, error);
            setStatusMessage(`Connection or ${actionType.toLowerCase()} error: ${error.message}. Ensure the FastAPI server is running.`);
            setMessageType('error');
        } finally {
            setLoadingState(false);
        }
    };

    const handleAnalyzeRepo = async (isReprocessRequest = false) => {
        const currentRepoUrl = isReprocessRequest ? lastRepoUrl : repoUrl;
        // For repo reprocess, use the globally selected targetLanguage,
        // or the one stored if you prefer (lastRepoTargetLanguage)
        const currentTargetLanguage = targetLanguage; 

        if (!currentRepoUrl) {
            setStatusMessage('Please enter a GitHub repository URL.');
            setMessageType('error');
            return;
        }
        if (!currentRepoUrl.startsWith('https://github.com/')) {
            setStatusMessage(`Invalid GitHub repository URL: ${currentRepoUrl}. It must start with "https://github.com/".`);
            setMessageType('error');
            return;
        }

        setIsAnalyzingRepo(true);
        setIsReprocessing(isReprocessRequest); // Use isReprocessing for loading state of repo reprocess
        setStatusMessage(isReprocessRequest ? 'Reprocessing repository...' : 'Cloning and analyzing repository...');
        setMessageType('info');
        setDownloadUrl(null);
        setDownloadFilename('');
        setSelectedFile(null); 
        setOriginalZipData(null);
        if (!isReprocessRequest) setHasProcessedResults(false); // Reset only for new analysis
        setZipFiles([]);

        const formData = new FormData();
        formData.append('repo_url', currentRepoUrl);
        if (currentTargetLanguage) {
            formData.append('target_language', currentTargetLanguage);
        }
        if (isReprocessRequest) {
            formData.append('is_reprocess', 'true');
        }

        try {
            const response = await fetch('http://localhost:8000/analyze_repo', {
                method: 'POST',
                body: formData,
            });

            if (response.ok) {
                const fileBlob = await response.blob();
                const contentDisposition = response.headers.get('Content-Disposition');
                let filename = 'repo_analysis_results.zip';
                if (contentDisposition) {
                    const filenameMatch = contentDisposition.match(/filename="(.+)"/);
                    if (filenameMatch && filenameMatch[1]) {
                        filename = filenameMatch[1];
                    }
                }
                const url = window.URL.createObjectURL(fileBlob);
                setDownloadUrl(url);
                setDownloadFilename(filename);
                setStatusMessage(`Repository analysis ${isReprocessRequest ? 'reprocessed' : 'complete'}. Click to download the ZIP file.`);
                setMessageType('success');
                setHasProcessedResults(true);
                if (!isReprocessRequest) {
                    setLastRepoUrl(currentRepoUrl);
                    setLastRepoTargetLanguage(currentTargetLanguage); // Store language used for this repo
                }
                setIsLastAnalysisRepo(true);
                await extractZipContents(fileBlob);
            } else {
                const errorText = await response.text();
                let backendErrorMessage = `Error (code: ${response.status} ${response.statusText}).`;
                try {
                    const errorJson = JSON.parse(errorText);
                    backendErrorMessage += ` Details: ${errorJson.detail || errorJson.error || 'Unknown backend error.'}`;
                } catch (e) {
                    backendErrorMessage += ` Details: ${errorText.slice(0,200)}...`;
                }
                setStatusMessage(backendErrorMessage);
                setMessageType('error');
                console.error('Backend error:', errorText);
            }
        } catch (error) {
            console.error('Error during repository analysis or connection:', error);
            setStatusMessage(`Connection or repository analysis error: ${error.message}. Ensure the FastAPI server is running.`);
            setMessageType('error');
        } finally {
            setIsAnalyzingRepo(false);
            setIsReprocessing(false); // Clear reprocessing state
        }
    };
    
    const handleReprocessRepo = async () => {
        if (!lastRepoUrl) {
            setStatusMessage('No previous repository analysis to reprocess.');
            setMessageType('error');
            return;
        }
        await handleAnalyzeRepo(true); // isReprocessRequest = true
    };

    const handleDownloadClick = () => {
        if (downloadUrl) {
            const a = document.createElement('a');
            a.href = downloadUrl;
            a.download = downloadFilename || 'analysis_results.zip';
            document.body.appendChild(a); // Required for Firefox
            a.click();
            document.body.removeChild(a); // Clean up
            // No revokeObjectURL here, it's handled by useEffect
        }
    };

    useEffect(() => {
        // Cleanup for downloadUrl
        let currentDownloadUrl = downloadUrl;
        return () => {
            if (currentDownloadUrl) {
                window.URL.revokeObjectURL(currentDownloadUrl);
            }
        };
    }, [downloadUrl]);

    // useEffect para limpiar URLs de blob de imágenes ya no es necesario aquí
    // porque se maneja en closePreviewModal y fetchFileContent

    const extractZipContents = async (zipBlob) => {
        try {
            const zip = await JSZip.loadAsync(zipBlob);
            const files = [];
            const loadTextFilesPromises = [];

            zip.forEach((relativePath, zipEntry) => {
                if (!zipEntry.dir) { // Ignorar directorios
                    const file = {
                        name: zipEntry.name,
                        content: null,
                        type: getFileType(zipEntry.name),
                        zipEntry: zipEntry,
                    };
                    files.push(file);

                    if (file.type === 'text') {
                        loadTextFilesPromises.push(
                            zipEntry.async('string').then(text => {
                                file.content = text;
                            }).catch(err => {
                                console.error(`Error loading text file ${file.name}:`, err);
                                file.content = `Error loading content: ${err.message}`;
                            })
                        );
                    }
                }
            });

            await Promise.all(loadTextFilesPromises);
            setZipFiles(files);
        } catch (error) {
            console.error('Error extracting ZIP contents:', error);
            setStatusMessage('Error processing ZIP for preview.');
            setMessageType('error');
        }
    };

    const getFileType = (filename) => {
        const ext = filename.split('.').pop().toLowerCase();
        if (['txt', 'cbl', 'cob', 'java', 'py', 'cs', 'js', 'html', 'css', 'md', 'json', 'xml', 'yaml', 'yml', 'log'].includes(ext)) {
            return 'text';
        } else if (ext === 'pdf') {
            return 'pdf';
        } else if (['jpg', 'jpeg', 'png', 'gif', 'svg', 'webp'].includes(ext)) {
            return 'image';
        } else {
            return 'other';
        }
    };

    const fetchFileContent = async (file) => {
        // Limpiar URL de blob de imagen anterior si existe
        if (selectedPreviewFile && selectedPreviewFile.isBlobUrl && selectedPreviewFile.content) {
            URL.revokeObjectURL(selectedPreviewFile.content);
        }

        if (file.type === 'pdf') {
            // Para PDF, solo preparamos para mostrar mensaje y botón de descarga
            // El contenido real se cargará al descargar si es necesario.
            setSelectedPreviewFile({ ...file, content: `This is a PDF file. Click below to download.` });
            setIsPreviewModalOpen(true);
        } else if (file.type === 'image') {
            try {
                const blobContent = await file.zipEntry.async('blob');
                const objectUrl = URL.createObjectURL(blobContent); // Create URL for image preview
                setSelectedPreviewFile({ ...file, content: objectUrl, isBlobUrl: true });
                setIsPreviewModalOpen(true);
            } catch (error) {
                console.error(`Error creating blob URL for image ${file.name}:`, error);
                setSelectedPreviewFile({ ...file, content: `Error loading image: ${error.message}` });
                setIsPreviewModalOpen(true);
            }
        } else if (file.type === 'text' && file.content !== null) {
             setSelectedPreviewFile(file);
             setIsPreviewModalOpen(true);
        } else if (file.type === 'other') {
            setSelectedPreviewFile({ ...file, content: `Cannot preview this file type directly.`});
            setIsPreviewModalOpen(true);
        } else { // Fallback for text files not pre-loaded or other unhandled cases
             try {
                const content = await file.zipEntry.async('string');
                setSelectedPreviewFile({ ...file, content });
                setIsPreviewModalOpen(true);
            } catch (error) {
                console.error(`Error fetching content for ${file.name}:`, error);
                setSelectedPreviewFile({ ...file, content: `Error loading content: ${error.message}` });
                setIsPreviewModalOpen(true);
            }
        }
    };

    const closePreviewModal = () => {
        setIsPreviewModalOpen(false);
        // Limpiar URL de blob de imagen si existe al cerrar el modal
        if (selectedPreviewFile && selectedPreviewFile.isBlobUrl && selectedPreviewFile.content) {
            URL.revokeObjectURL(selectedPreviewFile.content);
        }
        setSelectedPreviewFile(null);
    };
    
    const handleDownloadPreviewedFile = async (fileToDownload) => {
        if (!fileToDownload || !fileToDownload.zipEntry) return;
        try {
            const blob = await fileToDownload.zipEntry.async('blob');
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = fileToDownload.name;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
        } catch (error) {
            console.error('Error downloading file from preview:', error);
            setStatusMessage(`Error downloading ${fileToDownload.name}`);
            setMessageType('error');
        }
    };

    return (
        <div className="file-upload-container">
            <h2>AI Code Analysis and Migration</h2>

            <div className="language-select-container global-language-select">
                <label htmlFor="targetLanguage">Transcribe COBOL to:</label>
                <select
                    id="targetLanguage"
                    value={targetLanguage}
                    onChange={handleLanguageChange}
                    disabled={isUploading || isReprocessing || isAnalyzingRepo}
                >
                    <option value="">Summarize COBOL Only (No transcription)</option>
                    {['Java', 'Python', 'CSharp', 'JavaScript', 'C++', 'Ruby', 'PHP', 'Go', 'Swift', 'Kotlin'].map(lang => 
                        <option key={lang} value={lang}>{lang}</option>
                    )}
                </select>
            </div>
            
            <div className="input-section">
                <h3>Analyze a ZIP File</h3>
                <label htmlFor="archiveFile" className="file-input-label">Select ZIP File</label>
                <input
                    type="file"
                    id="archiveFile"
                    accept=".zip"
                    onChange={handleFileChange}
                    disabled={isUploading || isReprocessing || isAnalyzingRepo || !!repoUrl}
                    className="hidden-file-input"
                />
                {selectedFile && <p className="selected-file-name">Selected file: {selectedFile.name}</p>}
                <button
                    type="button"
                    onClick={handleUpload}
                    disabled={!selectedFile || isUploading || isReprocessing || isAnalyzingRepo}
                    className="action-button"
                >
                    {isUploading ? 'Processing ZIP...' : 'Upload and Analyze ZIP'}
                </button>
            </div>

            <div className="separator">OR</div>

            <div className="input-section">
                <h3>Analyze a GitHub Repository</h3>
                <input
                    type="text"
                    placeholder="Enter GitHub Repository URL (e.g., https://github.com/user/repo)"
                    value={repoUrl}
                    onChange={handleRepoUrlChange}
                    disabled={isUploading || isReprocessing || isAnalyzingRepo || !!selectedFile}
                    className="repo-url-input"
                />
                <button
                    type="button"
                    onClick={() => handleAnalyzeRepo(false)}
                    disabled={!repoUrl || isUploading || isReprocessing || isAnalyzingRepo || !!selectedFile}
                    className="action-button"
                >
                    {isAnalyzingRepo && !isReprocessing ? 'Analyzing Repo...' : 'Analyze Repository'}
                </button>
            </div>
            
            <div className="common-controls">
                <div className="button-group">
                    {hasProcessedResults && originalZipData && !isLastAnalysisRepo && (
                        <button
                            type="button"
                            onClick={handleReprocessZip}
                            disabled={isUploading || isReprocessing || isAnalyzingRepo}
                            className="reprocess-button"
                        >
                            {isReprocessing && !isAnalyzingRepo ? 'Reprocessing ZIP...' : 'Reprocess ZIP'}
                        </button>
                    )}
                    {hasProcessedResults && isLastAnalysisRepo && lastRepoUrl && (
                        <button
                            type="button"
                            onClick={handleReprocessRepo}
                            disabled={isUploading || isReprocessing || isAnalyzingRepo}
                            className="reprocess-button repo-reprocess-button"
                        >
                            {isReprocessing && isAnalyzingRepo ? 'Reprocessing Repo...' : 'Reprocess Repository'}
                        </button>
                    )}
                </div>
            </div>

            {statusMessage && (
                <p className={`message ${messageType}`}>
                    {statusMessage}
                </p>
            )}

            {downloadUrl && (
                <div className="download-section">
                    <button onClick={handleDownloadClick}>
                        Download "{downloadFilename || 'analysis_results.zip'}"
                    </button>
                    <button onClick={() => setIsPreviewModalOpen(true)} disabled={zipFiles.length === 0}>
                        Preview ZIP
                    </button>
                </div>
            )}

            {isPreviewModalOpen && (
                <div className="preview-modal">
                    <div className="preview-modal-content">
                        <span className="close-button" onClick={closePreviewModal}>&times;</span>
                        <h3>Files in ZIP</h3>
                        <ul className="preview-file-list">
                            {zipFiles.map((file, index) => (
                                <li
                                    key={index}
                                    onClick={() => fetchFileContent(file)}
                                    className={selectedPreviewFile?.name === file.name ? 'selected-file-preview' : ''}
                                >
                                    {file.name}
                                </li>
                            ))}
                        </ul>
                        {selectedPreviewFile && (
                            <div className="preview-content-area">
                                <h4>Preview: {selectedPreviewFile.name}</h4>
                                {selectedPreviewFile.type === 'text' && (
                                    <pre className="preview-text">{selectedPreviewFile.content}</pre>
                                )}
                                {selectedPreviewFile.type === 'pdf' && (
                                    <div className="pdf-placeholder-preview">
                                        <p>{selectedPreviewFile.content || "PDF file. Click download to view."}</p>
                                    </div>
                                )}
                                {selectedPreviewFile.type === 'image' && selectedPreviewFile.content && (
                                    selectedPreviewFile.isBlobUrl ?
                                    <img src={selectedPreviewFile.content} alt={selectedPreviewFile.name} className="preview-image" />
                                    : <p className="preview-text error-text">{selectedPreviewFile.content}</p> 
                                )}
                                {selectedPreviewFile.type === 'other' && (
                                    <p className="preview-text">
                                        {selectedPreviewFile.content || "Cannot preview this file type directly."}
                                    </p>
                                )}
                                <button 
                                    onClick={() => handleDownloadPreviewedFile(selectedPreviewFile)} 
                                    className="download-previewed-file-button"
                                    disabled={!selectedPreviewFile.zipEntry}
                                >
                                    Download "{selectedPreviewFile.name}"
                                </button>
                            </div>
                        )}
                         {!selectedPreviewFile && zipFiles.length > 0 && (
                            <p className="preview-placeholder">Select a file from the list to preview.</p>
                        )}
                        {!zipFiles.length && (
                             <p className="preview-placeholder">No files found in the ZIP or ZIP is empty.</p>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}

export default FileUpload;
