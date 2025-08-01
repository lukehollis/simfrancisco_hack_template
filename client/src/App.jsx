import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import SFMap from './views/SFMap.jsx';

export default function App() {
  return (
    <BrowserRouter basename="/">
      {/* Global style override for KaTeX display alignment */}
      <style>{`
      .katex-display{ text-align:left !important; }
      .katex-display > .katex{ text-align:left !important; }

      `}</style>
      <Routes>
        <Route path="/simfrancisco_hack_template/" element={<SFMap />} />
      </Routes>
    </BrowserRouter>
  );
}
