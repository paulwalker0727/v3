import React from 'react';
import './cimitir.css';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Toaster } from '@/components/ui/sonner';
import { AppProvider } from './store';
import Layout from './components/Layout';
import Access from './pages/Access';
import Dashboard from './pages/Dashboard';
import { Locuri, Inhumati, Contracte, Plati, Concesionari } from './pages/Tables';
import Harta from './pages/Harta';
import Cereri from './pages/Cereri';
import Cauta from './pages/Cauta';
import Rapoarte from './pages/Rapoarte';
import Utilizatori from './pages/Utilizatori';
import { Setari, System, Cloud } from './pages/Admin';
import Portal from './pages/Portal';

function App() {
  return (
    <AppProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Navigate to="/admin" replace />} />
          <Route path="/admin/access" element={<Access />} />
          <Route path="/portal" element={<Portal />} />
          <Route path="/admin" element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="cauta" element={<Cauta />} />
            <Route path="harta" element={<Harta />} />
            <Route path="locuri" element={<Locuri />} />
            <Route path="inhumati" element={<Inhumati />} />
            <Route path="contracte" element={<Contracte />} />
            <Route path="concesionari" element={<Concesionari />} />
            <Route path="plati" element={<Plati />} />
            <Route path="cereri" element={<Cereri />} />
            <Route path="rapoarte" element={<Rapoarte />} />
            <Route path="utilizatori" element={<Utilizatori />} />
            <Route path="system" element={<System />} />
            <Route path="cloud" element={<Cloud />} />
            <Route path="setari" element={<Setari />} />
          </Route>
          <Route path="*" element={<Navigate to="/admin" replace />} />
        </Routes>
      </BrowserRouter>
      <Toaster position="top-center" richColors />
    </AppProvider>
  );
}

export default App;
