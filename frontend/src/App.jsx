import React from 'react';
import './App.css'; // Mantén tus estilos CSS si los tienes
import FileUpload from './FileUpload.jsx'; // Importa tu componente

function App() {
  return (
    <div className="App">
      <header className="App-header">
        <h1>Aplicación de Análisis de Archivos</h1> {/* Título de tu app */}
      </header>
      <main>
        {/* Usa tu componente FileUpload */}
        <FileUpload />
      </main>
    </div>
  );
}

export default App;